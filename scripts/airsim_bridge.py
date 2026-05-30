#!/usr/bin/env python3
"""Stage 2.2 — Gazebo ↔ Cosys-AirSim overlay bridge.

Архитектура (per ТЗ): Gazebo физика → AirSim высокодетализированный
визуал + сенсоры. Bridge подсасывает текущую UAV pose из Gazebo
(через events.jsonl flight events которые orchestrator пишет) и
форвардит её в AirSim через `simSetVehiclePose`. AirSim, в свою
очередь, рендерит свои сенсоры (camera, LiDAR, etc.) в той самой
геометрической позиции — итог: physics из ArduPilot+Gazebo,
визуал/сенсоры из Cosys-AirSim.

Pattern из ТЗ DOCX (Андрончев + Федотенков):
  "Gazebo должен использоваться в качестве симулятора физики полета,
   результат моделирования которой должен быть передан в AirSim,
   который используется для высокореалистичного моделирования
   окружающей обстановки и сенсоров БАС."

Реализация bridge:
  1. Tail events.jsonl с тем же patterns что sionna_channel_publisher.
  2. На каждый flight event конвертирует (lat, lon, alt) → AirSim NED
     (x_north, y_east, z_down) через haversine flat approximation.
  3. Вызывает simSetVehiclePose с этим Pose.
  4. Опционально: stream AirSim camera → JPEG файл в logs/ или /tmp,
     с заданной периодичностью (без задержки rendering для каждого
     pose update).
  5. Опционально: pull LiDAR cloud → NDJSON.

Если AirSim endpoint недоступен (no UE5 Editor running) — переходит
в **stub mode**: pose log ведётся локально, но RPC calls пропускаются.
Это нужно для CI smoke тестов где UE5 нет.

Endpoint default `127.0.0.1:41451`; при запуске Cosys-AirSim на
Windows-стороне — пробросить через `--airsim-host=<windows IP>`.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# Импортируем minimal RPC клиент (наш fallback).
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from airsim_client import (   # noqa: E402
    AirSimRpcClient, AirSimRpcError, Pose, Quaternionr, Vector3r,
)

# Опциональный официальный Cosys-AirSim client (~30 RPC methods, image
# helpers, response unpacking). Установлен через
# python_api_client_33.whl с GitHub releases. Используем для get_image
# через стандартный response decoder (PNG/array). Без него — fallback
# на наш минимальный client (только pose forwarding).
try:
    import cosysairsim as _cosys   # type: ignore
    HAS_COSYS = True
except Exception:
    _cosys = None   # type: ignore
    HAS_COSYS = False


# AirSim default home — Canberra (та же что ArduPilot SITL default).
ORIGIN_LAT = -35.363262
ORIGIN_LON = 149.165237


def haversine_xy_m(lat: float, lon: float) -> tuple[float, float]:
    """(lat,lon) → (x_north, y_east) метры от ORIGIN. Точность ±1м в
    радиусе нескольких км — достаточно для AirSim overlay (он сам
    делает финальный rendering)."""
    R = 6_371_000.0
    dlat = math.radians(lat - ORIGIN_LAT)
    dlon = math.radians(lon - ORIGIN_LON)
    x_north = R * dlat
    y_east = R * dlon * math.cos(math.radians(ORIGIN_LAT))
    return x_north, y_east


def yaw_to_quaternion(yaw_rad: float) -> Quaternionr:
    """Z-axis-only yaw → quaternion. Для overlay heading достаточно."""
    return Quaternionr(
        w=math.cos(yaw_rad / 2.0),
        x=0.0, y=0.0,
        z=math.sin(yaw_rad / 2.0),
    )


@dataclass
class BridgeConfig:
    events_path: Path
    log_dir: Path
    airsim_host: str = "127.0.0.1"
    airsim_port: int = 41451
    vehicle_name: str = ""
    rpc_timeout_s: float = 5.0
    pose_log_name: str = "airsim_pose_forward.jsonl"
    image_period_s: float = 2.0
    camera_name: str = "front_center"
    image_dir_name: str = "airsim_camera"
    capture_images: bool = True
    capture_lidar: bool = False
    lidar_name: str = ""
    replay: bool = False
    max_seconds: float = 0.0
    connect_wait_s: float = 90.0   # ретраить подключение к AirSim до N с (cold boot)
    gcs_state_url: str = ""        # если задан — берём live-позу из GCS /api/state
    state_period_s: float = 0.1    # период опроса /api/state


def tail_flight_events(events_path: Path, from_start: bool = False):
    """tail -f → flight events с lat/lon/alt. Pattern идентичен
    sionna_channel_publisher."""
    fp = None
    while True:
        if not events_path.exists():
            time.sleep(0.1)
            continue
        if fp is None:
            fp = events_path.open("r", encoding="utf-8")
            if not from_start:
                fp.seek(0, os.SEEK_END)
        line = fp.readline()
        if not line:
            time.sleep(0.05)
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("event_type") != "flight":
            continue
        pos = ev.get("position", {})
        if "lat" not in pos:
            continue
        yield {
            "wall_time": ev.get("wall_time", time.time()),
            "lat": float(pos["lat"]),
            "lon": float(pos["lon"]),
            "alt_rel_m": float(pos.get("alt_rel_m", 0.0)),
            "yaw_deg": float(pos.get("heading_deg", 0.0)),
        }


def poll_gcs_state(gcs_url: str, period_s: float = 0.1):
    """Опрашивает Web GCS `/api/state` и отдаёт live-позу БАС в том же формате,
    что и tail_flight_events. Это источник РЕАЛЬНОЙ позиции дрона (events.jsonl
    в fpv_rf_demo несёт только команды, а не непрерывную телеметрию). Поле
    `local.{north,east}` — NED от того же origin, что и AirSim → дрон в AirSim
    повторяет полёт на карте GCS."""
    url = gcs_url.rstrip("/") + "/api/state"
    while True:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as resp:
                st = json.loads(resp.read().decode("utf-8"))
        except Exception:
            time.sleep(period_s)
            continue
        loc = st.get("local") or {}
        ev = {
            "wall_time": time.time(),
            "lat": float(st.get("lat_deg", 0.0) or 0.0),
            "lon": float(st.get("lon_deg", 0.0) or 0.0),
            "alt_rel_m": float(st.get("altitude_m", 0.0) or 0.0),
            "yaw_deg": float(st.get("heading_deg", 0.0) or 0.0),
        }
        if "north" in loc and "east" in loc:
            ev["north"] = float(loc["north"])
            ev["east"] = float(loc["east"])
        yield ev
        time.sleep(period_s)


def safe_connect_airsim(cfg: BridgeConfig,
                        quiet: bool = False) -> AirSimRpcClient | None:
    """Try connect; вернуть None если AirSim не запущен (stub mode).

    Если установлен официальный `cosysairsim` package — используем его
    для image decode и расширенных RPC методов. Иначе наш минимальный
    msgpack-rpc client (pose forwarding + ping + scene listing).

    quiet=True подавляет сообщение об отказе (для тихих ретраев).
    """
    try:
        client = AirSimRpcClient(
            host=cfg.airsim_host, port=cfg.airsim_port,
            timeout_s=cfg.rpc_timeout_s,
        )
        client.connect()
        pong = client.ping()
        version = client.get_server_version()
        print(f"[airsim] connected {cfg.airsim_host}:{cfg.airsim_port}"
              f" ping={pong} server_version={version}"
              f" (cosysairsim_pkg={HAS_COSYS})", flush=True)
        try:
            scene = client.list_scene_objects()
            print(f"[airsim] scene objects ({len(scene)}): {scene[:6]}",
                  flush=True)
            if version >= 4:
                print(f"[airsim] detected REAL Cosys-AirSim (server v{version})",
                      flush=True)
        except AirSimRpcError as exc:
            print(f"[airsim] scene listing failed (non-fatal): {exc}",
                  flush=True)
        return client
    except (OSError, AirSimRpcError) as exc:
        if not quiet:
            print(f"[airsim] no AirSim endpoint at {cfg.airsim_host}:{cfg.airsim_port}"
                  f" ({exc}); running in LOCAL POSE-LOG MODE", flush=True)
        return None


def connect_airsim_with_retry(cfg: BridgeConfig,
                              total_wait_s: float = 90.0,
                              interval_s: float = 3.0) -> AirSimRpcClient | None:
    """safe_connect_airsim с ретраями — Cosys-AirSim cold boot ~40-120с.

    Возвращает client при первом успешном подключении; None после
    total_wait_s секунд тихих попыток. Это делает мост устойчивым к
    медленному старту AirSim (UE5 биндит порт не сразу после запуска).
    """
    deadline = time.time() + max(0.0, total_wait_s)
    attempt = 0
    announced = False
    while True:
        attempt += 1
        client = safe_connect_airsim(cfg, quiet=True)
        if client is not None:
            return client
        if time.time() >= deadline:
            print(f"[airsim] no AirSim at {cfg.airsim_host}:{cfg.airsim_port} "
                  f"after {attempt} attempt(s)/{total_wait_s:.0f}s "
                  f"→ LOCAL POSE-LOG MODE (no camera)", flush=True)
            return None
        if not announced:
            print(f"[airsim] AirSim not up yet at {cfg.airsim_host}:{cfg.airsim_port}"
                  f"; retrying up to {total_wait_s:.0f}s (UE5 boot)...", flush=True)
            announced = True
        time.sleep(interval_s)


def forward_pose(
    client: AirSimRpcClient | None,
    event: dict[str, float],
    cfg: BridgeConfig,
) -> dict:
    """Конвертирует event → AirSim Pose; шлёт через RPC если есть.

    Если в event есть готовый NED (north/east из GCS /api/state) — используем
    его напрямую (точное совпадение с картой GCS); иначе считаем из lat/lon.
    """
    if "north" in event and "east" in event:
        x_north, y_east = float(event["north"]), float(event["east"])
    else:
        x_north, y_east = haversine_xy_m(event["lat"], event["lon"])
    alt = event["alt_rel_m"]
    yaw = math.radians(event.get("yaw_deg", 0.0))

    pose = Pose(
        position=Vector3r(x=x_north, y=y_east, z=-alt),   # NED: down positive
        orientation=yaw_to_quaternion(yaw),
    )

    record = {
        "wall_time": event["wall_time"],
        "lat": event["lat"], "lon": event["lon"],
        "alt_rel_m": alt, "yaw_deg": event.get("yaw_deg", 0.0),
        "ned_north": x_north, "ned_east": y_east, "ned_down": -alt,
        "rpc_sent": False,
        "rpc_error": None,
    }

    if client is not None:
        try:
            client.set_vehicle_pose(pose, ignore_collision=True,
                                    vehicle_name=cfg.vehicle_name)
            record["rpc_sent"] = True
        except (AirSimRpcError, OSError) as exc:
            record["rpc_error"] = str(exc)
            # Не падаем — может AirSim рестартанул, продолжаем в stub mode
            # до следующего успешного ping.
    return record


def capture_camera(
    client: AirSimRpcClient,
    cfg: BridgeConfig,
    frame_idx: int,
) -> dict:
    """Пытается снять снапшот камеры. AirSim возвращает PNG/JPG bytes."""
    record = {
        "frame_idx": frame_idx, "wall_time": time.time(),
        "saved": False, "size_bytes": 0,
        "camera_name": cfg.camera_name,
    }
    try:
        data = client.get_image(cfg.camera_name, image_type=0,
                                vehicle_name=cfg.vehicle_name)
    except (AirSimRpcError, OSError) as exc:
        record["error"] = str(exc)
        return record
    if not data:
        record["error"] = "empty image bytes (stub or camera missing)"
        return record
    img_dir = cfg.log_dir / cfg.image_dir_name
    img_dir.mkdir(parents=True, exist_ok=True)
    img_path = img_dir / f"frame_{frame_idx:06d}.bin"
    img_path.write_bytes(data)
    record.update({"saved": True, "size_bytes": len(data),
                   "path": str(img_path.relative_to(cfg.log_dir))})
    return record


def main() -> int:
    ap = argparse.ArgumentParser(description="Gazebo ↔ Cosys-AirSim overlay bridge")
    ap.add_argument("--events", required=True,
                    help="logs/<run>/events.jsonl orchestrator")
    ap.add_argument("--log-dir", required=True,
                    help="Папка прогона для bridge log + camera frames")
    ap.add_argument("--airsim-host", default="127.0.0.1",
                    help="Cosys-AirSim msgpack-rpc host (Windows IP при overlay)")
    ap.add_argument("--airsim-port", type=int, default=41451)
    ap.add_argument("--vehicle-name", default="",
                    help="AirSim multirotor vehicle_name; пусто = default")
    ap.add_argument("--rpc-timeout-s", type=float, default=5.0)
    ap.add_argument("--image-period-s", type=float, default=2.0,
                    help="Как часто захватывать AirSim camera (0 = выкл)")
    # Cosys-AirSim Blocks default cameras: "0"=front_center, "1"=front_right,
    # "front_left", "fpv", "back_center", "bottom_center". У нас тестировано
    # все 7 — все возвращают валидные PNG ~36-57 KB (256x144 RGBA) на
    # Windows GPU.
    ap.add_argument("--camera-name", default="front_center")
    ap.add_argument("--no-images", action="store_true",
                    help="Не запрашивать AirSim camera")
    ap.add_argument("--replay", action="store_true",
                    help="Прочитать events.jsonl с начала (smoke режим)")
    ap.add_argument("--max-seconds", type=float, default=0.0,
                    help="Auto-stop после N секунд (0 = forever)")
    ap.add_argument("--airsim-connect-wait", type=float,
                    default=float(os.environ.get("BAS_AIRSIM_CONNECT_WAIT", "90")),
                    help="Ретраить подключение к AirSim до N секунд (UE5 cold "
                         "boot ~40-120с; 0 = одна попытка)")
    ap.add_argument("--gcs-state-url",
                    default=os.environ.get("BAS_GCS_STATE_URL", ""),
                    help="Брать live-позу из Web GCS /api/state (напр. "
                         "http://127.0.0.1:8765). Если задан — приоритетнее "
                         "events.jsonl (реальная телеметрия дрона).")
    args = ap.parse_args()

    cfg = BridgeConfig(
        events_path=Path(args.events),
        log_dir=Path(args.log_dir).resolve(),
        airsim_host=args.airsim_host,
        airsim_port=args.airsim_port,
        vehicle_name=args.vehicle_name,
        rpc_timeout_s=args.rpc_timeout_s,
        image_period_s=args.image_period_s,
        camera_name=args.camera_name,
        capture_images=not args.no_images,
        replay=args.replay,
        max_seconds=args.max_seconds,
        connect_wait_s=args.airsim_connect_wait,
        gcs_state_url=args.gcs_state_url,
    )
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    pose_log = cfg.log_dir / cfg.pose_log_name
    pose_log.write_text("")   # truncate

    client = connect_airsim_with_retry(cfg, total_wait_s=cfg.connect_wait_s)
    mode_label = "RPC" if client is not None else "STUB (no AirSim)"
    print(f"[airsim-bridge] mode={mode_label}, log_dir={cfg.log_dir}",
          flush=True)

    start = time.time()
    frame_idx = 0
    last_image_t = 0.0
    pose_count = 0

    if cfg.gcs_state_url:
        print(f"[airsim-bridge] pose source: GCS {cfg.gcs_state_url}/api/state",
              flush=True)
        source = poll_gcs_state(cfg.gcs_state_url, cfg.state_period_s)
    else:
        print(f"[airsim-bridge] pose source: events.jsonl ({cfg.events_path})",
              flush=True)
        source = tail_flight_events(cfg.events_path, from_start=cfg.replay)

    try:
        with pose_log.open("a", encoding="utf-8") as poses_fp:
            for ev in source:
                record = forward_pose(client, ev, cfg)
                poses_fp.write(json.dumps(record, separators=(",", ":")) + "\n")
                poses_fp.flush()
                pose_count += 1

                # Periodic camera snapshot, только если RPC работает.
                now = time.time()
                if (client is not None and cfg.capture_images
                        and cfg.image_period_s > 0
                        and now - last_image_t >= cfg.image_period_s):
                    cap = capture_camera(client, cfg, frame_idx)
                    print(f"[airsim] camera frame {frame_idx}: "
                          f"saved={cap.get('saved')} "
                          f"size={cap.get('size_bytes', 0)}",
                          flush=True)
                    frame_idx += 1
                    last_image_t = now

                if cfg.max_seconds and (now - start) > cfg.max_seconds:
                    print(f"[airsim-bridge] max-seconds reached "
                          f"({pose_count} poses forwarded)", flush=True)
                    break
    finally:
        if client is not None:
            client.close()
        print(f"[airsim-bridge] done; pose_count={pose_count}",
              flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
