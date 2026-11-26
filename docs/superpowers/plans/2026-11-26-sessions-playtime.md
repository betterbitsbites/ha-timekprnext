# Sessions and Play Time Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** HA shows how many sessions each kid has (per PC, locked ones counted) and can end inactive sessions or log the kid out everywhere; play time (timekpr PlayTime) gets weekly and daily limits in HA, enforced by the agents like total time.

**Architecture:** Protocol gains optional play fields, a `Sessions` report and an `Action` message. Agents read sessions via `loginctl`, publish counts, execute actions, measure play like total (second `UsageMeter`) and write PlayTime left via `timekpra --setplaytimeleft`. tk-budget aggregates sessions, turns HA button presses into stamped actions, and computes play limits with the existing engine.

**Tech Stack:** Python 3.12 stdlib + paho-mqtt, SQLite, pytest; `loginctl` (systemd 262) and `timekpra` (timekpr-next 0.5.10).

**Spec:** `docs/superpowers/specs/2026-11-26-limits-play-sessions-design.md` (build steps 3 and 4). Probe facts: `spike/RESULTS.md` (h) play, (i) sessions. Fixtures: `tk-agent/tests/fixtures/getuserinfort-play.txt`, `show-session-manager.txt`, `show-session-user-locked.txt`, `show-user-sessions.txt`.

## Global Constraints

