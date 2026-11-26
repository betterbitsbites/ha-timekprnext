# Daily Limits and Family Link Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** tk-budget gets a daily total limit, a combined PC + phone daily limit fed by Family Link, per-limit remaining/percent sensors for dashboard bars, and ±15 buttons that act on week and today together.

**Architecture:** All limit logic is a pure function in `tk_budget/engine.py`; `BudgetService` collects inputs (settings, adjustments, ledger/store usage, phone usage) and publishes one effective `Remaining` plus a richer state JSON; discovery adds entities. Family Link is read by `main.py` from the HA Core API through the Supervisor and handed to the service. Agents and protocol are unchanged in this plan.

**Tech Stack:** Python 3.12 stdlib + paho-mqtt, SQLite, pytest. HA MQTT discovery.

**Spec:** `docs/superpowers/specs/2026-11-26-limits-play-sessions-design.md` (build steps 1 and 2).

## Global Constraints

- Never put real hostnames, usernames, MQTT credentials or kid names in the repo; use placeholders (`<kid-user>`, `<client-host>`, `<child>`).
- Code, comments, docs, commit messages in English. Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and never contain `Claude-Session` lines.
- tk-budget: stdlib + paho only. Tests: `cd tk-budget && python -m pytest -q`.
- A limit that is on with an unset value counts as 0 (fail closed). "Limit off" and "0 left" are never confused: `limited=False` only when no total limit is on.
- Existing entity unique_ids and the `limit_active` command topic stay unchanged.
- Family Link host key in statistics: `_familylink` (leading `_` cannot be an agent host id).

## Review Focus

1. `limit_active` switch must reflect the weekly switch only (today its template reads `value_json.limited`, which becomes "any limit on") — a weekly-off/daily-on kid must show the weekly switch OFF.
2. Midnight: today adjustments and daily usage roll over at local midnight with no new message (tick), and the first Family Link value after midnight that still carries yesterday's total must not be stored for the new day.
3. Family Link entity `unavailable`/`unknown`/non-numeric/HTTP error: keep the last value, never raise out of the poll, never lower stored usage.
4. Phone usage must not reduce the weekly or daily PC limits, only the combined one; the PC daily total must not include `_familylink`.
5. Kid without a Family Link mapping: no combined entities (and stale combined discovery is removed with an empty retained payload).

---

### Task 1: Limit engine and today adjustments in the store

**Files:**
- Modify: `tk-budget/tk_budget/engine.py`, `tk-budget/tk_budget/store.py`
- Test: `tk-budget/tests/test_engine.py`, `tk-budget/tests/test_store.py`

**Interfaces:**
- Produces (engine):
  ```python
  @dataclass(frozen=True)
  class Limit:
      key: str          # "week" | "day" | "combined"
      active: bool
      allowance_s: int  # limit + adjustment, >= 0
      remaining_s: int  # max(0, allowance - used)
      @property
      def pct(self) -> int: ...  # round(100*remaining/allowance) clamped 0..100; 0 if allowance == 0

  def limit(key: str, active: bool, limit_min: int, adjustment_s: int, used_s: int) -> Limit
  def effective(limits: list[Limit]) -> tuple[bool, int]  # (any active, min remaining over active; 0 if none active)
  ```
- Produces (store): table `day_adjustments(kid, day, adjustment_s)`; `get_day_adjustment_s(kid, day) -> int`, `add_day_adjustment_s(kid, day, delta_s) -> int`; `daily_totals` unchanged.

- [ ] **Step 1: Failing tests** in `test_engine.py`:
  - `limit("day", True, 120, 900, 3000) == Limit("day", True, 8100, 5100)` and `.pct == 63`.
  - `limit("week", True, 10, -100000, 0).allowance_s == 0`, `.remaining_s == 0`, `.pct == 0`.
  - `effective([limit("week", True, 600, 0, 3600), limit("day", True, 60, 0, 600)]) == (True, 3000)`.
  - `effective([limit("week", False, 600, 0, 0), limit("day", True, 60, 0, 0)]) == (True, 3600)`.
  - `effective([limit("week", False, 600, 0, 0)]) == (False, 0)`.
  And in `test_store.py`: `add_day_adjustment_s("k", "2026-11-16", 900)` twice → `get_day_adjustment_s` 1800; other day 0.
