# Spike results (plan Task 1)

Status: **PENDING**. Needs a run of `spike/probe.sh` as root on a real client with a test user.

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

- (a) Working `set time left` command format:
- (b) Time left ticks down while active? Readable via `--getuserinfort` (field, unit):
- (c) Spent counters change after `=`?
- (d) Midnight behaviour:
- (e) Accepted lockout-type values:
- (f) Play-time-left commands behave the same?

**Verdict:** (TimeLeftEnforcer as planned / fallback enforcer needed)

## Raw output

(paste here)
