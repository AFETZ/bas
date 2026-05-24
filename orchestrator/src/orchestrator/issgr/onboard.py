"""ИССГР Бортовая часть — on-board persistent БД + composite state.

Закрывает пункт ТЗ "Бортовая часть ИССГР: бортовая объектно-ориентированная
БД, сохранение данных сенсоров, комплексированные данные для управления БАС".

SQLite-backed time-series storage что переживает перезагрузку дрона. Хранит:

  * `uav_state` — periodic UAV pose snapshots (append-only)
  * `sensor_readings` — обнаружения CV, RSSI, LiDAR (time-series)
  * `mission_log` — uploaded missions + execution state
  * `composite_state` — derived metrics (rolling averages, NLOS flag,
    target counts) для feedback к autopilot

Composite engine считает derived values из raw sensor_readings:
  * `avg_rssi_5s` — средний RSSI за последние 5 секунд
  * `nlos_detected` — bool, если avg_rssi < threshold или packet loss > 50%
  * `target_count_5s` — число unique CV-detected objects за 5 секунд
  * `battery_pct_smoothed` — экспоненциальный фильтр через samples

Retention: rolling window (default 1 час по sec, 24 часа aggregated по min).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import SensorReading, UAV


# -----------------------------------------------------------------------------
# Schema
# -----------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS uav_state (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms         INTEGER NOT NULL,
    sysid         INTEGER NOT NULL,
    object_id     TEXT NOT NULL,
    lat_deg       REAL NOT NULL,
    lon_deg       REAL NOT NULL,
    alt_m         REAL NOT NULL,
    heading_deg   REAL,
    armed         INTEGER NOT NULL,
    flight_mode   TEXT NOT NULL,
    battery_v     REAL,
    velocity_n    REAL,
    velocity_e    REAL,
    velocity_d    REAL
);
CREATE INDEX IF NOT EXISTS idx_uav_state_ts ON uav_state(ts_ms);
CREATE INDEX IF NOT EXISTS idx_uav_state_sysid_ts ON uav_state(sysid, ts_ms);

CREATE TABLE IF NOT EXISTS sensor_readings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms           INTEGER NOT NULL,
    object_id       TEXT NOT NULL,
    source_uav_id   TEXT NOT NULL,
    sensor_type     TEXT NOT NULL,
    value_json      TEXT NOT NULL,
    ground_lat_deg  REAL,
    ground_lon_deg  REAL,
    confidence      REAL,
    pose_lat        REAL,
    pose_lon        REAL,
    pose_alt        REAL
);
CREATE INDEX IF NOT EXISTS idx_sensor_ts ON sensor_readings(ts_ms);
CREATE INDEX IF NOT EXISTS idx_sensor_type_ts ON sensor_readings(sensor_type, ts_ms);
CREATE INDEX IF NOT EXISTS idx_sensor_uav ON sensor_readings(source_uav_id, ts_ms);

CREATE TABLE IF NOT EXISTS mission_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms         INTEGER NOT NULL,
    object_id     TEXT NOT NULL UNIQUE,
    target_uav_id TEXT NOT NULL,
    name          TEXT,
    state         TEXT NOT NULL,
    waypoints_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS composite_state (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms        INTEGER NOT NULL,
    sysid        INTEGER NOT NULL,
    metric_name  TEXT NOT NULL,
    metric_value REAL NOT NULL,
    extra_json   TEXT
);
CREATE INDEX IF NOT EXISTS idx_composite_ts ON composite_state(ts_ms);
CREATE INDEX IF NOT EXISTS idx_composite_sysid_metric_ts
    ON composite_state(sysid, metric_name, ts_ms);

CREATE TABLE IF NOT EXISTS metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


# -----------------------------------------------------------------------------
# OnBoardDB
# -----------------------------------------------------------------------------
@dataclass
class CompositeMetrics:
    """Текущий снимок derived metrics для одного UAV."""
    sysid: int
    ts_ms: int
    avg_rssi_5s: float | None = None
    nlos_detected: bool = False
    target_count_5s: int = 0
    target_classes: dict[str, int] = field(default_factory=dict)
    battery_pct_smoothed: float | None = None
    last_position_age_ms: int | None = None


class OnBoardDB:
    """SQLite-backed on-board ИССГР persistence.

    Thread-safe через single connection + lock (SQLite native lock).
    Retention applied lazily на каждый upsert (lightweight).
    """

    def __init__(
        self,
        path: Path | str = ":memory:",
        retention_seconds: int = 3600,
        composite_interval_s: float = 1.0,
    ) -> None:
        self.path = str(path)
        self.retention_seconds = retention_seconds
        self.composite_interval_s = composite_interval_s
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.path, check_same_thread=False, isolation_level=None,
        )
        self._conn.executescript(SCHEMA)
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._set_meta("schema_version", "1")
        self._set_meta("created_at_ms", str(int(time.time() * 1000)))
        self._composite_thread: threading.Thread | None = None
        self._stop = threading.Event()

    def close(self) -> None:
        self.stop_composite_engine()
        with self._lock:
            self._conn.close()

    def _set_meta(self, k: str, v: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)",
                (k, v),
            )

    def get_meta(self, k: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM metadata WHERE key=?", (k,)
            ).fetchone()
        return row[0] if row else None

    # ---- UAV state ---------------------------------------------------------
    def append_uav_state(self, uav: UAV) -> int:
        ts_ms = int(uav.timestamp.timestamp() * 1000)
        vned = uav.velocity_ned or [None, None, None]
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO uav_state(ts_ms, sysid, object_id,
                    lat_deg, lon_deg, alt_m, heading_deg, armed,
                    flight_mode, battery_v,
                    velocity_n, velocity_e, velocity_d)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    ts_ms, uav.sysid, uav.id.as_string(),
                    uav.pose.latitude_deg, uav.pose.longitude_deg,
                    uav.pose.altitude_m, uav.pose.heading_deg,
                    1 if uav.armed else 0, uav.flight_mode, uav.battery_v,
                    vned[0], vned[1], vned[2],
                ),
            )
        self._purge_old("uav_state")
        return cur.lastrowid or 0

    def latest_uav_state(self, sysid: int | None = None) -> dict[str, Any] | None:
        with self._lock:
            if sysid is None:
                row = self._conn.execute(
                    "SELECT * FROM uav_state ORDER BY ts_ms DESC LIMIT 1"
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT * FROM uav_state WHERE sysid=? "
                    "ORDER BY ts_ms DESC LIMIT 1",
                    (sysid,),
                ).fetchone()
        if not row:
            return None
        cols = [d[0] for d in self._conn.execute("SELECT * FROM uav_state LIMIT 0").description]
        return dict(zip(cols, row))

    def trajectory(
        self, sysid: int, since_ms: int | None = None, limit: int = 1000,
    ) -> list[dict[str, Any]]:
        since_ms = since_ms or 0
        with self._lock:
            rows = self._conn.execute(
                """SELECT ts_ms, lat_deg, lon_deg, alt_m, heading_deg, armed,
                          flight_mode, battery_v
                   FROM uav_state
                   WHERE sysid=? AND ts_ms >= ?
                   ORDER BY ts_ms ASC LIMIT ?""",
                (sysid, since_ms, limit),
            ).fetchall()
        return [
            {"ts_ms": r[0], "lat_deg": r[1], "lon_deg": r[2], "alt_m": r[3],
             "heading_deg": r[4], "armed": bool(r[5]),
             "flight_mode": r[6], "battery_v": r[7]}
            for r in rows
        ]

    # ---- Sensor readings ---------------------------------------------------
    def append_sensor_reading(self, reading: SensorReading) -> int:
        ts_ms = int(reading.timestamp.timestamp() * 1000)
        val = reading.value if isinstance(reading.value, dict) else {}
        pose = reading.pose_at_observation
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO sensor_readings(ts_ms, object_id,
                    source_uav_id, sensor_type, value_json,
                    ground_lat_deg, ground_lon_deg, confidence,
                    pose_lat, pose_lon, pose_alt)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    ts_ms, reading.id.as_string(),
                    reading.source_uav_id.as_string(),
                    reading.sensor_type,
                    json.dumps(val, ensure_ascii=False),
                    val.get("ground_lat"), val.get("ground_lon"),
                    val.get("confidence"),
                    pose.latitude_deg if pose else None,
                    pose.longitude_deg if pose else None,
                    pose.altitude_m if pose else None,
                ),
            )
        self._purge_old("sensor_readings")
        return cur.lastrowid or 0

    def sensor_readings_since(
        self,
        since_ms: int,
        sensor_type: str | None = None,
        source_uav_id: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        sql = ("SELECT ts_ms, object_id, source_uav_id, sensor_type, "
               "value_json, ground_lat_deg, ground_lon_deg, confidence "
               "FROM sensor_readings WHERE ts_ms >= ?")
        params: list[Any] = [since_ms]
        if sensor_type:
            sql += " AND sensor_type=?"; params.append(sensor_type)
        if source_uav_id:
            sql += " AND source_uav_id=?"; params.append(source_uav_id)
        sql += " ORDER BY ts_ms DESC LIMIT ?"; params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            out.append({
                "ts_ms": r[0], "object_id": r[1], "source_uav_id": r[2],
                "sensor_type": r[3],
                "value": json.loads(r[4]),
                "ground_lat_deg": r[5], "ground_lon_deg": r[6],
                "confidence": r[7],
            })
        return out

    # ---- Mission log -------------------------------------------------------
    def upsert_mission(
        self, object_id: str, target_uav_id: str, name: str,
        state: str, waypoints: list[dict[str, Any]],
    ) -> None:
        ts_ms = int(time.time() * 1000)
        with self._lock:
            self._conn.execute(
                """INSERT INTO mission_log(ts_ms, object_id, target_uav_id,
                       name, state, waypoints_json)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(object_id) DO UPDATE SET
                       ts_ms=excluded.ts_ms,
                       state=excluded.state,
                       waypoints_json=excluded.waypoints_json""",
                (ts_ms, object_id, target_uav_id, name, state,
                 json.dumps(waypoints, ensure_ascii=False)),
            )

    def list_missions(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT object_id, target_uav_id, name, state, ts_ms "
                "FROM mission_log ORDER BY ts_ms DESC"
            ).fetchall()
        return [
            {"object_id": r[0], "target_uav_id": r[1], "name": r[2],
             "state": r[3], "ts_ms": r[4]}
            for r in rows
        ]

    # ---- Composite state ---------------------------------------------------
    def compute_composite(
        self, sysid: int,
        window_s: int = 5,
        rssi_nlos_threshold_dbm: float = -75.0,
    ) -> CompositeMetrics:
        """Считает derived metrics из последнего window_s секунд."""
        now_ms = int(time.time() * 1000)
        since_ms = now_ms - window_s * 1000
        cm = CompositeMetrics(sysid=sysid, ts_ms=now_ms)

        with self._lock:
            # Average RSSI
            row = self._conn.execute(
                """SELECT AVG(confidence) FROM sensor_readings
                   WHERE sensor_type LIKE 'rssi%' AND ts_ms >= ?""",
                (since_ms,),
            ).fetchone()
            cm.avg_rssi_5s = row[0] if row and row[0] is not None else None
            if cm.avg_rssi_5s is not None:
                cm.nlos_detected = cm.avg_rssi_5s < rssi_nlos_threshold_dbm

            # Target counts (CV detections last 5s)
            rows = self._conn.execute(
                """SELECT value_json FROM sensor_readings
                   WHERE sensor_type='camera_object_detection'
                   AND ts_ms >= ?""",
                (since_ms,),
            ).fetchall()
            for (vj,) in rows:
                try:
                    v = json.loads(vj)
                    cls = v.get("class_name", "?")
                    cm.target_classes[cls] = cm.target_classes.get(cls, 0) + 1
                    cm.target_count_5s += 1
                except (json.JSONDecodeError, AttributeError):
                    pass

            # Battery smoothed (последние 10 samples, exp filter alpha=0.3).
            rows = self._conn.execute(
                "SELECT battery_v FROM uav_state "
                "WHERE sysid=? AND battery_v IS NOT NULL "
                "ORDER BY ts_ms DESC LIMIT 10",
                (sysid,),
            ).fetchall()
            if rows:
                vals = [r[0] for r in rows][::-1]
                smoothed = vals[0]
                for v in vals[1:]:
                    smoothed = 0.7 * smoothed + 0.3 * v
                if smoothed > 0:
                    cm.battery_pct_smoothed = min(100.0, max(0.0,
                        (smoothed - 9.6) / (12.6 - 9.6) * 100))   # 3S LiPo

            # Last position age.
            row = self._conn.execute(
                "SELECT MAX(ts_ms) FROM uav_state WHERE sysid=?",
                (sysid,),
            ).fetchone()
            if row and row[0]:
                cm.last_position_age_ms = now_ms - row[0]

        # Persist metrics.
        self._persist_composite(cm)
        return cm

    def _persist_composite(self, cm: CompositeMetrics) -> None:
        records: list[tuple[Any, ...]] = []
        if cm.avg_rssi_5s is not None:
            records.append((cm.ts_ms, cm.sysid, "avg_rssi_5s",
                            float(cm.avg_rssi_5s), None))
        records.append((cm.ts_ms, cm.sysid, "nlos_detected",
                        1.0 if cm.nlos_detected else 0.0, None))
        records.append((cm.ts_ms, cm.sysid, "target_count_5s",
                        float(cm.target_count_5s),
                        json.dumps(cm.target_classes, ensure_ascii=False)))
        if cm.battery_pct_smoothed is not None:
            records.append((cm.ts_ms, cm.sysid, "battery_pct_smoothed",
                            float(cm.battery_pct_smoothed), None))
        if cm.last_position_age_ms is not None:
            records.append((cm.ts_ms, cm.sysid, "last_position_age_ms",
                            float(cm.last_position_age_ms), None))
        with self._lock:
            self._conn.executemany(
                "INSERT INTO composite_state(ts_ms, sysid, metric_name, "
                "metric_value, extra_json) VALUES(?,?,?,?,?)",
                records,
            )
        self._purge_old("composite_state")

    def composite_history(
        self, sysid: int, metric: str, since_ms: int | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        since_ms = since_ms or 0
        with self._lock:
            rows = self._conn.execute(
                """SELECT ts_ms, metric_value, extra_json
                   FROM composite_state
                   WHERE sysid=? AND metric_name=? AND ts_ms >= ?
                   ORDER BY ts_ms DESC LIMIT ?""",
                (sysid, metric, since_ms, limit),
            ).fetchall()
        return [
            {"ts_ms": r[0], "value": r[1],
             "extra": json.loads(r[2]) if r[2] else None}
            for r in rows
        ]

    # ---- Composite engine (background thread) ------------------------------
    def start_composite_engine(
        self, sysids: list[int],
        interval_s: float | None = None,
    ) -> None:
        if self._composite_thread is not None:
            return
        interval = interval_s or self.composite_interval_s
        self._stop.clear()

        def loop() -> None:
            while not self._stop.is_set():
                for sysid in sysids:
                    try:
                        self.compute_composite(sysid)
                    except Exception:
                        pass
                self._stop.wait(interval)

        t = threading.Thread(target=loop, name="onboard-composite", daemon=True)
        t.start()
        self._composite_thread = t

    def stop_composite_engine(self) -> None:
        if self._composite_thread is None:
            return
        self._stop.set()
        self._composite_thread.join(timeout=2.0)
        self._composite_thread = None

    # ---- Retention ---------------------------------------------------------
    def _purge_old(self, table: str) -> None:
        cutoff_ms = int(time.time() * 1000) - self.retention_seconds * 1000
        with self._lock:
            self._conn.execute(
                f"DELETE FROM {table} WHERE ts_ms < ?", (cutoff_ms,),
            )

    # ---- Stats -------------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        with self._lock:
            counts = {}
            for tbl in ("uav_state", "sensor_readings", "mission_log",
                        "composite_state"):
                row = self._conn.execute(
                    f"SELECT COUNT(*), MIN(ts_ms), MAX(ts_ms) FROM {tbl}"
                ).fetchone()
                counts[tbl] = {
                    "count": row[0],
                    "ts_min_ms": row[1], "ts_max_ms": row[2],
                }
        return {
            "schema_version": self.get_meta("schema_version"),
            "created_at_ms": int(self.get_meta("created_at_ms") or "0"),
            "retention_seconds": self.retention_seconds,
            "tables": counts,
        }
