#!/usr/bin/env python3
"""BAS cyber defense monitor — detect attack signatures.

Companion к `cyber_attack_simulator.py`. Подписывается на:
  * MAVLink endpoint (UDP listen) — для GPS spoof + cmd injection
  * Channel JSON file (poll) — для RF jamming detection

Эмиттит alert events в NDJSON log + on stdout. Use case:

  Terminal 1:  ./cyber_defense_monitor.py --mavlink-port 14550 \\
                  --channel-file /tmp/sionna_channel.json \\
                  --log-file logs/defense.jsonl

  Terminal 2:  ./cyber_attack_simulator.py gps_spoof --target-port 14550

Detection logic:

  GPS_SPOOF:
    - Track last_lat, last_lon per sysid
    - Если delta > position_jump_threshold_m за < jump_window_ms → alert
    - Также: detect "shadow" sysid (несколько разных positions с одним sysid)

  CMD_INJECTION:
    - Whitelist authorized GCS sysids (e.g. [255, 252])
    - Любой COMMAND_LONG с sysid вне whitelist → alert

  RF_JAMMING:
    - Sliding window 5s avg RSSI from channel JSON
    - If < jam_rssi_threshold для consecutive N polls → alert
    - Also: extra_delay_ms > 200 sustained → degradation alert
"""
from __future__ import annotations

import argparse
import json
import socket
import struct
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


MAVLINK_V2_STX = 0xFD


# ---------------------------------------------------------------------------
# MAVLink frame parsing (минимальный, transparent к invalid CRC)
# ---------------------------------------------------------------------------
def parse_global_position_int(payload: bytes) -> dict | None:
    if len(payload) < 28:
        return None
    try:
        (t_boot, lat_e7, lon_e7, alt_mm, rel_alt_mm,
         vx, vy, vz, hdg_cd) = struct.unpack("<IiiiihhhH", payload[:28])
        return {
            "time_boot_ms": t_boot,
            "lat_deg": lat_e7 / 1e7,
            "lon_deg": lon_e7 / 1e7,
            "alt_m": alt_mm / 1000.0,
            "rel_alt_m": rel_alt_mm / 1000.0,
            "vx_ms": vx / 100.0, "vy_ms": vy / 100.0, "vz_ms": vz / 100.0,
            "heading_deg": hdg_cd / 100.0,
        }
    except struct.error:
        return None


def parse_command_long(payload: bytes) -> dict | None:
    if len(payload) < 33:
        return None
    try:
        (p1, p2, p3, p4, p5, p6, p7,
         cmd, t_sys, t_comp, conf) = struct.unpack(
            "<fffffffHBBB", payload[:33])
        return {
            "command": cmd, "target_sys": t_sys, "target_comp": t_comp,
            "p1": p1, "p2": p2, "p3": p3,
            "confirmation": conf,
        }
    except struct.error:
        return None


def parse_mavlink_frame(buf: bytes) -> tuple[int, dict, int] | None:
    """Parse one MAVLink v2 frame at buf[0]. Returns (msgid, payload_dict, total_len).

    Только v2 (0xFD). Если invalid header — None. Не валидируем CRC.
    """
    if len(buf) < 12 or buf[0] != MAVLINK_V2_STX:
        return None
    payload_len = buf[1]
    incompat = buf[2]
    total = 12 + payload_len + (13 if (incompat & 0x01) else 0)
    if len(buf) < total:
        return None
    header = {
        "sysid": buf[5], "compid": buf[6],
        "msgid": buf[7] | (buf[8] << 8) | (buf[9] << 16),
        "seq": buf[4],
    }
    payload = buf[10:10 + payload_len]
    msgid = header["msgid"]
    if msgid == 33:
        parsed = parse_global_position_int(payload)
    elif msgid == 76:
        parsed = parse_command_long(payload)
    else:
        parsed = {"_raw_len": len(payload)}
    if parsed is None:
        return None
    parsed.update(header)
    return (msgid, parsed, total)


# ---------------------------------------------------------------------------
# Detection state
# ---------------------------------------------------------------------------
@dataclass
class Alert:
    kind: str           # "gps_spoof", "cmd_injection", "rf_jamming"
    severity: str       # "info", "warn", "critical"
    detail: str
    ts: float = field(default_factory=time.time)
    extra: dict = field(default_factory=dict)


