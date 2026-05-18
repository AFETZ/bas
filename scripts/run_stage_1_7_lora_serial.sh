#!/usr/bin/env bash
# Этап 1.7.g: LoRa Serial Bridge — буквальная реализация ТЗ «LoRa через
# Serial Port». Mission AUTO идёт через MAVLink-байтстрим по виртуальной
# PTY-паре, которую разводит ns-3 lorawan PHY+MAC (signetlabdei) с
# реальной LoRa физикой (SF7, BW125 kHz, distance=1000 m).
#
# Архитектура (поверх 1.7.c-fix dual-socat паттерна):
#
#   HOST orchestrator (pymavlink "serial:/tmp/ptyGCS_lora:57600")
#       ↕ host socat: /tmp/ptyGCS_lora ↔ /tmp/bas-bridge/lora-gcs.sock
#       ↕ container-side socat (внутри bas-ns3-stage17): UNIX-CONNECT ↔ PTY
#       ↕ ns-3 GCS PtyApp (читает GCS PTY, шлёт в LoRa)
#       ↕ ns-3 LoRa channel (signetlabdei, ITU-R RP.452, SF7/BW125)
#       ↕ ns-3 UAV PtyApp (получает из LoRa, пишет в UAV PTY)
#       ↕ container-side socat: PTY ↔ UNIX-CONNECT
#       ↕ host UNIX socket /tmp/bas-bridge/lora-uav.sock
#       ↕ bas-lora-uav-bridge (alpine/socat в bas-uav netns):
#         UNIX-CONNECT ↔ TCP:127.0.0.1:5760
#       ↕ SITL primary serial
#       ↕ Gazebo iris_with_gimbal (FDM UDP 9002/9003 в том же netns)
#
# Использование:
#   sudo bash scripts/run_stage_1_7_lora_serial.sh
#
# Дополнительные настройки:
#   BAS_LORA_SF=7|8|9|10|11|12          spreading factor (default 7)
#   BAS_LORA_BW=125000|250000|500000    bandwidth Hz (default 125000)
#   BAS_LORA_DISTANCE_M=1000            UAV-GCS distance (default 1000)
#   NS3_DURATION=600                    длительность ns-3 sim (default 600)
#   NS3_START_TIMEOUT_SECONDS=300       timeout на компиляцию ns-3
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${1:-lora_serial}"
RUN_ID="${BAS_RUN_ID:-stage_1_7_lora_serial_${PROFILE}_$(date -u +%Y%m%dT%H%M%SZ)}"
LOG_DIR="${REPO_ROOT}/logs/${RUN_ID}"
COMPOSE_FILE="${REPO_ROOT}/docker-compose.shared-netns.yml"
DEFAULT_COMPOSE_FILE="${REPO_ROOT}/docker-compose.yml"
NS3_BIN="/work/ns3-src/build/scratch/ns3.40-lora_serial-optimized"

# LoRa параметры (берём дефолты из configs/network_profiles/lora_serial.yaml).
SF="${BAS_LORA_SF:-7}"
BW_HZ="${BAS_LORA_BW:-125000}"
DISTANCE_M="${BAS_LORA_DISTANCE_M:-1000}"
NS3_DURATION="${NS3_DURATION:-600}"
NS3_START_TIMEOUT_SECONDS="${NS3_START_TIMEOUT_SECONDS:-300}"

# Сценарий orchestrator'а. Для LoRa берём baseline_wifi (без artificial
# деградации на control канал) — LoRa сама даёт реалистичную physical
# деградацию. Если нужно сравнение с WiFi: тот же scenario пусть, но
# transport = LoRa serial.
SCENARIO="baseline_wifi"

ensure_root() { [ "$EUID" -eq 0 ] || { echo "sudo only" >&2; exit 1; }; }

ensure_docker() {
    if docker info >/dev/null 2>&1; then return 0; fi
    echo "[preflight] Docker daemon не отвечает; пробую service docker start"
    service docker start >/dev/null 2>&1 || true
    for _ in $(seq 1 30); do
        if docker info >/dev/null 2>&1; then
            echo "  Docker daemon готов"
            return 0
        fi
        sleep 1
    done
    echo "Docker daemon не поднялся. Запусти: sudo service docker start" >&2
    return 1
}

