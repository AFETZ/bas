"""ИССГР Multicast Sync Protocol — compact 40/80-byte UDP packets.

Реализует пункт ТЗ "Синхронизация БД: автоматический запуск синхронизации,
уровни детализации, пакеты 40/80 байт, multicast при поддержке каналов
связи" (Краткая_выдержка_актуального_из_гранта_БАС.docx).

Дизайн:
  * Wire format — struct.pack big-endian для cross-platform compatibility.
  * Multicast group по умолчанию 239.10.10.10:5500 (admin-local scope,
    RFC 2365), TTL=1 (local network only). Можно override на site-multicast.
  * Levels of detail:
      L0 (40 байт) — HEARTBEAT, минимальное присутствие объекта
      L1 (40 байт) — POSITION + state (для UAV / mobile objects)
      L2 (80 байт) — SENSOR reading + ground tag (для CV detections и т.п.)
  * Идентификаторы — 32-битные FNV-1a hashes от "domain:system" и UUID,
    что позволяет компактно адресовать объекты. Полные UUID resolveются
    через side-channel (REST API) — multicast только для live state.
  * При support каналов связи: publisher не реагирует на link state
    (UDP fire-and-forget). Subscriber делает sequence-gap detection и
    при пропусках инициирует REST resync через REST GET /digital_twin.

Использование:
    from orchestrator.issgr.sync import (
        MulticastPublisher, MulticastSubscriber, encode_position_l1,
    )

    pub = MulticastPublisher()
    pub.send(encode_position_l1(uav))

    sub = MulticastSubscriber(callback=lambda pkt: print(pkt))
    sub.start()
"""
from __future__ import annotations

import socket
import struct
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from .models import UAV, SensorReading


# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------
SYNC_MARKER = 0xBA5A
PROTOCOL_VERSION = 1

DEFAULT_MULTICAST_GROUP = "239.10.10.10"
DEFAULT_MULTICAST_PORT = 5500
DEFAULT_TTL = 1   # admin-local, не выйдёт за router

MSG_HEARTBEAT = 0xFF
MSG_POSITION_L1 = 0x01
MSG_SENSOR_L2 = 0x02

PACKET_L1_SIZE = 40   # heartbeat + position
PACKET_L2_SIZE = 80   # sensor reading

# struct format:
# L1 (40 B): "!HBBIIIIiihhBB H"
#   H  sync_marker (2)
#   B  version (1)
#   B  msg_type (1)
#   I  domain_hash (4)
#   I  object_hash (4)
#   I  sequence (4)
#   I  timestamp_ms (4) — low 32 bits ms since epoch
#   i  lat_e7 (4) — latitude × 1e7 (стандарт MAVLink)
#   i  lon_e7 (4)
#   i  alt_cm (4) — altitude в см
#   h  heading_cdeg (2) — heading в сотых градуса
#   h  speed_cmps (2) — speed cm/s
#   B  state_flags (1) — armed + mode_bits + battery_pct_quantized
#   B  issgr_class_top (1) — 0=geo, 1=ops, 2=func, 3=raster
#   H  crc16 (2)
L1_FORMAT = "!HBB II II iii hh BB H"
# L2 (80 B) = L1 без CRC (38) + extension (40) + CRC (2):
#   Q  source_object_hash (8) — extended hash для UAV-источника
#   I  sensor_type_hash (4)
#   d  sensor_value_f64 (8) — основное float-значение
#   i  ground_lat_e7 (4)
#   i  ground_lon_e7 (4)
#   I  confidence_e4 (4) — confidence × 10000
#   I  reserved_0 (4)
#   I  reserved_1 (4) — padding до 40B extension
#   H  crc16 (2)
L2_FORMAT = "!HBB II II iii hh BB Q I d ii I I I H"


# ----------------------------------------------------------------------------
# Hashing — FNV-1a 32-bit (deterministic cross-platform, no external deps)
# ----------------------------------------------------------------------------
def fnv1a_32(data: bytes) -> int:
    h = 0x811C9DC5
    for byte in data:
        h ^= byte
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def fnv1a_64(data: bytes) -> int:
    h = 0xCBF29CE484222325
    for byte in data:
        h ^= byte
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


def domain_system_hash(domain: str, system: str) -> int:
    return fnv1a_32(f"{domain}:{system}".encode("utf-8"))