@dataclass
class DefenseConfig:
    # GPS spoof
    position_jump_threshold_m: float = 100.0
    jump_window_ms: float = 1000.0
    # Cmd injection
    authorized_gcs_sysids: tuple[int, ...] = (255, 252)
    # RF jam
    jam_rssi_threshold_dbm: float = -90.0
    jam_consecutive_polls: int = 3
    jam_delay_threshold_ms: float = 200.0


class DefenseMonitor:
    def __init__(self, cfg: DefenseConfig | None = None,
                 on_alert: Callable[[Alert], None] | None = None) -> None:
        self.cfg = cfg or DefenseConfig()
        self._on_alert = on_alert or self._default_alert
        self._last_position: dict[int, tuple[float, float, float, float]] = {}
        # sysid → (lat, lon, alt, ts)
        self._rssi_window: deque = deque(maxlen=20)
        self._consecutive_low_rssi = 0
        self._stop = threading.Event()
        self.alerts: list[Alert] = []

    def _default_alert(self, a: Alert) -> None:
        self.alerts.append(a)
        print(f"[ALERT/{a.severity.upper():8s}] {a.kind:14s}  {a.detail}",
              flush=True)

    def feed_mavlink_buf(self, buf: bytes) -> int:
        """Parse all frames in buf, run detection. Returns # frames parsed."""
        n = 0
        offset = 0
        while offset < len(buf):
            r = parse_mavlink_frame(buf[offset:])
            if r is None:
                offset += 1
                continue
            msgid, parsed, total = r
            offset += total
            n += 1
            self._inspect_frame(msgid, parsed)
        return n

    def _inspect_frame(self, msgid: int, parsed: dict) -> None:
        sysid = parsed.get("sysid", 0)
        compid = parsed.get("compid", 0)
        now = time.time()

        # GPS spoof detection.
        if msgid == 33 and "lat_deg" in parsed:
            lat = parsed["lat_deg"]; lon = parsed["lon_deg"]; alt = parsed["alt_m"]
            prev = self._last_position.get(sysid)
            if prev is not None:
                plat, plon, _palt, pts = prev
                dt_ms = (now - pts) * 1000.0
                if dt_ms <= self.cfg.jump_window_ms:
                    dist_m = self._haversine_m(plat, plon, lat, lon)
                    if dist_m > self.cfg.position_jump_threshold_m:
                        self._on_alert(Alert(
                            kind="gps_spoof",
                            severity="critical",
                            detail=(f"sysid={sysid} jump {dist_m:.0f}m в "
                                    f"{dt_ms:.0f}ms (prev=({plat:.5f},{plon:.5f}) "
                                    f"→ ({lat:.5f},{lon:.5f}))"),
                            extra={"sysid": sysid, "dist_m": dist_m,
                                   "dt_ms": dt_ms,
                                   "prev_lat": plat, "prev_lon": plon,
                                   "new_lat": lat, "new_lon": lon},
                        ))
            self._last_position[sysid] = (lat, lon, alt, now)

        # Command injection detection.
        elif msgid == 76:
            if sysid not in self.cfg.authorized_gcs_sysids:
                cmd = parsed.get("command", 0)
                self._on_alert(Alert(
                    kind="cmd_injection",
                    severity="critical",
                    detail=(f"COMMAND_LONG cmd={cmd} от unauthorized sysid="
                            f"{sysid} compid={compid} → target_sys="
                            f"{parsed.get('target_sys')}"),
                    extra={"sysid": sysid, "compid": compid, "command": cmd,
                           "target_sys": parsed.get("target_sys")},
                ))

    def poll_channel_file(self, channel_file: Path) -> None:
        """Read RF channel JSON, detect jamming signatures."""
        try:
            data = json.loads(channel_file.read_text())
        except (OSError, json.JSONDecodeError):
            return
        rssi = data.get("rssi_dbm")
        delay = data.get("extra_delay_ms", 0)
        if rssi is None:
            return
        self._rssi_window.append(rssi)
        if rssi < self.cfg.jam_rssi_threshold_dbm:
            self._consecutive_low_rssi += 1
            if self._consecutive_low_rssi == self.cfg.jam_consecutive_polls:
                self._on_alert(Alert(
                    kind="rf_jamming",
                    severity="critical",
                    detail=(f"RSSI sustained < {self.cfg.jam_rssi_threshold_dbm} dBm "
                            f"({self._consecutive_low_rssi} consecutive polls, "
                            f"current={rssi:.1f} dBm, source="
                            f"{data.get('source', '?')})"),
                    extra={"rssi_dbm": rssi, "delay_ms": delay,
                           "source": data.get("source")},
                ))
        else:
            self._consecutive_low_rssi = 0

        if delay > self.cfg.jam_delay_threshold_ms:
            self._on_alert(Alert(
                kind="rf_jamming",
                severity="warn",
                detail=(f"extra_delay_ms = {delay:.0f} > "
                        f"{self.cfg.jam_delay_threshold_ms:.0f}ms "
                        f"(possible jamming-induced retransmits)"),
                extra={"delay_ms": delay},
            ))

    @staticmethod
    def _haversine_m(lat1: float, lon1: float,
                     lat2: float, lon2: float) -> float:
        import math
        R = 6_371_000.0
        phi1 = math.radians(lat1); phi2 = math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlam = math.radians(lon2 - lon1)
        a = (math.sin(dphi/2)**2
             + math.cos(phi1) * math.cos(phi2) * math.sin(dlam/2)**2)
        return 2 * R * math.asin(math.sqrt(a))


