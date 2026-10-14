#!/bin/bash
# Says in plain words whether the Leetotter companion service is installed and running.
set -euo pipefail

dry_run=false
for arg in "$@"; do
    case "$arg" in
        --dry-run) dry_run=true ;;
        *) printf 'status.sh: unknown argument: %s\n' "$arg" >&2; exit 2 ;;
    esac
done
if "$dry_run" && [[ -z "${LEETOTTER_HOME:-}" ]]; then
    printf 'status.sh: --dry-run requires LEETOTTER_HOME\n' >&2
    exit 2
fi

target_home=${LEETOTTER_HOME:-$HOME}
label=com.leetotter.companion
plist="$target_home/Library/LaunchAgents/$label.plist"
app_dir="$target_home/Library/Application Support/Leetotter/app"
log="$target_home/Library/Logs/Leetotter/companion.log"
port=${LEETOTTER_PORT:-8765}

echo "Leetotter companion service (starts at login)"
if [[ -f "$plist" ]]; then
    echo "  Installed: yes ($plist)"
else
    echo "  Installed: no. Install it with ./scripts/install.sh --base-url URL"
fi

if "$dry_run"; then
    echo "  Running: not checked (dry run)"
else
    row=$(launchctl list | awk -v label="$label" '$3 == label { print $1, $2 }')
    if [[ -z "$row" ]]; then
        echo "  Running: no (not loaded)"
    else
        read -r pid code <<<"$row"
        if [[ "$pid" != "-" ]]; then
            echo "  Running: yes (process $pid)"
        else
            echo "  Running: no, loaded but not running (last exit code $code); see the log below"
        fi
    fi
    if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "  Listening: yes, on 127.0.0.1:$port"
    else
        echo "  Listening: no, nothing is listening on 127.0.0.1:$port"
    fi
    if security find-generic-password -a "$USER" -s NUS_SOCLAAS_API_KEY >/dev/null 2>&1; then
        echo "  API key in Keychain: yes"
    else
        echo "  API key in Keychain: no (model calls will fail until it is added)"
    fi
fi

echo "  Program copy: $app_dir"
echo "  Log: $log"
if [[ -f "$log" ]]; then
    echo "  Last log lines:"
    tail -5 "$log" | sed 's/^/    /'
fi
