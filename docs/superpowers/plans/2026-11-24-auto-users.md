# Auto-discovered users Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Users appear in HA automatically from the PCs' timekpr user lists. A user is limited only when "Limit active" is on. All PCs share one login, and nothing per user or per PC is configured.

**Architecture:**
- `Remaining` gets a `limited` flag.
- tk-budget learns kids from wildcard usage topics, persists them, and adds a `limit_active` switch.
- tk-agent reads `timekpra --userlist` every tick, derives its host key from the host name, subscribes with wildcards, and enforces only while limited. On the transition from limited to unlimited it releases the user once.

**Tech Stack:** Python ≥ 3.11, stdlib and paho-mqtt 2.x, SQLite, pytest, HA MQTT discovery.

**Spec:** `docs/superpowers/specs/2026-11-24-auto-users-design.md` (amends `docs/superpowers/specs/2026-11-16-timekpr-ha-design.md`)

## Global Constraints

- Touch timekpr only through the `timekpra` CLI. `tk-agent` uses stdlib + `python-paho-mqtt` only.
- Never put real hostnames, usernames, MQTT credentials or kid names in the repo; use placeholders (`<kid-user>`, `<client-host>`). Test fixtures use `kid-a`, `kid-b`, `parent`, `pc1`.
- Id pattern `[a-z0-9_-]+`. A kid id must not start with `_` (`_budget` is reserved).
- "No budget = no limit. Budget used up = 0 (locked)." Old `Remaining` payloads without `limited` count as limited (fail closed).
- After changing `protocol/`, run `scripts/sync-protocol.sh`. `test_vendored_protocol` enforces it.
- Code, comments, docs and commits are in English. Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Limit switched off while the kid is locked (left = 0).** Expect one write of `MAX_TIME_LEFT_S` and no further writes. This holds after an agent restart too, because the state is persisted. Test: `test_unlimit_releases_once_even_after_restart` (Task 3).
2. **`remaining` from tk-budget for a kid this PC doesn't have yet**, and the user is added to timekpr later. Expect the retained value to be applied on the first tick after the user appears. Test: `test_user_appearing_later_uses_retained_remaining` (Task 3).
3. **A new kid's first usage report.** Expect tk-budget to publish discovery and `remaining {limited:false}` immediately, and to know the kid after a tk-budget restart. Test: `test_kid_learned_from_usage_and_persisted` (Task 2).
4. **A timekpr user named like the host key, or with upper-case letters.** Expect the user to be skipped with a warning while the others keep working. Test: `test_invalid_and_host_named_users_skipped` (Task 3).
5. **`timekpra --userlist` fails during one tick.** Expect the last known list to be kept and enforcement to go on. Test: `test_userlist_failure_keeps_last_users` (Task 3).

---

### Task 1: Protocol: `Remaining.limited`

**Files:**
- Modify: `protocol/tk_protocol/payloads.py`
- Modify (generated): `tk-budget/vendor/tk_protocol/` via `scripts/sync-protocol.sh`
- Test: `protocol/tests/test_payloads.py`

**Interfaces:**
- Produces: `Remaining(week: str, total_s: int, limited: bool = True)`. `_Payload.from_json` lets a field that has a dataclass default be missing. `_check` accepts `bool` fields as exactly a JSON bool.

- [ ] **Step 1: Write failing tests**

```python
def test_remaining_limited_roundtrip():
    r = Remaining(week="2026-W47", total_s=0, limited=False)
    assert Remaining.from_json(r.to_json()) == r

def test_remaining_without_limited_is_limited():
    assert Remaining.from_json('{"week":"2026-W47","total_s":5}').limited is True

def test_remaining_limited_must_be_bool():
    with pytest.raises(ValueError):
        Remaining.from_json('{"week":"2026-W47","total_s":5,"limited":1}')
```

- [ ] **Step 2: Run them and see them fail**

Run: `cd protocol && python -m pytest -q tests/test_payloads.py`. Expected: 3 failures.

- [ ] **Step 3: Implement**

Add `limited: bool = True` to `Remaining`. In `from_json`, skip a missing field when `f.default is not dataclasses.MISSING`. In `_check`, accept `tp is bool` with `isinstance(value, bool)`. Then run `scripts/sync-protocol.sh`.

- [ ] **Step 4: Run the tests**

Run all three packages' suites. Expected: green. Old callers still pass because `limited` defaults to True.

- [ ] **Step 5: Commit**

`feat(protocol): Remaining carries whether the kid is limited`

---

### Task 2: tk-budget: learn kids from usage, "Limit active" switch

**Files:**
- Modify: `tk-budget/tk_budget/store.py`, `service.py`, `discovery.py`, `main.py`, `config.yaml` (drop `kids`, version `0.3.0`)
- Test: `tk-budget/tests/test_store.py`, `test_service.py`, `test_main.py`

