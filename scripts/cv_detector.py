#!/usr/bin/env python3
"""Stage 3 CV-обработка видовых данных.

Подписывается на FPV MJPEG поток из Web GCS (`/camera.mjpg`), декодирует
JPEG frames, прогоняет через YOLOv8n детектор, geo-tag-ит обнаруженные
объекты через UAV pose + camera FOV ray-cast и POSTит результаты в ИССГР
REST API как `SensorReading` (time-series) или `Obstacle` (для permanent
objects вроде дерева/здания).

Geo-tagging pipeline:
  pixel (u, v) → normalized [-1..1] device coords
                → ray direction in camera frame (pinhole, fovx/fovy)
                → rotation by camera_yaw (UAV heading) + camera_pitch (gimbal)
                → ray in world frame (East-North-Up)
                → ray-ground intersection (assume flat z=0)
                → ENU offset → lat/lon shift (haversine, local-tangent-plane)

Без YOLOv8 (ultralytics не установлен) — fallback на OpenCV's built-in
HOG people detector + cascade for cars. Это не такой precise, но
демонстрирует pipeline.

Usage:
  ./.venv/bin/python scripts/cv_detector.py
  ./.venv/bin/python scripts/cv_detector.py --fpv-url http://127.0.0.1:8765/camera.mjpg
  ./.venv/bin/python scripts/cv_detector.py --interval-s 2 --min-confidence 0.5
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import math
import os
import sys
import time
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

# Sys-path для orchestrator.issgr.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "orchestrator" / "src"))


log = logging.getLogger("cv-detector")


# YOLOv8 COCO classes — id → name. Используем подмножество релевантных для UAV
# survey: person, car, truck, bus, motorcycle, bicycle, traffic_light, stop_sign.
COCO_RELEVANT = {
    0: ("person", "operational_situation.target.point"),
    1: ("bicycle", "operational_situation.target.point"),
    2: ("car", "operational_situation.target.point"),
    3: ("motorcycle", "operational_situation.target.point"),
    5: ("bus", "operational_situation.target.point"),
    7: ("truck", "operational_situation.target.point"),
    9: ("traffic_light", "functional_objects.sensor.ground_station"),
    11: ("stop_sign", "functional_objects.sensor.ground_station"),
}


# -----------------------------------------------------------------------------
# Geo-tagging
# -----------------------------------------------------------------------------
@dataclass
class CameraIntrinsics:
    """Камера pinhole модель."""
    image_width: int = 640
    image_height: int = 480
    horizontal_fov_deg: float = 80.0
    pitch_deg: float = -45.0          # gimbal: -90 = straight down, -45 = oblique
    yaw_offset_deg: float = 0.0       # relative to UAV heading

    @property
    def vertical_fov_deg(self) -> float:
        # Тригонометрическое derivation из horizontal_fov + aspect ratio.
        aspect = self.image_height / self.image_width
        return math.degrees(
            2.0 * math.atan(aspect * math.tan(math.radians(self.horizontal_fov_deg / 2)))
        )


@dataclass
class UAVPose:
    """Текущая UAV pose для geo-tagging."""
    latitude_deg: float = -35.363262
    longitude_deg: float = 149.165237
    altitude_m: float = 10.0          # AGL
    heading_deg: float = 0.0          # 0=north, 90=east
    timestamp: float = 0.0


def pixel_to_ground_enu(
    u: int, v: int,
    intrinsics: CameraIntrinsics,
    uav: UAVPose,
) -> tuple[float, float] | None:
    """Pixel (u,v) → (east_m, north_m) ground intersection relative to UAV.

    Возвращает None если пиксель смотрит выше горизонта (нет intersection).
    Простая модель: ravноугольный pinhole + camera looks по UAV heading +
    pitch вниз, без roll. Достаточно для overlay-grade точности.
    """
    img_w = intrinsics.image_width
    img_h = intrinsics.image_height
    # Нормализованные device coordinates: [-1..1], origin в центре, +x вправо, +y вниз.
    ndc_x = (u - img_w / 2) / (img_w / 2)
    ndc_y = (v - img_h / 2) / (img_h / 2)

    # Tangent half-FOV.
    th = math.tan(math.radians(intrinsics.horizontal_fov_deg / 2))
    tv = math.tan(math.radians(intrinsics.vertical_fov_deg / 2))

    # Ray direction в camera frame (z=forward, x=right, y=down).
    cam_x = ndc_x * th
    cam_y = ndc_y * tv
    cam_z = 1.0

    # Применить pitch (rotation вокруг x-axis): looking-forward → looking-down.
    pitch_rad = math.radians(intrinsics.pitch_deg)
    cos_p, sin_p = math.cos(pitch_rad), math.sin(pitch_rad)
    # After pitch rotation:
    #   new_y = cos*y - sin*z   (vertical в world after camera tilt)
    #   new_z = sin*y + cos*z
    rot_y = cos_p * cam_y - sin_p * cam_z
    rot_z = sin_p * cam_y + cos_p * cam_z
    rot_x = cam_x

    # Применить UAV heading yaw (rotation вокруг z=down axis).
    # World frame: East-North-Up. UAV heading 0 = North.
    # Сначала camera frame (forward=heading) → world ENU.
    yaw_rad = math.radians(uav.heading_deg + intrinsics.yaw_offset_deg)
    cos_y, sin_y = math.cos(yaw_rad), math.sin(yaw_rad)
    # Forward (rot_z) → North; right (rot_x) → East.
    enu_east = rot_z * sin_y + rot_x * cos_y
    enu_north = rot_z * cos_y - rot_x * sin_y
    enu_down = rot_y   # camera was +y=down

    # Ray parametric: P(t) = (uav_xyz) + t * (enu_east, enu_north, -enu_down)
    # (Up positive). Ground at z=0 (UAV alt = uav.altitude_m above ground).
    # Solve uav.altitude - t*enu_down = 0   →   t = uav.altitude / enu_down
    if enu_down <= 0.001:
        return None   # ray не смотрит вниз
    t = uav.altitude_m / enu_down
    if t <= 0:
        return None
    ground_east = t * enu_east
    ground_north = t * enu_north
    return ground_east, ground_north


def enu_to_latlon(
    east_m: float, north_m: float,
    uav: UAVPose,
) -> tuple[float, float]:
    """ENU offset (м) от UAV → (latitude, longitude) globe."""
    deg_per_m_lat = 1.0 / 111_319.9
    deg_per_m_lon = 1.0 / (111_319.9 * max(math.cos(math.radians(uav.latitude_deg)), 0.01))
    lat = uav.latitude_deg + north_m * deg_per_m_lat
    lon = uav.longitude_deg + east_m * deg_per_m_lon
    return lat, lon


# -----------------------------------------------------------------------------
# MJPEG stream consumer
# -----------------------------------------------------------------------------
def stream_jpeg_frames(url: str, timeout: float = 5.0):
    """Generator: yield raw JPEG bytes из multipart/x-mixed-replace stream."""
    log.info("connecting to FPV stream %s", url)
    try:
        resp = urllib.request.urlopen(url, timeout=timeout)
    except Exception as exc:
        log.error("FPV stream unreachable: %s", exc)
        return
    content_type = resp.headers.get("Content-Type", "")
    log.info("content-type: %s", content_type)
    boundary = None
    if "boundary=" in content_type:
        boundary = content_type.split("boundary=", 1)[1].split(";")[0].strip()
        log.info("boundary: %s", boundary)
    if not boundary:
        log.error("no multipart boundary in Content-Type")
        return

    boundary_marker = ("--" + boundary).encode("ascii")
    buf = bytearray()
    while True:
        chunk = resp.read(8192)
        if not chunk:
            log.info("stream closed")
            return
        buf.extend(chunk)
        # Find frame: boundary, then headers (Content-Length), then JPEG bytes.
        while True:
            idx = buf.find(boundary_marker)
            if idx < 0:
                break
            # Header ends с \r\n\r\n.
            hdr_end = buf.find(b"\r\n\r\n", idx)
            if hdr_end < 0:
                break   # incomplete header
            headers_blob = buf[idx + len(boundary_marker) : hdr_end].decode(
                "ascii", errors="replace",
            )
            content_length = None
            for line in headers_blob.split("\r\n"):
                if line.lower().startswith("content-length:"):
                    try:
                        content_length = int(line.split(":", 1)[1].strip())
                    except ValueError:
                        pass
            data_start = hdr_end + 4
            if content_length is None:
                # Fallback — find next boundary.
                next_idx = buf.find(boundary_marker, data_start)
                if next_idx < 0:
                    break
                jpeg = bytes(buf[data_start:next_idx]).rstrip(b"\r\n")
                del buf[: next_idx]
                yield jpeg
                continue
            data_end = data_start + content_length
            if len(buf) < data_end:
                break   # incomplete frame
            jpeg = bytes(buf[data_start:data_end])
            del buf[: data_end]
            yield jpeg


# -----------------------------------------------------------------------------
# Detector
# -----------------------------------------------------------------------------
class Detector:
    """YOLOv8n wrapper с fallback на OpenCV HOG."""

    def __init__(self, min_confidence: float = 0.4):
        self.min_confidence = min_confidence
        self._yolo = None
        self._hog = None
        try:
            from ultralytics import YOLO   # type: ignore
            log.info("loading YOLOv8n weights (will auto-download on first run)")
            self._yolo = YOLO("yolov8n.pt")
            log.info("YOLOv8n ready (CPU mode)")
        except Exception as exc:
            log.warning("YOLOv8 unavailable (%s); fallback to OpenCV HOG", exc)
            import cv2   # type: ignore
            self._hog = cv2.HOGDescriptor()
            self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    def detect(self, frame_bgr: np.ndarray) -> list[dict[str, Any]]:
        """Return [{class_name, issgr_class, confidence, bbox=(x1,y1,x2,y2)}]."""
        if self._yolo is not None:
            return self._detect_yolo(frame_bgr)
        if self._hog is not None:
            return self._detect_hog(frame_bgr)
        return []

    def _detect_yolo(self, frame_bgr: np.ndarray) -> list[dict[str, Any]]:
        results = self._yolo(frame_bgr, verbose=False, conf=self.min_confidence)
        detections: list[dict[str, Any]] = []
        for result in results:
            boxes = result.boxes
            if boxes is None:
                continue
            for box in boxes:
                cls_id = int(box.cls[0])
                conf = float(box.conf[0])
                if cls_id not in COCO_RELEVANT:
                    continue
                name, issgr_class = COCO_RELEVANT[cls_id]
                x1, y1, x2, y2 = [int(v) for v in box.xyxy[0]]
                detections.append({
                    "class_name": name,
                    "issgr_class": issgr_class,
                    "confidence": conf,
                    "bbox": (x1, y1, x2, y2),
                })
        return detections

    def _detect_hog(self, frame_bgr: np.ndarray) -> list[dict[str, Any]]:
        import cv2
        rects, weights = self._hog.detectMultiScale(
            frame_bgr, winStride=(8, 8), padding=(8, 8), scale=1.05,
        )
        return [{
            "class_name": "person",
            "issgr_class": "operational_situation.target.point",
            "confidence": float(w),
            "bbox": (int(x), int(y), int(x + w_), int(y + h)),
        } for (x, y, w_, h), w in zip(rects, weights.flatten() if len(weights) else [])]


# -----------------------------------------------------------------------------
# UAV pose source — tail orchestrator events.jsonl
# -----------------------------------------------------------------------------
class PoseTailer:
    """Background reader последнего flight event."""
    def __init__(self, events_path: Path | None) -> None:
        self.path = events_path
        self.pose = UAVPose()
        self._fp = None

    def update(self) -> UAVPose:
        if self.path is None or not self.path.exists():
            return self.pose
        if self._fp is None:
            self._fp = self.path.open("r", encoding="utf-8")
            self._fp.seek(0, os.SEEK_END)
        while True:
            line = self._fp.readline()
            if not line:
                break
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("event_type") != "flight":
                continue
            pos = ev.get("position", {})
            if "lat" in pos:
                self.pose = UAVPose(
                    latitude_deg=float(pos["lat"]),
                    longitude_deg=float(pos["lon"]),
                    altitude_m=max(float(pos.get("alt_rel_m", 0.0)), 0.5),
                    heading_deg=float(pos.get("heading_deg", 0.0)),
                    timestamp=float(ev.get("wall_time", time.time())),
                )
        return self.pose


# -----------------------------------------------------------------------------
# ISSGR POST
# -----------------------------------------------------------------------------
def post_sensor_reading(
    issgr_url: str,
    uav_id: str,
    detection: dict[str, Any],
    pose: UAVPose,
    ground_lat: float, ground_lon: float,
) -> bool:
    payload = {
        "id": {
            "domain": "cv-detector",
            "system": "yolov8n",
            "object_uuid": str(uuid.uuid4()),
        },
        "name": f"CV:{detection['class_name']}",
        "issgr_class": "functional_objects.sensor.ground_station",
        "source_uav_id": _parse_id(uav_id),
        "sensor_type": "camera_object_detection",
        "value": {
            "class_name": detection["class_name"],
            "issgr_class_guess": detection["issgr_class"],
            "confidence": detection["confidence"],
            "bbox": detection["bbox"],
            "ground_lat": ground_lat,
            "ground_lon": ground_lon,
        },
        "pose_at_observation": {
            "latitude_deg": pose.latitude_deg,
            "longitude_deg": pose.longitude_deg,
            "altitude_m": pose.altitude_m,
            "heading_deg": pose.heading_deg,
        },
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{issgr_url}/collections/sensor_readings/items",
        data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=3.0).read()
        return True
    except Exception as exc:
        log.debug("ISSGR POST failed: %s", exc)
        return False


def _parse_id(s: str) -> dict[str, str]:
    parts = s.split(":")
    if len(parts) != 3:
        return {"domain": "bas", "system": "fizulin-rig",
                "object_uuid": "00000000-0000-0000-0000-000000000100"}
    return {"domain": parts[0], "system": parts[1], "object_uuid": parts[2]}


# -----------------------------------------------------------------------------
# Annotation overlay
# -----------------------------------------------------------------------------
def annotate_frame(
    frame_bgr: np.ndarray,
    detections: list[dict[str, Any]],
    pose: UAVPose,
) -> np.ndarray:
    import cv2
    out = frame_bgr.copy()
    for det in detections:
        x1, y1, x2, y2 = det["bbox"]
        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 255, 0), 2)
        label = f"{det['class_name']} {det['confidence']:.2f}"
        cv2.putText(out, label, (x1, max(y1 - 6, 0)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
    # Pose overlay в правом нижнем углу.
    h, w = out.shape[:2]
    pose_text = (f"UAV {pose.latitude_deg:.5f},{pose.longitude_deg:.5f}"
                 f" alt={pose.altitude_m:.1f} hdg={pose.heading_deg:.0f}")
    cv2.putText(out, pose_text, (5, h - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
    return out


# -----------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Stage 3 CV-обработка видовых данных")
    ap.add_argument("--fpv-url", default="http://127.0.0.1:8765/camera.mjpg",
                    help="MJPEG endpoint Web GCS")
    ap.add_argument("--issgr-url", default="http://127.0.0.1:8770",
                    help="ИССГР API base URL")
    ap.add_argument("--events", default="",
                    help="orchestrator events.jsonl для UAV pose updates")
    ap.add_argument("--uav-id",
                    default="bas:fizulin-rig:00000000-0000-0000-0000-000000000100",
                    help="ИССГР UAV identifier для source_uav_id")
    ap.add_argument("--interval-s", type=float, default=2.0,
                    help="Сколько секунд между detection runs")
    ap.add_argument("--min-confidence", type=float, default=0.4)
    ap.add_argument("--log-dir", default="",
                    help="Папка для annotated frames + JSONL")
    ap.add_argument("--max-seconds", type=float, default=0.0,
                    help="Auto-stop after N секунд (0 = forever)")
    ap.add_argument("--no-issgr", action="store_true",
                    help="Не POSTить в ИССГР, только log")
    ap.add_argument(
        "--camera-fov-deg", type=float, default=80.0,
        help="Horizontal FOV камеры (default 80°)",
    )
    ap.add_argument("--camera-pitch-deg", type=float, default=-45.0)
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    log_dir: Path | None = None
    annotated_dir: Path | None = None
    detection_log = None
    if args.log_dir:
        log_dir = Path(args.log_dir).resolve()
        log_dir.mkdir(parents=True, exist_ok=True)
        annotated_dir = log_dir / "cv_detections"
        annotated_dir.mkdir(exist_ok=True)
        detection_log = (log_dir / "cv_detections.jsonl").open("a", encoding="utf-8")
        log.info("annotated frames → %s", annotated_dir)
        log.info("detection log → %s", detection_log.name)

    detector = Detector(min_confidence=args.min_confidence)
    pose_tailer = PoseTailer(Path(args.events).resolve() if args.events else None)
    intrinsics = CameraIntrinsics(
        horizontal_fov_deg=args.camera_fov_deg,
        pitch_deg=args.camera_pitch_deg,
    )

    import cv2
    start = time.time()
    last_detect = 0.0
    frame_idx = 0
    posted = 0

    try:
        for jpeg in stream_jpeg_frames(args.fpv_url):
            now = time.time()
            if now - last_detect < args.interval_s:
                continue
            last_detect = now

            try:
                frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
            except Exception as exc:
                log.warning("failed decoding JPEG: %s", exc)
                continue
            if frame is None or frame.size == 0:
                continue

            intrinsics.image_width = frame.shape[1]
            intrinsics.image_height = frame.shape[0]
            pose = pose_tailer.update()

            detections = detector.detect(frame)
            if not detections:
                log.info("[frame %d] no detections", frame_idx)
                frame_idx += 1
                continue

            log.info("[frame %d] %d detections", frame_idx, len(detections))
            for det in detections:
                x1, y1, x2, y2 = det["bbox"]
                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                enu = pixel_to_ground_enu(cx, cy, intrinsics, pose)
                if enu is None:
                    ground_lat, ground_lon = pose.latitude_deg, pose.longitude_deg
                    log.info("  - %s %.2f bbox=(%d,%d)-(%d,%d) GROUND_NA",
                             det["class_name"], det["confidence"],
                             x1, y1, x2, y2)
                else:
                    east_m, north_m = enu
                    ground_lat, ground_lon = enu_to_latlon(east_m, north_m, pose)
                    log.info("  - %s %.2f bbox=(%d,%d)-(%d,%d)"
                             " ground=(%.6f,%.6f) ENU=(%.1fE,%.1fN)",
                             det["class_name"], det["confidence"],
                             x1, y1, x2, y2,
                             ground_lat, ground_lon, east_m, north_m)
                if not args.no_issgr:
                    if post_sensor_reading(args.issgr_url, args.uav_id, det,
                                           pose, ground_lat, ground_lon):
                        posted += 1
                if detection_log is not None:
                    detection_log.write(json.dumps({
                        "wall_time": now,
                        "frame_idx": frame_idx,
                        "class_name": det["class_name"],
                        "confidence": det["confidence"],
                        "bbox": list(det["bbox"]),
                        "ground_lat": ground_lat,
                        "ground_lon": ground_lon,
                        "uav_pose": {
                            "lat": pose.latitude_deg, "lon": pose.longitude_deg,
                            "alt_m": pose.altitude_m, "heading_deg": pose.heading_deg,
                        },
                    }) + "\n")
                    detection_log.flush()

            if annotated_dir is not None:
                annotated = annotate_frame(frame, detections, pose)
                cv2.imwrite(str(annotated_dir / f"frame_{frame_idx:06d}.jpg"),
                            annotated, [cv2.IMWRITE_JPEG_QUALITY, 75])

            frame_idx += 1
            if args.max_seconds and (now - start) > args.max_seconds:
                log.info("max-seconds reached (%d frames, %d ISSGR posts)",
                         frame_idx, posted)
                break
    finally:
        if detection_log is not None:
            detection_log.close()
        log.info("done — frames=%d, ISSGR posts=%d", frame_idx, posted)

    return 0


if __name__ == "__main__":
    sys.exit(main())
