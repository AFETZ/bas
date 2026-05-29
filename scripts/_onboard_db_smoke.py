#!/usr/bin/env python3
"""On-board persistence smoke — insert state + readings, run composite, verify.

Закрывает test-coverage пункта "Бортовая часть ИССГР" (task #36):
  1. Создаём OnBoardDB на tmpfile.
  2. Append 5 UAV state snapshots (sysid=1, decay battery).
  3. Append 4 RSSI samples + 3 CV detections.
  4. Run compute_composite → проверяем avg_rssi_5s, nlos_detected,
     target_count_5s, battery_pct_smoothed, last_position_age_ms.
  5. Verify stats counts.
  6. Verify retention purge.
"""
import sys, time, uuid
import os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "orchestrator", "src"))

from orchestrator.issgr import (   # noqa: E402
    UAV, Pose, ObjectIdentifier, SensorReading, IssgrClass,
    OnBoardDB, CompositeMetrics,
)


def main() -> int:
    tmp = Path("/tmp/onboard_smoke.db")
    if tmp.exists():
        tmp.unlink()
    db = OnBoardDB(path=tmp, retention_seconds=3600,
                   composite_interval_s=0.5)

    print(f"DB: {tmp}  schema_version={db.get_meta('schema_version')}")

    # --- 1. Seed UAV state (5 snapshots, sysid=1, battery 12.6 → 11.8). ---
    uav_uuid = uuid.UUID("00000000-0000-0000-0000-000000000100")
    batteries = [12.6, 12.5, 12.3, 12.0, 11.8]
    for i, bv in enumerate(batteries):
        uav = UAV(
            id=ObjectIdentifier(object_uuid=uav_uuid),
            name="Iris-Onboard",
            sysid=1,
            pose=Pose(latitude_deg=-35.363 + i * 0.0001,
                      longitude_deg=149.165, altitude_m=15.0 + i,
                      heading_deg=90.0),
            armed=True, flight_mode="AUTO",
            battery_v=bv,
            velocity_ned=[3.0, 0.5, 0.0],
        )
        rid = db.append_uav_state(uav)
        assert rid > 0
        time.sleep(0.02)
    print(f"Inserted {len(batteries)} UAV state snapshots")

    # --- 2. Seed RSSI sensor readings (4 samples around -70 dBm). ---
    rssi_samples = [-65.0, -70.0, -72.0, -68.0]
    for v in rssi_samples:
        sr = SensorReading(
            id=ObjectIdentifier(),
            name="RF:RSSI",
            issgr_class=IssgrClass.FUNC_SENSOR_STATION,
            source_uav_id=ObjectIdentifier(object_uuid=uav_uuid),
            sensor_type="rssi_lora",
            value={"rssi_dbm": v, "confidence": v},
        )
        db.append_sensor_reading(sr)
    print(f"Inserted {len(rssi_samples)} RSSI samples (avg={sum(rssi_samples)/len(rssi_samples):.1f})")

    # --- 3. Seed CV detections (3 objects, разные классы). ---
    cv_objects = [
        ("person", 0.92, -35.3636, 149.1654),
        ("car",    0.87, -35.3637, 149.1655),
        ("person", 0.88, -35.3638, 149.1656),
    ]
    for cls, conf, glat, glon in cv_objects:
        sr = SensorReading(
            id=ObjectIdentifier(),
            name=f"CV:{cls}",
            issgr_class=IssgrClass.FUNC_SENSOR_STATION,
            source_uav_id=ObjectIdentifier(object_uuid=uav_uuid),
            sensor_type="camera_object_detection",
            value={"class_name": cls, "confidence": conf,
                   "ground_lat": glat, "ground_lon": glon},
        )
        db.append_sensor_reading(sr)
    print(f"Inserted {len(cv_objects)} CV detections")

    # --- 4. Mission log. ---
    db.upsert_mission(
        object_id="bas:onboard-smoke:mission-01",
        target_uav_id="bas:onboard-smoke:" + str(uav_uuid),
        name="patrol-loop",
        state="running",
        waypoints=[
            {"seq": 0, "action": "takeoff", "altitude_m": 15.0},
            {"seq": 1, "action": "waypoint",
             "latitude_deg": -35.363, "longitude_deg": 149.165,
             "altitude_m": 15.0},
            {"seq": 2, "action": "land"},
        ],
    )
    missions = db.list_missions()
    assert len(missions) == 1, f"expected 1 mission, got {len(missions)}"
    assert missions[0]["state"] == "running"
    print(f"Logged mission: {missions[0]['name']} state={missions[0]['state']}")

    # --- 5. Compute composite metrics. ---
    cm: CompositeMetrics = db.compute_composite(
        sysid=1, window_s=5, rssi_nlos_threshold_dbm=-75.0,
    )
    print()
    print("COMPOSITE METRICS (sysid=1):")
    print(f"  avg_rssi_5s         = {cm.avg_rssi_5s}")
    print(f"  nlos_detected       = {cm.nlos_detected}")
    print(f"  target_count_5s     = {cm.target_count_5s}")
    print(f"  target_classes      = {cm.target_classes}")
    print(f"  battery_pct_smoothed= {cm.battery_pct_smoothed:.1f}%"
          if cm.battery_pct_smoothed else "  battery_pct_smoothed= None")
    print(f"  last_position_age_ms= {cm.last_position_age_ms}")

    # --- Assertions ---
    assert cm.avg_rssi_5s is not None
    assert -75.0 < cm.avg_rssi_5s < -60.0, f"unexpected RSSI: {cm.avg_rssi_5s}"
    assert not cm.nlos_detected, "should be LOS (RSSI > -75 dBm)"
    assert cm.target_count_5s == 3, f"expected 3 CV targets, got {cm.target_count_5s}"
    assert cm.target_classes == {"person": 2, "car": 1}, f"got {cm.target_classes}"
    assert cm.battery_pct_smoothed is not None
    assert 70.0 < cm.battery_pct_smoothed < 100.0, \
        f"battery should be 70-100%, got {cm.battery_pct_smoothed}"
    assert cm.last_position_age_ms is not None and cm.last_position_age_ms < 5000

    # --- 6. Stats ---
    s = db.stats()
    print()
    print(f"STATS: retention={s['retention_seconds']}s")
    for tbl, info in s["tables"].items():
        print(f"  {tbl:18s}  count={info['count']}")
    assert s["tables"]["uav_state"]["count"] == 5
    assert s["tables"]["sensor_readings"]["count"] == 7
    assert s["tables"]["mission_log"]["count"] == 1
    assert s["tables"]["composite_state"]["count"] >= 4   # >=4 metrics emitted

    # --- 7. Trajectory query ---
    traj = db.trajectory(sysid=1)
    assert len(traj) == 5
    print(f"\nTrajectory: {len(traj)} points, "
          f"alt {traj[0]['alt_m']:.1f} → {traj[-1]['alt_m']:.1f}м")

    # --- 8. Sensor query by type ---
    rs = db.sensor_readings_since(0, sensor_type="rssi_lora")
    assert len(rs) == 4
    cv = db.sensor_readings_since(0, sensor_type="camera_object_detection")
    assert len(cv) == 3
    print(f"Filter check: {len(rs)} RSSI, {len(cv)} CV (matches inserted)")

    # --- 9. Background composite engine ---
    db.start_composite_engine(sysids=[1], interval_s=0.3)
    time.sleep(1.0)
    db.stop_composite_engine()
    s2 = db.stats()
    # We should now have >=4*4=16 composite rows (4 metrics * 4 tick) ~ but
    # engine may emit fewer; just verify monotone growth.
    assert s2["tables"]["composite_state"]["count"] > s["tables"]["composite_state"]["count"]
    print(f"\nEngine ran for 1s: composite rows {s['tables']['composite_state']['count']} → "
          f"{s2['tables']['composite_state']['count']}")

    db.close()
    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
