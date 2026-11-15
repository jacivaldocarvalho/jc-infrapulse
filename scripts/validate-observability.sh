#!/usr/bin/env bash
# Run from the repository root. Uses only disposable projects and ephemeral passwords.
set -euo pipefail
exec python3 "$(dirname "$0")/validate-observability.py" "$@"
