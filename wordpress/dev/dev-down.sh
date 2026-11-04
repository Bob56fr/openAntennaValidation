#!/usr/bin/env bash
# Arrête le WordPress de test. --purge : supprime aussi sa base et ses fichiers.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
compose() { docker compose "$@" 2>/dev/null || docker-compose "$@"; }
if [[ "${1:-}" == "--purge" ]]; then
  compose down -v
  rm -f .admin-password
else
  compose down
fi
