#!/usr/bin/env bash
# Compatibility wrapper. The public lifecycle surface is scripts/install.sh.

set -euo pipefail
REPO="${DREAMING_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd -P)}"
exec "$REPO/scripts/install.sh" "$@"
