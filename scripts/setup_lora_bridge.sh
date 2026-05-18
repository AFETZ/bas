#!/usr/bin/env bash
# Этап 1.7.c-fix: PTY <-> UNIX socket bridge между host и ns-3 контейнером.
#
# Архитектура (community-canonical, см. ArduPilot Discourse + mavlink-router):
#
#   HOST:
#     /tmp/ptyUAV_lora (host-side slave)
#        | socat
#     /tmp/bas-bridge/lora-uav.sock (UNIX socket)
#                              |
#                       (bind-mount /tmp/bas-bridge:/bridge)
#                              |
#   CONTAINER:
#     /bridge/lora-uav.sock
#        | socat
#     /work/pty/ptyUAV_lora (container-side slave, открывается ns-3 PtyApp)
#
# Это решает docker pts namespace проблему (см. docker/for-linux#77).
#
# Использование:
#   bash scripts/setup_lora_bridge.sh up    # запустить host-side socats
#   bash scripts/setup_lora_bridge.sh down  # остановить
#
# В docker compose / docker run контейнер должен иметь:
#   -v /tmp/bas-bridge:/bridge
# И запустить ВНУТРИ перед ns-3:
#   socat PTY,link=/work/pty/ptyUAV_lora,raw,echo=0,b57600 \
#         UNIX-CONNECT:/bridge/lora-uav.sock &
set -euo pipefail

ACTION="${1:-up}"
BRIDGE_DIR="/tmp/bas-bridge"
HOST_PTY_UAV="/tmp/ptyUAV_lora"
HOST_PTY_GCS="/tmp/ptyGCS_lora"
SOCK_UAV="${BRIDGE_DIR}/lora-uav.sock"
SOCK_GCS="${BRIDGE_DIR}/lora-gcs.sock"
PID_UAV="${BRIDGE_DIR}/socat-uav.pid"
PID_GCS="${BRIDGE_DIR}/socat-gcs.pid"

ensure_socat() {
    if ! command -v socat >/dev/null 2>&1; then
        echo "socat не установлен. ставлю..."
        echo 1337 | sudo -S -p '' apt-get update >/dev/null
        echo 1337 | sudo -S -p '' apt-get install -y socat >/dev/null
    fi
}

up() {
    ensure_socat
    mkdir -p "$BRIDGE_DIR"
    chmod 1777 "$BRIDGE_DIR"

    # Удалить старые stale-сокеты/PTY.
    rm -f "$SOCK_UAV" "$SOCK_GCS" "$HOST_PTY_UAV" "$HOST_PTY_GCS"

    # GCS side: host PTY ↔ UNIX-LISTEN. orchestrator/pymavlink открывает
    # host PTY как UART (pyserial требует tty-device, не UNIX socket).
    # ns-3 контейнер connect'ит UNIX socket через container-side socat.
    echo "==> host socat GCS: $HOST_PTY_GCS <-> $SOCK_GCS"
    socat -d -d \
        PTY,link=${HOST_PTY_GCS},raw,echo=0,b57600,mode=666 \
        UNIX-LISTEN:${SOCK_GCS},reuseaddr,fork,mode=666 \
        > "${BRIDGE_DIR}/socat-gcs.log" 2>&1 &
    echo $! > "$PID_GCS"

    # UAV side: host PTY НЕ нужен. На UAV стороне нет host-процесса который
    # читал бы UART (SITL уже сидит в контейнере и слушает TCP 5760).
    # UAV-UNIX-LISTEN socket поднимает ns-3 контейнер (см. ниже), а
    # lora-uav-bridge подключается к нему как UNIX-CONNECT и проксирует
    # bytes на SITL TCP 5760. Если запускают 1.7.c-fix smoke без mavlink
    # стека — старый паттерн host PTY ↔ UNIX-LISTEN можно поднять руками,
    # но для wrapper'а 1.7.g он не нужен.

    # socat needs ~200 ms to create the PTY device.
    sleep 1
    echo "==> host PTY (только GCS):"
    ls -la "$HOST_PTY_GCS" 2>&1 | sed 's/^/    /'
    echo "==> UNIX socket GCS (для ns-3 container):"
    ls -la "$SOCK_GCS" 2>&1 | sed 's/^/    /'
    echo
    echo "Готово. В docker run для ns-3 контейнера добавь:"
    echo "    -v /tmp/bas-bridge:/bridge"
    echo "Внутри ns-3 контейнера ДО запуска scenario поднимаются два socat:"
    echo "    # GCS: connect к host UNIX socket → PTY для PtyApp"
    echo "    socat PTY,link=/tmp/ptyGCS_lora,raw,echo=0,b57600 \\"
    echo "          UNIX-CONNECT:/bridge/lora-gcs.sock &"
    echo "    # UAV: создаёт UNIX socket НА КОТОРЫЙ подключится lora-uav-bridge"
    echo "    socat PTY,link=/tmp/ptyUAV_lora,raw,echo=0,b57600 \\"
    echo "          UNIX-LISTEN:/bridge/lora-uav.sock,fork &"
}

down() {
    for pidf in "$PID_UAV" "$PID_GCS"; do
        if [ -f "$pidf" ]; then
            pid=$(cat "$pidf")
            kill "$pid" 2>/dev/null || true
            rm -f "$pidf"
        fi
    done
    # Дополнительно прибиваем любые orphan'ы.
    pkill -f "socat.*lora-uav.sock" 2>/dev/null || true
    pkill -f "socat.*lora-gcs.sock" 2>/dev/null || true
    rm -f "$SOCK_UAV" "$SOCK_GCS" "$HOST_PTY_UAV" "$HOST_PTY_GCS"
    # Заодно прибиваем socket файлы которые мог создать ns-3 контейнер
    # (UNIX-LISTEN на UAV side) если он умер некрасиво.
    rm -f "${BRIDGE_DIR}"/socat-*.log
    echo "host LoRa bridge остановлен"
}

case "$ACTION" in
    up) up ;;
    down) down ;;
    *) echo "Использование: $0 up|down" >&2; exit 1 ;;
esac
