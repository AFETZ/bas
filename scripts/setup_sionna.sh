#!/usr/bin/env bash
# Этап 2.1.a: установка Sionna RT venv.
#
# Изолированный Python venv для Sionna RT (TensorFlow + Mitsuba 3).
# Не смешивается с orchestrator/.venv -- у Sionna жёсткие version-constraints,
# и mixing с pymavlink приводит к dependency conflicts.
#
# После запуска:
#   ./sionna_env/bin/python -c "import sionna.rt; print(sionna.__version__)"
#   ./sionna_env/bin/python scripts/compute_radio_map.py --smoke
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SIONNA_VENV="${REPO_ROOT}/sionna_env"

if [ -d "$SIONNA_VENV" ] && [ "${1:-}" != "--force" ]; then
    echo "Sionna venv уже существует: $SIONNA_VENV"
    echo "Для пересоздания: bash scripts/setup_sionna.sh --force"
    exit 0
fi

if [ -d "$SIONNA_VENV" ] && [ "${1:-}" = "--force" ]; then
    echo "==> удаляю старый Sionna venv"
    rm -rf "$SIONNA_VENV"
fi

echo "==> создаю Sionna venv в $SIONNA_VENV"
python3 -m venv "$SIONNA_VENV"

echo "==> обновляю pip"
"$SIONNA_VENV/bin/pip" install --upgrade pip wheel setuptools >/dev/null

echo "==> ставлю Sionna + TF + Mitsuba (это может занять 5-10 минут)"
"$SIONNA_VENV/bin/pip" install -r "$REPO_ROOT/requirements_sionna.txt"

echo
echo "==> smoke check: import sionna.rt"
"$SIONNA_VENV/bin/python" -c "
import sionna
import sionna.rt
import tensorflow as tf
print(f'sionna {sionna.__version__}')
print(f'tensorflow {tf.__version__}')
gpus = tf.config.list_physical_devices('GPU')
print(f'GPU devices visible to TF: {len(gpus)}')
for g in gpus:
    print(f'  - {g.name}')
"

echo
echo "==> готово. Sionna venv: $SIONNA_VENV"
echo "    Использование: $SIONNA_VENV/bin/python scripts/<sionna_script>.py"