cleanup() {
    set +e
    echo "[cleanup]"
    timeout 30 sg docker -c "docker rm -f bas-ns3-stage17 2>/dev/null" >/dev/null 2>&1
    timeout 60 sg docker -c "docker compose -f ${COMPOSE_FILE} --profile lora down -v 2>/dev/null" >/dev/null 2>&1
    bash "${REPO_ROOT}/scripts/setup_lora_bridge.sh" down >/dev/null 2>&1 || true
    rm -f /var/run/netns/bas-uav
    set -e
}
trap cleanup EXIT INT TERM

ensure_root
ensure_docker
mkdir -p "$LOG_DIR"
echo "==> run_id=${RUN_ID}, profile=${PROFILE}, scenario=${SCENARIO}"
echo "==> логи: $LOG_DIR"
echo "==> LoRa params: SF=${SF}, BW=${BW_HZ} Hz, distance=${DISTANCE_M} m, duration=${NS3_DURATION}s"

echo "[1/8] host LoRa PTY bridge (setup_lora_bridge.sh up)"
bash "${REPO_ROOT}/scripts/setup_lora_bridge.sh" down >/dev/null 2>&1 || true
bash "${REPO_ROOT}/scripts/setup_lora_bridge.sh" up | tail -8

# Sanity: только GCS host PTY (для orchestrator pyserial).
# UAV host PTY больше не создаётся — UAV-side UNIX socket поднимает ns-3
# контейнер сам (UNIX-LISTEN), к нему подключается lora-uav-bridge.
if [ ! -e /tmp/ptyGCS_lora ]; then
    echo "host PTY /tmp/ptyGCS_lora не создан после setup_lora_bridge.sh" >&2
    exit 2
fi

echo "[2/8] тушим default compose"
sg docker -c "docker compose -f ${DEFAULT_COMPOSE_FILE} down -v 2>/dev/null" >/dev/null 2>&1 || true

echo "[3/8] pause-контейнер uav-net"
sg docker -c "docker compose -f ${COMPOSE_FILE} up -d uav-net" 2>&1 | tail -3

# bas-uav netns symlink (нужен для ss проверок ниже).
UAV_PID=$(sg docker -c "docker inspect --format '{{.State.Pid}}' bas-uav-net")
mkdir -p /var/run/netns
ln -sf "/proc/${UAV_PID}/ns/net" /var/run/netns/bas-uav

echo "[4/8] gazebo + sitl (БЕЗ mavbridge — его место занимает lora-uav-bridge для LoRa-режима)"
# Gazebo стартуем первым и даём 6с на инициализацию ArduPilotPlugin (FDM 9002/9003).
sg docker -c "docker compose -f ${COMPOSE_FILE} up -d gazebo" 2>&1 | tail -3
echo "  ждём 6с пока Gazebo откроет FDM"
sleep 6
sg docker -c "docker compose -f ${COMPOSE_FILE} up -d sitl" 2>&1 | tail -3

echo "[5/8] ждём SITL MAVLink на :5760"
for i in $(seq 1 60); do
    if ip netns exec bas-uav ss -tln 2>/dev/null | grep -q ":5760"; then break; fi
    sleep 1
done
ip netns exec bas-uav ss -tln 2>/dev/null | grep -q ":5760" || {
    echo "SITL 5760 не открылся" >&2
    sg docker -c "docker logs --tail 20 bas-sitl 2>&1"
    exit 2
}
# SITL settles (EKF, GPS, sensors) ~10с — иначе ранний MAVLink connect ловит "Closed connection".
sleep 8

echo "[6/8] поднимаем lora-uav-bridge (UNIX socket ↔ SITL TCP 5760)"
sg docker -c "docker compose -f ${COMPOSE_FILE} --profile lora up -d lora-uav-bridge" 2>&1 | tail -3
sleep 2

echo "[7/8] запуск ns-3 lora_serial контейнер (compile + RealtimeSimulator)"
sg docker -c "docker rm -f bas-ns3-stage17 2>/dev/null" >/dev/null 2>&1 || true

