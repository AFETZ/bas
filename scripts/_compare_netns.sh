#!/usr/bin/env bash
# Сравнить sysctl/iptables/routes в bas-uav netns ДО и ПОСЛЕ старта gazebo.
set -e
REPO=/home/afetz/bas-prototype
COMPOSE="${REPO}/docker-compose.shared-netns.yml"

[ "$EUID" -eq 0 ] || { echo sudo only; exit 1; }

# clean
sg docker -c "docker rm -f bas-gazebo bas-sitl bas-uav-net 2>/dev/null" >/dev/null 2>&1
ip link del veth-uav-br 2>/dev/null
rm -f /var/run/netns/bas-uav

bash "${REPO}/scripts/setup_radio_net.sh" down >/dev/null 2>&1 || true
bash "${REPO}/scripts/setup_radio_net.sh" up >/dev/null

sg docker -c "docker compose -f ${COMPOSE} up -d uav-net" 2>&1 | tail -1
UAV_PID=$(sg docker -c "docker inspect --format '{{.State.Pid}}' bas-uav-net")
ln -sf "/proc/${UAV_PID}/ns/net" /var/run/netns/bas-uav
ip link add veth-uav type veth peer name veth-uav-br
ip link set veth-uav-br master br-ctrl-near
ip link set veth-uav-br up
ip link set veth-uav netns bas-uav
ip -n bas-uav link set veth-uav name eth0
ip -n bas-uav addr add 10.10.0.2/24 dev eth0
ip -n bas-uav link set eth0 up
ip -n bas-uav link set lo up

snapshot() {
    local label="$1"
    echo "=========${label}========="
    echo "--- eth0 state ---"
    ip -n bas-uav link show eth0
    ip -n bas-uav addr show eth0 | head -8
    echo "--- routes ---"
    ip -n bas-uav route
    echo "--- iptables filter ---"
    ip netns exec bas-uav iptables -L -n 2>/dev/null | head -15
    echo "--- iptables nat ---"
    ip netns exec bas-uav iptables -t nat -L -n 2>/dev/null | head -15
    echo "--- key sysctls ---"
    ip netns exec bas-uav sysctl -a 2>/dev/null | grep -E '\.eth0\.(rp_filter|arp_ignore|arp_announce|accept_local|forwarding|disable_ipv6|proxy_arp)|all\.(rp_filter|arp_ignore|forwarding|disable_ipv6|proxy_arp)' | sort
}

snapshot "BEFORE gazebo"

sg docker -c "docker compose -f ${COMPOSE} up -d gazebo" 2>&1 | tail -1
sleep 8
snapshot "AFTER gazebo"

echo
echo "=== Now compare both blocks above ==="
