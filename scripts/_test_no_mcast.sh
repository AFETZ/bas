#!/usr/bin/env bash
# Тест: отключаем multicast на eth0 в bas-uav. Если ARP заработает,
# multicast flood от Gazebo подтверждается как root cause.
set -e
REPO=/home/afetz/bas-prototype
COMPOSE="${REPO}/docker-compose.shared-netns.yml"
NS3_BIN=/work/ns3-src/build/scratch/ns3.40-two_channel-optimized

[ "$EUID" -eq 0 ] || { echo sudo only; exit 1; }

cleanup() {
    set +e
    sg docker -c "docker rm -f bas-ns3-stage15 2>/dev/null; docker compose -f ${COMPOSE} down -v 2>/dev/null" >/dev/null 2>&1
    ip link del veth-uav-br 2>/dev/null
    rm -f /var/run/netns/bas-uav
    set -e
}
trap cleanup EXIT INT TERM

bash "${REPO}/scripts/setup_radio_net.sh" down >/dev/null 2>&1 || true
bash "${REPO}/scripts/setup_radio_net.sh" up >/dev/null

sg docker -c "docker compose -f ${COMPOSE} up -d uav-net" >/dev/null
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

# CRITICAL FIX: отключаем multicast на eth0 в bas-uav.
# Это запретит Gazebo Transport flood'ить ns-3 multicast пакетами.
echo "[fix] ip link set eth0 multicast off"
ip -n bas-uav link set eth0 multicast off
ip -n bas-uav link show eth0 | head -2

# ns-3
sg docker -c "docker run -d --name bas-ns3-stage15 --network host --cap-add NET_ADMIN --privileged \
    -v ${REPO}/ns3:/work/ns3:ro -v ${REPO}/logs:/work/logs --entrypoint bash bas/ns3:dev -c '\
        cp /work/ns3/scenarios/two_channel.cc /work/ns3-src/scratch/ \
        && cd /work/ns3-src && ./ns3 build > /tmp/build.log 2>&1 \
        && ${NS3_BIN} --runId=test_no_mcast --duration=120 --ctrlDelayMs=250 --ctrlLoss=0.02 --ploadDelayMs=200 --ploadLoss=0.0'" >/dev/null
for i in $(seq 1 60); do
    [ -s /home/afetz/bas-prototype/logs/test_no_mcast/ns3_events.jsonl ] && break
    sleep 2
done
sleep 5

echo "[probe BEFORE gazebo]"
ip netns exec bas-ctrl-far ip neigh flush all
ip netns exec bas-ctrl-far ping -c 3 -W 5 10.10.0.2 2>&1 | tail -3

echo "[run] starting gazebo"
sg docker -c "docker compose -f ${COMPOSE} up -d gazebo" >/dev/null
sleep 8

echo "[probe AFTER gazebo с multicast off]"
ip netns exec bas-ctrl-far ip neigh flush all
ip netns exec bas-ctrl-far ping -c 5 -W 5 10.10.0.2 2>&1 | tail -5
echo "  arp:" $(ip netns exec bas-ctrl-far ip neigh)

echo "[run] starting sitl"
sg docker -c "docker compose -f ${COMPOSE} up -d sitl" >/dev/null
for i in $(seq 1 60); do
    ip netns exec bas-uav ss -tln 2>/dev/null | grep -q ":5760" && break
    sleep 1
done
sleep 8

echo "[probe TCP 10.10.0.2:5760]"
timeout 6 ip netns exec bas-ctrl-far nc -zvw5 10.10.0.2 5760 2>&1 || true
