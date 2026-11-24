# ha-timekprnext

Home Assistant control of [Timekpr-nExT](https://launchpad.net/timekpr-next) on Arch/Manjaro PCs, with **one shared weekly time budget per kid across all PCs**.

- **tk-budget** (Home Assistant add-on) sums usage per kid and ISO week, owns limit switch, budget, weekly adjustment and time-up action, keeps daily statistics (SQLite), publishes `remaining` and the HA entities via MQTT discovery.
- **tk-agent** (each PC, root, systemd) measures usage of every timekpr user through the `timekpra` CLI, reports it, and enforces the remaining time as timekpr's "time left today" for kids whose limit is active.
- **tk_protocol** (`protocol/`) is shared by both.

Design: `docs/superpowers/specs/2026-11-16-timekpr-ha-design.md`. Spike findings: `spike/RESULTS.md`.

## Behaviour worth knowing

- **Kids appear by themselves.** Every user in timekpr on any PC (`timekpra --userlist`) shows up in HA as "Screen time <user>" after its first report, parents included. The user name is the kid id, so it must be the same on every PC and match `[a-z0-9_-]+`.
- **Limits are opt-in.** "Limit active" off (default): no limit, the agent writes nothing to timekpr; usage is still counted. On: the weekly budget applies; no budget set = 0 minutes. Turning it off releases a locked kid (time left back to the end of the day). timekpr's own daily limit, if configured there, still applies.
- **Fail closed.** Without a fresh `remaining` the agent keeps enforcing the last one it received, minus what the PC used since; it never grants unlimited time. The last value survives a reboot without broker.
- Adjustments (`+15 min` / `−15 min`) apply to the current week only and reset on Monday.
- Two PCs used at once drain the budget twice as fast. Overshoot is bounded by about one or two report intervals (45 s).
- At midnight timekpr resets "time left" to its own daily limit; the agent writes the budget again within one interval.
- Each PC's id is its host name (lower case, first label). A timekpr user with the same name as a PC is skipped. Override with `host_id` in `/etc/tk-agent.conf` if needed.
- To remove a user from HA, delete its device there (Settings → Devices).

## 1. Broker (Mosquitto add-on)

Nothing to change in Mosquitto. Requirements: the Mosquitto broker add-on runs and the MQTT integration is set up in HA.

- **tk-budget** gets its broker login from the Supervisor automatically (`services: mqtt:need`).
- **All PCs** log in with one Home Assistant user (the Mosquitto add-on accepts HA users): HA → Settings → People → Users → Add user, user name `tk-agent`, not an administrator, "Can only log in from the local network". If HA rejects the `-`, use `tk_agent`. Put this user name and password into every PC's `/etc/tk-agent.conf`.

Without an ACL every MQTT user may read and write every topic (the add-on's default). The kid cannot use the PCs' login: `/etc/tk-agent.conf` is readable by root only. Optional hardening: activate `mosquitto/acl.example` via the Mosquitto add-on's `customize` option — first add the add-on's internal users `homeassistant` and `addons` and every existing device user with `topic readwrite #`, or they lose access.

## 2. tk-budget add-on

1. HA → Settings → Add-ons → Add-on store → ⋮ → Repositories → add this repository's URL.
2. Install **tk-budget**, set the options:
   - `db_path`: keep `/data/tk-budget.db` (statistics live there and are kept with add-on backups)
   - `broker_host` / `broker_port` / `broker_user` / `broker_password`: leave empty; only to use a broker other than the Mosquitto add-on
3. Start it. The log shows `using MQTT broker core-mosquitto:1883 as addons`. Once the PCs report, per user a device "Screen time <user>" appears with: Limit active, Remaining total, Total time today, Weekly total budget, +15 min, −15 min, Time-up action.
4. For each kid: set the weekly total budget, then turn on "Limit active".

## 3. tk-agent on each PC

```sh
git clone <repository-url> ha-timekprnext
cd ha-timekprnext/tk-agent/packaging
makepkg -si                         # depends: python, python-paho-mqtt, timekpr-next
sudoedit /etc/tk-agent.conf         # HA host + the shared HA user tk-agent (same file on every PC)
sudo systemctl enable --now tk-agent
journalctl -u tk-agent -f
```

`/etc/tk-agent.conf` is installed with mode 600 (it holds the MQTT password) and kept on upgrades. Kids are the timekpr users: add a kid to timekpr on a PC and it appears in HA after the next report (≤ 45 s).

## Example dashboard card

Entity ids for kid `kid-a` (HA turns `-` into `_`):

```yaml
type: entities
title: Screen time kid-a
entities:
  - entity: switch.tk_kid_a_limit_active
    name: Limit active
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
```

## Development

Tests per package (Python ≥ 3.11, pytest):

```sh
(cd protocol && python -m pytest -q)
(cd tk-budget && python -m pytest -q)
(cd tk-agent && python -m pytest -q)
```

The add-on is built by the HA Supervisor with `tk-budget/` as Docker context, so it ships a copy of `tk_protocol` in `tk-budget/vendor/`. After changing `protocol/`, run `scripts/sync-protocol.sh` (a test fails if the copy is stale).
