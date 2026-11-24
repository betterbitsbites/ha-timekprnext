# Auto-discovered users, opt-in limits, shared agent login

Status: approved in conversation (2026-11-24). Amends `2026-11-16-timekpr-ha-design.md`.

## Intent

Nothing per kid or per PC should need configuring on the PCs. All PCs share one HA user `tk-agent`. A kid's user name is the same on every PC (and in HA), so the user name *is* the kid id. Users appear in HA automatically. A user is limited only once a budget is enabled for them.

## Decisions (human partner)

- One shared MQTT login for all PCs. PCs don't need to be told apart in config.
- The user list is generated automatically. No `kids` list in tk-budget, no `[kids]` mapping on the PCs. Explicit user→kid mapping is dropped (not needed).
- **No budget = no limit. Budget used up = 0 (locked).** These two cases must be distinct.
- Same kid on two PCs at once drains the budget twice as fast (unchanged).
- A per-PC key, if needed, is derived automatically from the PC's own host name.
- Showing on which PC a kid is logged in: later, with the login/logout events plan.
- Approach for learning the list: tk-budget does not publish a list; usage reports create users (see Approaches).

## Approaches

1. **tk-budget publishes a kids list** (`timekpr/_budget/kids`), the agent caches it. Was chosen first, then made obsolete by "list is generated automatically".
2. **Agent derives the kids from retained `remaining`.** Rejected: a removed kid lingers as a stale retained value.
3. **Chosen: the agents report every timekpr user; tk-budget creates a user on first usage.** `remaining` carries whether the user is limited. No extra topic, and the source of truth is timekpr's own user list.

## Design

### Agent (`tk-agent`)

- Users: `timekpra --userlist`. Output: a header line `N users in total:`, then one user name per line. Re-read on every tick. A name not matching `[a-z0-9_-]+` is skipped with one warning. Kid id = user name.
- Host key: `socket.gethostname()`. Take the part before the first `.`, lowercase it, and turn chars outside `[a-z0-9_-]` into `-`. An optional `host_id` in `[agent]` overrides it. Used in usage topics, the status/LWT topic and the MQTT client id. If the host key equals a known user name, log an error and skip that user (shared topic namespace).
- Config: `[broker]` plus `[agent] interval_s`, `state_dir`, and optional `host_id`. `[kids]` is gone. If an old config still has `[kids]`, warn that it is ignored.
- It subscribes `timekpr/+/remaining`, `timekpr/+/settings` and its own `timekpr/<host>/+/usage`, with wildcards. Messages for users not on this PC are ignored. A user who appears later (new timekpr user) picks up retained values: keep the last retained payload per kid even before the user exists locally.
- Per user and tick: measure and publish usage as now, for every user (limited or not).
- Enforcement depends on the last `remaining`:
  - **none ever received:** leave timekpr untouched.
  - **limited** `{week,total_s}`: as now, fail closed.
  - **unlimited**: write nothing. On the transition limited → unlimited, write `MAX_TIME_LEFT_S` once (capped by timekpr at midnight), so a locked kid is released. Persist that state, so a restart doesn't repeat or skip the transition.
- Events time_low/time_up only while limited.
- The PC online discovery sensor is dropped. The status/LWT topic stays.

### Protocol

- `Remaining` gets `limited: bool`. When `limited` is false, `total_s` is 0 and ignored. Old payloads without the field parse as `limited: true` (the fail-closed reading).

### tk-budget

- The option `kids` is removed. Kids are learned from `timekpr/+/+/usage` (subscribe with wildcards). The kid id must match `[a-z0-9_-]+` and must not start with `_` (reserved for `_budget`). A new kid is stored in SQLite and gets discovery, settings and remaining.
- Each kid gets a new entity, switch `limit_active` ("Limit active"), default off. Stored as setting `limit_active`.
- remaining: `limited = limit_active`. With the switch off, the `remaining_total` sensor shows unknown/empty and the agent gets `limited: false`. With it on, budget (unset = 0), adjustments and usage apply as now.
- Usage is recorded and daily statistics are kept for every kid, limited or not.
- Removing a kid: not automated. Delete the device in HA. A retained discovery that reappears is acceptable for now.

### Docs

- README: one HA user `tk-agent` for all PCs. Users appear automatically. Turn on "Limit active" and set the budget. An own daily limit in timekpr still applies in "unlimited" mode.
- `tk-agent.conf.example` and `acl.example` change to match.

## Non-goals

- Mapping between different user names.
- Showing which PC a kid uses (later).
- Removing kids automatically.

## Left to the builder

- Exact entity name and icon for the switch.
- How the sensor shows "unlimited": empty state vs. attribute.
- Migration of an existing `kids` option: ignore it, and the kids get learned again from usage.
