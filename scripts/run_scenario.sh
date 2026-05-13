#!/usr/bin/env bash
# Запуск сценария.
#
# Использование:
#   ./scripts/run_scenario.sh baseline_wifi          # stub-режим (без Docker)
#   ./scripts/run_scenario.sh degraded_lora --real   # реальный режим (Docker)
set -euo pipefail

SCENARIO="${1:-baseline_wifi}"
shift || true

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# venv для оркестратора при первом запуске
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -e ./orchestrator -e ./analyzer
fi

source ./.venv/bin/activate

echo "==> Запуск сценария: $SCENARIO"
bas-orchestrator "$SCENARIO" "$@"

# Самый свежий каталог логов
LAST_RUN="$(ls -td logs/*/ 2>/dev/null | head -n1 || true)"
if [ -n "$LAST_RUN" ]; then
  echo "==> Построение отчёта по $LAST_RUN"
  bas-analyzer "$LAST_RUN"
fi
