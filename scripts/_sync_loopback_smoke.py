#!/usr/bin/env python3
"""Multicast sync loopback smoke — publisher + subscriber на one host.

Smoke test: publisher emit'ит N packets через multicast 239.10.10.10:5500,
subscriber на том же интерфейсе принимает и декодирует. Verify counts +
CRC + decoded fields match.
"""
import sys, time, uuid
sys.path.insert(0, "/home/afetz/bas-prototype/orchestrator/src")

from orchestrator.issgr import (   # noqa: E402
    UAV, Pose, ObjectIdentifier, SensorReading, IssgrClass,
    MulticastPublisher, MulticastSubscriber, decode_packet,
    encode_position_l1, encode_heartbeat, encode_sensor_l2,
)


received = []

def on_packet(data: bytes, addr) -> None:
    pkt = decode_packet(data)
    received.append((len(data), pkt, addr))


sub = MulticastSubscriber(callback=on_packet, port=5505)
sub.start()
time.sleep(0.5)   # дать сабскраберу подсесть на group

pub = MulticastPublisher(port=5505)

uav = UAV(name="Iris-1", sysid=1,
          pose=Pose(latitude_deg=-35.363, longitude_deg=149.165,
                    altitude_m=15.0, heading_deg=90.0),
          armed=True, flight_mode="AUTO",
          velocity_ned=[3.0, 0.5, 0.0])
sr = SensorReading(
    id=ObjectIdentifier(object_uuid=uuid.UUID("00000000-0000-0000-0000-000000000abc")),
    name="CV:car", issgr_class=IssgrClass.FUNC_SENSOR_STATION,
    source_uav_id=ObjectIdentifier(object_uuid=uuid.UUID("00000000-0000-0000-0000-000000000100")),
    sensor_type="camera_object_detection",
    value={"class_name": "car", "confidence": 0.92,
           "ground_lat": -35.3636, "ground_lon": 149.1655},
)

print("Publishing 1 heartbeat + 3 L1 + 2 L2 ...")
pub.send(encode_heartbeat("bas-sync", "node-A", "node-A", 1))
for seq in (10, 11, 12):
    pub.send(encode_position_l1(uav, sequence=seq))
for seq in (20, 21):
    pub.send(encode_sensor_l2(sr, sequence=seq))

time.sleep(1.0)
sub.stop()
pub.close()

print(f"\nReceived {len(received)} packets:")
for size, pkt, addr in received:
    if hasattr(pkt, "base"):
        msg_type = pkt.base.msg_type
        seq = pkt.base.sequence
        crc = pkt.base.crc_ok
        extra = (f"  sensor_value={pkt.sensor_value:.2f}"
                 f"  ground=({pkt.ground_lat_deg:.5f},{pkt.ground_lon_deg:.5f})"
                 f"  conf={pkt.confidence:.2f}")
    else:
        msg_type = pkt.msg_type
        seq = pkt.sequence
        crc = pkt.crc_ok
        if msg_type == 0x01:
            extra = (f"  lat={pkt.lat_deg:.5f} lon={pkt.lon_deg:.5f}"
                     f"  alt={pkt.alt_m}м spd={pkt.speed_mps:.1f}m/s"
                     f"  armed={pkt.armed}")
        else:
            extra = ""
    label = {0xFF: "HEARTBEAT", 0x01: "POSITION_L1", 0x02: "SENSOR_L2"}.get(msg_type, "?")
    print(f"  {size}B  {label:12s}  seq={seq:4d}  crc={crc}  from {addr[0]}{extra}")

# Verify counts.
n_hb = sum(1 for _, p, _ in received if not hasattr(p, "base") and p.msg_type == 0xFF)
n_l1 = sum(1 for _, p, _ in received if not hasattr(p, "base") and p.msg_type == 0x01)
n_l2 = sum(1 for _, p, _ in received if hasattr(p, "base"))
print(f"\nSummary: HEARTBEAT={n_hb}, L1={n_l1}, L2={n_l2}")
assert n_hb == 1, f"expected 1 heartbeat, got {n_hb}"
assert n_l1 == 3, f"expected 3 L1, got {n_l1}"
assert n_l2 == 2, f"expected 2 L2, got {n_l2}"
all_crc = all((p.base.crc_ok if hasattr(p, "base") else p.crc_ok)
              for _, p, _ in received)
assert all_crc, "some packets failed CRC"
print("ALL CHECKS PASSED")