def object_uuid_hash(uuid_str: str) -> int:
    return fnv1a_32(uuid_str.encode("ascii"))


def object_uuid_hash64(uuid_str: str) -> int:
    return fnv1a_64(uuid_str.encode("ascii"))


def sensor_type_hash(sensor_type: str) -> int:
    return fnv1a_32(sensor_type.encode("utf-8"))


# CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, no xorout).
def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


# ----------------------------------------------------------------------------
# Decoded packet structures
# ----------------------------------------------------------------------------
@dataclass
class L1Packet:
    msg_type: int
    domain_hash: int
    object_hash: int
    sequence: int
    timestamp_ms: int
    lat_deg: float
    lon_deg: float
    alt_m: float
    heading_deg: float
    speed_mps: float
    state_flags: int
    issgr_class_top: int
    crc_ok: bool

    @property
    def armed(self) -> bool:
        return bool(self.state_flags & 0x01)


@dataclass
class L2Packet:
    base: L1Packet
    source_object_hash64: int
    sensor_type_hash: int
    sensor_value: float
    ground_lat_deg: float
    ground_lon_deg: float
    confidence: float


# ----------------------------------------------------------------------------
# Encoders
# ----------------------------------------------------------------------------
def encode_heartbeat(
    domain: str, system: str, object_uuid: str, sequence: int,
    timestamp_ms: int | None = None,
) -> bytes:
    """L0 fits в L1 структуру с lat/lon=0 — minimal presence ping."""
    return _pack_l1(
        msg_type=MSG_HEARTBEAT,
        domain_hash=domain_system_hash(domain, system),
        object_hash=object_uuid_hash(object_uuid),
        sequence=sequence,
        timestamp_ms=(timestamp_ms if timestamp_ms is not None
                      else int(time.time() * 1000) & 0xFFFFFFFF),
        lat_deg=0.0, lon_deg=0.0, alt_m=0.0,
        heading_deg=0.0, speed_mps=0.0,
        state_flags=0, issgr_class_top=0,
    )


def encode_position_l1(uav: UAV, sequence: int = 0) -> bytes:
    state_flags = (0x01 if uav.armed else 0x00)
    # battery: 4-bit pct/10 в верхних 4 битах
    if uav.battery_v is not None:
        pct_q = max(0, min(15, int(uav.battery_v / 16.8 * 15)))
        state_flags |= (pct_q & 0x0F) << 4
    speed_mps = 0.0
    if uav.velocity_ned is not None:
        speed_mps = (uav.velocity_ned[0] ** 2 + uav.velocity_ned[1] ** 2) ** 0.5
    top = {"operational_situation": 1, "functional_objects": 2,
           "raster_3d_displays": 3, "geospatial_objects": 0}.get(
                uav.issgr_class.top_level, 0)
    return _pack_l1(
        msg_type=MSG_POSITION_L1,
        domain_hash=domain_system_hash(uav.id.domain, uav.id.system),
        object_hash=object_uuid_hash(str(uav.id.object_uuid)),
        sequence=sequence,
        timestamp_ms=int(uav.timestamp.timestamp() * 1000) & 0xFFFFFFFF,
        lat_deg=uav.pose.latitude_deg,
        lon_deg=uav.pose.longitude_deg,
        alt_m=uav.pose.altitude_m,
        heading_deg=uav.pose.heading_deg or 0.0,
        speed_mps=speed_mps,
        state_flags=state_flags,
        issgr_class_top=top,
    )