**Interfaces:**
- Consumes: `Remaining(..., limited=)` from Task 1.
- Produces:
  - `Store.add_kid(kid: str) -> bool`: True if the kid is new. Uses a new table `kids(kid TEXT PRIMARY KEY)`.
  - `Store.kids() -> list[str]`: sorted.
  - `BudgetService(bus, store, clock)`: the `kids` parameter is removed.
  - `discovery.LIMIT_KEY = "limit_active"`.
  - `discovery.valid_kid(kid: str) -> bool`.
  - Entity: switch `limit_active` named "Limit active", `command_topic=set_topic(kid,"limit_active")`, `payload_on="ON"`, `payload_off="OFF"`, `value_template="{{ 'ON' if value_json.limited else 'OFF' }}"`, `icon="mdi:timer-lock"`.
  - The `remaining_total` sensor's template is `{{ ((value_json.remaining_s / 60) | round(0) | int) if value_json.limited else None }}`. The builder may choose another way to show an empty state, as long as HA shows unknown when the kid is unlimited.
  - State JSON gains `"limited": bool`.

- [ ] **Step 1: Write failing tests**

Change the `env` fixture: `BudgetService(bus, store, clock)`. Then `store.add_kid(KID)`, `store.set_setting(KID, "weekly_total_min", "600")`, and `store.set_setting(KID, "limit_active", "ON")` before `start()`. Existing tests keep their meaning.

New tests:
- `test_kid_learned_from_usage_and_persisted`:
  - A usage report on `timekpr/pc1/kid-b/usage` leads to `homeassistant/switch/tk_kid-b/limit_active/config` retained, and `remaining("kid-b") == Remaining(week="2026-W47", total_s=0, limited=False)`.
  - `store.kids() == ["kid-a", "kid-b"]`.
  - A new `BudgetService` on the same store, on a fresh bus, publishes `kid-b`'s discovery on `start()`.
- `test_invalid_kid_ignored`: usage for `_budget`, `Kid`, `a.b` creates no kid and no discovery.
- `test_limit_off_means_unlimited`: set `limit_active` to `OFF` → `limited is False` and `total_s == 0`. Usage is still recorded: `state["today_s"]` grows.
- `test_limit_on_without_budget_is_zero`: a new kid gets limit `ON` with no budget set → `Remaining(week, 0, True)`.
- `test_limit_active_rejects_other_payloads`: `"maybe"` changes nothing.
- Store: `test_add_kid_reports_new_once`.
- main: `load_options` without `kids` works, and a leftover `kids` value is ignored.

- [ ] **Step 2: Run them and see them fail**

Run: `cd tk-budget && python -m pytest -q`. Expected: the new tests fail, and the fixture fails on the constructor.

- [ ] **Step 3: Implement**

- `start()`: for each `store.kids()` publish discovery, settings and remaining. Subscribe once to `timekpr/+/+/usage`, `timekpr/+/cmd` and `timekpr/+/set/+`.
- `_on_usage` on an unknown valid kid: `add_kid`, then discovery, settings and a forced publish, then process the usage.
- `_on_cmd` and `_on_set` ignore kids not in the store, which keeps HA writes from creating kids.
- `limit_active` is stored as `"ON"`/`"OFF"`, default `"OFF"`.
- `_publish`:
  - limited: `Remaining(week, rem, True)`.
  - Otherwise: `Remaining(week, 0, False)`.
- In `main.py`, drop the `kids` option and its check. The log line lists `store.kids()`.

- [ ] **Step 4: Run the tests**

Run: `cd tk-budget && python -m pytest -q`. Expected: all green.

- [ ] **Step 5: Commit**

`feat(budget): learn kids from usage; limit is opt-in per kid`

---

### Task 3: tk-agent: timekpr users, host name key, opt-in enforcement

**Files:**
- Modify: `tk-agent/tk_agent/timekpra.py`, `config.py`, `agent.py`, `main.py`
- Create: `tk-agent/tests/fixtures/userlist.txt`, containing `3 users in total:\nkid-a\nkid-b\nparent\n`
- Test: `tk-agent/tests/test_timekpra.py`, `test_config.py`, `test_agent.py`

**Interfaces:**
- Consumes: `Remaining.limited` from Task 1.
- Produces:
  - `Timekpra.users() -> list[str]`: runs `["--userlist"]`. It drops the line matching `^\d+ users? in total:$` and blank lines. A missing header raises `TimekpraError`.
  - `config.host_key(hostname: str) -> str`: takes the part before the first `.`, lowercases it, maps characters outside `[a-z0-9_-]` to `-`, and returns `"pc"` if the result is empty.
  - `Config(broker_host, broker_port, username, password, host_id, interval_s=45, state_dir=...)`: `kids` is removed. `load(path, hostname=socket.gethostname)` sets `host_id` from the optional `[agent] host_id` or from `host_key(hostname())`. A `[kids]` section only logs a warning.
  - The `Agent` constructor is unchanged. `TimekpraLike` gains `users()`.
  - State file `<state_dir>/<user>.remaining.json` stores `{week,total_s,receipt_week,receipt_usage_s,limited}`. An unlimited snapshot is stored as `limited:false` and a released flag `released:true` once the release write is done.

