#!/usr/bin/env bash
# Install ArduPilot SITL — idempotent.
#
# Steps (per https://ardupilot.org/dev/docs/setting-up-sitl-on-linux.html):
#   1. git clone (skip если уже есть)
#   2. submodule update --init --recursive
#   3. Tools/environment_install/install-prereqs-ubuntu.sh -y
#   4. ./waf configure --board sitl
#   5. ./waf copter
#   6. verify sim_vehicle.py runs --help OK
#
# Idempotent — safe to re-run; skip уже сделанные шаги.
#
# Default install root: $HOME/ardupilot
set -euo pipefail

ARDUPILOT_ROOT="${ARDUPILOT_ROOT:-$HOME/ardupilot}"
ARDUPILOT_BRANCH="${ARDUPILOT_BRANCH:-Copter-4.5}"
NPROC="${NPROC:-$(nproc 2>/dev/null || echo 4)}"

echo "==> ArduPilot SITL installer"
echo "    root:   $ARDUPILOT_ROOT"
echo "    branch: $ARDUPILOT_BRANCH"
echo "    nproc:  $NPROC"

# 1. Clone.
if [ ! -d "$ARDUPILOT_ROOT/.git" ]; then
    echo "==> [1/5] git clone (~500 MB)..."
    git clone --depth 100 --branch "$ARDUPILOT_BRANCH" \
        https://github.com/ArduPilot/ardupilot.git "$ARDUPILOT_ROOT"
else
    echo "==> [1/5] git already cloned, skip"
fi
cd "$ARDUPILOT_ROOT"

# 2. Submodules.
echo "==> [2/5] git submodule update --init --recursive (~ 1.5 GB)..."
git submodule update --init --recursive --depth 1 -j "$NPROC" 2>&1 \
    | grep -E "Submodule path|Cloning into" | head -30 || true

# 3. Install prerequisites (apt + pip3).
PREREQ_MARKER="$ARDUPILOT_ROOT/.bas_prereqs_done"
if [ ! -f "$PREREQ_MARKER" ]; then
    echo "==> [3/5] install-prereqs-ubuntu.sh (apt + pip3, ~5 min)..."
    # Skip apt prereqs if BAS_SKIP_APT=1 — useful когда apt deps уже есть
    # из других stages и не нужен sudo.
    if [ "${BAS_SKIP_APT:-0}" = "1" ]; then
        echo "    BAS_SKIP_APT=1 — пропускаем apt install"
    elif [ "$EUID" -eq 0 ]; then
        Tools/environment_install/install-prereqs-ubuntu.sh -y
    else
        echo "    [warn] не root и BAS_SKIP_APT не задан — рискуем без apt deps"
        echo "    Если build упадёт, запустить вручную:"
        echo "        sudo bash $ARDUPILOT_ROOT/Tools/environment_install/install-prereqs-ubuntu.sh -y"
    fi
    touch "$PREREQ_MARKER"
else
    echo "==> [3/5] prereqs already installed, skip"
fi

# 4. Configure SITL board.
if [ ! -f "build/sitl/c4che/_cache.py" ]; then
    echo "==> [4/5] waf configure --board sitl..."
    ./waf configure --board sitl
else
    echo "==> [4/5] already configured, skip"
fi

# 5. Build Copter.
if [ ! -x "build/sitl/bin/arducopter" ]; then
    echo "==> [5/5] waf copter (compile, ~15-25 min)..."
    ./waf -j "$NPROC" copter
else
    echo "==> [5/5] arducopter already built, skip"
fi

# Verify sim_vehicle.py.
echo
echo "==> Verify sim_vehicle.py..."
SV="$ARDUPILOT_ROOT/Tools/autotest/sim_vehicle.py"
if [ -x "$SV" ]; then
    "$SV" --help 2>&1 | head -5
else
    echo "    [!] sim_vehicle.py NOT executable at $SV"
    exit 1
fi

echo
echo "==> ArduPilot SITL installed успешно!"
echo "    Запуск SITL:"
echo "        $SV -v ArduCopter -f JSON --no-mavproxy"
echo "    или с JSON frame для real physics bridge:"
echo "        $SV -v ArduCopter -f JSON --no-mavproxy --out=udp:127.0.0.1:14550"