# Контейнер делает (внутри):
#   1. container-side socat:
#        - GCS PTY: UNIX-CONNECT к host UNIX socket (host socat — listen'ит)
#        - UAV PTY: UNIX-LISTEN на UNIX socket (lora-uav-bridge — connect'ится)
#      Асимметрия из-за того что host orchestrator открывает host PTY GCS
#      (pyserial требует tty-device), а на UAV-стороне host PTY не нужен —
#      lora-uav-bridge сразу проксирует UNIX socket → SITL TCP 5760.
#   2. cp lora_serial.cc → scratch/ + ./ns3 build
#   3. ns3.40-lora_serial-optimized --runId --duration --sf --bandwidth
#      --distance --ptyUavPath --ptyGcsPath
# /tmp/bas-bridge → /bridge: shared volume для UNIX sockets.
# REPO_ROOT/logs → /work/logs: для ns3_events.jsonl в правильную папку.
sg docker -c "docker run -d --name bas-ns3-stage17 --network host --cap-add NET_ADMIN --privileged \
    -v /tmp/bas-bridge:/bridge \
    -v ${REPO_ROOT}/ns3:/work/ns3:ro \
    -v ${REPO_ROOT}/logs:/work/logs \
    --entrypoint bash bas/ns3:dev -c '\
        echo \"[ns3-container] container-side socat: GCS UNIX-CONNECT, UAV UNIX-LISTEN\"; \
        socat -d -d PTY,link=/tmp/ptyGCS_lora,raw,echo=0,b57600 \
                    UNIX-CONNECT:/bridge/lora-gcs.sock > /tmp/socat-gcs.log 2>&1 & \
        socat -d -d PTY,link=/tmp/ptyUAV_lora,raw,echo=0,b57600 \
                    UNIX-LISTEN:/bridge/lora-uav.sock,fork,mode=666 > /tmp/socat-uav.log 2>&1 & \
        sleep 2; \
        ls -la /tmp/ptyGCS_lora /tmp/ptyUAV_lora /bridge/lora-uav.sock 2>&1; \
        echo \"[ns3-container] compiling lora_serial.cc\"; \
        cp /work/ns3/scenarios/lora_serial.cc /work/ns3-src/scratch/; \
        cd /work/ns3-src && ./ns3 build > /tmp/build.log 2>&1; \
        echo \"[ns3-container] launching lora_serial (SF=${SF}, BW=${BW_HZ}, dist=${DISTANCE_M}m, dur=${NS3_DURATION}s)\"; \
        ${NS3_BIN} \
            --runId=${RUN_ID} --duration=${NS3_DURATION} \
            --sf=${SF} --bandwidth=${BW_HZ} --distance=${DISTANCE_M} \
            --ptyUavPath=/tmp/ptyUAV_lora --ptyGcsPath=/tmp/ptyGCS_lora'" > /dev/null

NS3_LOG="${LOG_DIR}/ns3_events.jsonl"
for i in $(seq 1 $((NS3_START_TIMEOUT_SECONDS / 2))); do
    [ -s "$NS3_LOG" ] && break
    if ! sg docker -c "docker inspect -f '{{.State.Running}}' bas-ns3-stage17 2>/dev/null" | grep -q true; then
        echo "ns-3 контейнер завершился до старта" >&2
        sg docker -c "docker exec bas-ns3-stage17 cat /tmp/build.log 2>&1" >&2 || true
        sg docker -c "docker logs --tail 80 bas-ns3-stage17 2>&1" >&2 || true
        exit 3
    fi
    sleep 2
done
[ -s "$NS3_LOG" ] || {
    echo "ns-3 не стартовал за ${NS3_START_TIMEOUT_SECONDS}s" >&2
    sg docker -c "docker exec bas-ns3-stage17 tail -80 /tmp/build.log 2>&1" >&2 || true
    sg docker -c "docker logs --tail 80 bas-ns3-stage17 2>&1" >&2 || true
    exit 3
}
echo "  ns-3 lora_serial готов"
# Ещё пара секунд чтобы PtyApp запустилcя (sim_time >= 1.5s) и host socat
# увидел client connect для UAV bridge.
sleep 3

