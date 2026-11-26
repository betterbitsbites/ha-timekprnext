# Spike results (plan Task 1)

Status: **DONE** (2026-11-17, timekpr-next 0.5.10-1, Manjaro, test user `tkprobe`).
Raw output: `raw/probe-full-condensed.txt`, `raw/probe-tick-and-later.txt`; fixtures in `tk-agent/tests/fixtures/`.

## How to run

```sh
sudo spike/probe.sh setup                 # creates test user `tkprobe`, asks for a password
# log in as tkprobe once (graphical session), then from another session as root:
sudo spike/probe.sh full 2>&1 | tee probe-full.txt
# keep tkprobe logged in and active for ~2 min, then:
sudo spike/probe.sh tick 2>&1 | tee probe-tick.txt
# after midnight, if possible:
sudo spike/probe.sh tick 2>&1 | tee probe-midnight.txt
# finally:
sudo spike/probe.sh cleanup               # logs tkprobe out, deletes it and its timekpr files
```

A different name can be given as second argument (`setup <name>`, `full <name>`, ...).
`full`, `tick` and `cleanup` refuse users that `setup` did not create.
Replace real host names with `<client-host>` before pasting.

## Decisions

- (a) **`timekpra --settimeleft <user> = <seconds>`** works and takes effect immediately (visible in the next `--getuserinfort`). `+ 15m` was also accepted (+900 s); the agent uses plain seconds only. **Every command exited 0**, so the exit code does not prove a value was applied: the agent reads time left back after each write.
- (b) **Yes.** Time left ticks down 1:1 while the user is active: 5400 → 5130 in 270 s (01:02:52 → 01:07:22). Field **`ACTUAL_TIME_LEFT_DAY`** from `--getuserinfort`, unit **seconds**.
- (c) `=` changes only **`TIME_SPENT_BALANCE`** (= daily limit − time left; 86400 − 3600 = 82800). `TIME_SPENT_DAY`, `TIME_SPENT_WEEK` are unchanged. This confirms the spec: do not use timekpr's spent counters for usage.
- (d) **Day change resets the balance**: at 22:28 the balance was 81024 (time left 5376); after midnight it was 210 and time left back to the daily limit. A value set with `settimeleft` does **not** carry into the next day — after midnight timekpr grants its own daily limit until the agent writes again. **`ACTUAL_TIME_LEFT_DAY` is also capped by the time until midnight** (baseline 82630 s at 01:02:50 = time to 24:00, although limit 86400 and balance 210). `ACTUAL_TIME_LEFT_CONTINUOUS` (169030) spans past midnight, so timekpr does not kick the user at 24:00.
- (e) `lock`, `shutdown`, `terminate`, `suspend` are all accepted and stored in `LOCKOUT_TYPE`. What each does at time-up was not tested (acceptance, Task 9).
- (f) `--setplaytimeleft <user> = <seconds>` is accepted and shows as `PLAYTIME_LEFT_DAY` in `--getuserinfo`; `ACTUAL_PLAYTIME_LEFT_DAY` stays 0 while PlayTime is disabled. To be explored in the play-time plan with PlayTime enabled.
- (g) **Logged-out users** (found in acceptance, 2026-11-24): `--getuserinfort` prints only the header `# Configuration for user <u>:` (exit 0), and `--settimeleft` is accepted but has no effect (`TIME_LEFT_DAY` stays at the time until midnight). The agent reports unchanged usage for them and enforces from the first tick after login (≤ one interval).
- (h) **PlayTime, read-only observation** (2026-11-26, real kid account, a Steam client running, PlayTime on in the timekpr GUI): `--getuserinfort` has **`ACTUAL_PLAYTIME_LEFT_DAY`** (seconds). Over 171 s with 51 s screen-locked, `ACTUAL_TIME_LEFT_DAY` fell 120 s (32580 → 32460) and `ACTUAL_PLAYTIME_LEFT_DAY` fell 120 s (17436 → 17316): play time ticks 1:1 with total time while a PlayTime activity runs, and locked time counts for neither (`TRACK_INACTIVE: False`; locked time shows in `ACTUAL_TIME_INACTIVE_SESSION`). `--getuserinfo` printed `PLAYTIME_ENABLED: False` although PlayTime was on and counting (the known stale flag): do not read that flag. `ACTUAL_TIME_LEFT_DAY` (32580) was below the time to midnight because timekpr's own **weekly** limit (`LIMIT_PER_WEEK` 72000, spent 39408) was binding: the agent's read-back already copes, but timekpr's own limits must be set non-binding for HA budgets to govern. `--setplaytimeleft` with PlayTime on is still unverified (write probe not run): the agent reads back after every play write and warns if timekpr did not apply it.
- (i) **Sessions** (systemd 262): `loginctl list-sessions -o json` ignores `-o` and prints the table. Robust path: `loginctl show-user <u> -p Sessions --value` (space-separated ids), then `loginctl show-session <id> -p Id -p Class -p Type -p State -p Remote -p Seat -p TTY -p LockedHint` (key=value lines). Each graphical login has a `Class=user` session plus a `Class=manager` session (the user's systemd): count only classes starting with `user`. A locked screen keeps `State=active` and sets **`LockedHint=yes`**; fast user switching leaves the other session `State=online`. "Inactive" = `LockedHint=yes` or `State` not `active`.

**Verdict:** `TimeLeftEnforcer` as planned (Tasks 6–7 proceed), with two consequences:

1. **Baseline = read-back.** After `set_time_left_s(x)` the agent reads `ACTUAL_TIME_LEFT_DAY` and persists *that* as the last written value. Writing more than the time until midnight is otherwise measured as fake usage (write 86400 at 23:00 → read 3600 → "82800 s used").
2. **Midnight gap.** Between 00:00 and the agent's next tick (≤ 45 s) timekpr uses its own daily limit (24 h). With the agent running this is harmless: the next tick writes the remaining budget again; the jump is not counted as usage. If the agent is not running at midnight, the kid gets timekpr's own limit for that day. Keep systemd `Restart=always`; a lower timekpr daily limit as a backstop is an option for later.
