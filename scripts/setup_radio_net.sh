#!/usr/bin/env bash
# Host setup для радио-петли через ns-3 TapBridge UseLocal.
#
# Топология (на каждый канал control/payload своя):
#
#   netns-<chan>-near                                       netns-<chan>-far
#   ┌───────────────────┐                                   ┌───────────────────┐
#   │ veth-<chan>-near  │                                   │ veth-<chan>-far   │
#   │  10.<X>.0.1/24    │                                   │  10.<X>.0.2/24    │
#   └────────┬──────────┘                                   └────────┬──────────┘
#            │ veth-pair                                             │ veth-pair
#   ┌────────▼──────────┐   ┌─────────────────┐   ┌──────────────┐   │
#   │  veth-..near-br   │   │ tap-<chan>-near │   │ ns-3 node 0  │   │
#   │  (no IP, in br)   │───┤ (no IP, in br)  │===│  TapBridge   │   │
#   └────────┬──────────┘   └─────────────────┘   │  UseLocal    │   │
#            │ bridged              │              └──────┬───────┘   │
#   ╔════════▼══════════════════════▼═══╗   simulated     │           │
#   ║   br-<chan>-near (Linux bridge)   ║   link in ns-3  │           │
#   ╚═══════════════════════════════════╝   (delay/loss)  │           │
#                                                          ▼           │
#   ╔═══════════════════════════════════╗   ┌──────────────────┐       │
#   ║   br-<chan>-far  (Linux bridge)   ║   │ ns-3 node 1      │       │
#   ╚════════┬══════════════════════┬═══╝   │  TapBridge       │       │
#            │ bridged              │       │  UseLocal        │       │
#   ┌────────▼──────────┐   ┌──────▼──────┐ └──────────────────┘       │
#   │  veth-..far-br    │   │ tap-<chan>- │                            │
#   │  (no IP, in br)   │   │   far       │                            │
#   └────────┬──────────┘   └─────────────┘                            │
#            │                                                          │
#            └──────────────── veth-pair ──────────────────────────────┘
#
# subnet'ы: control 10.10.0.0/24, payload 10.20.0.0/24.
# IP near = .1, far = .2 в обоих случаях (в разных netns не конфликтуют).
#
# Использование:
#   sudo bash scripts/setup_radio_net.sh up      # развернуть
#   sudo bash scripts/setup_radio_net.sh down    # снести
#   sudo bash scripts/setup_radio_net.sh status  # показать состояние
#
# Требует root (либо sudo).

set -euo pipefail

USER_NAME="${SUDO_USER:-${USER:-afetz}}"

# каналы: имя → subnet (без последнего октета)
# Адресный план:
#   .1 = far (внутри netns bas-<chan>-far, GCS-сторона)
#   .2 = near (внутри netns bas-<chan>-near, БАС-сторона)
#   .99 = host-gateway (IP на br-<chan>-near для proxy на host'е, этап 1.5.0)
declare -A CHANNELS=(
  [ctrl]="10.10.0"
  [pload]="10.20.0"
)

ensure_root() {
    if [ "$EUID" -ne 0 ]; then
        echo "Запускайте с sudo (нужен root для bridge/TAP/netns)." >&2
        exit 1
    fi
}

