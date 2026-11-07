#!/bin/sh
# Title:       PagerCtl Demo
# Description: Feature demo for the pure-Python pagerctl.py library.
# Author:      brAinphreAk
# Version:     1.0
# Category:    Utilities
#
# Loads the pagerctl.py library as a payload and runs its demo (display, text,
# images, LEDs, sound, vibration, brightness, and button input). No bundled
# binaries — only python3, installed from opkg on first run if missing.

# The Pager runs payloads from /tmp, so $0 is NOT the install dir — hardcode the
# standard install path (fall back to $0's dir when run directly for testing).
PAYLOAD_DIR="/root/payloads/user/utilities/pagerctl"
[ -f "$PAYLOAD_DIR/demo.py" ] || PAYLOAD_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PAYLOAD_DIR" || exit 1

export PATH="/mmc/usr/bin:/usr/bin:/bin:$PATH"
export LD_LIBRARY_PATH="/mmc/usr/lib:$LD_LIBRARY_PATH"
export PYTHONPATH="$PAYLOAD_DIR:$PYTHONPATH"

# First run: install python3 to the SD card (mmc) if it isn't present.
if ! command -v python3 >/dev/null 2>&1; then
    LOG ""
    LOG "yellow" "First-run setup: installing python3 (needs internet)..."
    opkg update 2>&1 | while IFS= read -r l; do LOG "  $l"; done
    opkg -d mmc install python3-light 2>&1 | while IFS= read -r l; do LOG "  $l"; done
    if ! command -v python3 >/dev/null 2>&1; then
        LOG "red" "Could not install python3 — check the connection and retry."
        LOG "Press any button to exit..."
        WAIT_FOR_INPUT >/dev/null 2>&1
        exit 1
    fi
fi

# Pre-launch screen (plain-text pager console, shown before we take the display).
LOG ""
LOG "green" "PagerCtl Demo"
LOG "cyan" "Pure-Python Pager graphics library"
LOG ""
LOG "green" "GREEN = Start"
LOG "red" "RED = Exit"
LOG ""
while true; do
    B=$(WAIT_FOR_INPUT 2>/dev/null)
    case "$B" in
        GREEN|A) break ;;
        RED|B) exit 0 ;;
    esac
done

# Hold UI_TAKEOVER for the whole session; pagerctl.py then skips its own
# takeover/release (PAGERCTL_EXTERNAL_TAKEOVER) so there's no screen flicker.
export PAGERCTL_EXTERNAL_TAKEOVER=1
UI_TAKEOVER
trap 'UI_RELEASE 2>/dev/null' EXIT INT TERM

python3 demo.py

exit 0
