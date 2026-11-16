# ha-timekprnext

Home Assistant control of Timekpr-nExT parental controls on Arch/Manjaro clients, with one shared time budget per kid across all PCs. Two parts: `tk-agent` (per client) and `tk-budget` (HA add-on), sharing `tk_protocol`.

## Where things are

- Design (approved in conversation, decisions and terms): `docs/superpowers/specs/2026-11-16-timekpr-ha-design.md`
- Plan to execute now: `docs/superpowers/plans/2026-11-16-core-v1.md` (spec build steps 0–2: spike, one kid, two PCs)
- Background notes and timekpr findings (private repo): `betterbitsbites/ha-timekprnext--notes` (`TIMEKPR-NOTES.md`, `CURRENT-SETUP.md`)

## How to work here

- The human partner writes German; answer in German. Code, comments, docs and commit messages are English.
- Use the superpowers skills. Execute the plan task by task (`superpowers:executing-plans` or `superpowers:subagent-driven-development`); the execution method has not been chosen yet — ask once, recommend native.
- **Task 1 (spike) needs the human partner** to run `spike/probe.sh` as root on a real client with a test user and paste the output. Do not start Tasks 6–7 before `spike/RESULTS.md` is filled; if the spike disproves the time-left approach, add the fallback task first (see plan Task 1, Step 4).
- Plans for play time, play-lock actions (pfSense/Pi-hole), Family Link adapter and statistics views come later, after the spike. The spec already describes them.
- TDD, small commits. Run tests per package (`protocol`, `tk-budget`, `tk-agent`).

## Rules

- Commit messages must not contain `Claude-Session` lines or links to Claude sessions.
- Touch timekpr only through the `timekpra` CLI. `tk-agent` uses stdlib + `python-paho-mqtt` (pacman) only.
- Never put real hostnames, usernames, MQTT credentials or kid names in the repo; use placeholders (`<kid-user>`, `<client-host>`).
- When giving Home Assistant card YAML, always give the whole card element, never partial snippets.
