# Daily limits, play time, sessions, Family Link, dashboard bars — design

Extends `2026-11-16-timekpr-ha-design.md` and `2026-11-24-auto-users-design.md`. Where this document
differs from the 2026-11-16 spec, this one wins.

## Intent and why

The parent wants to see at a glance, per kid, how much is left of each limit, and to steer
everything from HA:

- a **daily limit** for total time, in addition to the weekly budget (which stays);
- **play time** (timekpr PlayTime: games matched by process masks) with its own limits;
- **how many sessions** a kid has and where, and a way to **end sessions** from HA (a forgotten
  locked session on a PC, or a kid that has to stop now);
- **Family Link**: one daily limit across PC and phone, so screen time is one budget no matter the
  device;
- a **dashboard card with bars** per limit, starting with one kid.

The human partner said "build" without further questions; the calls below are mine and are listed
for review at the end.

A suspicion that time ran too fast for one kid is **parked**: it is only investigated if it shows
again. (Analysis so far: the agent measures per PC and user, not per session, so a second session on
the same PC cannot double-count; two PCs at once drain twice as fast by design.)

## Goals

1. Total time: weekly limit (exists) and daily limit, each switchable on/off independently.
2. Play time: weekly play limit and daily play limit, each switchable; play time is a subset of total
   time (a game minute also uses up total time).
3. Combined PC + phone daily limit (Family Link), switchable; phone usage counts only against this
   limit.
4. Sessions: per kid a sensor with the number of sessions (all PCs) and details per PC; buttons
   "end inactive sessions" and "log out everywhere".
5. Per limit a "remaining %" sensor for bars, plus remaining-minutes sensors.
6. Dashboard card for one kid with native HA bars.

## Non-goals

- Locking the phone from this system (Family Link stays input only; its own controls exist in HA).
- Play-lock actions (pfSense/Pi-hole) — later, unchanged from the 2026-11-16 spec.
- Per-weekday limits (one value for every day; can follow later).
- Investigating the "time ran fast" suspicion (parked, see above).

## Anti-goals

- "No limit" and "limit reached (0)" must never be confused (carried over).
- A kid must never get more time because something failed (fail closed), except that a kid without
  any active limit is never touched.
- Ending sessions must never hit a user other than the kid on whose device the button was pressed.

## Approaches

1. **Central (chosen):** tk-budget computes one final `remaining` per kid (total and play) as the
   minimum over all active limits; agents stay dumb and enforce the two numbers. Same shape as today;
   all limit logic is testable in one place; agents need only a protocol extension for play.
2. Agents compute daily limits locally from settings. Rejected: duplicates logic per PC and cannot
   see other PCs' or the phone's usage.
3. Separate add-on for Family Link. Rejected: one more moving part; tk-budget already talks to the
   Supervisor and can read HA states with `homeassistant_api: true`.

## Design

### Limits (tk-budget)

Per kid, each limit = a number (minutes) + an on/off switch. A limit that is on with an unset value
counts as 0 (fail closed, as today).

| Limit | Number entity | Switch | Counts |
|---|---|---|---|
| Weekly total | `weekly_total_budget` (exists) | `limit_active` (exists, renamed "Weekly limit active") | PC usage this ISO week |
| Daily total | `daily_total_limit` | `daily_limit_active` | PC usage today |
| Daily PC + phone | `daily_combined_limit` | `combined_limit_active` | PC usage today + phone usage today |
| Weekly play | `weekly_play_budget` | `play_limit_active` | PC play this week |
| Daily play | `daily_play_limit` | `daily_play_active` | PC play today |

- `remaining_total` = min over the active total limits (weekly: budget + week adjustment − used week;
  daily: limit + today adjustment − used today; combined: limit + today adjustment − used today − phone
  today), clamped ≥ 0. `limited` = any total limit on.
- `remaining_play` = min over the active play limits (play adjustments analogous), and also
  ≤ `remaining_total` when total is limited. `play_limited` = any play limit on.
- Existing `±15 min` buttons add to the **week adjustment and today's adjustment** together, so
  pressing +15 grants 15 minutes whichever limit binds. New `±15 min play` buttons do the same for
  play. Week adjustments reset at week change, today adjustments at midnight (existing semantics).
- Existing kids keep their entities and ids; the upgrade adds the new ones, all switches off.

### Protocol

