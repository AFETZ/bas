#!/usr/bin/env bash
# Тест: blocking gz-transport multicast в bas-uav netns должен починить ARP.
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

# CRITICAL FIX: блокируем multicast и broadcast (кроме ARP) на eth0 ИЗНУТРИ bas-uav netns.
# Это запретит Gazebo Transport (239.255.0.7) и любую другую болтовню вылетать в радио-канал.
# ARP-broadcast НЕ блокируется (это L2-протокол, не IP).
echo "[fix] blocking gz-transport multicast на eth0"
ip netns exec bas-uav iptables -I OUTPUT -o eth0 -d 224.0.0.0/4 -j DROP
ip netns exec bas-uav iptables -I OUTPUT -o eth0 -d 239.0.0.0/8 -j DROP

# ns-3
sg docker -c "docker run -d --name bas-ns3-stage15 --network host --cap-add NET_ADMIN --privileged \
    -v ${REPO}/ns3:/work/ns3:ro -v ${REPO}/logs:/work/logs --entrypoint bash bas/ns3:dev -c '\
        cp /work/ns3/scenarios/two_channel.cc /work/ns3-src/scratch/ \
        && cd /work/ns3-src && ./ns3 build > /tmp/build.log 2>&1 \
        && ${NS3_BIN} --runId=test_mcast_block --duration=120 --ctrlDelayMs=250 --ctrlLoss=0.02 --ploadDelayMs=200 --ploadLoss=0.0'" >/dev/null
for i in $(seq 1 60); do
    [ -s /home/afetz/bas-prototype/logs/test_mcast_block/ns3_events.jsonl ] && break
    sleep 2
done
sleep 5

# start gazebo
echo "[run] starting gazebo"
sg docker -c "docker compose -f ${COMPOSE} up -d gazebo" >/dev/null
sleep 8

echo "[probe] ping bas-ctrl-far → 10.10.0.2 ПОСЛЕ старта gazebo (с iptables-block):"
ip netns exec bas-ctrl-far ip neigh flush all
ip netns exec bas-ctrl-far ping -c 5 -W 5 10.10.0.2 2>&1 | tail -5
echo "[probe] arp:"
ip netns exec bas-ctrl-far ip neigh
echo "[probe] tcp connect to 5760 (no listener yet — ожидаем refused, не EHOSTUNREACH):"
timeout 6 ip netns exec bas-ctrl-far nc -zvw5 10.10.0.2 5760 2>&1 || true
echo
echo "[probe] start sitl и подождать MAVLink:"
sg docker -c "docker compose -f ${COMPOSE} up -d sitl" >/dev/null
for i in $(seq 1 60); do
    ip netns exec bas-uav ss -tln 2>/dev/null | grep -q ":5760" && break
    sleep 1
done
sleep 8
echo "[probe] tcp connect to 5760 (теперь должен PASS):"
timeout 6 ip netns exec bas-ctrl-far nc -zvw5 10.10.0.2 5760 2>&1 || true
