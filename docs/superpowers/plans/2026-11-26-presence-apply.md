# Presence, Remote Links and Apply Feedback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The dashboard shows per kid where they are logged in (state, session kind) with a one-click remote link for a Linux desktop, and an hourglass until the PCs confirm a parent's change; agents apply changes immediately.

**Architecture:** `Sessions` carries the kid's primary-session state/kind/connect URL; tk-budget aggregates it into `where` + a `presence` text sensor and serves `GET /connect/<kid>/<host>` redirects. A per-kid revision travels in `Remaining`/`Settings`; agents wake on change, enforce, and ack with `Applied(rev)`; tk-budget derives `applying`.

**Tech Stack:** Python 3.12 stdlib (`http.server`, `threading`) + paho-mqtt, SQLite, pytest; `loginctl`.

**Spec:** `docs/superpowers/specs/2026-11-26-presence-apply-design.md`.

## Global Constraints

- Never put real hostnames, usernames, MQTT credentials or kid names in the repo; use placeholders (`kid-a`, `pc1`, `<kid-user>`, `<client-host>`).
- Code, comments, docs, commit messages in English. Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`, never `Claude-Session` lines.
- tk-agent: stdlib + `python-paho-mqtt` only; timekpr only via `timekpra`; sessions via `loginctl`.
- After changing `protocol/`, run `scripts/sync-protocol.sh`.
- Tests per package: `cd protocol && python -m pytest -q`, `cd tk-budget && python -m pytest -q`, `cd tk-agent && python -m pytest -q`.
- Backward compatible: all new payload fields optional with defaults; old agents (no presence, no acks) and old budgets (no rev) keep working; an agent that never acked is never waited for.
- The redirect server only ever redirects to a URL with scheme `rdp` or `vnc` that an agent published for that exact (kid, host); anything else is 404. It serves nothing else.

## Review Focus

1. Hourglass never sticks: old agents, offline PCs, logged-out kids, lost acks and a 120 s timeout all end `applying`.
2. `rev` increments only on parent input (set/cmd), never on usage-driven republishes — otherwise the hourglass flickers on every report.
3. The agent's immediate wake must not run ticks on paho's thread or double-tick; it must not starve the regular interval.
4. Redirect server: path parsing (`..`, encoded slashes, unknown kid/host, non-ASCII), no open redirect, no crash of the add-on on bad requests; binds the configured port only.
5. Primary-session choice and URL building for: Wayland active; X11 `:1` locked; session switched away (`online`); remote ssh (`tty`) only; no session.

---

### Task 1: Protocol — presence fields, revisions, acks

**Files:** `protocol/tk_protocol/payloads.py`, `protocol/tk_protocol/topics.py`, tests; `scripts/sync-protocol.sh`.

**Interfaces (produces):**
- `Sessions`: add `state: Literal["active","locked","away","none"] = "none"`, `kind: Literal["wayland","x11","tty","none"] = "none"`, `connect: str = ""`.
- `_check`/`from_json`: a `str` field whose dataclass default is `""` accepts `""` (other str fields still require non-empty).
- `Remaining.rev: int = 0`, `Settings.rev: int = 0` (≥ 0).
- `@dataclass(frozen=True) class Applied(_Payload): rev: int` (≥ 0).
- `topics.applied(host, kid) -> "timekpr/<host>/<kid>/applied"`, `topics.parse_applied_topic(topic) -> tuple[str, str] | None`.

- [ ] **Step 1: Failing tests:** old `Sessions` JSON (`{"count":1,"locked":0}`) parses with state/kind "none", connect ""; `Sessions(..., connect="")` round-trips; `Settings.from_json('{"time_up_action":"lock","rev":""}')` raises; `Sessions` with `kind="mir"` raises; `Applied(rev=-1)` raises; topic round-trip.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement + sync. **Step 4:** all three suites PASS.
- [ ] **Step 5:** Commit `feat(protocol): presence fields, revisions and apply acks`.

### Task 2: Agent — presence, remote config, immediate apply, acks

**Files:** `tk-agent/tk_agent/config.py`, `sessions.py`, `agent.py`, `main.py`, `packaging/tk-agent.conf.example`; tests `test_config.py`, `test_sessions.py`, `test_agent.py`.

**Interfaces:**
- `Config` gains `remote_host: str` (default: the raw `hostname()` value's first label as given by the OS, not lower-cased), `rdp_port: int = 3389`, `vnc_base_port: int = 5900`, from optional section `[remote]` (`host`, `rdp_port`, `vnc_base_port`; ports 1..65535; host must match `^[A-Za-z0-9.-]+$`).
- `Session` gains `display: str` (from `-p Display`; `show-session` argv gets `-p Display`).
- `primary(sessions: list[Session]) -> Session | None` in `sessions.py`: graphical (`type` in wayland/x11) active & unlocked; else graphical locked; else graphical `online`; else first non-closing; else None.
- `connect_url(cfg, kid, s: Session | None) -> str`: wayland → `rdp://<kid>@<remote_host>:<rdp_port>`; x11 with display `:N` (N digits, optional `.screen`) → `vnc://<remote_host>:<vnc_base_port+N>`; else `""`.
- Published `Sessions` gains `state` (`active` if primary is active & unlocked, `locked` if locked, `away` if state `online`, `none` if no primary), `kind` (primary type mapped to wayland/x11/tty, else none), `connect`.
- Immediate apply: `Agent.wake: threading.Event`; `_on_remaining` / `_on_settings` set it when a part changed; `main.py` loop: `agent.wake.wait(cfg.interval_s)`, `agent.wake.clear()`, then `agent.tick()` (ticks stay on the main thread).
- Acks: snapshot/settings remember the received `rev` (persisted with the snapshot; settings rev in memory). After `_tick_user` completes for a logged-in user, if `max(rev of remaining, rev of settings)` > last acked for that user, publish `Applied(rev)` retained on `topics.applied(host, user)` and remember it. Logged-out users are not acked.

