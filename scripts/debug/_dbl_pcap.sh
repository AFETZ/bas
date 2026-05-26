#!/usr/bin/env bash
# Дуальный tcpdump на tap-ctrl-near и tap-ctrl-far чтобы увидеть на каком звене
# пакеты теряются.
set -e
REPO=/home/afetz/bas-prototype
COMPOSE="${REPO}/docker-compose.shared-netns.yml"
NS3_BIN=/work/ns3-src/build/scratch/ns3.40-two_channel-optimized
LOG=${REPO}/logs/dbl_pcap_$(date -u +%H%M%S)
mkdir -p $LOG

[ "$EUID" -eq 0 ] || { echo sudo only; exit 1; }

cleanup() {
    set +e
    [ -n "${T1:-}" ] && kill $T1 2>/dev/null
    [ -n "${T2:-}" ] && kill $T2 2>/dev/null
    [ -n "${T3:-}" ] && kill $T3 2>/dev/null
    sg docker -c "docker rm -f bas-ns3-stage15 2>/dev/null; docker compose -f ${COMPOSE} down -v 2>/dev/null" >/dev/null 2>&1
    ip link del veth-uav-br 2>/dev/null
    rm -f /var/run/netns/bas-uav
    set -e
}
trap cleanup EXIT INT TERM

bash "${REPO}/scripts/setup_radio_net.sh" down >/dev/null 2>&1 || true
bash "${REPO}/scripts/setup_radio_net.sh" up >/dev/null
sg docker -c "docker compose -f ${REPO}/docker-compose.yml down -v 2>/dev/null" >/dev/null 2>&1

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

# ns-3
sg docker -c "docker run -d --name bas-ns3-stage15 --network host --cap-add NET_ADMIN --privileged \
    -v ${REPO}/ns3:/work/ns3:ro -v ${REPO}/logs:/work/logs --entrypoint bash bas/ns3:dev -c '\
        cp /work/ns3/scenarios/two_channel.cc /work/ns3-src/scratch/ \
        && cd /work/ns3-src && ./ns3 build > /tmp/build.log 2>&1 \
        && ${NS3_BIN} --runId=$(basename $LOG) --duration=180 --ctrlDelayMs=250 --ctrlLoss=0.02 --ploadDelayMs=200 --ploadLoss=0.0'" >/dev/null
for i in $(seq 1 60); do
    [ -s "${LOG}/ns3_events.jsonl" ] && break
    sleep 2
done
sleep 5

# dual tcpdump
tcpdump -i tap-ctrl-near -nn -w "${LOG}/tap-near.pcap" 2>/dev/null &
T1=$!
tcpdump -i tap-ctrl-far  -nn -w "${LOG}/tap-far.pcap"  2>/dev/null &
T2=$!
tcpdump -i veth-uav-br   -nn -w "${LOG}/veth-uav-br.pcap" 2>/dev/null &
T3=$!
sleep 1

echo "=== before gazebo: ping ==="
ip netns exec bas-ctrl-far ip neigh flush all
ip netns exec bas-ctrl-far ping -c 3 -W 5 10.10.0.2 2>&1 | tail -3
echo "  bas-uav arp:" $(ip -n bas-uav neigh)
sleep 2

echo "=== start gazebo ==="
sg docker -c "docker compose -f ${COMPOSE} up -d gazebo" 2>&1 | tail -1
sleep 8

echo "=== after gazebo: ping ==="
ip netns exec bas-ctrl-far ip neigh flush all
ip netns exec bas-ctrl-far ping -c 3 -W 5 10.10.0.2 2>&1 | tail -3
echo "  bas-uav arp:" $(ip -n bas-uav neigh)
sleep 2

kill $T1 $T2 $T3 2>/dev/null
sleep 1

echo
echo "=== tap-near pcap ==="
tcpdump -r "${LOG}/tap-near.pcap" -nn 2>&1 | grep -E "ARP|10.10.0" | head -40
echo
echo "=== tap-far pcap ==="
tcpdump -r "${LOG}/tap-far.pcap" -nn 2>&1 | grep -E "ARP|10.10.0" | head -40
echo
echo "=== veth-uav-br pcap ==="
tcpdump -r "${LOG}/veth-uav-br.pcap" -nn 2>&1 | grep -E "ARP|10.10.0" | head -40
echo
echo logs: $LOG