- Never put real hostnames, usernames, MQTT credentials or kid names in the repo; use placeholders (`kid-a`, `<kid-user>`, `<client-host>`).
- Code, comments, docs, commit messages in English. Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`, never `Claude-Session` lines.
- Touch timekpr only through the `timekpra` CLI. `tk-agent` uses stdlib + `python-paho-mqtt` only; sessions via the `loginctl` CLI (subprocess, timeout, no shell).
- After changing `protocol/`, run `scripts/sync-protocol.sh` (a tk-budget test fails if the vendored copy is stale).
- Tests per package: `cd protocol && python -m pytest -q`, `cd tk-budget && python -m pytest -q`, `cd tk-agent && python -m pytest -q`.
- Backward compatibility: every new payload field is optional with a default; old payloads keep parsing. Old `Remaining` without play fields = play unlimited (`play_limited=False`). Old agents (no play fields) report play 0.
- Fail closed for limits that are on; never touch a kid (time or play) without an active limit, except one release write when a limit is switched off.
- Actions (`end_inactive`, `logout`) act only on a user that is a timekpr user on that PC; stale actions (|now − ts| > 120 s) are ignored; actions are never retained.

## Review Focus

1. A stale or replayed action must never log a kid out (retained flag off; ts check on the agent; budget never publishes actions retained).
2. Sessions: `Class=manager` sessions are not counted; a user with no sessions (`loginctl` exits non-zero) is count 0, not an error spam; an offline PC's last report does not keep counting.
3. Play when PlayTime is off in timekpr: `ACTUAL_PLAYTIME_LEFT_DAY` stays 0 and writes have no effect — the agent must not write every tick forever nor count fake play usage.
4. Play limited → unlimited releases play once; total limits unaffected by play switches; play remaining never exceeds total remaining when total is limited.
5. Upgrade: existing state files/snapshots without play fields load; tk-budget DB from 0.4.0 migrates (new tables only, `CREATE TABLE IF NOT EXISTS`).

---

### Task 1: Protocol — play fields, sessions report, actions

**Files:** Modify `protocol/tk_protocol/payloads.py`, `protocol/tk_protocol/topics.py`; vendored copy via `scripts/sync-protocol.sh`. Test `protocol/tests/test_payloads.py`, `protocol/tests/test_topics.py` (create if missing).

**Interfaces (produces):**
- `Usage`: add `play_s: int = 0`, `play_day_s: int = 0` (non-negative).
- `Remaining`: add `play_s: int = 0`, `play_limited: bool = False` (non-negative play_s).
- `CmdOp = Literal["adjust_total", "adjust_play", "end_inactive", "logout"]`; `Cmd.minutes: int = 0`.
- `@dataclass(frozen=True) class Sessions(_Payload): count: int; locked: int` (both ≥ 0, `locked <= count`).
- `ActionOp = Literal["end_inactive", "logout"]`; `@dataclass(frozen=True) class Action(_Payload): op: ActionOp; ts: str`.
- topics: `sessions(host, kid) -> "timekpr/<host>/<kid>/sessions"`, `action(kid) -> "timekpr/<kid>/action"`, `parse_sessions_topic(topic) -> tuple[str, str] | None`, `parse_status_topic(topic) -> str | None` (host of `timekpr/<host>/status`, None for `_budget` or malformed).

- [ ] **Step 1: Failing tests:** old `Usage` JSON (4 fields) parses with play 0; `Remaining.from_json('{"week":"2026-W48","total_s":5}')` has `play_limited is False, play_s == 0`; `Remaining(..., play_s=-1)` raises; `Sessions(count=1, locked=2)` raises; `Action.from_json` rejects `op="shutdown"`; `Cmd.from_json('{"op":"logout"}').minutes == 0`; topic builders/parsers round-trip and reject `timekpr/_budget/status`.
- [ ] **Step 2:** `cd protocol && python -m pytest -q` → new tests FAIL.
- [ ] **Step 3:** Implement; run `scripts/sync-protocol.sh`.
- [ ] **Step 4:** protocol, tk-budget and tk-agent suites → PASS.
- [ ] **Step 5:** Commit `feat(protocol): play fields, sessions report, session actions`.

### Task 2: Agent — sessions and actions

**Files:** Create `tk-agent/tk_agent/sessions.py`; modify `tk-agent/tk_agent/agent.py`, `tk-agent/tk_agent/main.py`. Tests `tk-agent/tests/test_sessions.py`, `tk-agent/tests/test_agent.py`.

**Interfaces:**
- Consumes Task 1: `Sessions`, `Action`, `topics.sessions`, `topics.action`.
- `sessions.py`: `@dataclass(frozen=True) class Session: id: str; cls: str; type: str; state: str; locked: bool; remote: bool`; `class Loginctl(runner=subprocess_runner)`: `sessions(user) -> list[Session]` (only `cls.startswith("user")`; `show-user <u> -p Sessions --value` non-zero exit or empty → `[]`; one `show-session <id> -p Id -p Class -p Type -p State -p Remote -p LockedHint` per id; a session that vanished meanwhile is skipped), `terminate_session(id)`, `terminate_user(user)`. Runner signature `Callable[[list[str]], str]` raising `LoginctlError` on non-zero exit (like `timekpra.subprocess_runner`, but running `loginctl`). User names are validated like `timekpra._user` (no leading `-`, no whitespace); session ids must be `^[0-9a-z]+$`.
- `inactive(s: Session) -> bool` = `s.locked or s.state != "active"`.
- `Agent(cfg, bus, timekpra, meter_factory, clock, sessions: SessionsLike | None = None)`; per tick per user (also when logged out): publish `Sessions(count, locked)` retained on `topics.sessions(host, user)` only when it changed since the last publish (and once after start). A `loginctl` error logs a warning once per user until it works again, publishes nothing.
- `start()` subscribes `topics.action("+")`. Handler: parse `Action`; ignore unknown users (not in this PC's current user list — do NOT park in `_pending`), malformed payloads, and `abs(now − ts) > 120 s` (ts parsed with `datetime.fromisoformat`, naive local time as the agent's clock). `end_inactive` → `terminate_session` for each inactive session of that user; `logout` → `terminate_user(user)`. Log at INFO what was ended. Republish sessions on the next tick.

- [ ] **Step 1: Failing tests** (`test_sessions.py` with a fake runner fed from the fixtures): `sessions("kid-a")` with `show-user-sessions.txt` + the two show-session fixtures → one `Session(id="5", cls="user", type="wayland", state="active", locked=True, remote=False)`; non-zero `show-user` → `[]`; `terminate_user("-x")` raises `ValueError`; `terminate_session("5; rm")` raises `ValueError`. `test_agent.py` (FakeSessions): sessions published `{"count":1,"locked":1}` retained, not republished when unchanged, republished on change; an `Action(op="end_inactive", ts=now)` terminates only locked/online sessions of that user; `logout` calls `terminate_user`; action 3 minutes old ignored; action for a user not on this PC ignored; action published retained by mistake is still handled once per message (no state kept) — and the agent never publishes to the action topic.
- [ ] **Step 2:** `cd tk-agent && python -m pytest -q` → new tests FAIL.
- [ ] **Step 3:** Implement; `main.py` passes `Loginctl()`.
- [ ] **Step 4:** Suite PASS.
- [ ] **Step 5:** Commit `feat(agent): report login sessions and end them on request`.

### Task 3: Agent — play time

**Files:** Modify `tk-agent/tk_agent/timekpra.py`, `tk-agent/tk_agent/agent.py`, `tk-agent/tk_agent/main.py`. Tests `tk-agent/tests/test_timekpra.py`, `tk-agent/tests/test_agent.py`.

**Interfaces:**
- Consumes Task 1: `Usage.play_s/play_day_s`, `Remaining.play_s/play_limited`.
- `Timekpra.play_left_s(user) -> int | None` from `ACTUAL_PLAYTIME_LEFT_DAY` in `--getuserinfort` (None when logged out, same header-only rule as `time_left_s`); `Timekpra.set_play_left_s(user, seconds)` → `--setplaytimeleft <user> = <s>`.
- `Agent(..., meter_factory, ...)` is called with `user` for total and `f"{user}.play"` for play (main.py builds `<state_dir>/<name>.json`); play meter observes `play_left_s` every tick the user is logged in.
- `Usage` published with `play_s = play_meter.week_total_s(week)`, `play_day_s = play_meter.day_total_s(day)`.
- `_Snapshot` gains `play_s: int = 0`, `play_limited: bool = False`, `play_receipt_usage_s: int = 0`, `play_released: bool = True`; `_load_snapshot` accepts files without them.
- Enforcement while `play_limited`: `target_play = min(max(0, snap.play_s − play_used_since_receipt), MAX_TIME_LEFT_S)`; write via `set_play_left_s` when `abs(min(target_play, expected_total_cap) − play_left) >= WRITE_TOLERANCE_S or (target_play == 0 and play_left > 0)`, where `expected_total_cap` = the total value expected after this tick's total enforcement (or the observed time left when total is unlimited); read back with `play_left_s` and feed the play meter's `wrote()`. **Unenforceable guard (Review Focus 3):** if a play write's read-back differs from the written value by ≥ `WRITE_TOLERANCE_S` and is not explained by the total cap, mark play unenforceable for that user, log one WARNING ("PlayTime seems off in timekpr for <user>"), and skip play writes until a new play snapshot arrives or the day changes.
- Play limited → unlimited: one `set_play_left_s(user, MAX_TIME_LEFT_S)` + read-back (release), persisted like `released`. A first-ever unlimited play snapshot never writes.
- Play switches never change total enforcement and vice versa; lockout type untouched by play.

- [ ] **Step 1: Failing tests:** `play_left_s` = 17316 from `getuserinfort-play.txt`, None for the logged-out fixture; `set_play_left_s` argv `["--setplaytimeleft", "kid-a", "=", "600"]`. FakeTimekpra gains `play_left`, `play_writes`, `play_enabled` (when False, play left stays 0 and writes are ignored) and `run(seconds, playing=True)`. Agent tests: play usage reported in `Usage.play_s/play_day_s` while playing, not while not playing; play limited 1800 s → after playing 600 s the agent writes ≈1200 to PlayTime; play unlimited from the start → no play writes; limited → unlimited → exactly one release write; with `play_enabled=False` a limited play snapshot causes at most one play write per snapshot and one warning; a snapshot file from before (no play keys) loads; total-only snapshot keeps play untouched.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Suite PASS.
- [ ] **Step 5:** Commit `feat(agent): measure and enforce play time`.

### Task 4: tk-budget — sessions and actions

**Files:** Modify `tk-budget/tk_budget/service.py`, `tk-budget/tk_budget/discovery.py`. Test `tk-budget/tests/test_service.py`.

**Interfaces:**
- Consumes Task 1 (`Sessions`, `Action`, `Cmd` ops, `topics.sessions/action/parse_sessions_topic/parse_status_topic`).
- Subscribes `timekpr/+/+/sessions` and `timekpr/+/status`. Keeps latest `Sessions` per (host, kid) for known kids; a host whose status is `offline` contributes 0 until it reports again. State adds `sessions` (sum count), `sessions_locked` (sum locked), `sessions_by_host` ({host: count}, only hosts with count > 0).
- `Cmd` ops `end_inactive` / `logout` on `topics.cmd(kid)` → publish `Action(op, ts=clock.now().isoformat(timespec="seconds"))` on `topics.action(kid)`, **not retained**.
- Entities: sensor `sessions` (state `value_json.sessions`, icon `mdi:account-multiple`, `json_attributes_topic` state topic with template giving `locked` and `by_host`); buttons `end_inactive_sessions` ("End inactive sessions", payload `Cmd(op="end_inactive")`, icon `mdi:account-lock`) and `logout` ("Log out everywhere", payload `Cmd(op="logout")`, icon `mdi:logout`), both on `topics.cmd(kid)`.

- [ ] **Step 1: Failing tests:** two hosts report `Sessions(1,0)` and `Sessions(2,1)` → state sessions 3, locked 1, by_host both; `timekpr/pc2/status` "offline" → sessions 1; pc2 reports again → 3; logout cmd → one non-retained publish on `timekpr/kid-a/action` with `op == "logout"` and the clock's ts; cmd for unknown kid → nothing; discovery count/ids updated.
- [ ] **Step 2:** → FAIL. **Step 3:** Implement. **Step 4:** Suite PASS.
- [ ] **Step 5:** Commit `feat(budget): session counts and end/logout buttons`.

### Task 5: tk-budget — play limits

**Files:** Modify `tk-budget/tk_budget/store.py`, `service.py`, `discovery.py`. Tests `tk-budget/tests/test_store.py`, `test_service.py`.

**Interfaces:**
- Store: new tables `play_usage(kid, host, week, play_s)`, `play_daily(kid, day, host, play_s)`, `play_adjustments(kid, week, adjustment_s)`, `play_day_adjustments(kid, day, adjustment_s)` with methods mirroring the total ones: `save_play_usage`, `load_play_usage`, `record_play_daily`, `play_daily_totals`, `get/add_play_adjustment_s`, `get/add_play_day_adjustment_s` (MAX semantics for usage, additive for adjustments).
- Service: a second `UsageLedger` for play fed from `Usage.play_s`/`play_day_s` (restored from `load_play_usage` on start). Settings `weekly_play_min` (0..10080) + `play_limit_active`, `daily_play_min` (0..1440) + `daily_play_active`. Play limits via `engine.limit("play_week", ...)`, `limit("play_day", ...)`; `play_limited, play_rem = effective(play limits)`; if total is limited, `play_rem = min(play_rem, total effective)`. `Remaining(..., play_s=play_rem if play_limited else 0, play_limited=play_limited)`. Cmd `adjust_play` adds minutes*60 to play week and play today adjustments.
- State adds `play_limited`, `play_week_limited`, `play_daily_limited`, `weekly_play_min`, `daily_play_min`, `play_today_s`, `play_remaining_s`, `limits.play_week`, `limits.play_day`.
- Entities: switch `play_limit_active` ("Weekly play limit active"), number `weekly_play_budget` (step 15, max 10080), switch `daily_play_active`, number `daily_play_limit` (step 5, max 1440), sensors `play_today` (min), `remaining_play` (min, None unless play limited), `play_week_left_pct`, `play_today_left_pct` (same pattern as the total pct sensors incl. null attributes when off), buttons `play_plus_15` / `play_minus_15` (`Cmd(op="adjust_play", minutes=±15)`).

- [ ] **Step 1: Failing tests:** weekly play 120 ON, play report 1800 s → `Remaining.play_s == 5400`, `play_limited`; daily play 30 ON + weekly play 120 ON, play today 600 → 1200; total limited with effective 900 and play 5400 → play_s 900; play switches OFF → `play_limited False, play_s 0` and total unchanged; `adjust_play +15` → +900 on both play limits; restart restores play usage; store tests for the new methods; discovery count/ids updated.
- [ ] **Step 2:** → FAIL. **Step 3:** Implement. **Step 4:** Suite PASS.
- [ ] **Step 5:** Commit `feat(budget): weekly and daily play limits`.

### Task 6: Versions, docs, smoke

**Files:** `tk-budget/config.yaml` (version "0.5.0"), `tk-agent/packaging/PKGBUILD` + `tk-agent/pyproject.toml` (0.2.0), `README.md`.

- [ ] **Step 1:** README: sessions (what counts, locked, the two buttons and that they act on all PCs, ≤ 45 s delay), play limits (timekpr PlayTime must be enabled with activity masks in timekpr; timekpr's own PlayTime daily limit and its own daily/weekly time limits should be set non-binding, e.g. `timekpra --setlimitweek <kid-user> 604800`, because the lower limit always wins), upgrade note (agent 0.2.0 + add-on 0.5.0; old agents simply report no play/sessions), extend the example card (whole card YAML, `tk_kid_a_*`) with play bars, play switches/numbers, play ±15, sessions sensor and the two buttons.
- [ ] **Step 2:** Docker smoke as in the previous plan: usage with play for `kid-a`, `play_limit_active` ON with `weekly_play_min` 60 → `remaining` has `play_limited: true`, `play_s` ≤ 3600; publish `Cmd(op="logout")` on `timekpr/kid-a/cmd` → a non-retained `timekpr/kid-a/action` message is seen by a subscriber.
- [ ] **Step 3:** All three suites PASS; commit `docs: sessions and play limits; bump versions`.
