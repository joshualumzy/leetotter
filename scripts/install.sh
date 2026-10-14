#!/bin/bash
set -euo pipefail

dry_run=false
base_url=
provided_url=false
while (($#)); do
    case "$1" in
        --dry-run) dry_run=true; shift ;;
        --base-url)
            if (($# < 2)); then
                printf 'install.sh: --base-url requires a URL\n' >&2
                exit 2
            fi
            base_url=$2
            provided_url=true
            shift 2
            ;;
        *) printf 'install.sh: unknown argument: %s\n' "$1" >&2; exit 2 ;;
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
        printf 'install.sh: --dry-run requires an existing LEETOTTER_HOME outside the real home\n' >&2
        exit 2
    fi
    target_home=$LEETOTTER_HOME
else
    target_home=$HOME
fi

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
data_dir="$target_home/Library/Application Support/Leetotter"
app_dir="$data_dir/app"
launch="$app_dir/scripts/launch-companion.sh"
provider_env="$data_dir/provider.env"
agent_dir="$target_home/Library/LaunchAgents"
plist="$agent_dir/com.leetotter.companion.plist"
log_dir="$target_home/Library/Logs/Leetotter"
log="$log_dir/companion.log"

if "$provided_url"; then
    if [[ -z "$base_url" || "$base_url" == *$'\n'* || "$base_url" == *$'\r'* ]]; then
        printf 'install.sh: --base-url must be a nonempty, single-line URL\n' >&2
        exit 2
    fi
elif [[ ! -f "$provider_env" ]]; then
    printf 'install.sh: --base-url URL is required on the first install\n' >&2
    exit 2
fi

uv_path=$(command -v uv) || {
    printf 'install.sh: uv is required on PATH\n' >&2
    exit 1
}
uv_dir=$(dirname "$uv_path")
launch_path="$uv_dir:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

umask 077
mkdir -p "$data_dir" "$agent_dir" "$log_dir"
if "$provided_url"; then
    printf 'NUS_SOCLAAS_BASE_URL=%q\n' "$base_url" > "$provider_env"
fi

if ! "$dry_run"; then
    service="gui/$(id -u)/com.leetotter.companion"
    if launchctl print "$service" >/dev/null 2>&1; then
        launchctl bootout "$service"
    fi
fi

# Keep the runtime file list here. Stage it so the destination sync can delete
# obsolete files without deleting its installed virtual environment.
stage=$(mktemp -d "$data_dir/.app-stage.XXXXXX")
trap 'rm -rf "$stage"' EXIT
rsync -a --delete \
    --exclude='__pycache__/' --exclude='*.pyc' \
    --include='/companion/***' \
    --include='/roles.toml' --include='/pyproject.toml' --include='/uv.lock' \
    --include='/scripts/' --include='/scripts/launch-companion.sh' \
    --exclude='*' \
    "$repo/" "$stage/"
rsync -a --delete --exclude='/.venv/' "$stage/" "$app_dir/"

if ! "$dry_run"; then
    (cd "$app_dir" && "$uv_path" sync --frozen)
fi

python3 - "$plist" "$app_dir" "$launch" "$log" "$launch_path" <<'PY'
import plistlib
import sys

plist, app, launch, log, path = sys.argv[1:]
with open(plist, "wb") as out:
    plistlib.dump({
        "Label": "com.leetotter.companion",
        "ProgramArguments": [launch],
        "WorkingDirectory": app,
        "EnvironmentVariables": {"PATH": path},
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }, out)
PY

if ! "$dry_run"; then
    launchctl bootstrap "gui/$(id -u)" "$plist"
    launchctl kickstart -k "$service"
fi
printf 'Installed %s\n' "$plist"