# Sanity-check: lora-uav-bridge container всё ещё работает (не упал из-за
# отсутствия client'а на UNIX-CONNECT'е).
if ! sg docker -c "docker inspect -f '{{.State.Running}}' bas-lora-uav-bridge 2>/dev/null" | grep -q true; then
    echo "[warn] lora-uav-bridge не работает — перезапускаю"
    sg docker -c "docker compose -f ${COMPOSE_FILE} --profile lora up -d lora-uav-bridge" 2>&1 | tail -3
    sleep 2
fi

echo "[8/8] HEARTBEAT через LoRa Serial — pymavlink ловит N штук через PTY"
echo "  endpoint=serial:/tmp/ptyGCS_lora:57600, целевое количество=${BAS_LORA_HB_TARGET:-5}"
echo "  (mission AUTO через WiFi/UDP остаётся отдельным прогоном 1.5.2/2.1 — для"
echo "   bi-directional mission upload через LoRa нужен ED Class C, отдельный этап)"

# Запускаем pymavlink listener в венде на host: открывает /tmp/ptyGCS_lora как
# UART через pyserial, ждёт HEARTBEAT messages из SITL → через LoRa channel
# → ns-3 GCS PtyApp → host PTY. Это buchstabe ТЗ: MAVLink-byte stream идёт
# через LoRa Serial Port.
HB_TARGET="${BAS_LORA_HB_TARGET:-5}"
HB_TIMEOUT_S="${BAS_LORA_HB_TIMEOUT_S:-180}"

set +e
"${REPO_ROOT}/.venv/bin/python" - "${LOG_DIR}/lora_heartbeat_log.jsonl" \
    "${HB_TARGET}" "${HB_TIMEOUT_S}" <<'PY' 2>&1 | tee "${LOG_DIR}/orchestrator_stdout.log"
import json
import os
import sys
import time
from pymavlink import mavutil

log_path, hb_target, hb_timeout_s = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
print(f"[lora-hb] open serial:/tmp/ptyGCS_lora baud=57600, target={hb_target} HBs, timeout={hb_timeout_s}s")
mav = mavutil.mavlink_connection("/tmp/ptyGCS_lora", baud=57600, source_system=254)

# Шлём наш GCS HEARTBEAT — UAV (SITL) узнает GCS как peer.
try:
    mav.mav.heartbeat_send(
        mavutil.mavlink.MAV_TYPE_GCS,
        mavutil.mavlink.MAV_AUTOPILOT_INVALID, 0, 0, 0)
except Exception as exc:
    print(f"[lora-hb] heartbeat_send error (ok если канал ещё warming up): {exc}")

t0 = time.time()
hb_count = 0
last_hb_wall = None
mode_seen = set()
with open(log_path, "w") as f:
    while time.time() - t0 < hb_timeout_s and hb_count < hb_target:
        msg = mav.recv_match(type="HEARTBEAT", blocking=True, timeout=10)
        if msg is None:
            print(f"[lora-hb] no HEARTBEAT in 10s (elapsed={time.time()-t0:.0f}s)")
            continue
        hb_count += 1
        last_hb_wall = time.time()
        flight_mode = mavutil.mode_string_v10(msg) if hasattr(mavutil, "mode_string_v10") else ""
        if flight_mode:
            mode_seen.add(flight_mode)
        f.write(json.dumps({
            "event_type": "lora_heartbeat",
            "hb_count": hb_count,
            "wall_time": last_hb_wall,
            "elapsed_s": last_hb_wall - t0,
            "sys_id": msg.get_srcSystem(),
            "comp_id": msg.get_srcComponent(),
            "type": int(msg.type),
            "autopilot": int(msg.autopilot),
            "base_mode": int(msg.base_mode),
            "custom_mode": int(msg.custom_mode),
            "flight_mode": flight_mode,
            "armed": bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED),
        }) + "\n")
        f.flush()
        print(f"[lora-hb] #{hb_count}: sys={msg.get_srcSystem()} mode={flight_mode} armed={bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)} elapsed={last_hb_wall-t0:.1f}s")

elapsed = time.time() - t0
if hb_count >= hb_target:
    print(f"[lora-hb] ✓ SUCCESS: получено {hb_count}/{hb_target} HEARTBEAT через LoRa Serial за {elapsed:.1f}s")
    print(f"[lora-hb] flight_modes observed: {sorted(mode_seen)}")
    sys.exit(0)