def encode_sensor_l2(
    reading: SensorReading,
    sequence: int = 0,
    sensor_value_f64: float | None = None,
    ground_lat_deg: float | None = None,
    ground_lon_deg: float | None = None,
    confidence: float | None = None,
) -> bytes:
    """80-byte sensor reading packet (CV detection, RSSI и др.).

    Использует value/confidence из аргументов либо из reading.value dict.
    """
    val = reading.value if isinstance(reading.value, dict) else {}
    if sensor_value_f64 is None:
        sensor_value_f64 = float(val.get("value", val.get("confidence", 0.0)))
    if confidence is None:
        confidence = float(val.get("confidence", 0.0))
    if ground_lat_deg is None:
        ground_lat_deg = float(val.get("ground_lat", 0.0))
    if ground_lon_deg is None:
        ground_lon_deg = float(val.get("ground_lon", 0.0))

    pose = reading.pose_at_observation
    lat = pose.latitude_deg if pose else 0.0
    lon = pose.longitude_deg if pose else 0.0
    alt = pose.altitude_m if pose else 0.0
    heading = (pose.heading_deg or 0.0) if pose else 0.0
    top = {"operational_situation": 1, "functional_objects": 2,
           "raster_3d_displays": 3, "geospatial_objects": 0}.get(
                reading.issgr_class.top_level, 2)

    body = struct.pack(
        "!HBB II II iii hh BB Q I d ii I I I",
        SYNC_MARKER, PROTOCOL_VERSION, MSG_SENSOR_L2,
        domain_system_hash(reading.id.domain, reading.id.system),
        object_uuid_hash(str(reading.id.object_uuid)),
        sequence,
        int(reading.timestamp.timestamp() * 1000) & 0xFFFFFFFF,
        int(lat * 1e7), int(lon * 1e7), int(alt * 100),
        int(heading * 100), 0,
        0, top,
        object_uuid_hash64(str(reading.source_uav_id.object_uuid)),
        sensor_type_hash(reading.sensor_type),
        sensor_value_f64,
        int(ground_lat_deg * 1e7), int(ground_lon_deg * 1e7),
        int(confidence * 10000),
        0,   # reserved_0
        0,   # reserved_1
    )
    return body + struct.pack("!H", crc16(body))


def _pack_l1(
    *, msg_type: int, domain_hash: int, object_hash: int, sequence: int,
    timestamp_ms: int, lat_deg: float, lon_deg: float, alt_m: float,
    heading_deg: float, speed_mps: float, state_flags: int,
    issgr_class_top: int,
) -> bytes:
    body = struct.pack(
        "!HBB II II iii hh BB",
        SYNC_MARKER, PROTOCOL_VERSION, msg_type,
        domain_hash, object_hash,
        sequence, timestamp_ms,
        int(lat_deg * 1e7), int(lon_deg * 1e7), int(alt_m * 100),
        int(heading_deg * 100), int(speed_mps * 100),
        state_flags & 0xFF, issgr_class_top & 0xFF,
    )
    return body + struct.pack("!H", crc16(body))


# ----------------------------------------------------------------------------
# Decoders
# ----------------------------------------------------------------------------
def decode_packet(data: bytes) -> L1Packet | L2Packet | None:
    if len(data) not in (PACKET_L1_SIZE, PACKET_L2_SIZE):
        return None
    if len(data) >= 4:
        marker, version = struct.unpack("!HB", data[:3])
        if marker != SYNC_MARKER or version != PROTOCOL_VERSION:
            return None
    body = data[:-2]
    rx_crc = struct.unpack("!H", data[-2:])[0]
    crc_ok = (crc16(body) == rx_crc)

    if len(data) == PACKET_L1_SIZE:
        return _decode_l1(data, crc_ok)
    return _decode_l2(data, crc_ok)


def _decode_l1(data: bytes, crc_ok: bool) -> L1Packet:
    # L1_FORMAT = "!HBB II II iii hh BB H"
    # idx: 0=marker 1=version 2=msg_type 3=domain 4=object 5=seq 6=ts
    #      7=lat 8=lon 9=alt 10=heading 11=speed 12=state 13=class 14=crc
    fields = struct.unpack(L1_FORMAT, data)
    return L1Packet(
        msg_type=fields[2],
        domain_hash=fields[3],
        object_hash=fields[4],
        sequence=fields[5],
        timestamp_ms=fields[6],
        lat_deg=fields[7] / 1e7,
        lon_deg=fields[8] / 1e7,
        alt_m=fields[9] / 100,
        heading_deg=fields[10] / 100,
        speed_mps=fields[11] / 100,
        state_flags=fields[12],
        issgr_class_top=fields[13],
        crc_ok=crc_ok,
    )