- [ ] **Step 2:** `cd tk-budget && python -m pytest -q tests/test_engine.py tests/test_store.py` → FAIL (ImportError / AttributeError).
- [ ] **Step 3:** Implement. Keep `remaining_total_s` if still used, otherwise remove it with its test.
- [ ] **Step 4:** Same command → PASS; then the whole suite → PASS.
- [ ] **Step 5:** Commit `feat(budget): limit engine and today adjustments`.

### Task 2: Daily limit in the service, per-limit sensors, ±15 on week and today

**Files:**
- Modify: `tk-budget/tk_budget/service.py`, `tk-budget/tk_budget/discovery.py`
- Test: `tk-budget/tests/test_service.py`

**Interfaces:**
- Consumes: Task 1 (`limit`, `effective`, `Limit.pct`, day adjustments).
- Settings keys: `daily_total_min` (0..1440), `daily_limit_active` ("ON"/"OFF", default OFF). Constants in discovery: `DAILY_KEY = "daily_limit_active"`, `MAX_DAILY_MIN = 1440`.
- State JSON (retained on `discovery.state_topic(kid)`) — keep existing keys, with these meanings/additions:
  - `limited`: any total limit on; `week_limited`: weekly switch; `daily_limited`: daily switch.
  - `remaining_s`: effective remaining (min over active limits; weekly rest when none is active).
  - `daily_total_min`, `today_s` (PC hosts only, excludes `_familylink`).
  - `limits`: `{"week": {"on", "allowance_s", "remaining_s", "pct"}, "day": {...}}` (+ `"combined"` in Task 3).
- New entities (unique_id `tk_<kid>_<obj>`): number `daily_total_limit` (cmd `set/daily_total_min`, step 5, max 1440), switch `daily_limit_active`, sensors `remaining_week`, `remaining_today` (min; `None` when that limit is off), `week_left_pct`, `today_left_pct` (unit `%`, `None` when off, `json_attributes_topic` = state topic with a template giving `remaining_min` and `limit_min` of that limit).
- `limit_active` switch: name "Weekly limit active", template `{{ 'ON' if value_json.week_limited else 'OFF' }}`.
- `adjust_total` cmd adds to the week adjustment **and** today's adjustment.

