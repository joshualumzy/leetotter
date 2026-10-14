#!/bin/bash
set -euo pipefail

app=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
provider_env="$HOME/Library/Application Support/Leetotter/provider.env"
if [[ ! -f "$provider_env" ]]; then
    printf 'Leetotter: provider configuration is missing: %s\n' "$provider_env" >&2
    exit 1
fi

# install.sh writes this file with a shell-quoted, non-secret base URL.
source "$provider_env"
export NUS_SOCLAAS_BASE_URL

if NUS_SOCLAAS_API_KEY=$(security find-generic-password -a "$USER" -s NUS_SOCLAAS_API_KEY -w 2>/dev/null); then
    export NUS_SOCLAAS_API_KEY
else
    unset NUS_SOCLAAS_API_KEY
    printf 'Leetotter: API key is not in the Keychain; model calls will fail until it is added.\n' >&2
fi

cd "$app"
exec uv run python -m companion