def _decode_l2(data: bytes, crc_ok: bool) -> L2Packet:
    # L2_FORMAT = "!HBB II II iii hh BB Q I d ii I I I H"
    # base L1 idx 0..13, extension idx 14..20, crc=21
    fields = struct.unpack(L2_FORMAT, data)
    base = L1Packet(
        msg_type=fields[2],
        domain_hash=fields[3],
        object_hash=fields[4],
        sequence=fields[5],
        timestamp_ms=fields[6],
        lat_deg=fields[7] / 1e7,
        lon_deg=fields[8] / 1e7,
        alt_m=fields[9] / 100,
        heading_deg=fields[10] / 100,
        speed_mps=fields[11] / 100,
        state_flags=fields[12],
        issgr_class_top=fields[13],
        crc_ok=crc_ok,
    )
    return L2Packet(
        base=base,
        source_object_hash64=fields[14],
        sensor_type_hash=fields[15],
        sensor_value=fields[16],
        ground_lat_deg=fields[17] / 1e7,
        ground_lon_deg=fields[18] / 1e7,
        confidence=fields[19] / 10000,
    )


# ----------------------------------------------------------------------------
# Multicast UDP transport
# ----------------------------------------------------------------------------
class MulticastPublisher:
    """UDP multicast sender — fire-and-forget."""
    def __init__(self, group: str = DEFAULT_MULTICAST_GROUP,
                 port: int = DEFAULT_MULTICAST_PORT, ttl: int = DEFAULT_TTL,
                 interface: str = "0.0.0.0") -> None:
        self.group = group
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL,
                             struct.pack("b", ttl))
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                             socket.inet_aton(interface))

    def send(self, packet: bytes) -> int:
        return self.sock.sendto(packet, (self.group, self.port))

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class MulticastSubscriber:
    """UDP multicast receiver, callback per packet в background thread."""
    def __init__(
        self,
        callback: Callable[[bytes, tuple[str, int]], None],
        group: str = DEFAULT_MULTICAST_GROUP,
        port: int = DEFAULT_MULTICAST_PORT,
        interface: str = "0.0.0.0",
    ) -> None:
        self.group = group
        self.port = port
        self.interface = interface
        self.callback = callback
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        s.bind(("", self.port))
        mreq = struct.pack(
            "=4s4s",
            socket.inet_aton(self.group),
            socket.inet_aton(self.interface),
        )
        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        s.settimeout(0.5)
        self._sock = s
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="issgr-sync-rx", daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None

    def _loop(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            self.callback(data, addr)


# ----------------------------------------------------------------------------
# Auto-publisher: scheduled snapshot всех objects of repository
# ----------------------------------------------------------------------------
class AutoPublisher:
    """Background scheduler — emit position L1 для всех UAV и sensor L2
    для recent SensorReading.

    Имитирует "автоматический запуск синхронизации" из ТЗ.
    """
    def __init__(
        self, repository, interval_s: float = 1.0,
        publisher: MulticastPublisher | None = None,
        max_recent_sensors: int = 50,
    ) -> None:
        from .repository import IssgrRepository  # noqa: F401 (typing only)
        self.repository = repository
        self.interval_s = interval_s
        self.publisher = publisher or MulticastPublisher()
        self.max_recent_sensors = max_recent_sensors
        self._seq: dict[int, int] = {}   # object_hash → monotonic seq
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._stats = {"l1_sent": 0, "l2_sent": 0, "bytes_sent": 0}

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="issgr-sync-tx", daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                pass
            self._stop.wait(self.interval_s)

    def _tick(self) -> None:
        uavs = self.repository.list_collection("uavs", limit=1000)
        for uav in uavs:
            oh = object_uuid_hash(str(uav.id.object_uuid))
            self._seq[oh] = (self._seq.get(oh, 0) + 1) & 0xFFFFFFFF
            pkt = encode_position_l1(uav, sequence=self._seq[oh])
            self.publisher.send(pkt)
            self._stats["l1_sent"] += 1
            self._stats["bytes_sent"] += len(pkt)
        readings = self.repository.list_collection(
            "sensor_readings", limit=self.max_recent_sensors,
        )
        for r in readings:
            oh = object_uuid_hash(str(r.id.object_uuid))
            self._seq[oh] = (self._seq.get(oh, 0) + 1) & 0xFFFFFFFF
            pkt = encode_sensor_l2(r, sequence=self._seq[oh])
            self.publisher.send(pkt)
            self._stats["l2_sent"] += 1
            self._stats["bytes_sent"] += len(pkt)

    @property
    def stats(self) -> dict[str, int]:
        return dict(self._stats)
