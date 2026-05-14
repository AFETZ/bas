# Stage 1.5.1: known issues and root-cause findings

## Status (post-v0.4)

| Scenario | Status | Why |
|---|---|---|
| `wifi_good` через ns-3 | works | low delay + duplicate-IP fix applied |
| `moderate` (50ms delay, 0 loss) | works (manually verified) | small queue ok |
| `degraded_lora` (250ms delay, 2% loss) | **NOT working** | secondary issue with Gazebo multicast — see below |

## Found root cause #1: duplicate IP 10.10.0.2 (FIXED in v0.4)

`setup_radio_net.sh` assigned `10.10.0.2/24` to `bas-ctrl-near` netns; `run_stage_1_5_1_mission.sh`
injects veth into `bas-uav` netns with same IP. Both netns connected to `br-ctrl-near` →
**two ARP responders for the same IP**.

Confirmed via tcpdump on `tap-ctrl-far`:
```
ARP, Request who-has 10.10.0.2 tell 10.10.0.1
ARP, Reply 10.10.0.2 is-at 42:3c:7a:f2:10:4d  (bas-uav)        <- correct responder
ARP, Reply 10.10.0.2 is-at c6:84:e4:f6:4d:1b  (bas-ctrl-near)  <- duplicate
```

With low delay (wifi_good ~5ms), both replies arrive ~ms apart and Linux picks one quickly.
With high delay (degraded_lora ~250ms), replies arrive 250ms apart and Linux ARP state
machine gets confused → TCP/MAVLink unstable.

**Fix:** `setup_radio_net.sh` no longer assigns IP to the near netns by default. Legacy 1.4-era
ping tests can opt in via `RADIO_NEAR_NETNS_IP=1`.

Confirmed via smoke test (`scripts/_smoke_radio.sh degraded_lora 10.10.0.2`):
- Before fix: 2 ARP replies, ping RTT jitter 500-1252ms, TCP unstable
- After fix: 1 ARP reply, ping 5/5 100% PDR, TCP clean

## Root cause #2: Gazebo multicast saturates ns-3 channel (NOT FIXED)

After duplicate-IP fix, smoke test without Gazebo passes. But mission with Gazebo+SITL
joining the shared netns still fails with `EHOSTUNREACH` from `bas-ctrl-far`.

Confirmed via step-by-step smoke (`scripts/_smoke_step_by_step.sh`):
- After pause container + veth + ns-3: `ping 10.10.0.2: 2/2 received` ✓
- After `docker compose up gazebo`: `ping: 0/2 received, Destination Host Unreachable` ✗

tcpdump on `veth-uav-br` immediately after Gazebo starts shows ~30 multicast UDP packets/sec
to `239.255.0.7:10317/10318` (gz-transport discovery and topic data). These traverse
`br-ctrl-near` → `tap-ctrl-near` → ns-3 → `tap-ctrl-far`, and saturate the simulated 20 Mbps
channel with 250ms delay. ARP request from `bas-ctrl-far` gets queued behind multicast and
either dropped or arrives after Linux ARP solicit timeout (3 seconds).

### Attempted mitigations that DID NOT work

1. **iptables OUTPUT drop multicast in `bas-uav` netns** — rule didn't catch the packets
   (Linux locally-generated multicast bypasses some iptables paths).
2. **`ip link set eth0 multicast off`** — also disables ARP broadcast (Linux treats them similarly).
3. **`bridge link set veth-uav-br mcast_flood off`** — also breaks ARP through the bridge.
4. **ebtables on host** — not supported in WSL2 kernel without nf_tables backend.
5. **CSMA queue 5000p** in ns-3 — queue size isn't the bottleneck; per-second stats show only
   ~3 control packets/sec, so backlog never builds (multicast goes on the same CSMA but stats
   only sample TX/RX counters, not queue depth).

### Hypotheses still to test

A. **Gazebo env vars** — set `GZ_PARTITION` / `GZ_TRANSPORT_TOPIC_MULTICAST_NIC=lo` in
   `docker-compose.shared-netns.yml` to force gz-transport onto loopback only. Most promising
   path — addresses root cause directly. **APPLIED in v0.5** — works, gz multicast больше
   не уходит через ns-3.

B. **tc filter on `veth-uav-br`** — egress filter dropping IPv4 multicast at qdisc level.
   Should work in WSL2 where ebtables doesn't.

C. **Replace `tap-ctrl-near` bridge port mode** — make TapBridge see ONLY ARP and unicast
   to/from `10.10.0.2`. Requires deeper TapBridge tinkering.

D. **Higher CSMA `DataRate`** — bumping channel bandwidth from 20 Mbps to e.g. 1 Gbps would
   reduce the simulated transmission delay component. But this affects the radio model
   semantics — defeats the purpose of "LoRa-like degraded channel".

## Root cause #4 (v0.6): MAVLink mission upload OPERATION_CANCELLED в degraded_lora