else:
    print(f"[lora-hb] ✗ FAIL: получено только {hb_count}/{hb_target} HEARTBEAT за {hb_timeout_s}s timeout")
    sys.exit(2)
PY
RC=${PIPESTATUS[0]}
set -e

# Дать ns-3 пару секунд догрести events.jsonl до закрытия.
sleep 3

# Сохранить логи всех контейнеров для post-mortem.
sg docker -c "docker logs bas-sitl 2>&1"             > "${LOG_DIR}/sitl.log" 2>&1 || true
sg docker -c "docker logs bas-gazebo 2>&1"           > "${LOG_DIR}/gazebo.log" 2>&1 || true
sg docker -c "docker logs bas-lora-uav-bridge 2>&1"  > "${LOG_DIR}/lora_uav_bridge.log" 2>&1 || true
sg docker -c "docker logs bas-ns3-stage17 2>&1"      > "${LOG_DIR}/ns3_stdout.log" 2>&1 || true

# Дополнительно: дамп host socat логов — диагностика дедлоков.
cp /tmp/bas-bridge/socat-gcs.log "${LOG_DIR}/socat_host_gcs.log" 2>/dev/null || true

# LoRa sanity: phy_send / phy_received counts + HB count.
PHY_SEND=$(grep -c '"phase":"phy_send"'     "${NS3_LOG}" 2>/dev/null || echo 0)
PHY_RECV=$(grep -c '"phase":"phy_received"' "${NS3_LOG}" 2>/dev/null || echo 0)
PTY_READ=$(grep -c '"phase":"pty_read"'     "${NS3_LOG}" 2>/dev/null || echo 0)
HB_RECV=$(wc -l < "${LOG_DIR}/lora_heartbeat_log.jsonl" 2>/dev/null || echo 0)

# Сборка короткого markdown-отчёта для grant-таблицы.
cat > "${LOG_DIR}/report.md" <<EOF
# Stage 1.7.g LoRa Serial Bridge — отчёт

- **run_id:** \`${RUN_ID}\`
- **профиль:** \`${PROFILE}\`
- **scenario:** \`${SCENARIO}\` (используется только для config_hash)
- **LoRa параметры:** SF=${SF}, BW=${BW_HZ} Hz, distance=${DISTANCE_M} m, duration=${NS3_DURATION}s

## ТЗ-требование

«Канал связи 2 — LoRa через Serial Port» (буквально из ТЗ).
Реализация: virtual PTY + ns-3 lorawan PHY+MAC (signetlabdei) + dual-socat
bridge между host orchestrator и docker container. SITL пишет MAVLink
байты в SERIAL0 (TCP 5760), они проксируются через socat → UNIX socket
→ ns-3 PtyApp → LoRa channel (ITU-R RP.452 path loss) → GCS PtyApp →
PTY → host pymavlink. Никакого IP-stack в радио-цепочке нет.

## Результат прогона

- HEARTBEAT через LoRa Serial: получено **${HB_RECV}** штук (orchestrator pyserial)
- LoRa PHY: phy_send=${PHY_SEND}, phy_received=${PHY_RECV}, pty_read=${PTY_READ}
- exit code: ${RC} ($([ ${RC} -eq 0 ] && echo "✓ SUCCESS" || echo "✗ FAIL"))

## Известное ограничение

Текущий PtyApp реализует ED Class A (uplink + RX-window downlink). Для
buchstabe-полного **mission upload** через LoRa требуется ED Class C
(always-on RX), отдельный этап после 1.7.g. Mission AUTO с landed=True
остаётся демонстрироваться через WiFi/UDP control канал в stage 1.5.2 /
2.1, где он уже закрыт.
EOF

echo
echo "Stage 1.7.g sanity:"
echo "  HEARTBEAT через LoRa: ${HB_RECV}"
echo "  ns-3 phy_send=${PHY_SEND}, phy_received=${PHY_RECV}, pty_read=${PTY_READ}"
echo "  report: ${LOG_DIR}/report.md"

echo
echo "Прогон завершён (RC=${RC}). Логи: ${LOG_DIR}"
exit ${RC}