- [ ] **Step 1: Failing tests:** config defaults and `[remote]` overrides, invalid port/host rejected; `primary` for the five Review Focus 5 cases (fixtures: add an X11 `show-session` fixture with `Display=:1`, `Type=x11`, `LockedHint=yes`, and an `online` one); `connect_url` cases; agent publishes `Sessions(count=1, locked=1, state="locked", kind="x11", connect="vnc://pc1:5901")` for the X11 fixture (cfg remote_host "pc1"); a new remaining with rev 3 sets `wake`; after the next tick an `Applied(rev=3)` retained is published once; not for a logged-out user; a settings-only change with rev 4 → `Applied(4)`.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement (incl. conf example: commented `[remote]` with `host = <client-host>`, ports). **Step 4:** suite PASS.
- [ ] **Step 5:** Commit `feat(agent): report presence and remote URL, apply changes at once and ack them`.

### Task 3: tk-budget — rev, applying, presence, redirect server

**Files:** `tk-budget/tk_budget/service.py`, `discovery.py`, `store.py` (rev in settings), create `tk-budget/tk_budget/links.py`, `main.py`, `config.yaml`; tests `test_service.py`, `test_links.py`, `test_main.py`.

**Interfaces:**
- `rev`: stored as setting `rev` per kid; incremented in `_on_set` (accepted values only) and `_on_cmd` (`adjust_total`, `adjust_play`; NOT `end_inactive`/`logout`), before publishing. `Remaining.rev` and `Settings.rev` carry it; `_publish_settings` is called with it.
- Acks: subscribe `timekpr/+/+/applied`; keep `acked[(host, kid)] = rev`; `rev_at[kid]` = clock time of the last increment (in memory; after restart treat as old). `applying(kid)` = `now - rev_at < 120 s` and some host h with `(h, kid)` in `acked`, h not offline, latest Sessions count > 0 for (h, kid), and `acked[(h, kid)] < rev`. The tick republishes so `applying` turns off by timeout; log late hosts once.
- State adds `applying` (bool), `rev`, `where` (list of `{host, state, kind, connect, link}` for non-offline hosts with count > 0, sorted by host; `link = "/connect/<kid>/<host>"` only when connect is non-empty, else `""`), `presence` (text: `"<host>: <state> (<Kind>)"` joined by ` · `, Kind = Wayland/X11/Text; `"not logged in"` when empty).
- Entities: `binary_sensor` `applying` (name "Applying", `payload_on`/`off` from `value_json.applying`, icon `mdi:timer-sand`), sensor `presence` (state `value_json.presence`, truncated to 255 chars, attributes `where` via `json_attributes_template`).
- `links.py`: `class LinkServer(lookup: Callable[[str, str], str | None], port: int)`; `start()` runs `http.server.ThreadingHTTPServer` on `0.0.0.0:port` in a daemon thread; `GET /connect/<kid>/<host>` with both segments matching `^[a-z0-9][a-z0-9_-]*$` → `lookup(kid, host)`; if the URL's scheme is `rdp` or `vnc` → `302` with `Location`, else `404`; every other path/method → 404; no request logging to stderr beyond one INFO line per redirect. `BudgetService.connect_url(kid, host) -> str | None` (thread-safe, from the latest presence; None if offline/unknown).
- `config.yaml`: option `link_port: 8099` (schema `port`), `ports: {"8099/tcp": 8099}`, `ports_description`, version `"0.6.0"`. `main.load_options` returns `link_port`; `main()` starts `LinkServer(service.connect_url, link_port)`.

