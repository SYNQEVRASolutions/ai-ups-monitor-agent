# AI UPS Monitor Agent

A small, working agent that reads UPS telemetry, learns what normal looks like on your site, and sends **one alert per incident** with what failed, what is affected, how urgent it is, and what to do next.

Built by [SYNQEVRA Solutions](https://github.com/SYNQEVRASolutions). No cloud account, no API key, no database. One file, standard library only.

```bash
python3 ai_ups_monitor.py --simulate --cycles 20
```

That runs a full cycle against synthetic telemetry with a fault injected at cycle 8, and prints alerts as JSON lines.

## The problem this solves

A UPS sends dozens of alarms a day. Almost none of them say what to do. Operators learn to ignore them, and the one that mattered gets lost in the noise. Meanwhile the real early signals (a slow battery voltage sag, a cabinet running hot, load creeping past the design point) never fire an alarm at all, because no threshold was set for them.

This agent flips that around: instead of fixed thresholds, it learns the **baseline at your site** from normal operation, then alerts on statistically unusual behaviour.

## What it does

1. **Reads** UPS metrics: output load, input voltage, battery bus voltage, battery temperature, estimated runtime.
2. **Learns** a rolling baseline (mean and standard deviation) per metric, using only cycles that were considered normal.
3. **Scores** each new sample in sigma units against that baseline.
4. **Alerts** only past a per-rule threshold, with a cooldown so the same rule cannot repeat for 15 minutes.
5. **Explains** every alert in four fields: what failed, what is affected, how urgent, what to do now.

## Install

Python 3.10 or newer. Nothing else is required for simulation mode.

```bash
git clone https://github.com/SYNQEVRASolutions/ai-ups-monitor-agent.git
cd ai-ups-monitor-agent
python3 ai_ups_monitor.py --simulate --cycles 20
```

For real hardware, install the SNMP client:

```bash
pip install pysnmp
python3 ai_ups_monitor.py --host 10.0.0.20 --community public --cycles 5
```

## Example output

```json
{
  "rule": "battery_temp_high",
  "severity": "medium",
  "metric": "temp_c",
  "value": 32.9,
  "baseline": 27.04,
  "deviation_sigma": 22.18,
  "what_failed": "Battery cabinet temperature is above baseline. Heat shortens battery life.",
  "what_is_affected": "UPS output on this unit; downstream loads share the same bus",
  "what_to_do": "Check cabinet airflow and cooling setpoint.",
  "ts": "2026-11-08T06:06:45+00:00"
}
```

Pipe it into anything: email, Slack webhook, Telegram bot, or your ticketing system.

```bash
python3 ai_ups_monitor.py --host 10.0.0.20 --webhook https://example.com/alerts
```

## Rules included

| Rule | Metric | Direction | Fires when |
|---|---|---|---|
| `mains_voltage_drop` | input voltage | low | 3 sigma below baseline |
| `battery_voltage_sag` | battery voltage | low | 3 sigma below baseline |
| `battery_temp_high` | cabinet temperature | high | 2.5 sigma above baseline |
| `load_over_baseline` | output load | high | 3 sigma above baseline |
| `runtime_low` | estimated runtime | low | 2.5 sigma below baseline |

Add your own in the `RULES` list. Each rule is one line of data, not code.

## How the baseline stays honest

Only cycles with no alerts feed the baseline. If a fault persists, the baseline does not "learn" the fault as normal, which is the usual failure mode of naive anomaly detection. The rolling window is 24 samples by default (`Baseline(window=...)`).

## Why not just use fixed thresholds?

Fixed thresholds need a per-site, per-model tuning exercise, and they go stale the moment your load changes. Baselines adapt, so a UPS that is normally at 40 percent load and one at 80 percent load both get correct alerts without separate configuration.

For sites with strict compliance requirements, keep your existing threshold alarms. Run this alongside them as the early-warning layer.

## Limitations, stated plainly

- SNMP OID mapping is for the RFC 1628 UPS-MIB. Vendor private MIBs need their OIDs added to `UPS_MIB`.
- The baseline is a simple statistical model, not a trained prediction model. It catches drift and abrupt change, not long-horizon battery degradation curves.
- Single unit per process. Run one process per UPS, or extend the reader to loop over a device list.
- Polling interval is set by the caller, not internally scheduled. Use cron or systemd timer.

## Extending it

The natural next steps, in order of value:

1. Read generator and chiller telemetry too, so one alert can cover the whole power and cooling chain.
2. Correlate rules, so a voltage drop plus a battery sag becomes one incident with both facts.
3. Push the same alerts to WhatsApp or your ticketing tool.

## Need this built for your site?

If your data center, plant or workshop needs this, contact SYNQEVRA Solutions.

- Email: fatircompanyai@gmail.com
- GitHub: [github.com/SYNQEVRASolutions](https://github.com/SYNQEVRASolutions)

We build and deploy these agents against the equipment you already own.
