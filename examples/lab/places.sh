#!/bin/sh
# Create the six places that `labgrid-tui tour` shows, on a real coordinator.
#
#   ./places.sh          create the places, claim the exporter groups, tag them
#   ./places.sh down     delete them again
#
# Reruns are safe for idle places: create is skipped for places that exist,
# add-match skips patterns already present, and set-tags replaces the whole
# tag set. labgrid-client refuses to change a place someone else has
# acquired, so a rerun fails on benches that are in use at that moment.
#
# Pair this with exporter.yaml in the same directory. labgrid-tui itself
# never runs this script; it is documentation you apply by hand.
set -eu

# The two lines to change for your lab: the exporter name (labgrid-exporter
# -n, defaulting to its hostname) and the coordinator to talk to.
EXPORTER="${LG_EXPORTER:-rack-1}"
COORDINATOR="${LG_COORDINATOR:-127.0.0.1:20408}"

# name:board:env:site. The tags are free-form; labgrid-tui shows them in
# the Tags column, and `labgrid-client reserve` filters on the same pairs.
BENCHES="bench-01:am62x:ci:lab1
bench-02:stm32mp1:ci:lab1
bench-03:imx8:dev:lab1
bench-04:imx8:dev:lab2
bench-05:rpi4:staging:lab2
bench-06:rpi4:staging:lab2"

# Second names for the two benches people ask for by role. labgrid-client
# takes an alias anywhere it takes a place name, and the table prints it
# beside the name.
ALIASES="bench-01:smoke
bench-03:bringup"

lg() {
    labgrid-client -x "$COORDINATOR" "$@"
}

# One `places` call, so the loop below does not ask the coordinator per bench.
place_names() {
    lg places | awk 'NF {print $1}'
}

up() {
    known=$(place_names)
    echo "$BENCHES" | while IFS=: read -r name board env site; do
        echo "$known" | grep -qx "$name" || lg -p "$name" create
        lg -p "$name" add-match "$EXPORTER/$name/*"
        lg -p "$name" set-tags "board=$board" "env=$env" "site=$site"
    done
    # add-alias rejects an alias the place already has, which is the one
    # thing here a rerun would otherwise fail on.
    echo "$ALIASES" | while IFS=: read -r name alias; do
        lg -p "$name" add-alias "$alias" || true
    done
}

# delete refuses a place that is currently acquired; release it first.
down() {
    known=$(place_names)
    echo "$BENCHES" | while IFS=: read -r name _rest; do
        echo "$known" | grep -qx "$name" && lg -p "$name" delete || true
    done
}

case "${1:-up}" in
    up) up ;;
    down) down ;;
    *) echo "usage: $0 [up|down]" >&2; exit 2 ;;
esac