- `Usage` gains optional `play_s`, `play_day_s` (default 0; old agents report no play).
- `Remaining` gains optional `play_s` (default 0) and `play_limited` (default **false**: play was
  never enforced before, so an old payload must not suddenly lock games).
- New non-retained topic `timekpr/<kid>/action` with payload `{"op": "end_inactive" | "logout",
  "ts": "<iso>"}`; agents ignore messages older than 120 s (a late redelivery must not log a kid out).
- New retained topic `timekpr/<host>/<kid>/sessions` with `{"count": n, "sessions": [{"id", "state",
  "type", "seat", "remote"}]}`, published by each agent per tick when it changed.

### tk-agent

- Play: reads PlayTime left from timekpr and measures play usage with the same baseline method as
  total (`UsageMeter` instance per user for play). Enforces `remaining.play_s` via
  `timekpra --setplaytimeleft <user> = <s>` only while `play_limited`; on play limited → unlimited it
  writes a full day once (same release logic as total). Exact fields and semantics come from the
  play probe (`spike/probe-play.sh`), recorded in `spike/RESULTS.md` (h). **Blocking:** the play
  part of the agent is built after the probe results are in.
- Sessions: `loginctl list-sessions -o json` (systemd is a given on Arch/Manjaro; stdlib only).
  Counts the user's sessions of class `user` (excluding `manager`/`greeter`, if present per the
  probe). `end_inactive` → `loginctl terminate-session <id>` for each of the kid's sessions whose
  state is not `active`; `logout` → `loginctl terminate-user <kid>`. The kid name is the timekpr user
  known on this PC; anything else is ignored.

### tk-budget: sessions and Family Link

- Sessions: subscribes `timekpr/+/+/sessions`, keeps the latest per host, publishes in the kid's
  state `sessions` (sum) and `sessions_by_host`. Sensor `sessions` (count) with attribute per host.
  Hosts whose `status` is `offline` count 0.
- Family Link: add-on option `familylink` = list of `{kid, entity}` (entity =
  `sensor.<child>_daily_screen_time`, minutes today, all the child's devices). `homeassistant_api:
  true`; every tick (30 s) GET `http://supervisor/core/api/states/<entity>`. Value stored as host
  `_familylink` in the daily statistics (so it shows separately) and used only by the combined limit.
  Missing/unavailable/non-numeric → keep the last value for today (no invented usage). Day boundary:
  after midnight, a value is accepted for the new day only once it is lower than yesterday's last
  value or after 00:15, because the integration resets "at the first refresh of the day".
  The combined-limit entities exist only for kids with a Family Link mapping.

### Bars and dashboard

- Native HA only: tile card with the `bar-gauge` feature (min 0, max 100). tk-budget publishes per
  limit a sensor `*_left_pct` (remaining in % of that limit, unavailable when the limit is off), with
  attributes `remaining_min` and `limit_min`. `bar-card` (HACS) is not needed.
- Remaining-minutes sensors per limit: `remaining_total` (exists, the effective value), plus
  `remaining_week`, `remaining_today`, `remaining_combined`, `remaining_play`.
- Card for one kid (the human partner's kid first): bars, switches, numbers, ±15 buttons, sessions,
  end/logout buttons. Whole card YAML, as always.

## Build order (each a plan with its own tests)

1. **Daily limit + limit-wise remaining + % sensors + combined ±15** (tk-budget only).
2. **Family Link combined daily limit** (tk-budget only).
3. **Sessions + end/logout** (protocol, agent, budget) — after the probe's session output.
4. **Play time** (protocol, agent, budget) — after the play probe.
5. Dashboard card for the kid, extended after each step.

## Calls I made (change if wrong)

- Each limit has its own switch; "Limit active" becomes "Weekly limit active" (id unchanged).
- ±15 min acts on week and today at once.
- Phone usage counts only against the combined daily limit, not against the PC weekly or daily
  limits, and not against play.
- No per-weekday limits; one daily value.
- Bars with native tile `bar-gauge` + % sensors, no custom cards.
- Session end acts on all PCs of the kid; per-PC buttons later if needed.
- Old `Remaining` payloads mean play unlimited.
- Family Link midnight guard: accept after a drop or after 00:15.
- Over a broker outage across midnight, an agent keeps the last snapshot (possibly yesterday's 0)
  until tk-budget is reachable: fail closed.
