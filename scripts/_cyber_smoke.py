#!/usr/bin/env python3
"""Cyber attack ↔ defense smoke — verify все 3 attack types triggered alerts.

Сценарий:
  1. Запустить DefenseMonitor в фоне на UDP :17550 + watch /tmp/_smoke_chan.json
  2. Сначала seed нормальный baseline position для sysid=1 (без alert).
  3. Run gps_spoof → expect "gps_spoof" alert (jump 200м).
  4. Run cmd_inject → expect "cmd_injection" alert (sysid=254 unauthorized).
  5. Run rf_jam → expect "rf_jamming" alert (RSSI -110 dBm < -90 threshold).
  6. Verify all 3 alert types появились в monitor.alerts.
"""
import json
import socket
import struct
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, "/home/afetz/bas-prototype/scripts")

from cyber_defense_monitor import (   # noqa: E402
    DefenseMonitor, DefenseConfig, listen_mavlink, poll_channel,
)
from cyber_attack_simulator import (   # noqa: E402
    GpsSpoofConfig, CmdInjectConfig, RfJamConfig,
    run_gps_spoof, run_cmd_inject, run_rf_jamming,
    _mavlink_v2_frame, _pack_global_position_int, MSG_GLOBAL_POSITION_INT,
)


PORT = 17550
CHAN = Path("/tmp/_smoke_chan.json")


def seed_baseline_position(sysid: int = 1) -> None:
    """Шлём один нормальный GLOBAL_POSITION_INT чтобы у monitor было
    previous position для последующей jump detection."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    payload = _pack_global_position_int(
        time_boot_ms=0,
        lat_deg=-35.363262, lon_deg=149.165237,
        alt_m=50.0, vx_ms=0, vy_ms=0, vz_ms=0, hdg_deg=0,
    )
    frame = _mavlink_v2_frame(
        sysid=sysid, compid=1, msgid=MSG_GLOBAL_POSITION_INT, payload=payload,
    )
    s.sendto(frame, ("127.0.0.1", PORT))
    s.close()


def main() -> int:
    if CHAN.exists():
        CHAN.unlink()
    CHAN.write_text(json.dumps({"loss_ratio": 0.01, "rssi_dbm": -60.0,
                                "extra_delay_ms": 5, "ts": time.time(),
                                "source": "baseline"}))

    cfg = DefenseConfig(
        position_jump_threshold_m=100.0,
        jump_window_ms=3000.0,
        authorized_gcs_sysids=(255, 252),
        jam_rssi_threshold_dbm=-90.0,
        jam_consecutive_polls=2,
    )
    monitor = DefenseMonitor(cfg=cfg)

    t_mav = threading.Thread(
        target=listen_mavlink, args=(monitor, "127.0.0.1", PORT), daemon=True)
    t_chan = threading.Thread(
        target=poll_channel, args=(monitor, CHAN, 0.1), daemon=True)
    t_mav.start(); t_chan.start()
    time.sleep(0.3)
    print(f"==> defense monitor up on :{PORT}, polling {CHAN}")

    # --- Baseline ---
    print("\n[1] Send baseline GLOBAL_POSITION_INT (sysid=1, normal position)")
    seed_baseline_position(sysid=1)
    time.sleep(0.5)
    base_n = len(monitor.alerts)
    print(f"    alerts after baseline: {base_n} (expected 0)")
    assert base_n == 0, f"baseline triggered {base_n} alerts: {monitor.alerts}"

    # --- GPS spoof ---
    print("\n[2] Run gps_spoof (sysid=1, offset +200m N/E for 2s)")
    spoof_cfg = GpsSpoofConfig(
        target_host="127.0.0.1", target_port=PORT,
        rate_hz=5.0, sysid=1,
        spoof_offset_lat_m=200.0, spoof_offset_lon_m=200.0,
    )
    spoof_result = run_gps_spoof(spoof_cfg, duration_s=2.0)
    print(f"    {spoof_result}")
    time.sleep(0.5)
    spoof_alerts = [a for a in monitor.alerts if a.kind == "gps_spoof"]
    print(f"    gps_spoof alerts: {len(spoof_alerts)}")
    assert len(spoof_alerts) >= 1, "no GPS spoof alert raised"

    # --- Cmd injection ---
    print("\n[3] Run cmd_inject (fake sysid=254, COMMAND_LONG → SET_MODE)")
    inject_cfg = CmdInjectConfig(
        target_host="127.0.0.1", target_port=PORT,
        target_sysid=1, fake_sysid=254,
        command=176, p1=1.0, p2=9.0,
    )
    inject_result = run_cmd_inject(inject_cfg, n_injections=3)
    print(f"    {inject_result}")
    time.sleep(0.5)
    cmd_alerts = [a for a in monitor.alerts if a.kind == "cmd_injection"]
    print(f"    cmd_injection alerts: {len(cmd_alerts)}")
    assert len(cmd_alerts) >= 3, f"expected ≥3 cmd_inject alerts, got {len(cmd_alerts)}"

    # --- RF jamming ---
    print("\n[4] Run rf_jam (loss=0.95, rssi=-110 dBm for 1s)")
    jam_cfg = RfJamConfig(
        channel_file=CHAN, duration_s=1.0,
        jam_loss_ratio=0.95, jam_extra_delay_ms=500.0,
        jam_rssi_dbm=-110.0,
    )
    jam_result = run_rf_jamming(jam_cfg)
    print(f"    {jam_result}")
    time.sleep(0.5)
    jam_alerts = [a for a in monitor.alerts if a.kind == "rf_jamming"]
    print(f"    rf_jamming alerts: {len(jam_alerts)}")
    assert len(jam_alerts) >= 1, "no RF jamming alert raised"

    # --- Summary ---
    print(f"\n=== Total alerts: {len(monitor.alerts)} ===")
    by_kind: dict[str, int] = {}
    for a in monitor.alerts:
        by_kind[a.kind] = by_kind.get(a.kind, 0) + 1
    print(f"    by kind: {by_kind}")
    assert "gps_spoof" in by_kind
    assert "cmd_injection" in by_kind
    assert "rf_jamming" in by_kind

    monitor._stop.set()
    time.sleep(0.5)
    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
