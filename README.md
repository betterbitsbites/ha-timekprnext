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
- timekpr ignores writes for users who are not logged in, so a kid is limited from the first report after login (≤ 45 s); until then timekpr's own settings apply.
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
   - `familylink`: optional, kids with a Google Family Link sensor (see below)
   - `broker_host` / `broker_port` / `broker_user` / `broker_password`: leave empty; only to use a broker other than the Mosquitto add-on
3. Start it. The log shows `using MQTT broker core-mosquitto:1883 as addons`. Once the PCs report, per user a device "Screen time <user>" appears with: the limit numbers and switches below, Remaining total, Remaining this week / today, Week left / Today left (percent), Total time today, +15 min, −15 min, Time-up action, plus the play, session and button entities described below.
4. For each kid: set a limit and turn on its switch.

### Limits

Each limit is a number plus a switch; a limit applies only while its switch is on (default off). The effective "Remaining total" is the smallest remaining time of all active limits.

| Limit | Number | Switch |
|---|---|---|
| Weekly (all PCs) | Weekly total budget | Weekly limit active |
| Daily (all PCs) | Daily total limit | Daily limit active |
| Daily, PC + phone (Family Link kids only) | Daily combined limit | Combined limit active |

Per limit there is a sensor "Remaining this week / today / combined" (minutes) and a percent sensor "Week left / Today left / Combined left" (state: percent of that limit left, unknown while the limit is off; attributes `remaining_min` and `limit_min`). The +15 / −15 min buttons add to this week's and today's allowance only; they do not change the saved limits.

### Play time (optional)

A separate play budget per kid: weekly and daily play limits, each with its own number and switch (default off), plus "Play +15 / −15 min" buttons, "Play time today", "Remaining play" and the percent sensors "Play week left / Play today left". Play is capped by the total: the effective remaining play time is never more than "Remaining total".

