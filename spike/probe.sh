#!/usr/bin/env bash
# Spike probe for timekpr-next CLI semantics (throwaway, see plan Task 1).
#
# Usage (as root, with a TEST user, never a kid's account):
#   spike/probe.sh <test-user>          full probe, prints everything
#   spike/probe.sh <test-user> tick     one timestamped --getuserinfort (run again after ~2 min
#                                       with the test user logged in, and after midnight)
#
# Redirect output to a file and paste it into spike/RESULTS.md, e.g.:
#   sudo spike/probe.sh <test-user> 2>&1 | tee /tmp/probe-full.txt
#
# The probe changes the test user's time left and lockout type. At the end it
# restores the lockout type found at the start; time left is left as set.

set -u

if [[ $EUID -ne 0 ]]; then
    echo "probe.sh: must run as root (timekpra needs root)" >&2
    exit 1
fi
if [[ $# -lt 1 ]]; then
    echo "usage: $0 <test-user> [tick]" >&2
    exit 2
fi

U="$1"
MODE="${2:-full}"

run() {
    echo
    echo "===== \$ timekpra $* ====="
    echo "----- at $(date '+%Y-%m-%d %H:%M:%S %z')"
    timekpra "$@"
    echo "----- exit=$?"
}

if [[ "$MODE" == "tick" ]]; then
    run --getuserinfort "$U"
    exit 0
fi

echo "##### timekpr probe, user=<test-user>, host=<client-host>"
echo "##### timekpr version:"
pacman -Q timekpr-next 2>/dev/null || echo "(pacman -Q timekpr-next failed)"

ORIG_LOCKOUT=$(timekpra --getuserinfo "$U" 2>/dev/null | sed -n 's/^LOCKOUT_TYPE:[[:space:]]*//p' | head -n1)
echo "##### original LOCKOUT_TYPE: '${ORIG_LOCKOUT}'"

echo
echo "##### (1) baseline"
run --getuserinfo "$U"
run --getuserinfort "$U"

echo
echo "##### (a) set time left: '=' with seconds"
run --settimeleft "$U" = 3600
run --getuserinfort "$U"
run --getuserinfo "$U"

echo
echo "##### (a) set time left: '=' with '1h' (format check, may be rejected)"
run --settimeleft "$U" = 1h
run --getuserinfort "$U"

echo
echo "##### (a) set time left: '+' with '15m' (format check, may be rejected)"
run --settimeleft "$U" + 15m
run --getuserinfort "$U"

echo
echo "##### (a) set time left: '+' with seconds (900)"
run --settimeleft "$U" + 900
run --getuserinfort "$U"
run --getuserinfo "$U"

echo
echo "##### (f) play time left: '=' with seconds"
run --setplaytimeleft "$U" = 1800
run --getuserinfort "$U"

echo
echo "##### (e) lockout types"
for t in lock shutdown terminate suspend; do
    run --setlockouttype "$U" "$t"
    run --getuserinfo "$U"
done

if [[ -n "$ORIG_LOCKOUT" ]]; then
    echo
    echo "##### restore original lockout type"
    run --setlockouttype "$U" "$ORIG_LOCKOUT"
fi

echo
echo "##### done. Now: keep <test-user> logged in and active ~2 min, then run:"
echo "#####   sudo $0 <test-user> tick"
echo "##### and once more after midnight if possible."
