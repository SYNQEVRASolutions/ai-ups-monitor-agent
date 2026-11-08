#!/usr/bin/env python3
"""AI UPS monitor agent - a small, working baseline-and-alert agent for UPS telemetry.

Reads UPS metrics either from a simulator (default, no hardware) or from a real
SNMP agent (pysnmp required), learns a baseline per metric, and emits one alert
per incident with what failed, what is affected, how urgent it is, and what to do.

Run:
    python3 ai_ups_monitor.py --simulate --cycles 20
    python3 ai_ups_monitor.py --host 10.0.0.20 --community public --cycles 5

Output: JSON alerts to stdout (and to --webhook if given).
No cloud service, no API key, no external database.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone


# ---------------------------------------------------------------- telemetry ---

@dataclass
class Sample:
    ts: str
    load_pct: float          # UPS output load percentage
    input_v: float           # input voltage
    battery_v: float         # battery bus voltage
    temp_c: float            # battery / cabinet temperature
    runtime_min: float       # estimated runtime on battery


def simulate_cycle(cycle: int) -> Sample:
    """Deterministic-ish synthetic telemetry with an injected fault after cycle 8."""
    fault = cycle >= 8
    load = 42 + 4 * random.random() + (2 if cycle % 5 == 0 else 0)
    return Sample(
        ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        load_pct=round(load, 1),
        input_v=round(230 + random.uniform(-3, 3) - (18 if fault else 0), 1),
        battery_v=round(54.2 + random.uniform(-0.2, 0.2) - (0.9 if fault else 0), 2),
        temp_c=round(27 + random.uniform(-0.5, 0.5) + (6 if fault else 0), 1),
        runtime_min=round(38 - load * 0.35, 1),
    )


# SNMP OIDs commonly exposed by UPS vendors (RFC 1628 UPS-MIB).
UPS_MIB = {
    "load_pct": "1.3.6.1.2.1.33.1.4.4.1.5.1",       # upsOutputPercentLoad
    "input_v": "1.3.6.1.2.1.33.1.3.3.1.3.1",        # upsInputVoltage
    "battery_v": "1.3.6.1.2.1.33.1.2.5.0",          # upsBatteryVoltage
    "temp_c": "1.3.6.1.2.1.33.1.2.7.0",             # upsBatteryTemperature
    "runtime_min": "1.3.6.1.2.1.33.1.2.3.0",        # upsEstimatedMinutesRemaining
}


def read_snmp(host: str, community: str) -> Sample:
    """Read one cycle from a real UPS over SNMPv2c. Requires pysnmp."""
    from pysnmp.hlapi import (CommunityData, ContextData, ObjectIdentity, ObjectType,
                              SnmpEngine, UdpTransportTarget, getCmd)  # noqa: WPS433

    values = {}
    for name, oid in UPS_MIB.items():
        it = getCmd(SnmpEngine(), CommunityData(community, mpModel=1),
                    UdpTransportTarget((host, 161), timeout=2, retries=1),
                    ContextData(), ObjectType(ObjectIdentity(oid)))
        err_ind, err_status, _, var_binds = next(it)
        if err_ind or err_status:
            raise RuntimeError(f"SNMP read failed for {name} ({oid})")
        values[name] = float(var_binds[0][1])
    return Sample(ts=datetime.now(timezone.utc).isoformat(timespec="seconds"), **values)


# ------------------------------------------------------------- baseline ------

@dataclass
class Baseline:
    """Rolling mean/stdev per metric, learned from normal operation only."""
    window: int = 24
    history: dict[str, list[float]] = field(default_factory=dict)

    def add(self, s: Sample) -> None:
        for k, v in s.__dict__.items():
            if k == "ts":
                continue
            self.history.setdefault(k, []).append(float(v))
            self.history[k] = self.history[k][-self.window:]

    def ready(self) -> bool:
        return all(len(v) >= 6 for v in self.history.values())

    def z(self, name: str, value: float) -> float:
        data = self.history.get(name) or []
        if len(data) < 6:
            return 0.0
        mean = statistics.fmean(data)
        sd = statistics.pstdev(data) or 1e-6
        return (value - mean) / sd

    def mean(self, name: str) -> float:
        data = self.history.get(name) or [0.0]
        return statistics.fmean(data)


# ------------------------------------------------------------------ rules -----

RULES = [
    # name, metric, direction, z-threshold, severity, message, action
    ("mains_voltage_drop", "input_v", -1, 3.0, "high",
     "Input voltage is well below the learned baseline. Likely mains dip or upstream breaker issue.",
     "Check the upstream breaker and utility feed; confirm the UPS is on battery or bypass."),
    ("battery_voltage_sag", "battery_v", -1, 3.0, "high",
     "Battery bus voltage is sagging below baseline. Battery string may be failing.",
     "Run a battery test and check for a weak block before the next outage."),
    ("battery_temp_high", "temp_c", 1, 2.5, "medium",
     "Battery cabinet temperature is above baseline. Heat shortens battery life.",
     "Check cabinet airflow and cooling setpoint."),
    ("load_over_baseline", "load_pct", 1, 3.0, "medium",
     "Output load is above the learned baseline. Capacity headroom is shrinking.",
     "Review what was added on this circuit and check runtime at current load."),
    ("runtime_low", "runtime_min", -1, 2.5, "high",
     "Estimated runtime on battery is below baseline for this load.",
     "Confirm runtime against load and consider load shedding."),
]


def evaluate(sample: Sample, base: Baseline) -> list[dict]:
    alerts = []
    if not base.ready():
        return alerts
    for name, metric, direction, threshold, severity, msg, action in RULES:
        value = float(getattr(sample, metric))
        z = base.z(metric, value) * direction
        if z >= threshold:
            alerts.append({
                "rule": name,
                "severity": severity,
                "metric": metric,
                "value": round(value, 2),
                "baseline": round(base.mean(metric), 2),
                "deviation_sigma": round(z, 2),
                "what_failed": msg,
                "what_is_affected": "UPS output on this unit; downstream loads share the same bus",
                "what_to_do": action,
                "ts": sample.ts,
            })
    return alerts


def dedupe(alerts: list[dict], open_rules: dict[str, str], cooldown_s: int = 900) -> list[dict]:
    """One alert per rule per cooldown window, so an operator does not get repeats."""
    now = datetime.now(timezone.utc).timestamp()
    out = []
    for a in alerts:
        last = open_rules.get(a["rule"])
        if last and now - last < cooldown_s:
            continue
        open_rules[a["rule"]] = now
        out.append(a)
    return out


def post(webhook: str, alert: dict) -> None:
    body = json.dumps(alert).encode()
    req = urllib.request.Request(webhook, data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()


# ------------------------------------------------------------------- main -----

def main() -> int:
    ap = argparse.ArgumentParser(description="Baseline-and-alert agent for UPS telemetry")
    ap.add_argument("--simulate", action="store_true", help="use synthetic telemetry (default)")
    ap.add_argument("--host", help="UPS IP for SNMP polling")
    ap.add_argument("--community", default="public", help="SNMP community string")
    ap.add_argument("--cycles", type=int, default=20, help="number of polling cycles")
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between cycles")
    ap.add_argument("--webhook", help="optional URL that receives each alert as JSON")
    ap.add_argument("--cooldown", type=int, default=900, help="seconds before the same rule can fire again")
    a = ap.parse_args()

    if not a.host and not a.simulate:
        a.simulate = True
    base, open_rules, fired = Baseline(), {}, 0
    print(f"mode={'simulate' if a.simulate else 'snmp:' + a.host}", file=sys.stderr)

    for cycle in range(a.cycles):
        sample = simulate_cycle(cycle) if a.simulate else read_snmp(a.host, a.community)
        fresh = dedupe(evaluate(sample, base), open_rules, a.cooldown)
        for alert in fresh:
            fired += 1
            line = json.dumps(alert)
            print(line)
            if a.webhook:
                try:
                    post(a.webhook, alert)
                except Exception as exc:  # webhook failure must not stop the agent
                    print(f"webhook failed: {exc}", file=sys.stderr)
        # only learn from cycles that did not alert: keeps the baseline clean
        if not fresh:
            base.add(sample)
        time.sleep(a.interval)

    print(f"cycles={a.cycles} alerts={fired} baseline_metrics={len(base.history)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