- **PlayTime must be enabled in timekpr** for the kid, with activity masks (process names) for the games or launchers. Do not rely on the `PLAYTIME_ENABLED` flag in `timekpra --getuserinfo`; it can be stale.
- **Play counts only while a matching process runs.** A running Steam client counts as play if a mask matches it. Locked screen time counts for neither total nor play (timekpr's `TRACK_INACTIVE` off).
- **timekpr's own limits must not bind**, because the lower limit always wins: HA governs. On every PC, per kid (values in seconds):

  ```sh
  sudo timekpra --settimelimitweek <kid-user> 604800        # weekly limit: 7 days
  sudo timekpra --setalloweddays <kid-user> '1;2;3;4;5;6;7' # all days allowed (check with `timekpra --help`)
  sudo timekpra --settimelimits <kid-user> '86400;86400;86400;86400;86400;86400;86400'          # daily: 24 h per allowed day
  sudo timekpra --settimelimitmonth <kid-user> 2678400      # monthly limit: 31 days
  sudo timekpra --setplaytimeenabled <kid-user> true
  sudo timekpra --setplaytimealloweddays <kid-user> '1;2;3;4;5;6;7'  # PlayTime on all days (check with `timekpra --help`)
  sudo timekpra --setplaytimelimits <kid-user> '86400;86400;86400;86400;86400;86400;86400'      # PlayTime daily: 24 h
  ```

  The option names are taken from our timekpr notes and `spike/probe-play.sh`, not from a captured `timekpra --help`; `--setalloweddays` and its value format in particular are unverified, so check them with `timekpra --help`. Allowed hours (`--setallowedhours`) are a separate restriction: HA does not lift them, so only restrict hours there on purpose. PlayTime also needs the global switch `TIMEKPR_PLAYTIME_ENABLED = True` in `/etc/timekpr/timekpr.conf` (then `systemctl restart timekpr`).
- The agent writes the remaining play time with `timekpra --setplaytimeleft`. If timekpr does not apply it (for example PlayTime is off), the agent logs one warning "PlayTime seems off" and leaves play alone, retrying every 15 minutes, on the next day and when a play limit is switched on or off.
- **Play limits are verified on real hardware only once** `sudo spike/probe-play.sh write` (test user only) has been run. Until then, watch the agent log (`journalctl -u tk-agent`) for "PlayTime seems off".

### Sessions

The sensor "Sessions" shows how many login sessions the kid has across all PCs (only `Class=user` sessions count, sessions already closing do not; the attributes list `locked` and `by_host`; a PC that is offline counts 0). Two buttons act on all PCs of the kid:

- **End inactive sessions** ends sessions that are locked or not the active one.
- **Log out everywhere** terminates all sessions of the kid (`loginctl terminate-user`).

Agents run an action as soon as the message arrives; only the session count refreshes on the next report (≤ 45 s). They ignore actions older than 2 minutes, so a PC that was offline does not log the kid out later. This needs the HA host and the PCs within 2 minutes of each other and in the same timezone: keep their clocks synced (NTP).

### Presence and remote links

The sensor "Presence" says where the kid is logged in, e.g. `pc1: active (Wayland) · pc2: locked (X11)` (or `not logged in`). Its attribute `where` is a list with one entry per PC where the kid has a session: `host`, `state` (`active`, `locked`, ...; `none` from old agents), `kind` (`wayland`, `x11`, `tty`; `none` from old agents), `connect` (the remote-desktop URL) and `link` (`/connect/<kid>/<host>`, empty without a URL). The example card below shows it with a connect link per PC.

- **The agent reports the connect URL** from `[remote]` in `/etc/tk-agent.conf`: Wayland sessions get `rdp://<kid>@<host>:<rdp_port>`, X11 sessions `vnc://<host>:<vnc_base_port + display number>` (display `:1` is port 5901 with the default base 5900). `host` defaults to the PC's host name; set a host name or an IPv4 address (not IPv6) your PC can reach. Defaults: `rdp_port = 3389`, `vnc_base_port = 5900`.
- **The PC must offer the service.** For Wayland (Plasma) `krdp` has to be set up in the kid's own Plasma session; for X11 a VNC server per display (display number + base port). The agent only reports the URL, it does not install or start anything.
- **Your Linux desktop needs a handler for `rdp://` and `vnc://`**: KRDC or Remmina.
- **Why a redirect?** HA's markdown card drops `rdp://` and `vnc://` links, so the card links to `http://<ha-host>:8099/connect/<kid>/<host>` and tk-budget answers with a 302 to the current connect URL. The add-on option `link_port` stays at 8099; to use another port on the HA host, change the host-side port in the add-on's Network settings.
- **Port 8099 has no authentication and is for the LAN only.** It serves nothing but redirects to `rdp://` and `vnc://` URLs (everything else is a 404); the redirect itself does not log anybody in. If the port cannot be bound, the add-on logs an error ("remote links disabled") and runs on without links.

### Apply feedback (hourglass)

After you change anything as a parent (limits, switches, the time-up action, the ±15 min buttons; each input bumps the revision), the agent wakes at once and applies it (no waiting for the next 45 s interval), then acknowledges the revision it applied. The binary sensor "Applying" is on until every PC that matters has acknowledged:

- It waits only for PCs that are online, where the kid is logged in, and whose agent has acknowledged before (agent 0.3.0 or later). PCs with an older agent, PCs without a session and offline PCs are not waited for.
- At most 120 s after the change (in practice up to about 150 s, as the check runs every 30 s); then it goes off and the add-on log says on which PCs the revision was not applied.
- It works from the first change on, once a PC with agent 0.3.0 has received a remaining value and the kid is logged in there. A PC where the kid only has a text or ssh session that timekpr does not track may not acknowledge; the hourglass then ends after the 120 s timeout.
- The example card shows an hourglass while it is on.

### Upgrade to add-on 0.6.0 / agent 0.3.0

Rebuild and install the package on every PC (`makepkg -si`, tk-agent 0.3.0) and update the add-on to 0.6.0. Presence, remote links and the hourglass need both; with an old agent the sensor "Presence" shows the sessions without state, kind or link and the hourglass does not wait for that PC. Add `[remote]` to `/etc/tk-agent.conf` if the host name is not what your PC can reach (see `packaging/tk-agent.conf.example`). In the add-on's Network settings port 8099 appears after the update.

### Upgrade to add-on 0.5.0 / agent 0.2.0

Rebuild and install the package on every PC (`makepkg -si`, tk-agent 0.2.0) and update the add-on to 0.5.0. On PCs still running agent 0.1.x the total limits keep working, but **play limits are not enforced** there and no sessions are reported.

### Phone time from Google Family Link (optional)

1. Install the HACS integration "Google Family Link" (github.com/noiwid/HAFamilyLink) and its authentication add-on, and make sure it provides a daily screen-time sensor for the child.
2. Map the kid to that sensor in the tk-budget options:

   ```yaml
   familylink:
     - kid: <kid-user>
       entity: sensor.<child>_daily_screen_time
   ```

3. Such kids get the entities "Phone time today", "Daily combined limit", "Combined limit active", "Remaining combined" and "Combined left". Phone minutes count only against the combined daily limit, not against the weekly or the PC-only daily limit. A midnight guard at 00:15 keeps yesterday's phone value from being counted for the new day.

## 3. tk-agent on each PC

```sh
git clone <repository-url> ha-timekprnext
cd ha-timekprnext/tk-agent/packaging
makepkg -si                         # depends: python, python-paho-mqtt, timekpr-next
sudoedit /etc/tk-agent.conf         # HA host + the shared HA user tk-agent (same file on every PC)
sudo systemctl enable --now tk-agent
journalctl -u tk-agent -f
```

`/etc/tk-agent.conf` is installed with mode 600 (it holds the MQTT password) and kept on upgrades. The optional `[remote]` section sets the host and ports of the remote-desktop links (see Presence and remote links). Kids are the timekpr users: add a kid to timekpr on a PC and it appears in HA after the next report (≤ 45 s).

## Example dashboard card

Entity ids for kid `kid-a` (`-` becomes `_`). tk-budget ≥ 0.3.1 sets them via `default_entity_id`; HA applies that only when it first creates an entity. Devices created by 0.3.0 got ids like `sensor.screen_time_kid_a_remaining_total`: delete the device in HA and restart tk-budget to get the ids below (budget and settings are kept in tk-budget).

The card below is one whole card element. The five tiles show the percent left per limit (week, today, combined, play week, play today) with a bar (tile feature `bar-gauge`) and the remaining minutes as secondary text; the combined and phone rows exist only for kids with a Family Link mapping, remove them otherwise. At the top a conditional card shows an hourglass while "Applying" is on, and a markdown card lists where the kid is logged in with a connect link per PC (replace `<ha-host>` with your HA host name or IP; the link goes through port 8099, see Presence and remote links).

```yaml
type: vertical-stack
cards:
  - type: conditional
    conditions:
      - condition: state
        entity: binary_sensor.tk_kid_a_applying
        state: "on"
    card:
      type: tile
      entity: binary_sensor.tk_kid_a_applying
      name: Applying
      icon: mdi:timer-sand
  - type: markdown
    content: >-
      {% set w = state_attr('sensor.tk_kid_a_presence', 'where') or [] %}
      {% set kinds = {'wayland': 'Wayland', 'x11': 'X11', 'tty': 'Text'} %}

      {% if w | count == 0 %}kid-a is not logged in.{% endif %}

      {% for p in w %}**{{ p.host }}**: {{ 'logged in' if p.state == 'none' else p.state }}{% if p.kind in kinds %} ({{ kinds[p.kind] }}){% endif %}{% if p.link %} - [connect](http://<ha-host>:8099{{ p.link }}){% endif %}

      {% endfor %}
  - type: tile
    entity: sensor.tk_kid_a_week_left_pct
    name: Week (min left)
    state_content:
      - state
      - remaining_min
    features:
      - type: bar-gauge
        min: 0
        max: 100
  - type: tile
    entity: sensor.tk_kid_a_today_left_pct
    name: Today (min left)
    state_content:
      - state
      - remaining_min
    features:
      - type: bar-gauge
        min: 0
        max: 100
  - type: tile
    entity: sensor.tk_kid_a_combined_left_pct
    name: PC + phone (min left)
    state_content:
      - state
      - remaining_min
    features:
      - type: bar-gauge
        min: 0
        max: 100
  - type: tile
    entity: sensor.tk_kid_a_play_week_left_pct
    name: Play week (min left)
    state_content:
      - state
      - remaining_min
    features:
      - type: bar-gauge
        min: 0
        max: 100
  - type: tile
    entity: sensor.tk_kid_a_play_today_left_pct
    name: Play today (min left)
    state_content:
      - state
      - remaining_min
    features:
      - type: bar-gauge
        min: 0
        max: 100
  - type: entities
    title: Screen time kid-a
    entities:
      - entity: sensor.tk_kid_a_remaining_total
        name: Remaining (effective)
      - entity: sensor.tk_kid_a_total_today
        name: Used today
      - entity: sensor.tk_kid_a_phone_today
        name: Phone today
      - entity: switch.tk_kid_a_limit_active
        name: Weekly limit active
      - entity: number.tk_kid_a_weekly_total_budget
        name: Weekly limit
      - entity: switch.tk_kid_a_daily_limit_active
        name: Daily limit active
      - entity: number.tk_kid_a_daily_total_limit
        name: Daily limit
      - entity: switch.tk_kid_a_combined_limit_active
        name: Combined limit active
      - entity: number.tk_kid_a_daily_combined_limit
        name: Combined limit
      - type: buttons
        entities:
          - entity: button.tk_kid_a_adjust_plus_15
            name: +15 min
            icon: mdi:plus-circle
          - entity: button.tk_kid_a_adjust_minus_15
            name: −15 min
            icon: mdi:minus-circle
      - entity: sensor.tk_kid_a_play_today
        name: Play today
      - entity: sensor.tk_kid_a_remaining_play
        name: Remaining play
      - entity: switch.tk_kid_a_play_limit_active
        name: Weekly play limit active
      - entity: number.tk_kid_a_weekly_play_budget
        name: Weekly play limit
      - entity: switch.tk_kid_a_daily_play_active
        name: Daily play limit active
      - entity: number.tk_kid_a_daily_play_limit
        name: Daily play limit
      - type: buttons
        entities:
          - entity: button.tk_kid_a_play_plus_15
            name: Play +15 min
            icon: mdi:plus-circle
          - entity: button.tk_kid_a_play_minus_15
            name: Play −15 min
            icon: mdi:minus-circle
      - entity: sensor.tk_kid_a_sessions
        name: Sessions
      - type: buttons
        entities:
          - entity: button.tk_kid_a_end_inactive_sessions
            name: End inactive sessions
            icon: mdi:account-lock
            tap_action:
              action: perform-action
              perform_action: button.press
              target:
                entity_id: button.tk_kid_a_end_inactive_sessions
              confirmation:
                text: End kid-a's inactive sessions on all PCs?
          - entity: button.tk_kid_a_logout
            name: Log out everywhere
            icon: mdi:logout
            tap_action:
              action: perform-action
              perform_action: button.press
              target:
                entity_id: button.tk_kid_a_logout
              confirmation:
                text: Log kid-a out on all PCs?
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
