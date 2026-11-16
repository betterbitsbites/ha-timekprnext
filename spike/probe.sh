#!/usr/bin/env bash
# Spike probe for timekpr-next CLI semantics (throwaway, see plan Task 1).
#
# Usage (as root):
#   spike/probe.sh setup   [user]   create a throwaway test user (asks for its password)
#   spike/probe.sh full    [user]   full probe, prints everything
#   spike/probe.sh tick    [user]   one timestamped --getuserinfort (run again after ~2 min
#                                   with the test user logged in, and after midnight)
#   spike/probe.sh cleanup [user]   log the test user out and delete it with its timekpr files
#
# [user] defaults to `tkprobe`. Never use a kid's account: `cleanup` only deletes
# users that `setup` created (marked in the GECOS field).
#
# Redirect output to a file and paste it into spike/RESULTS.md, e.g.:
#   sudo spike/probe.sh full 2>&1 | tee probe-full.txt
#
# `full` changes the test user's time left and lockout type. At the end it
# restores the lockout type found at the start; time left is left as set.

set -u

MARK="tk-probe test user"
TK_CONFIG_DIR=/var/lib/timekpr/config
TK_WORK_DIR=/var/lib/timekpr/work

if [[ $EUID -ne 0 ]]; then
    echo "probe.sh: must run as root (timekpra needs root)" >&2
    exit 1
fi

usage() {
    echo "usage: $0 {setup|full|tick|cleanup} [user]   (default user: tkprobe)" >&2
    exit 2
}

[[ $# -ge 1 ]] || usage
MODE="$1"
U="${2:-tkprobe}"

run() {
    echo
    echo "===== \$ timekpra $* ====="
    echo "----- at $(date '+%Y-%m-%d %H:%M:%S %z')"
    timekpra "$@"
    echo "----- exit=$?"
}

is_probe_user() {
    [[ "$(getent passwd "$U" | cut -d: -f5)" == "$MARK" ]]
}

require_probe_user() {
    if ! getent passwd "$U" >/dev/null; then
        echo "probe.sh: user '$U' does not exist; run: $0 setup $U" >&2
        exit 1
    fi
    if ! is_probe_user; then
        echo "probe.sh: '$U' was not created by '$0 setup'; refusing (never probe a real account)" >&2
        exit 1
    fi
}

do_setup() {
    if getent passwd "$U" >/dev/null; then
        echo "probe.sh: user '$U' already exists; refusing to touch it" >&2
        exit 1
    fi
    useradd --create-home --shell /bin/bash --comment "$MARK" "$U" || exit 1
    echo "Set a password for '$U' (needed to log in graphically for the tick test):"
    until passwd "$U"; do echo "try again"; done
    echo
    echo "Waiting for timekpr to list '$U' ..."
    for _ in $(seq 1 30); do
        if timekpra --userlist 2>/dev/null | grep -qw -- "$U"; then
            echo "timekpr knows '$U'."
            echo "Next: log in as '$U' once (graphical session), then run: sudo $0 full $U"
            return 0
        fi
        sleep 2
    done
    echo "timekpr does not list '$U' yet. Try: systemctl restart timekpr ; timekpra --userlist" >&2
    echo "If it shows up after a first login of '$U', that is fine too." >&2
}

do_cleanup() {
    require_probe_user
    echo "Logging out '$U' ..."
    loginctl terminate-user "$U" 2>/dev/null
    sleep 2
    pkill -KILL -u "$U" 2>/dev/null
    sleep 1
    userdel --remove "$U" 2>&1 | grep -v "mail spool" || true
    if getent passwd "$U" >/dev/null; then
        echo "probe.sh: userdel failed, '$U' still exists" >&2
        exit 1
    fi
    for f in "$TK_CONFIG_DIR/timekpr.$U.conf" "$TK_WORK_DIR/$U.time"; do
        if [[ -e "$f" ]]; then rm -f -- "$f" && echo "removed $f"; fi
    done
    echo "Test user '$U' removed."
}

do_tick() {
    require_probe_user
    run --getuserinfort "$U"
}

do_full() {
    require_probe_user
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
    echo "##### done. Now: keep the test user logged in and active ~2 min, then run:"
    echo "#####   sudo $0 tick $U"
    echo "##### and once more after midnight if possible. Finally: sudo $0 cleanup $U"
}

case "$MODE" in
    setup) do_setup ;;
    full) do_full ;;
    tick) do_tick ;;
    cleanup) do_cleanup ;;
    *) usage ;;
esac
