# Timekpr-nExT control from Home Assistant — design

Status: DRAFT for approval (2026-11-16). Nothing is built yet. Supersedes the draft in `ARCHITECTURE.md` where they differ.

Amended by `2026-11-24-auto-users-design.md`: kids are timekpr's users (auto-discovered), limits are opt-in per kid, all PCs share one login.

## Intent

Today the parent logs in to a kid's Linux PC over SSH and changes limits with `timekpra` on the CLI. That is slow and unusable on a phone. The goal is to view and change screen time comfortably on a Home Assistant (HA) dashboard, also from outside the house, with **one shared time budget per kid across all PCs**, and to keep daily statistics per kid.

## Terms (used consistently below and in the HA UI)

| Term | Meaning |
|---|---|
| **Total time** | All time the kid is logged in and active (PC usage). |
| **Play time** | Time spent in games/launchers (matched by the PlayTime activity masks). It is a subset of total time: it also uses up total time. |
| **Weekly total budget** | Total time per week (week = Monday–Sunday). |
| **Weekly play budget** | Play time per week. |
| **Weekly adjustment (total)** | `+/- XX min` on this week's total budget. Current week only; the base budget stays unchanged. |
| **Weekly adjustment (play)** | `+/- XX min` on this week's play budget. Optional switch **"also applies to total time"**: when on, the same amount is applied to the weekly total adjustment. |
| **Daily play cap** | Optional per-kid maximum of play time per day (on/off + minutes). Default: off. |
| **Today's play adjustment** | `+/- XX min` (positive or negative) on the daily play cap for today only, plus a **reset to 0**. Expires at midnight. |
| **Time-up action** | What happens on the PC when total time runs out; selectable per kid in HA. |
| **Play-lock actions** | HA entities (switches/scripts) per kid that are turned ON when play time runs out and OFF when play time is available again — e.g. the existing pfSense "anti-games" firewall rules and a Pi-hole filter group. |
| **Family Link source** | Usage on the kid's Android devices (Google Family Link, via the HA integration), counted like desktop usage: the phone's total screen time reduces the **total time**, and the part of it spent in game apps reduces the **play time**. Game minutes are a subset of the phone's total minutes, so nothing is counted twice. |
| **Remaining (total / play)** | What the kid may still use right now, as computed by the budget service. |
| **Daily stats** | Per kid and day: total time and play time used (also per PC). |

## Goals

- Set the weekly total budget and weekly play budget per kid from the HA dashboard.
- Required functions (all from the dashboard, also on a phone):
  - Weekly adjustment of total time, `+/- XX min`.
  - Weekly adjustment of play time, `+/- XX min`, optionally also applied to total time.
  - Daily play cap: on/off and minutes.
  - Today's play adjustment, `+/- XX min`, and reset to 0.
  - Time-up action: selectable per kid (see below).
  - Play-lock actions: when play time runs out, automatically turn on the pfSense anti-games firewall rules (already exist) and a Pi-hole filter (so far only switched in the Pi-hole UI, not in HA); turn them off again when play time is available again (new day or week, adjustment, extension). Which entities are used is chosen per kid.
- **Family Link:** usage on the kid's Android devices counts against the shared budgets (the desktop budgets shrink accordingly): phone screen time reduces total time, the game-app part of it reduces play time. Optional, off by default.
- **Statistics:** per kid and day, total time and play time are recorded and viewable in HA (history graphs); data is kept long term.
- See remaining total and play time per kid, and which PCs are online.
- Budget is shared per kid across all PCs (a kid logged in on two PCs drains it twice as fast — accepted).
- Enforcement keeps working when a PC is offline or HA/broker are down (fail closed: last known remaining, never unlimited).
- A PC that was off picks up current settings when it boots.
- Easy to install on a new client.
- HA automations on `time_low` / `time_up` events.

## Non-goals

- No replacement for timekpr-next's enforcement, warnings, session handling or process detection.
- No custom HA integration (MQTT discovery only).
- No SSH from HA to clients.
- The manual pfSense internet on/off switches stay as they are; showing them on the same per-kid dashboard card is **last-step cosmetics**. (The play-lock actions above are different: they are functional and are built with play time.)
- No locking or limiting of the Android devices from this system in v1 (Family Link is an input only); possible later.

## Anti-goals (would make it a failure even if it "works")

- Time can be lost or double-counted after restarts, reconnects or resends.
- A broker/HA outage grants unlimited time.
- Breaks on Arch/Manjaro or HA updates (e.g. relying on timekpr internals, pip packages).
- The kid account can read credentials or forge usage.
- Statistics lost or rewritten after a restart or reinstall of the budget service.

## Constraints

- Clients: Arch/Manjaro Linux, timekpr-next (`timekpra` CLI, root). Mosquitto already runs; MQTT is in heavy use.
- HA is available and reachable remotely.
- Agent dependencies: Python stdlib + `python-paho-mqtt` from pacman.
- timekpr facts (see `TIMEKPR-NOTES.md`): PlayTime limits are daily only; PlayTime counts towards total time (override off); `--getuserinfo` may show a stale `PLAYTIME_ENABLED`; lockout type is a per-user setting (`LOCKOUT_TYPE`).

