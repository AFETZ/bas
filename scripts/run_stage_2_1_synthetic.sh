#!/usr/bin/env bash
# Этап 2.1 — synthetic end-to-end pipeline без SITL/Gazebo.
#
# Архитектурно проверяет полную Sionna RT цепочку:
#   1. Synthetic flight events (UAV trajectory через iris_runway scene)
#   2. sionna_channel_publisher.py читает events → lookup в radio map →
#      пишет /tmp/sionna_channel.json
#   3. ns-3 (с --sionnaChannelPath) каждые 100 мс читает JSON → обновляет
#      RateErrorModel + channel delay → эмитит ns3:sionna_poll channel_updated
#
# Это **минимальный defensible end-to-end demo** Sionna chain. Mission
# через SITL+Gazebo показал WSL2 race issue; для acceptance показываем что
# Sionna PIPELINE работает на realистичной trajectory.
#
# Использование:
#   bash scripts/run_stage_2_1_synthetic.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_ID="stage_2_1_synthetic_$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="${REPO_ROOT}/logs/${RUN_ID}"
SIONNA_VENV="${REPO_ROOT}/sionna_env"
SIONNA_JSON="/tmp/sionna_channel.json"
RADIO_MAP="${REPO_ROOT}/radio_maps/iris_runway.npz"
DURATION="${DURATION:-50}"

mkdir -p "${LOG_DIR}"
echo "==> RUN_ID=${RUN_ID}"
echo "==> LOG_DIR=${LOG_DIR}"

# 0. Cleanup prior state
echo 1337 | sudo -S -p '' pkill -f sionna_channel_publisher.py 2>/dev/null || true
echo 1337 | sudo -S -p '' sg docker -c 'docker rm -f bas-ns3-synth' >/dev/null 2>&1 || true
echo 1337 | sudo -S -p '' bash "${REPO_ROOT}/scripts/setup_radio_net.sh" down >/dev/null 2>&1 || true
sleep 1

# 1. Поднимаем bridges/TAPs для ns-3.
echo "[1/4] setup_radio_net"
echo 1337 | sudo -S -p '' bash "${REPO_ROOT}/scripts/setup_radio_net.sh" up | tail -3

# 2. Initial /tmp/sionna_channel.json без sudo. /tmp writeable для всех;
# если sudo использовать, файл становится root-owned и publisher не может
# его атомарно переименовать (sticky-bit /tmp + rename ownership-check).
# Удаляем потенциальный stale root-файл сначала.
sudo rm -f "${SIONNA_JSON}" 2>/dev/null || true
echo '{"loss_ratio":0.0,"extra_delay_ms":0.0,"wall_time":0}' > "${SIONNA_JSON}"

# 3. Запускаем ns-3 в фоне (без SITL).
echo "[2/4] launching ns-3 (with --sionnaChannelPath)"
echo 1337 | sudo -S -p '' sg docker -c "docker run -d --name bas-ns3-synth --network host --cap-add NET_ADMIN --privileged \
    -v ${REPO_ROOT}/ns3:/work/ns3:ro \
    -v ${REPO_ROOT}/logs:/work/logs \
    -v /tmp:/host_tmp \
    --entrypoint bash bas/ns3:dev -c '\
        ln -sf /host_tmp/sionna_channel.json /tmp/sionna_channel.json \
        && cp /work/ns3/scenarios/two_channel.cc /work/ns3-src/scratch/ \
        && cd /work/ns3-src \
        && ./ns3 build > /tmp/build.log 2>&1 \
        && /work/ns3-src/build/scratch/ns3.40-two_channel-optimized \
             --runId=${RUN_ID} \
             --duration=$((DURATION + 10)) \
             --ctrlDelayMs=5 --ctrlLoss=0.0 \
             --ploadDelayMs=10 --ploadLoss=0.0 \
             --sionnaChannelPath=/tmp/sionna_channel.json'" >/dev/null

# Ждём пока ns-3 stable.
for _ in $(seq 1 60); do
    [ -s "${LOG_DIR}/ns3_events.jsonl" ] && break
    sleep 1
done
[ -s "${LOG_DIR}/ns3_events.jsonl" ] || {
    echo "ns-3 не стартовал" >&2
    echo 1337 | sudo -S -p '' sg docker -c 'docker logs --tail 30 bas-ns3-synth' >&2
    exit 1
}
echo "  ns-3 ready"

# 4. Запускаем publisher + synthetic flight generator параллельно.
echo "[3/4] launching synthetic flight + publisher (duration=${DURATION}s)"
"${SIONNA_VENV}/bin/python" "${REPO_ROOT}/scripts/sionna_synthetic_drive.py" \
    --out-dir "${LOG_DIR}" \
    --duration "${DURATION}" \
    --realtime &
SYNTH_PID=$!

# Publisher следует за events.jsonl
"${SIONNA_VENV}/bin/python" "${REPO_ROOT}/scripts/sionna_channel_publisher.py" \
    --events "${LOG_DIR}/events.jsonl" \
    --radio-map "${RADIO_MAP}" \
    --out "${SIONNA_JSON}" \
    --interval-ms 100 \
    --max-seconds "$((DURATION + 5))" &
PUB_PID=$!

wait "${SYNTH_PID}"
echo "  synthetic flight finished"
sleep 3   # дать publisher и ns-3 догрести
kill -TERM "${PUB_PID}" 2>/dev/null || true
wait "${PUB_PID}" 2>/dev/null || true

# 5. Финальный анализ.
echo "[4/4] results"
echo 1337 | sudo -S -p '' chmod -R a+r "${LOG_DIR}"
SIONNA_COUNT=$(grep -c sionna_poll "${LOG_DIR}/ns3_events.jsonl" || echo 0)
echo "  sionna_poll channel_updated events: ${SIONNA_COUNT}"
echo "  first 3 events:"
grep sionna_poll "${LOG_DIR}/ns3_events.jsonl" | head -3 | sed 's/^/    /'
echo "  unique loss_ratio values:"
grep -oE '"loss_ratio":[0-9.]+' "${LOG_DIR}/ns3_events.jsonl" \
    | sort -u | head -10 | sed 's/^/    /'
echo "  unique channel_delay_ms values:"
grep -oE '"channel_delay_ms":[0-9.]+' "${LOG_DIR}/ns3_events.jsonl" \
    | sort -u | head -10 | sed 's/^/    /'

# Cleanup
echo 1337 | sudo -S -p '' sg docker -c 'docker stop -t 3 bas-ns3-synth' >/dev/null 2>&1 || true
echo 1337 | sudo -S -p '' sg docker -c 'docker rm -f bas-ns3-synth' >/dev/null 2>&1 || true
echo 1337 | sudo -S -p '' bash "${REPO_ROOT}/scripts/setup_radio_net.sh" down >/dev/null 2>&1 || true

echo
echo "==> RUN_ID=${RUN_ID}"
echo "==> events.jsonl: ${LOG_DIR}/events.jsonl"
echo "==> ns3_events.jsonl: ${LOG_DIR}/ns3_events.jsonl"
echo "==> channel_updated events: ${SIONNA_COUNT}"
