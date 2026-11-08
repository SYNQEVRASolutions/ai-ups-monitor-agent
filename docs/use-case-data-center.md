# Use case: early warning on a colocation power chain

## Situation

A colocation site runs N+1 UPS units, two generators, and chilled water cooling spread across three vendor platforms. Each vendor has its own alarming. The facility manager's day starts with a phone call rather than a dashboard.

## What the agent does

Two processes per UPS, polling every 60 seconds over SNMP:

- one reader process per unit, using the RFC 1628 UPS-MIB OIDs in `UPS_MIB`
- one alert webhook per site, receiving JSON and posting into the operations channel

After roughly 30 minutes of normal operation the baseline is usable and alerts begin. In practice the first useful signal is usually a slow change that no threshold was ever set for: battery cabinet temperature rising across a season, or load creeping past the design point after a customer adds racks.

## What changes for the team

- Alerts carry an action, so the first responder does not need to interpret the alarm code.
- Repeated alarms for the same condition are suppressed for 15 minutes, which is what turns 40 daily alarms into a handful of incidents.
- The baseline adapts when load changes, so nobody re-tunes thresholds after adding equipment.

## What it does not replace

The site's existing threshold alarms stay in place, because compliance and warranty terms usually depend on them. This runs as the layer above: correlates, explains, and covers the slow drifts that thresholds miss.

## Scope of an implementation

Roughly one to three weeks for a single site, depending on how many devices need a private MIB added and whether alert delivery needs to land in an existing ticketing system.

Contact: fatircompanyai@gmail.com