## Approaches considered

1. **A — every agent computes the shared budget** (old draft). No central part, but duplicated logic; week rollover, weekly play budget and statistics live in every agent; hard to test.
2. **B — central budget service, thin agents** (**chosen by human partner**). One place for budget math and statistics; agents only measure and enforce; testable; HA shows what the service publishes.
3. **C — logic in HA YAML.** No new service, but fragile, hard to test, breaks with HA changes.
4. **Drop timekpr, own enforcer** (raised by human partner, rejected). Would rebuild session handling, warnings, lockout, offline behaviour, process detection and tamper resistance.

## Decisions (as decided with the human partner)

- timekpr-next stays on every client as **meter and enforcer**.
- A central **budget service** owns the budget math and the statistics (approach B).
- HA is UI/automation only, via MQTT discovery.
- Weekly play budget only; no fixed daily play limit. The previous 5 h/day PlayTime limit is replaced by the optional **daily play cap**.
- Weekly play adjustment optionally also applies to total time (switch in HA).
- Today's play adjustment may be negative and can be reset to 0.
- The time-up action is selectable in HA.
- Per-user daily statistics of PC usage and play time.
- Play time running out triggers play-lock actions (pfSense anti-games rules, Pi-hole filter) via HA.
- Family Link usage counts against the shared budgets: phone screen time → total time; game-app part → play time (not counted twice).

## Architecture

```
HA dashboard / automations / history graphs
        │  MQTT discovery entities, buttons, numbers, selects
   ┌────┴─────┐
   │ Mosquitto│  retained = state store
   └─┬──────┬─┘
     │      │
tk-budget   tk-agent (per client, root)
(HA add-on)   └─ timekpra CLI → timekpr-next (enforces locally)
```

### tk-budget (central, my call: runs as an HA add-on)

