# Spike results (plan Task 1)

Status: **PENDING**. Needs a run of `spike/probe.sh` as root on a real client with a test user.

## How to run

```sh
sudo spike/probe.sh <test-user> 2>&1 | tee probe-full.txt
# keep <test-user> logged in and active for ~2 min, then:
sudo spike/probe.sh <test-user> tick 2>&1 | tee probe-tick.txt
# after midnight, if possible:
sudo spike/probe.sh <test-user> tick 2>&1 | tee probe-midnight.txt
```

Replace real user and host names with `<test-user>` / `<client-host>` before pasting.

## Decisions

- (a) Working `set time left` command format:
- (b) Time left ticks down while active? Readable via `--getuserinfort` (field, unit):
- (c) Spent counters change after `=`?
- (d) Midnight behaviour:
- (e) Accepted lockout-type values:
- (f) Play-time-left commands behave the same?

**Verdict:** (TimeLeftEnforcer as planned / fallback enforcer needed)

## Raw output

(paste here)