- [ ] **Step 1: Failing tests:** rev increments on set/adjust, not on usage or logout cmd; `Remaining.rev`/`Settings.rev` follow; applying: host acked rev 1, parent sets → rev 2 → `applying` true while pc1 has a session; ack 2 → false; pc1 offline → false; kid logged out on pc1 → false; host that never acked → not waited for; 121 s later via tick → false; `where`/`presence` for two hosts (one wayland active, one x11 locked) and for none ("not logged in"); discovery includes the two new entities. `test_links.py` (real server on an ephemeral port, `http.client`): redirect for a known pair; 404 for unknown, for `..`/encoded slash paths, for a published `http://` URL, for POST; server survives a malformed request line.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** suite PASS.
- [ ] **Step 5:** Commit `feat(budget): presence, remote links and apply feedback`.

### Task 4: Docs, versions, smoke

**Files:** `README.md`, `tk-agent/packaging/PKGBUILD` + `tk-agent/pyproject.toml` (0.3.0), `tk-budget/config.yaml` (0.6.0 if not done).

- [ ] **Step 1:** README: presence field and links (Linux desktop: KRDC or Remmina as handler for `rdp://`/`vnc://`; `krdp` must be set up in the kid's Plasma session; X11 VNC port = base + display; `[remote]` in tk-agent.conf; the redirect port 8099 is unauthenticated LAN-only and only redirects); the hourglass (what it waits for, 120 s timeout, old agents not waited for). Extend the example card (whole card YAML) with: an entities row for `binary_sensor.tk_kid_a_applying` (or a conditional card showing "⏳ Applying…" while on), and a markdown card:
  ```
  {% set w = state_attr('sensor.tk_kid_a_presence', 'where') or [] %}
  {% if w | count == 0 %}kid-a is not logged in.{% endif %}
  {% for p in w %}**{{ p.host }}**: {{ p.state }} ({{ p.kind }}){% if p.link %} — [🐧 connect](http://<ha-host>:8099{{ p.link }}){% endif %}
  {% endfor %}
  ```
  (verify the template against the attribute shape produced in Task 3).
- [ ] **Step 2:** Docker smoke: run tk-budget with `link_port` 8099 mapped; publish usage + `Sessions` with `kind:"wayland"`, `state:"active"`, `connect:"rdp://kid-a@pc1:3389"` for kid-a on pc1; `curl -si http://127.0.0.1:8099/connect/kid-a/pc1` → `302` with that Location; `/connect/kid-a/pc9` → 404; set `daily_total_min` → state `applying` false (no acks yet); publish `Applied` rev 1 for pc1, set again → `applying` true; publish `Applied` with the new rev → false.
- [ ] **Step 3:** All suites PASS; commit `docs: presence, remote links, apply feedback; bump versions`.