После реализации AUTO-режима (mission upload + AUTO mode + MISSION_START — Codex'овский
вариант для устойчивости к live-control потерям) проблема сместилась с TAKEOFF на сам
mission upload. ArduPilot отвечает MISSION_ACK type=15 (OPERATION_CANCELLED) во время
upload. Причина: TCP head-of-line blocking настолько серьёзный что между SITL'овым
MISSION_REQUEST(seq=N) и нашим MISSION_ITEM(seq=N) проходит >ArduPilot's internal
mission timeout (~5s), и ArduPilot отменяет upload.

AUTO режим verified working на wifi_good: 1824 flight events, max alt 30.04m, mission
landed at +195s. degraded_lora — OPERATION_CANCELLED посреди upload (item seq=0
переотправлен 4 раза, потом cancel).

### Hypotheses for #4

A. **UDP MAVLink через socat sidecar в bas-uav netns** — обходит TCP HoL blocking.
   `socat TCP4:127.0.0.1:5760 UDP4-LISTEN:14550,reuseaddr` запускается рядом с SITL,
   orchestrator подключается по `udp:10.10.0.2:14550`. UDP пакеты не queue-ятся,
   каждый MAVLink-message отправляется независимо. Mission upload protocol сам
   retransmits лост-айтемы. Самый чистый путь.

B. **MAVProxy между SITL и ns-3** — MAVProxy known-good в lossy MAVLink сценариях
   (используется в реальных дронах с радио). Запустить mavproxy в SITL netns,
   bridge TCP-SITL ↔ UDP-out:14550.

C. **TCP_NODELAY + increased buffer** в pymavlink — паллиатив, не решает root cause.

D. **"Less degraded" профиль** — уменьшить delay до 100ms или loss до 0.5% где
   MAVLink mission upload успевает в SITL timeout. Это "characterization" подход.

### State per stage (post-v0.6)

| Stage | Status |
|---|---|
| 1.4 (host network mission) | ✅ works |
| 1.5.0 (shadow GCS in netns through ns-3) | ✅ works |
| 1.5.1 wifi_good via ns-3 (GUIDED) | ✅ works |
| 1.5.1 wifi_good via ns-3 (AUTO, v0.6) | ✅ works (mission upload + autonomous flight) |
| 1.5.1 degraded_lora via ns-3 (GUIDED) | ❌ TCP HoL → TAKEOFF не доходит |
| 1.5.1 degraded_lora via ns-3 (AUTO, v0.6) | ❌ MAVLink mission upload OPERATION_CANCELLED |

## Root cause #3: MAVLink TCP backlog in lossy channel (v0.5 partial — ARM works, TAKEOFF
still fails)

После фикса #1 и #2 ARM проходит в degraded_lora (250ms delay + 2% loss), но БАС
авто-disarm'ится через 30с потому что TAKEOFF не реагирует. Глубокая причина — **TCP
backlog**: MAVLink commands отправленные orchestrator'ом приходят к SITL с задержкой 8-22
секунды (видно по COMMAND_ACK с large wall_time delta после command_long_send). При
RTT=500ms + 2% loss TCP retransmits + slow-start вызывают throughput collapse.

ARM удалось обойти через `_arm()` спамом 10 команд × 6 attempts (общее окно 6 минут).
TAKEOFF/SET_POSITION_TARGET с такой стратегией не успели — auto-disarm срабатывает за 30с
после ARM, а команды накапливаются в TCP-очереди.

### Hypotheses for #3

- Send ALL flight commands periodically (every 0.5s) instead of one-shot
- Increase TCP send/recv buffers via PARAM_SET `TCP_NODELAY` or socket options
- Use MAVLink 1 (smaller packets, faster propagation)
- Lower the simulated loss from 2% to 0.5% for working degraded scenario
- Don't auto-disarm: PARAM_SET `DISARM_DELAY` to long value

### State per stage (post-v0.5)

| Stage | Status |
|---|---|
| 1.4 (host network mission) | ✅ works |
| 1.5.0 (shadow GCS in netns through ns-3) | ✅ works |
| 1.5.1 wifi_good mission через ns-3 | ✅ works (mission landed) |
| 1.5.1 degraded_lora mission через ns-3 | ⚠️ ARM works, TAKEOFF fails (RC #3 above) |

## Files involved in debug

- `scripts/_smoke_radio.sh` — minimal smoke (no Gazebo); reproduces dup-IP, verifies post-fix
- `scripts/_smoke_step_by_step.sh` — incremental smoke that demonstrates Gazebo breaks ping
- `scripts/_smoke_mission_setup.sh` — mission-style setup without orchestrator
- `scripts/_dbl_pcap.sh` — three-way tcpdump (tap-near/tap-far/veth-uav-br)
- `scripts/_compare_netns.sh` — netns state diff before/after Gazebo
- `scripts/_test_block_mcast.sh`, `_test_no_mcast.sh`, `_test_bridge_mcast.sh`,
  `_test_ebtables.sh` — failed mitigation attempts (kept for record)