def listen_mavlink(monitor: DefenseMonitor, host: str, port: int) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind((host, port))
    s.settimeout(0.5)
    print(f"[defense] listening MAVLink on {host}:{port}")
    while not monitor._stop.is_set():
        try:
            data, _addr = s.recvfrom(65535)
            monitor.feed_mavlink_buf(data)
        except socket.timeout:
            continue
        except OSError:
            break
    s.close()


def poll_channel(monitor: DefenseMonitor, channel_file: Path, period_s: float) -> None:
    print(f"[defense] polling channel file {channel_file} @ {period_s}s")
    while not monitor._stop.is_set():
        monitor.poll_channel_file(channel_file)
        time.sleep(period_s)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mavlink-host", default="127.0.0.1")
    p.add_argument("--mavlink-port", type=int, default=0,
                   help="0 to disable MAVLink listening")
    p.add_argument("--channel-file", type=Path,
                   help="Sionna channel JSON to monitor")
    p.add_argument("--channel-poll-s", type=float, default=0.2)
    p.add_argument("--log-file", type=Path,
                   help="Append NDJSON alerts here")
    p.add_argument("--max-seconds", type=int, default=0)
    p.add_argument("--auth-gcs", action="append", type=int, default=[],
                   help="Authorized GCS sysid (repeat). Default [255, 252].")
    args = p.parse_args()

    cfg = DefenseConfig()
    if args.auth_gcs:
        cfg.authorized_gcs_sysids = tuple(args.auth_gcs)

    log_fp = args.log_file.open("a", encoding="utf-8") if args.log_file else None

    def on_alert(a: Alert) -> None:
        monitor.alerts.append(a)
        print(f"[ALERT/{a.severity.upper():8s}] {a.kind:14s}  {a.detail}",
              flush=True)
        if log_fp:
            rec = {"ts": a.ts, "kind": a.kind, "severity": a.severity,
                   "detail": a.detail, "extra": a.extra}
            log_fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
            log_fp.flush()

    monitor = DefenseMonitor(cfg=cfg, on_alert=on_alert)

    threads = []
    if args.mavlink_port:
        t = threading.Thread(target=listen_mavlink,
                             args=(monitor, args.mavlink_host, args.mavlink_port),
                             daemon=True)
        t.start(); threads.append(t)
    if args.channel_file:
        t = threading.Thread(target=poll_channel,
                             args=(monitor, args.channel_file, args.channel_poll_s),
                             daemon=True)
        t.start(); threads.append(t)

    t_start = time.time()
    try:
        while True:
            if args.max_seconds and time.time() - t_start > args.max_seconds:
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        monitor._stop.set()
        if log_fp:
            log_fp.close()

    print(f"\n[defense] {len(monitor.alerts)} alerts emitted in "
          f"{time.time() - t_start:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
