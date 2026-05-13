#!/usr/bin/env bash
# Тест: ebtables на host'е дропает IP multicast на bridge перед попаданием в tap-ctrl-near.
# ARP пройдёт (он Ethernet broadcast), multicast от Gazebo - нет.
set -e
REPO=/home/afetz/bas-prototype
COMPOSE="${REPO}/docker-compose.shared-netns.yml"
NS3_BIN=/work/ns3-src/build/scratch/ns3.40-two_channel-optimized

[ "$EUID" -eq 0 ] || { echo sudo only; exit 1; }
which ebtables 2>/dev/null || apt-get install -y ebtables 2>&1 | tail -3

cleanup() {
    set +e
    sg docker -c "docker rm -f bas-ns3-stage15 2>/dev/null; docker compose -f ${COMPOSE} down -v 2>/dev/null" >/dev/null 2>&1
    ip link del veth-uav-br 2>/dev/null
    rm -f /var/run/netns/bas-uav
    ebtables -F 2>/dev/null
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

# ebtables: на FORWARD дропаем IP multicast (dst MAC 01:00:5e:*) с veth-uav-br к tap-ctrl-near.
# ARP остаётся (Ethernet broadcast ff:ff:ff:ff:ff:ff).
echo "[fix] ebtables: drop IP-multicast veth-uav-br -> bridge"
ebtables -A FORWARD -i veth-uav-br -d Multicast -j DROP
ebtables -L FORWARD --Lc 2>&1 | head -5

# ns-3
sg docker -c "docker run -d --name bas-ns3-stage15 --network host --cap-add NET_ADMIN --privileged \
    -v ${REPO}/ns3:/work/ns3:ro -v ${REPO}/logs:/work/logs --entrypoint bash bas/ns3:dev -c '\
        cp /work/ns3/scenarios/two_channel.cc /work/ns3-src/scratch/ \
        && cd /work/ns3-src && ./ns3 build > /tmp/build.log 2>&1 \
        && ${NS3_BIN} --runId=test_ebt --duration=120 --ctrlDelayMs=250 --ctrlLoss=0.02 --ploadDelayMs=200 --ploadLoss=0.0'" >/dev/null
for i in $(seq 1 60); do
    [ -s /home/afetz/bas-prototype/logs/test_ebt/ns3_events.jsonl ] && break
    sleep 2
done
sleep 5

echo "[probe BEFORE gazebo]"
ip netns exec bas-ctrl-far ip neigh flush all
ip netns exec bas-ctrl-far ping -c 3 -W 5 10.10.0.2 2>&1 | tail -3

echo "[start gazebo+sitl]"
sg docker -c "docker compose -f ${COMPOSE} up -d gazebo sitl" >/dev/null
for i in $(seq 1 60); do
    ip netns exec bas-uav ss -tln 2>/dev/null | grep -q ":5760" && break
    sleep 1
done
sleep 10

echo "[probe AFTER gazebo+sitl]"
ip netns exec bas-ctrl-far ip neigh flush all
ip netns exec bas-ctrl-far ping -c 5 -W 5 10.10.0.2 2>&1 | tail -5
echo "  arp:" $(ip netns exec bas-ctrl-far ip neigh)

echo "[probe TCP 5760]"
timeout 6 ip netns exec bas-ctrl-far nc -zvw5 10.10.0.2 5760 2>&1 || true

echo "[ebtables stats]"
ebtables -L FORWARD --Lc 2>&1 | head -5
