#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOTLAUNCHER_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"

exec bash "$BOTLAUNCHER_ROOT/Script/EnvTools/linux/remove_env.sh"