- [ ] **Step 1: Write failing tests**

Fixture: `FakeTimekpra.users()` returns `self.users`, by default `["kid-a"]`. A user's kid id is the user itself, and `Config` has no `kids`. Rename `USER = KID = "kid-a"`. Existing tests keep their meaning.

New tests:
- `test_timekpra_users_parses_fixture` → `["kid-a","kid-b","parent"]`, plus a missing-header error.
- `test_host_key`: `"Kid-PC.fritz.box"` → `"kid-pc"`, `"a_b c"` → `"a_b-c"`, `""` → `"pc"`.
- `test_config_without_kids_and_host_from_hostname`: host_id comes from the injected hostname, and a `[kids]` section is tolerated.
- `test_unlimited_never_writes`: retained `Remaining(WEEK,0,False)`, ticks → no `set_time_left_s` and no events, but usage is published.
- `test_no_remaining_never_writes` → no writes, usage is published for every listed user.
- `test_unlimit_releases_once_even_after_restart`:
  - limited 0 → the agent writes 0.
  - Then unlimited → exactly one write of `86400`.
  - A new `Agent` on the same `state_dir` → no further write.
  - limited again → enforcement resumes.
- `test_user_appearing_later_uses_retained_remaining`:
  - Retained `remaining` for `kid-b` is limited 600 while `users == ["kid-a"]`.
  - Then `users.append("kid-b")` and a tick → a write for `kid-b` ≈ 600.
- `test_invalid_and_host_named_users_skipped`: users `["Kid", "pc1", "kid-a"]` with host `pc1` → only `kid-a` gets usage, and the warning is logged once per name.
- `test_userlist_failure_keeps_last_users`: `users()` raises once → `kid-a` is still enforced.
- `test_no_online_discovery_published`: nothing under `homeassistant/`.

- [ ] **Step 2: Run them and see them fail**

Run: `cd tk-agent && python -m pytest -q`. Expected: the new tests fail.

- [ ] **Step 3: Implement**

- Per-user dicts (`meters`, `snap`, `action_*`, `*_sent`) are created lazily on a user's first appearance.
- Retained `remaining` and `settings` payloads are kept per kid, as raw strings, in `_pending`. They are applied when the user appears.
- `start()` publishes status online and subscribes `timekpr/+/remaining`, `timekpr/+/settings` and `timekpr/<host>/+/usage`.
- `_tick_user`:
  - No snapshot: return.
  - Unlimited snapshot: if `not released`, write `MAX_TIME_LEFT_S`, then `meter.wrote(read-back)`, mark released and persist. Return without events.
  - Limited snapshot: as now.
- A changed `remaining` with `limited:false` keeps `released` from the old snapshot only if the old one was also unlimited. A snapshot that was never limited (first payload unlimited) is stored as `released:true`, so users like the parent are never touched.
- In `main.py`:
  - client id: `f"tk-agent-{cfg.host_id}"`.
  - log line: `"tk-agent %s running for timekpr users"`.

- [ ] **Step 4: Run the tests**

Run: `cd tk-agent && python -m pytest -q`. Expected: all green.

- [ ] **Step 5: Commit**

`feat(agent): auto users from timekpr, host name key, opt-in limits`

---

### Task 4: Docs, packaging, smoke

**Files:**
- Modify: `README.md`, `tk-agent/packaging/tk-agent.conf.example`, `mosquitto/acl.example`, `docs/superpowers/specs/2026-11-16-timekpr-ha-design.md` (one pointer line to the new spec)

**Changes:**
- **README:**
  - One HA user `tk-agent` for all PCs.
  - No kids option: users appear after their first report, and the parent shows up too, unlimited.
  - To limit a kid: turn "Limit active" on and set the weekly budget.
  - timekpr's own daily limit still applies.
  - To remove a user, delete the device in HA.
  - The card YAML gains the switch `switch.tk_kid_a_limit_active` and loses the PC online sensor. Give the whole card.
- **conf example:** `username = tk-agent`. No `host_id` and no `[kids]`. `# host_id = <client-host>` stays as an optional override.
- **ACL:** a single `user tk-agent` with `readwrite timekpr/#`.

- [ ] **Step 1: Write the docs**

- [ ] **Step 2: Docker smoke run**

Build `tk-budget`. Start it with an override broker against `eclipse-mosquitto:2.0.20`, and publish usage for `kid-b` with `mosquitto_pub`. Expected:
- a retained `homeassistant/switch/tk_kid-b/limit_active/config`;
- `timekpr/kid-b/remaining` = `{"limited": false, "total_s": 0, "week": ...}`;
- after `mosquitto_pub -t timekpr/kid-b/set/limit_active -m ON`, `limited: true, total_s: 0`.

- [ ] **Step 3: Check that the package builds**

Run `makepkg` in a scratch copy. Expected: the build succeeds and the modules import.

- [ ] **Step 4: Commit and push**

Commit `docs: shared agent login, auto users, opt-in limits`. Fast-forward `main` and push.