up_channel() {
    local chan="$1"
    local subnet="$2"
    local br_near="br-${chan}-near"
    local br_far="br-${chan}-far"
    local tap_near="tap-${chan}-near"
    local tap_far="tap-${chan}-far"
    local veth_near="veth-${chan}-near"
    local veth_near_br="veth-${chan}-nbr"
    local veth_far="veth-${chan}-far"
    local veth_far_br="veth-${chan}-fbr"
    local netns_near="bas-${chan}-near"
    local netns_far="bas-${chan}-far"

    echo "=== канал ${chan} (subnet ${subnet}.0/24) ==="

    # 1. Bridges (L2, без IP).
    ip link add name "$br_near" type bridge 2>/dev/null || true
    ip link add name "$br_far"  type bridge 2>/dev/null || true
    ip link set "$br_near" up
    ip link set "$br_far"  up

    # 2. TAPs - не назначаем IP, добавляем в bridge.
    #    user=$USER_NAME чтобы ns-3 контейнер мог открывать без root.
    ip tuntap add dev "$tap_near" mode tap user "$USER_NAME" 2>/dev/null || true
    ip tuntap add dev "$tap_far"  mode tap user "$USER_NAME" 2>/dev/null || true
    ip link set "$tap_near" master "$br_near"
    ip link set "$tap_far"  master "$br_far"
    ip link set "$tap_near" up
    ip link set "$tap_far"  up

    # 3. veth-пары для near и far.
    ip link add "$veth_near" type veth peer name "$veth_near_br" 2>/dev/null || true
    ip link add "$veth_far"  type veth peer name "$veth_far_br"  2>/dev/null || true
    ip link set "$veth_near_br" master "$br_near"
    ip link set "$veth_far_br"  master "$br_far"
    ip link set "$veth_near_br" up
    ip link set "$veth_far_br"  up

    # 4. netns'ы и перенос veth туда.
    ip netns add "$netns_near" 2>/dev/null || true
    ip netns add "$netns_far"  2>/dev/null || true
    ip link set "$veth_near" netns "$netns_near"
    ip link set "$veth_far"  netns "$netns_far"

    # 5. IP и UP внутри netns'ов.
    # ВАЖНО: near (БАС-сторона) = .2, far (GCS-сторона) = .1.
    # Это нужно потому что в этапе 1.5.0 host-gateway (.99) сидит на br-near.
    ip netns exec "$netns_near" ip addr add "${subnet}.2/24" dev "$veth_near"
    ip netns exec "$netns_far"  ip addr add "${subnet}.1/24" dev "$veth_far"
    ip netns exec "$netns_near" ip link set "$veth_near" up
    ip netns exec "$netns_far"  ip link set "$veth_far"  up
    ip netns exec "$netns_near" ip link set lo up
    ip netns exec "$netns_far"  ip link set lo up

    # 6. Отключить bridge filtering для br-nf-call-iptables, чтобы пакеты
    #    проходили через bridge без вмешательства iptables.
    sysctl -w "net.bridge.bridge-nf-call-iptables=0" >/dev/null 2>&1 || true

    # 7. Host-gateway IP на br-near: позволяет host-приложениям (socat-proxy)
    # быть видимыми из far-netns через ns-3 (этап 1.5.0).
    # Если в будущем SITL переедет в свой netns (этап 1.5.1), эту IP можно убрать.
    ip addr add "${subnet}.99/24" dev "$br_near" 2>/dev/null || true

    echo "  br-${chan}-{near,far}: up"
    echo "  tap-${chan}-{near,far}: user=${USER_NAME}, в bridge"
    echo "  netns bas-${chan}-near (БАС-сторона):  ${subnet}.2/24"
    echo "  netns bas-${chan}-far  (GCS-сторона):  ${subnet}.1/24"
    echo "  br-${chan}-near host-gateway:          ${subnet}.99/24"
}

down_channel() {
    local chan="$1"
    local br_near="br-${chan}-near"
    local br_far="br-${chan}-far"
    local tap_near="tap-${chan}-near"
    local tap_far="tap-${chan}-far"
    local veth_near_br="veth-${chan}-nbr"
    local veth_far_br="veth-${chan}-fbr"
    local netns_near="bas-${chan}-near"
    local netns_far="bas-${chan}-far"

    echo "=== снятие канала ${chan} ==="
    # netns'ы удаляют свои veth-end автоматически.
    ip netns del "$netns_near" 2>/dev/null || true
    ip netns del "$netns_far"  2>/dev/null || true
    ip link del "$veth_near_br" 2>/dev/null || true
    ip link del "$veth_far_br"  2>/dev/null || true
    ip link del "$tap_near"     2>/dev/null || true
    ip link del "$tap_far"      2>/dev/null || true
    ip link del "$br_near"      2>/dev/null || true
    ip link del "$br_far"       2>/dev/null || true
}

status() {
    echo "--- bridges ---"
    ip link show type bridge 2>/dev/null | grep -E "br-(ctrl|pload)-" || echo "(нет)"
    echo "--- taps ---"
    ip link show 2>/dev/null | grep -E "tap-(ctrl|pload)-" || echo "(нет)"
    echo "--- netns ---"
    ip netns list 2>/dev/null | grep -E "bas-(ctrl|pload)-" || echo "(нет)"
    for chan in "${!CHANNELS[@]}"; do
        local sub="${CHANNELS[$chan]}"
        echo "--- netns bas-${chan}-near (${sub}.1) ---"
        ip netns exec "bas-${chan}-near" ip addr 2>/dev/null | grep -E "inet " | head -3 || echo "  (отсутствует)"
        echo "--- netns bas-${chan}-far  (${sub}.2) ---"
        ip netns exec "bas-${chan}-far"  ip addr 2>/dev/null | grep -E "inet " | head -3 || echo "  (отсутствует)"
    done
}

main() {
    local cmd="${1:-up}"
    ensure_root
    case "$cmd" in
        up)
            for chan in "${!CHANNELS[@]}"; do
                up_channel "$chan" "${CHANNELS[$chan]}"
            done
            echo
            echo "Готово. Проверка:"
            echo "  sudo ip netns exec bas-ctrl-near ping -c 3 10.10.0.2   # должно НЕ ходить (ns-3 ещё не запущен)"
            ;;
        down)
            for chan in "${!CHANNELS[@]}"; do
                down_channel "$chan"
            done
            ;;
        status)
            status
            ;;
        *)
            echo "Использование: $0 {up|down|status}" >&2
            exit 1
            ;;
    esac
}

main "$@"