- [ ] **Step 1: Failing tests** in `test_service.py` (fixture kid has weekly 600 min ON):
  - `test_daily_limit_binds`: set `daily_total_min` "60", `daily_limit_active` "ON", report 1800 s today → `remaining(bus).total_s == 1800`; state `limits.day.pct == 50`, `limits.week.remaining_s == 36000 - 1800`.
  - `test_daily_only_kid_is_limited`: weekly OFF, daily ON 30 → `Remaining(..., total_s=1800, limited=True)`; state `week_limited is False`, `limited is True`.
  - `test_daily_on_without_value_is_zero`: daily ON, no `daily_total_min` → total_s 0.
  - `test_adjust_adds_to_week_and_today`: daily 60 ON, +15 cmd → total_s 4500; next day (clock +1 d, tick) daily allowance back to 3600, week adjustment still +900 in `limits.week.allowance_s`.
  - `test_day_rollover_by_tick`: usage 3600 today with daily 60 ON → 0; clock to next day 00:00:30, `svc.tick()` → total_s 3600.
  - `test_today_excludes_familylink_host`: `store.record_daily(KID, day, "_familylink", 1200)` then a PC report 600 → state `today_s == 600`.
  - `test_limit_switch_reflects_weekly_only` (Review Focus 1): weekly OFF, daily ON → discovery template of `limit_active` rendered against the state gives "OFF" (assert on `state["week_limited"] is False` and that the switch payload's `value_template` references `week_limited`).
  - `test_discovery_has_all_entities`: update the expected set/count for the new entities.
  - `daily_total_min` rejects non-numbers and clamps to 0..1440; `daily_limit_active` accepts only ON/OFF.
- [ ] **Step 2:** `python -m pytest -q tests/test_service.py` → the new tests FAIL.
- [ ] **Step 3:** Implement in service/discovery.
- [ ] **Step 4:** Whole suite → PASS.
- [ ] **Step 5:** Commit `feat(budget): daily total limit, per-limit remaining and percent sensors`.

### Task 3: Family Link combined daily limit

**Files:**
- Modify: `tk-budget/tk_budget/service.py`, `tk-budget/tk_budget/discovery.py`, `tk-budget/tk_budget/main.py`, `tk-budget/config.yaml`
- Create: `tk-budget/tk_budget/familylink.py`
- Test: `tk-budget/tests/test_familylink.py`, `tk-budget/tests/test_service.py`, `tk-budget/tests/test_main.py`

**Interfaces:**
- `config.yaml`: `homeassistant_api: true`; option `familylink: []`, schema `familylink: [{kid: str, entity: str}]`; version `"0.4.0"`.
- `main.load_options` returns `familylink: dict[str, str]` (kid → entity; entries with invalid kid or blank entity skipped with a warning).
- `familylink.read_minutes(entity: str, env: Mapping[str, str], fetch: Fetch) -> float | None`: GET `http://supervisor/core/api/states/<entity>` with `Authorization: Bearer $SUPERVISOR_TOKEN`; returns the numeric state in minutes, `None` for no token, HTTP/JSON error, `unavailable`/`unknown`/non-numeric. Never raises.
- `BudgetService(bus, store, clock, phone_kids: Collection[str] = ())`; `set_phone_minutes(kid: str, minutes: float) -> None`: converts to seconds, applies the midnight guard, records `store.record_daily(kid, today, "_familylink", s)`, republishes. Guard: on a day where nothing is stored yet for `_familylink`, accept the value only if it is lower than yesterday's stored `_familylink` value, or the local time is ≥ 00:15, or yesterday has no value.
- Settings `combined_total_min` (0..1440), `combined_limit_active`; entities (only for `phone_kids`): number `daily_combined_limit`, switch `combined_limit_active`, sensors `remaining_combined`, `combined_left_pct`, `phone_today` (min). For other kids, publish an empty retained payload to these five config topics.
- Combined limit: `limit("combined", on, combined_total_min, day_adjustment_s, today_pc_s + phone_today_s)`. State adds `combined_limited`, `combined_total_min`, `phone_today_s`, `limits.combined`.
- `main()`: every `TICK_S`, for each mapped kid `read_minutes` → if not None, `service.set_phone_minutes`. Exceptions logged, loop continues.

- [ ] **Step 1: Failing tests**:
  - `test_familylink.py`: fake fetch returning `{"state": "42"}` → 42.0; `"unavailable"`, `"unknown"`, `"abc"`, fetch raising `OSError`, bad JSON, missing token → `None`; request URL is `http://supervisor/core/api/states/sensor.x` and the header carries the bearer token.
  - `test_service.py`: `test_combined_limit_counts_phone_and_pc` (combined 60 ON, phone 20 min, PC 1200 s → total_s 1200; weekly/daily rests unaffected by phone); `test_phone_ignored_when_combined_off`; `test_phone_value_never_lowers` (40 then 30 → stays 40 min); `test_midnight_guard` (yesterday 90 min stored; at 00:05 a value 90 is not stored for today, a value 3 is; at 00:20 a value 95 is stored); `test_combined_entities_only_for_phone_kids` (kid not in `phone_kids` → its combined config topics retained as `""`).
  - `test_main.py`: `load_options` maps `familylink` list to a dict and skips `{"kid": "Bad Kid", ...}` and blank entities; missing key → `{}`.
- [ ] **Step 2:** Run the three files → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Whole suite → PASS.
- [ ] **Step 5:** Commit `feat(budget): combined PC + phone daily limit from Family Link`.

### Task 4: Docs and smoke check

**Files:**
- Modify: `README.md`, `docs/superpowers/specs/2026-11-26-limits-play-sessions-design.md` (only if implementation forced a change; note it)

- [ ] **Step 1:** README: new limits and switches; Family Link setup (add-on option example with `<kid-user>` / `sensor.<child>_daily_screen_time`, the integration and its auth add-on must be installed); an example dashboard card (whole card YAML, entities `tk_kid_a_*`) using tile cards with `features: [{type: bar-gauge, min: 0, max: 100}]` on the `*_left_pct` sensors, plus switches, numbers, ±15 buttons.
- [ ] **Step 2:** `docker build -t tk-budget-test tk-budget` and a smoke run against `eclipse-mosquitto:2.0.20` with `broker_host` set and `familylink: []`: a usage report for `kid-a` → state JSON has `limits.week` and `limits.day`; setting `daily_limit_active` ON with `daily_total_min` 30 → `remaining` total_s ≤ 1800. (If Docker is unavailable, say so in the ledger.)
- [ ] **Step 3:** Whole tk-budget suite → PASS. Commit `docs: daily and combined limits, Family Link, bar card`.