- Holds per kid: weekly total budget, weekly play budget, this week's adjustments (total, play), the "also applies to total" switch, daily play cap (on/off, minutes), today's play adjustment, time-up action.
- Consumes usage from all hosts, sums per kid per ISO week (Monday start) and per day, and publishes `remaining` retained:
  - `remaining_total` = weekly total budget + total adjustment − total used this week.
  - `remaining_play` = min(`remaining_total`, weekly play budget + play adjustment − play used this week, and — if the daily play cap is on — daily play cap + today's play adjustment − play used today). All results are clamped at ≥ 0.
- Adjustments reset at week change, today's play adjustment at midnight. Nothing carries over.
- **Statistics:** at each report it updates a persistent daily record per kid (total_s, play_s, also per host) in its data dir (my call: SQLite). Records are append/update-only by day, kept indefinitely, and rebuildable for the current day/week from the retained usage topics. It also publishes "total time today" / "play time today" sensors so HA's own recorder/long-term statistics can draw history graphs.
- Publishes the HA discovery entities (numbers, buttons, selects, switches, sensors, online state).
- Publishes per-kid `settings` retained, including the time-up action.
- **Play-lock actions:** as an HA add-on, tk-budget reads and controls HA entities through the HA API. When `remaining_play` reaches 0 it turns on the kid's configured play-lock entities; when it is > 0 again it turns them off. It reconciles the state periodically and after restart (idempotent, so manual changes or an HA restart do not leave a stale state), and uses a small hysteresis to avoid flapping. Any HA switch, script or input_boolean works, so tk-budget needs no pfSense or Pi-hole code. Prerequisite for Pi-hole: the filter must be switchable as an HA entity (e.g. a switch that calls the Pi-hole API to enable a group); this is part of the build.
- A `play_locked` binary sensor per kid is published for dashboards and automations.
- Consequence worth knowing: raising only the play budget never grants more total time, because play time uses up total time (hence the optional "also applies to total" switch).

### tk-agent (per client, root, systemd)

- Talks to timekpr **only via `timekpra`**; no timekpr Python internals.
- Applies `remaining` to timekpr as "time left today" (total) and "PlayTime left today"; timekpr's own weekly/daily limits stay non-binding (24 h daily, large weekly).
- Applies the **time-up action** from `settings` as timekpr's lockout type (to be verified in the spike which values the CLI accepts; offered in HA: shutdown, lock, logout/terminate, suspend). When play time runs out, timekpr ends the matching game processes (verify in the spike); the time-up action applies to total time.
- **Usage measurement (key principle):** the agent does not trust timekpr's spent counters (setting time left may change them). It persists the value it last wrote, and on the next poll computes `usage = last_written − time_left_now`. Total and play time are measured separately. Persisting the last written value makes a crash/restart lose nothing.
- Publishes usage as cumulative per ISO week and per day per host (idempotent, resend-safe), `status` with LWT, and events (`time_low`, `time_up`, `login`, `logout`).
- Poll/report interval 30–60 s; overshoot is bounded by that interval.
- If no fresh `remaining` arrives: keep enforcing the last known one.

### Family Link source (optional adapter inside tk-budget)

- The community HA integration `noiwid/HAFamilyLink` (already installed in HA) exposes the kid's Android device usage as HA entities. tk-budget reads them through the HA API and treats Family Link as one more "host" (`familylink`) that reports **cumulative per-day minutes**: total screen time (all apps, all the child's supervised devices) and, as a subset, play minutes. That fits the existing idempotent model: a late or repeated update never double-counts.
- Rule (decided): total phone screen time reduces **total time**; the game-app part reduces **play time**. Play minutes are part of the phone's total, so they are not subtracted from total a second time — the same relation as on the desktop (play ⊂ total).
- Which apps are "games" is configurable: a list of game apps (e.g. Minecraft) per kid. A mapping Family Link child ↔ kid is part of the configuration.
- If the integration cannot separate game minutes from total phone time (spike result), fallback (my call): phone screen time reduces total time only, and play time is not reduced from Family Link; the human partner is told before this ships.
- Constraints: the integration uses unofficial, reverse-engineered Google endpoints (its own disclaimer: may break or violate Google's ToS). The adapter is therefore optional and isolated. If the data is missing or stale, tk-budget keeps the last known value and does not invent usage; once data returns, the cumulative daily value catches up. Update frequency of the Family Link data is coarser than desktop (minutes, not seconds), so overshoot of the play budget can be larger for phone use.
- To verify first (spike): which entities/attributes expose per-app daily usage (the integration's own dashboard shows per-app minutes), how often they update, and how the day boundary is defined.
- Statistics show Family Link as its own "host", so desktop and phone play time are visible separately.

### Topics (draft, builder to finalize)

| Topic | Retained | Written by |
|---|---|---|
| `timekpr/<kid>/remaining` (`{total, play, week}`) | yes | tk-budget |
| `timekpr/<kid>/settings` (`{time_up_action, …}`) | yes | tk-budget |
| `timekpr/<host>/<kid>/usage` (`{week, total_s, play_s, day, play_day_s}` cumulative per week / per day) | yes | tk-agent |
| `timekpr/<host>/status` (LWT) | yes | tk-agent |
| `timekpr/<host>/<kid>/event` | no | tk-agent |
| `homeassistant/…/config` | yes | tk-budget (agent for host entities) |

### Security

One Mosquitto user per client with ACL: clients write only their own `usage/status/event`, read only `remaining` and `settings`; only tk-budget/HA write budgets, `remaining` and `settings`. Agent config `/etc/tk-agent.conf` mode 600 root. TLS on Mosquitto if not already.

### Install

PKGBUILD from a code repo (this repo stays notes-only) → `systemctl enable --now tk-agent`; broker already holds the state.

## Build order

0. **Spike on one client (throwaway):** verify `--settimeleft =` and `--setplaytimeleft =` semantics, midnight rollover, how they affect spent counters, that `usage = last_written − time_left_now` works, which lockout types the CLI can set, and what happens when play time runs out. Fallback if the time-left approach does not work: adjust the weekly limit to `local_spent + remaining` (old draft formula).
1. tk-agent v1 + tk-budget v1 for one kid, one client; HA entities; weekly total budget, weekly adjustment (total), time-up action, **daily statistics from day one** (data cannot be recovered later).
2. Second client, shared budget verified with two concurrent sessions.
3. Play time: weekly play budget, weekly adjustment (play) with the "also applies to total" switch, daily play cap, today's play adjustment (± and reset).
4. Play-lock actions: HA entity for the Pi-hole filter, per-kid entity lists, on/off with reconcile and hysteresis; test with the existing pfSense anti-games rules.
5. Family Link adapter (after a spike on the integration's entities), game-app list, mapping child ↔ kid.
6. Statistics views in HA (history graphs per kid/PC/source), PKGBUILD, Mosquitto ACL template, example dashboard.
7. Cosmetic: manual pfSense switches on the dashboard card.

## Calls I made (change if wrong)

- tk-budget runs as an HA add-on; statistics stored in SQLite in its data dir and mirrored to HA sensors.
- Week starts Monday; unused budget and adjustments do not carry over.
- Weekly adjustments change the current week only; today's play adjustment only today.
- "Also applies to total time" is a per-kid switch in HA, default off.
- Today's play adjustment acts on the daily play cap. If the cap is off, it has no effect (the UI shows it as unavailable). To restrict play for one day, turn the cap on.
- Daily play cap is one value per kid (not per weekday); a per-weekday table can follow later.
- Time-up action is one value per kid, applies to total time; offered values: shutdown, lock, logout/terminate, suspend (final list after the spike).
- Stats are kept per kid, per day and per PC, without time limit; no export in v1.
- Play-lock actions trigger only on play time running out, not on total time running out (that is the time-up action).
- Family Link is read-only input in v1; it is optional and off by default.
- Two concurrent sessions drain the budget twice as fast (not blocked in v1).
- Design is generic for N kids × M clients; no names hard-coded.
- Kids see timekpr's normal tray warnings; no new kid-facing UI.

## Open for the human partner

Nothing blocking.

## Left to the builder

Exact topic/payload schema, discovery entity naming, poll interval within 30–60 s, database schema, add-on packaging, test setup, logging.
