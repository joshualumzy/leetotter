#!/bin/bash
set -euo pipefail

dry_run=false
while (($#)); do
    case "$1" in
        --dry-run) dry_run=true; shift ;;
        *) printf 'uninstall.sh: unknown argument: %s\n' "$1" >&2; exit 2 ;;
    esac
done

if "$dry_run"; then
    if [[ -z "${LEETOTTER_HOME:-}" || "$LEETOTTER_HOME" != /* || ! -d "$LEETOTTER_HOME" ]] ||
        python3 - "$LEETOTTER_HOME" "$HOME" <<'PY'
import os
import sys
target, home = map(os.path.realpath, sys.argv[1:])
sys.exit(0 if os.path.commonpath((target, home)) == home else 1)
PY
    then
        printf 'uninstall.sh: --dry-run requires an existing LEETOTTER_HOME outside the real home\n' >&2
        exit 2
    fi
    target_home=$LEETOTTER_HOME
else
    target_home=$HOME
fi

plist="$target_home/Library/LaunchAgents/com.leetotter.companion.plist"
if ! "$dry_run"; then
    service="gui/$(id -u)/com.leetotter.companion"
    if launchctl print "$service" >/dev/null 2>&1; then
        launchctl bootout "$service"
    fi
fi
rm -f "$plist"
printf 'Removed %s\n' "$plist"
