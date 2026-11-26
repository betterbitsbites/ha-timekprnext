#!/usr/bin/env bash
# Spike probe for PlayTime and login sessions (throwaway, play-time / sessions plan).
#
# Usage (as root):
#   spike/probe-play.sh observe <user>   READ-ONLY. Safe on a kid's account. Prints timekpr's
#                                        time and PlayTime values and the user's login sessions.
#                                        Run it while the kid plays a PlayTime game, and again
#                                        ~2 minutes later (still playing).
#   spike/probe-play.sh write [user]     Writes PlayTime values. Only for the test user created
#                                        by `spike/probe.sh setup` (default `tkprobe`); refuses others.
#
# Replace real host and user names with <client-host> / <kid-user> before pasting.

set -u

MARK="tk-probe test user"

if [[ $EUID -ne 0 ]]; then
    echo "probe-play.sh: must run as root (timekpra needs root)" >&2
    exit 1
fi

usage() {
    echo "usage: $0 observe <user> | $0 write [user]" >&2
    exit 2
}

[[ $# -ge 1 ]] || usage
MODE="$1"

run() {
    echo
    echo "===== \$ $* ====="
    echo "----- at $(date '+%Y-%m-%d %H:%M:%S %z')"
    "$@"
    echo "----- exit=$?"
}

sessions() {
    local u="$1"
    run loginctl list-sessions
    run loginctl list-sessions -o json
    run loginctl show-user "$u" -p State -p Sessions -p Display
    for s in $(loginctl show-user "$u" -p Sessions --value 2>/dev/null); do
        run loginctl show-session "$s" -p Id -p Name -p Class -p Type -p State -p Active \
            -p Remote -p Seat -p TTY -p IdleHint -p LockedHint -p Timestamp
    done
}

do_observe() {
    local u="${1:-}"
    [[ -n "$u" ]] || usage
    echo "##### observe (read-only), user=<kid-user>, host=<client-host>"
    pacman -Q timekpr-next systemd 2>/dev/null
    run timekpra --getuserinfo "$u"
    run timekpra --getuserinfort "$u"
    sessions "$u"
    echo
    echo "##### PlayTime processes running for the user (by command line):"
    ps -u "$u" -o pid=,etimes=,args= 2>/dev/null | head -n 60
}

do_write() {
    local u="${1:-tkprobe}"
    if [[ "$(getent passwd "$u" | cut -d: -f5)" != "$MARK" ]]; then
        echo "probe-play.sh: '$u' was not created by 'spike/probe.sh setup'; refusing" >&2
        exit 1
    fi
    echo "##### write probe, user=<test-user>"
    run timekpra --getuserinfo "$u"
    echo
    echo "##### (p1) enable PlayTime, 1 h/day limit, activity 'sleep' (so 'sleep' counts as a game)"
    run timekpra --setplaytimeenabled "$u" true
    run timekpra --setplaytimelimits "$u" "3600;3600;3600;3600;3600;3600;3600"
    run timekpra --setplaytimeactivities "$u" "^sleep\$[probe]"
    run timekpra --getuserinfo "$u"
    run timekpra --getuserinfort "$u"
    echo
    echo "##### (p2) set PlayTime left = 600 (below the daily PlayTime limit)"
    run timekpra --setplaytimeleft "$u" = 600
    run timekpra --getuserinfo "$u"
    run timekpra --getuserinfort "$u"
    echo
    echo "##### (p3) set PlayTime left = 7200 (above the daily PlayTime limit)"
    run timekpra --setplaytimeleft "$u" = 7200
    run timekpra --getuserinfo "$u"
    run timekpra --getuserinfort "$u"
    echo
    echo "##### (p4) set PlayTime left = 600 again, then start 'sleep 150' as the user (a 'game')."
    echo "#####      The test user must be logged in graphically and active for this part."
    run timekpra --setplaytimeleft "$u" = 600
    run timekpra --getuserinfort "$u"
    runuser -u "$u" -- sleep 150 &
    sleep 120
    run timekpra --getuserinfo "$u"
    run timekpra --getuserinfort "$u"
    wait
    echo
    echo "##### (p5) PlayTime left = 0 while a 'game' runs: does timekpr end it?"
    runuser -u "$u" -- sleep 120 &
    local pid=$!
    sleep 5
    run timekpra --setplaytimeleft "$u" = 0
    sleep 60
    if kill -0 "$pid" 2>/dev/null; then echo "##### 'game' still running after 60 s"; else echo "##### 'game' was ended"; fi
    run timekpra --getuserinfort "$u"
    wait 2>/dev/null
    sessions "$u"
    echo
    echo "##### done. Clean up later with: sudo spike/probe.sh cleanup $u"
}

case "$MODE" in
    observe) shift; do_observe "$@" ;;
    write) shift; do_write "$@" ;;
    *) usage ;;
esac
