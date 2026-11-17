# ha-timekprnext

Home Assistant control of [Timekpr-nExT](https://launchpad.net/timekpr-next) on Arch/Manjaro PCs, with **one shared weekly time budget per kid across all PCs**.

- **tk-budget** (Home Assistant add-on) sums usage per kid and ISO week, owns budget, weekly adjustment and time-up action, keeps daily statistics (SQLite), publishes `remaining` and the HA entities via MQTT discovery.
- **tk-agent** (each PC, root, systemd) measures usage through the `timekpra` CLI, reports it, and enforces the remaining time as timekpr's "time left today".
- **tk_protocol** (`protocol/`) is shared by both.

Design: `docs/superpowers/specs/2026-11-16-timekpr-ha-design.md`. Spike findings: `spike/RESULTS.md`.

## Behaviour worth knowing

- **Fail closed.** Without a fresh `remaining` the agent keeps enforcing the last one it received, minus what the PC used since; it never grants unlimited time. The last value survives a reboot without broker.
- **New kid = 0 minutes** until a weekly budget is set in HA.
- Adjustments (`+15 min` / `−15 min`) apply to the current week only and reset on Monday.
- Two PCs used at once drain the budget twice as fast. Overshoot is bounded by about one or two report intervals (45 s).
- At midnight timekpr resets "time left" to its own daily limit; the agent writes the budget again within one interval.
- Host ids and kid ids share the `timekpr/` topic namespace: they must be different (`[a-z0-9_-]+`).

## 1. Broker (Mosquitto)

Create one MQTT user per PC (`tk-agent-<client-host>`), one for the add-on (`tk-budget`) and use your existing HA MQTT user. Merge `mosquitto/acl.example` into the broker's ACL file and restart Mosquitto.

## 2. tk-budget add-on

1. HA → Settings → Add-ons → Add-on store → ⋮ → Repositories → add this repository's URL.
2. Install **tk-budget**, set the options:
   - `broker_host` / `broker_port` / `broker_user` / `broker_password`
   - `kids`: list of kid ids, e.g. `kid-a`
   - `db_path`: keep `/data/tk-budget.db` (statistics live there and are kept with add-on backups)
3. Start it. Per kid a device "Screen time <kid>" appears with: Remaining total, Total time today, Weekly total budget, +15 min, −15 min, Time-up action.
4. Set the weekly total budget.

## 3. tk-agent on each PC

```sh
git clone <repository-url> ha-timekprnext
cd ha-timekprnext/tk-agent/packaging
makepkg -si                         # depends: python, python-paho-mqtt, timekpr-next
sudoedit /etc/tk-agent.conf         # broker, host_id, [kids] <timekpr-user> = <kid-id>
sudo systemctl enable --now tk-agent
journalctl -u tk-agent -f
```

`/etc/tk-agent.conf` is installed with mode 600 (it holds the MQTT password) and kept on upgrades. The PC appears in HA as "PC <host_id>" with an online sensor.

## Example dashboard card

Entity ids for kid `kid-a` and PC `<client-host>` (HA turns `-` into `_`):

```yaml
type: entities
title: Screen time kid-a
entities:
  - entity: sensor.tk_kid_a_remaining_total
    name: Remaining this week
  - entity: sensor.tk_kid_a_total_today
    name: Used today
  - entity: number.tk_kid_a_weekly_total_budget
    name: Weekly budget
  - type: buttons
    entities:
      - entity: button.tk_kid_a_adjust_plus_15
        name: +15 min
        icon: mdi:plus-circle
      - entity: button.tk_kid_a_adjust_minus_15
        name: −15 min
        icon: mdi:minus-circle
  - entity: select.tk_kid_a_time_up_action
    name: When time is up
  - entity: binary_sensor.tk_<client-host>_online
    name: PC online
```

## Development

Tests per package (Python ≥ 3.11, pytest):

```sh
(cd protocol && python -m pytest -q)
(cd tk-budget && python -m pytest -q)
(cd tk-agent && python -m pytest -q)
```

The add-on is built by the HA Supervisor with `tk-budget/` as Docker context, so it ships a copy of `tk_protocol` in `tk-budget/vendor/`. After changing `protocol/`, run `scripts/sync-protocol.sh` (a test fails if the copy is stale).
