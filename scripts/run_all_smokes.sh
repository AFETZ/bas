#!/usr/bin/env bash
# BAS regression suite — прогоняет все offline-safe smoke-тесты одной командой.
#
# По умолчанию: быстрые self-contained smoke (без сети / GPU / реального SITL).
# --live: дополнительно network/GPU/SITL smoke (медленные, нужны deps).
#
#   bash scripts/run_all_smokes.sh          # offline regression (~1-2 мин)
#   bash scripts/run_all_smokes.sh --live   # + network + GPU + real SITL
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$REPO_ROOT"
VENV="${REPO_ROOT}/.venv/bin/python"
SIONNA_VENV="${REPO_ROOT}/sionna_env/bin/python"   # sionna lives here, not .venv
PER_TIMEOUT="${BAS_SMOKE_TIMEOUT:-120}"
RUN_LIVE=0
[ "${1:-}" = "--live" ] && RUN_LIVE=1

# offline-safe: "label|command"
OFFLINE=(
  "multirotor_dynamics|$VENV scripts/_multirotor_dynamics_smoke.py"
  "large_map|$VENV scripts/_large_map_smoke.py"
  "mavlink_sim_router|$VENV scripts/_mavlink_sim_router_smoke.py"
  "onboard_db|$VENV scripts/_onboard_db_smoke.py"
  "parallel_compute|$VENV scripts/_parallel_smoke.py"
  "arducopter_airsim_fdm|$VENV scripts/_arducopter_airsim_smoke.py"
  "cyber_attack_defense|$VENV scripts/_cyber_smoke.py"
  "multicast_sync_loopback|$VENV scripts/_sync_loopback_smoke.py"
  "airsim_scene_spawn|$VENV scripts/_airsim_scene_smoke.py"
  "admin_web|$VENV scripts/_admin_web_smoke.py"
  "osm_import_offline|$VENV scripts/_osm_import_smoke.py"
  "terrain_offline|$VENV scripts/_terrain_smoke.py"
)

# sionna scene resolution требует sionna_env (TensorFlow + sionna) — добавляем
# только если он установлен. На CI (без GPU) его нет → offline-набор остаётся
# полностью зелёным (12/12); локально с sionna_env — 13/13.
if [ -x "$SIONNA_VENV" ]; then
  OFFLINE+=("sionna_scenes_resolve|$SIONNA_VENV scripts/_sionna_scenes_smoke.py --resolve-only")
fi

# live: нужны сеть / GPU / ArduPilot binary
LIVE=(
  "osm_import_live|$VENV scripts/_osm_import_smoke.py --live"
  "terrain_live|$VENV scripts/_terrain_smoke.py --live"
  "sync_stats|$VENV scripts/_sync_stats_smoke.py"
  "admin_web_integration|$VENV scripts/_admin_web_integration_smoke.py"
  "sionna_scenes_live|bash scripts/_run_scenes_smoke.sh"
  "real_sitl_e2e|$VENV scripts/_real_sitl_e2e_smoke.py"
)

run_one() {
  local label="$1"; local cmd="$2"
  printf "  %-26s " "$label"
  local out; out="$(timeout "$PER_TIMEOUT" bash -c "$cmd" 2>&1)"
  if echo "$out" | grep -q "ALL CHECKS PASSED"; then
    echo "PASS"; return 0
  else
    echo "FAIL"
    echo "$out" | tail -4 | sed 's/^/      /'
    return 1
  fi
}

echo "=========================================================================="
echo "  BAS regression suite  (offline set; --live для network/GPU/SITL)"
echo "  per-test timeout=${PER_TIMEOUT}s"
echo "=========================================================================="
echo
echo "[offline-safe smokes]"
n_pass=0; n_fail=0; failed=()
for item in "${OFFLINE[@]}"; do
  if run_one "${item%%|*}" "${item#*|}"; then n_pass=$((n_pass+1)); else n_fail=$((n_fail+1)); failed+=("${item%%|*}"); fi
done

if [ "$RUN_LIVE" = "1" ]; then
  echo
  echo "[live smokes (network/GPU/SITL)]"
  for item in "${LIVE[@]}"; do
    if run_one "${item%%|*}" "${item#*|}"; then n_pass=$((n_pass+1)); else n_fail=$((n_fail+1)); failed+=("${item%%|*}"); fi
  done
fi

echo
echo "=========================================================================="
echo "  RESULT: ${n_pass} passed, ${n_fail} failed"
[ "$n_fail" -gt 0 ] && echo "  failed: ${failed[*]}"
echo "=========================================================================="
exit "$n_fail"
