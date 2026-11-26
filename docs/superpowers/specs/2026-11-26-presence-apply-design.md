# Presence, remote links and apply feedback — design

Extends `2026-11-26-limits-play-sessions-design.md`. The human partner asked for it and said "build as
discussed"; the calls below are mine.

## Intent

1. **Where is the kid, and how do I get on that screen?** A dashboard field per kid lists every PC where
   the kid is logged in: active / locked / switched away, the session kind (Wayland, X11, text), and a
   link that opens the right remote viewer on the parent's **Linux desktop** — RDP (`krdp`, Plasma 6
   Wayland) or VNC (X11 sessions, port given). Icons for Android/Apple/Windows come later; only Linux now.
2. **Did the PC take my change yet?** After a parent changes a limit, a switch, the time-up action or
   presses ±15, an hourglass shows until every PC where the kid is logged in confirms it has applied it.
   Agents apply changes immediately instead of at the next 45 s tick.

## Non-goals

- Setting up `krdp`/x11vnc on the PCs (the human partner does that).
- Links for Android/Apple/Windows (later).
- Remote access to the login screen through this field (the field is per kid; the login screen is not).

## Design

### Presence (agent → budget)

- `Sessions` gains optional `state` (`active` | `locked` | `away` | `none`), `kind` (`wayland` | `x11` |
  `tty` | `none`) and `connect` (URL or empty). They describe the kid's **primary** session on that PC:
  the graphical session (`Type` wayland/x11) that is active and unlocked, else a locked one, else one that
  is `online` (switched away), else the first remaining session; `none` when there is no session.
- `loginctl show-session` additionally reads `-p Display`.
- `connect` (built by the agent from `[remote]` in `/etc/tk-agent.conf`):
  - Wayland: `rdp://<kid>@<host>:<rdp_port>` (default port 3389),
  - X11: `vnc://<host>:<vnc_base_port + display number>` (default base 5900, `:1` → 5901),
  - otherwise empty. `host` defaults to the PC's hostname; `[remote] host = <name or IP>` overrides.
- tk-budget keeps the latest per (host, kid) (offline hosts count as none, as for sessions) and publishes in
  the kid's state `where`: a list of `{host, state, kind, connect, link}` for hosts with a session, plus a
  text sensor `presence` (e.g. "pc1: active (Wayland) · pc2: locked (X11)", "not logged in").

### Links that work from the dashboard

The HA markdown card sanitizes links and is likely to drop `vnc://`/`rdp://` hrefs. tk-budget therefore
serves a tiny HTTP redirect on the add-on port `link_port` (default 8099, mapped in `config.yaml`):
`GET /connect/<kid>/<host>` → `302 Location: <connect URL>` if that host currently reports one for that
kid (scheme `rdp` or `vnc` only), else 404. The dashboard link is `http://<ha-host>:8099/connect/<kid>/<host>`;
the browser hands the custom scheme to the Linux desktop's handler (KRDC or Remmina). No other data is
served. `link` in `where` is the path `/connect/<kid>/<host>`.

### Apply feedback

- tk-budget keeps a per-kid revision `rev` (persisted), incremented on every parent input it accepts (set
  topic, `adjust_*` cmd). `Remaining` and `Settings` carry `rev` (optional, default 0).
- Agents wake immediately when `remaining` or `settings` changes (a `threading.Event` the main loop waits
  on, instead of a plain sleep) and, after a tick in which they enforced the snapshot with revision r for a
  logged-in kid, publish `Applied(rev=r)` retained on `timekpr/<host>/<kid>/applied`.
- tk-budget: `applying` = some online host where the kid has a session (count > 0) has acked a revision
  before but not the current one, and the current revision is less than 120 s old. Hosts that never acked
  (old agents) are not waited for. After 120 s without ack, `applying` goes off and the late hosts are
  logged. Entity: `binary_sensor` `applying` ("Applying", icon `mdi:timer-sand`).

## Calls I made

- Presence and links live in tk-budget's per-kid device; the login screen is not part of the field.
- Redirect server instead of raw `vnc://` links in markdown (sanitizer); unauthenticated, LAN only, serves
  nothing but redirects to URLs agents published.
- VNC port for X11 = base + display number; RDP user in the URL = the kid's user name.
- Hourglass waits only for PCs where the kid is logged in and only for agents that have acked before;
  120 s timeout.
