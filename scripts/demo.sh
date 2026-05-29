#!/usr/bin/env bash
# BAS demo launcher — единая точка входа для всех демонстраций.
#
# Вместо запоминания 28 обёрток run_stage_*.sh: выбираете ЧТО хотите
# увидеть, скрипт зовёт нужный стек. Capability-меню, не roadmap-нумерация.
#
# Использование:
#   bash scripts/demo.sh           # интерактивное меню
#   bash scripts/demo.sh 6         # сразу пункт 6 (весь стенд)
#   bash scripts/demo.sh --list    # список без запуска
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$REPO_ROOT"

# Пункты меню: "Название|Что увидите|команда"
ITEMS=(
  "Весь стенд (master demo)|Все модули + Admin Dashboard в браузере (рекомендую для первого знакомства)|bash scripts/run_master_demo.sh"
  "Автоматический flight-фильм|10 шагов полёта TAKEOFF→ангар LOS/NLOS→LAND, видео+скриншоты+Markdown отчёт|sudo bash scripts/run_stage_2_4_auto_demo.sh"
  "Ручной полёт + RF-панель|WASD-управление, FPV-окно борта, RSSI/loss/delay при заходе за здание|sudo bash scripts/run_stage_2_4_fpv_rf_demo.sh"
  "Цифровой двойник ИССГР|OGC REST API, бортовая БД, /digital_twin для имитаторов АСУ|bash scripts/run_stage_3_issgr_demo.sh"
  "Падение связи (Sionna RT + ns-3)|Live ray-tracing, деградация обоих каналов в реальном времени|sudo bash scripts/run_stage_2_4_rt_online_demo.sh"
  "Реальный город из OSM|Импорт зданий+рельефа любой точки Земли в ИССГР+Gazebo|__OSM__"
  "Sionna RT в реальном городе|Ray-trace радиополя по Мюнхену/городу (нужен GPU)|bash scripts/run_sionna_live.sh real_tile --scene-name munich --freq-mhz 2400"
  "Multi-UAV (рой)|2 SITL + 2 iris в Gazebo, единый mavp2p router|sudo bash scripts/run_stage_2_4_multi_uav_demo.sh"
  "QGroundControl + Web GCS|Оба GCS одновременно через MAVLink router|sudo bash scripts/run_stage_2_4_qgc_demo.sh"
  "Реальный автопилот + наша физика|ArduPilot SITL ↔ JsonFdmBridge, ARM+takeoff (smoke)|.venv/bin/python scripts/_real_sitl_e2e_smoke.py"
  "Кибер-защита канала|3 атаки (GPS spoof/cmd inject/RF jam) + детектор алертов|.venv/bin/python scripts/_cyber_smoke.py"
  "WiFi vs LoRa сравнение|Side-by-side отчёт двух сетевых профилей|sudo bash scripts/run_stage_1_6_compare.sh"
)

print_menu() {
  echo
  echo "=========================================================================="
  echo "  BAS стенд — что хотите увидеть?"
  echo "=========================================================================="
  local i=1
  for item in "${ITEMS[@]}"; do
    local name="${item%%|*}"
    local rest="${item#*|}"
    local what="${rest%%|*}"
    printf "  %2d) %-32s %s\n" "$i" "$name" "$what"
    i=$((i + 1))
  done
  echo "   q) выход"
  echo "--------------------------------------------------------------------------"
  echo "  Подробности: docs/SCENARIOS.md (связки) · docs/MODULE_MAP.md (модули)"
  echo "=========================================================================="
}

run_osm() {
  echo
  read -r -p "  Место (например 'Тверская, Москва') или Enter для bbox-демо: " place
  if [ -z "$place" ]; then
    echo "  → демо-bbox (центр Москвы, ~250м)"
    .venv/bin/python scripts/import_osm_scenario.py \
      --bbox 55.7585,37.6150,55.7608,37.6190 --name demo_moscow \
      --with-terrain --out-dir generated/demo_moscow
  else
    .venv/bin/python scripts/import_osm_scenario.py \
      --place "$place" --radius-m 300 --with-terrain \
      --name "$(echo "$place" | tr ' ,' '__')" --out-dir generated/osm_demo
  fi
  echo
  echo "  Сгенерировано в generated/. Чтобы увидеть в ИССГР, добавь --issgr-url"
  echo "  к работающему серверу (run_stage_3_issgr_demo.sh)."
}

dispatch() {
  local n="$1"
  if [ "$n" -lt 1 ] || [ "$n" -gt "${#ITEMS[@]}" ]; then
    echo "Нет пункта $n (1..${#ITEMS[@]})"; return 1
  fi
  local item="${ITEMS[$((n - 1))]}"
  local name="${item%%|*}"
  local cmd="${item##*|}"
  echo
  echo "==> [$n] $name"
  echo "==> $ ${cmd}"
  echo
  if [ "$cmd" = "__OSM__" ]; then
    run_osm
  else
    eval "$cmd"
  fi
}

# --- CLI modes ---
if [ "${1:-}" = "--list" ] || [ "${1:-}" = "-l" ]; then
  print_menu
  exit 0
fi

if [ "${1:-}" != "" ]; then
  dispatch "$1"
  exit $?
fi

# Interactive.
print_menu
read -r -p "  Выбор [1-${#ITEMS[@]} / q]: " choice
case "$choice" in
  q|Q|"") echo "выход"; exit 0 ;;
  *) dispatch "$choice" ;;
esac
