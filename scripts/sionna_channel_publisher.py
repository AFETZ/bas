#!/usr/bin/env python3
"""Этап 2.1.e: real-time Sionna channel publisher.

Читает текущую позицию UAV из `events.jsonl` (tail -f аналог), делает
2D lookup в pre-computed radio map (.npz, см. compute_radio_map.py) и
публикует текущие `loss_ratio` + `delay_ms` в JSON-файл, который ns-3
читает каждые 100 мс через FileWatcher (см. ns3/scenarios/two_channel.cc).

Это минимальная интеграция — не требует кастомного `SionnaErrorModel` в
C++ и nzp-loader'а; вместо этого orchestrator делает физику в Python и
обновляет существующий `RateErrorModel::SetRate()` через файл-IPC.

Запуск (обычно из run_stage_2_1_sionna.sh):
  ./sionna_env/bin/python scripts/sionna_channel_publisher.py \
      --events logs/<run_id>/events.jsonl \
      --radio-map radio_maps/iris_runway.npz \
      --out /tmp/sionna_channel.json \
      --interval-ms 100
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np  # type: ignore


# Origin для маппинга lat/lon -> метры. iris в Gazebo стартует в этой точке,
# config совпадает с iris_runway.sdf <spherical_coordinates>.
ORIGIN_LAT = -35.363262
ORIGIN_LON = 149.165237


def haversine_xy_m(lat: float, lon: float) -> tuple[float, float]:
    """Local-tangent-plane conversion (lat,lon) -> (x_north, y_east) в метрах
    относительно ORIGIN_LAT/LON. Точность ±1м в радиусе нескольких км —
    достаточно для radio map с 10 м cell-size."""
    R = 6_371_000.0
    dlat = math.radians(lat - ORIGIN_LAT)
    dlon = math.radians(lon - ORIGIN_LON)
    x_north = R * dlat
    y_east = R * dlon * math.cos(math.radians(ORIGIN_LAT))
    return x_north, y_east


class RadioMap:
    """Wrapper над .npz: 2D bilinear lookup path_loss / RSS по (x, y)."""

    def __init__(self, npz_path: Path) -> None:
        data = np.load(npz_path)
        self.rss_db = data["rss_db"]              # (n_y, n_x), dB
        self.path_loss_db = data["path_loss_db"]
        cx, cy, _ = data["map_center"]
        sx, sy = data["map_size"]
        cell_x, cell_y = data["cell_size"]
        self.x_min = float(cx - sx / 2)
        self.x_max = float(cx + sx / 2)
        self.y_min = float(cy - sy / 2)
        self.y_max = float(cy + sy / 2)
        self.cell_x = float(cell_x)
        self.cell_y = float(cell_y)
        self.tx_pos = data["tx_position"]
        self.carrier_hz = float(data["carrier_hz"])
        print(f"RadioMap loaded: {self.rss_db.shape}, "
              f"x∈[{self.x_min:.0f},{self.x_max:.0f}] "
              f"y∈[{self.y_min:.0f},{self.y_max:.0f}] "
              f"carrier={self.carrier_hz/1e9:.2f} GHz")

    def lookup(self, x: float, y: float, z: float = 0.0) -> tuple[float, float]:
        """Возвращает (rss_db, path_loss_db) для позиции (x, y) в метрах.

        За пределами карты возвращает path_loss = 130 dB (полный blackout),
        rss = -130 dB. В рамках карты делает bilinear interpolation.

        `z` параметр игнорируется (radio map 2D), нужен для совместимости
        интерфейса с LiveRTChannel.lookup.
        """
        del z   # 2D radio map не зависит от высоты
        if not (self.x_min <= x <= self.x_max and self.y_min <= y <= self.y_max):
            return (-130.0, 130.0)

        fx = (x - self.x_min) / self.cell_x
        fy = (y - self.y_min) / self.cell_y
        ix = int(np.floor(fx))
        iy = int(np.floor(fy))
        # bilinear weights
        dx = fx - ix
        dy = fy - iy
        ix = max(0, min(self.rss_db.shape[1] - 2, ix))
        iy = max(0, min(self.rss_db.shape[0] - 2, iy))
        rss = (
            (1 - dx) * (1 - dy) * self.rss_db[iy,   ix]
            + dx * (1 - dy)       * self.rss_db[iy,   ix+1]
            + (1 - dx) * dy       * self.rss_db[iy+1, ix]
            + dx * dy             * self.rss_db[iy+1, ix+1]
        )
        return float(rss), float(-rss)  # path_loss = -RSS (dB-domain)


class LiveRTChannel:
    """Online Sionna RT PathSolver — real-time ray tracing вместо offline
    lookup. Загружает Mitsuba scene один раз, добавляет статичный
    Transmitter (GCS mast) и Receiver placeholder, на каждый lookup()
    меняет позицию Receiver и запускает PathSolver.

    На WSL2 без OptiX SDK Mitsuba CUDA variant не работает (OptiX context
    init fails). Используем `llvm_ad_mono_polarized` — CPU JIT, ~55 мс на
    ray-tracing call в нашей сцене (iris_runway + 3 obstacles + runway).
    Достаточно для 10 Hz update rate (ns-3 polling каждые 100 мс).

    JIT warm-up на первом вызове ~2 сек (LLVM compilation); subsequent
    calls 40-120 мс. Чтобы UI не висел, делаем warm-up в __init__.

    API соответствует RadioMap.lookup() — возвращает (rss_db, path_loss_db).
    """

    def __init__(
        self,
        scene_path: Path,
        tx_position: tuple[float, float, float] = (0.0, -60.0, 1.5),
        tx_power_dbm: float = 23.0,
        carrier_hz: float = 2.4e9,
        max_depth: int = 2,
    ) -> None:
        # Lazy import — модуль импортируется ТОЛЬКО если оператор включил
        # --rt-online (иначе sionna_env не обязателен).
        import os as _os
        _os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
        import mitsuba as _mi
        # CUDA в WSL2 без OptiX SDK — pinned LLVM. На bare Linux можно
        # переопределить через MITSUBA_VARIANT env.
        variant = _os.environ.get("MITSUBA_VARIANT", "llvm_ad_mono_polarized")
        if _mi.variant() is None:
            _mi.set_variant(variant)
        import sionna.rt as _rt   # type: ignore

        print(f"[rt-live] Mitsuba variant: {_mi.variant()}")
        print(f"[rt-live] loading scene {scene_path}")
        t0 = time.time()
        scene = _rt.load_scene(str(scene_path))
        print(f"[rt-live]  loaded in {(time.time()-t0)*1000:.0f} ms, "
              f"objects={len(scene.objects)}")

        scene.tx_array = _rt.PlanarArray(
            num_rows=1, num_cols=1,
            vertical_spacing=0.5, horizontal_spacing=0.5,
            pattern="iso", polarization="V",
        )
        scene.rx_array = _rt.PlanarArray(
            num_rows=1, num_cols=1,
            vertical_spacing=0.5, horizontal_spacing=0.5,
            pattern="iso", polarization="V",
        )
        scene.add(_rt.Transmitter(name="gcs_tx", position=list(tx_position)))
        scene.add(_rt.Receiver(name="uav_rx", position=[0.0, 0.0, 10.0]))

        self._scene = scene
        self._solver = _rt.PathSolver()
        self._max_depth = max_depth
        self._tx_position = tx_position
        self.tx_pos = np.array(tx_position)
        self.carrier_hz = carrier_hz
        self.tx_power_dbm = tx_power_dbm

        # Warm-up: LLVM JIT компилирует kernel при первом вызове (~2 сек).
        # Делаем здесь чтобы UI не зависал на первом UAV update.
        print("[rt-live] JIT warm-up (first PathSolver call)...")
        t0 = time.time()
        self._solver(scene=scene, max_depth=max_depth)
        print(f"[rt-live]  warm-up: {(time.time()-t0)*1000:.0f} ms")

    def lookup(self, x: float, y: float, z: float = 10.0) -> tuple[float, float]:
        """Real-time PathSolver call: возвращает (rss_db, path_loss_db).

        rss_db = tx_power_dbm + total_received_power_dB (включая path loss
        и antenna gains). Полностью симметрично с RadioMap.lookup() — drop-in
        replacement в sionna_publisher main loop.
        """
        # Двигаем существующий receiver — дешевле чем remove+add.
        self._scene.get("uav_rx").position = [float(x), float(y), float(z)]
        paths = self._solver(scene=self._scene, max_depth=self._max_depth)
        a, _tau = paths.cir(out_type="numpy")
        # a shape: (num_rx=1, num_rx_ant=1, num_tx=1, num_tx_ant=1, num_paths, num_time_samples=1)
        # Total received power = sum |a_k|^2 over paths.
        power_linear = float((np.abs(a) ** 2).sum())
        if power_linear <= 0.0:
            # Полный blackout (нет любого пути) — типично за высокими obstacles.
            return (-130.0, 130.0)
        # Sionna a уже включает path-loss; RSS [dB] = 10*log10(power) +
        # tx_power_dbm. Для consistency с offline radio map нашего проекта
        # отдельно возвращаем path_loss_db = -10*log10(power) (без TX power).
        path_loss_db = -10.0 * math.log10(power_linear + 1e-30)
        rss_db = self.tx_power_dbm - path_loss_db
        return (rss_db, path_loss_db)


def rss_to_loss_ratio(rss_db: float) -> float:
    """Эвристическое отображение RSS dB -> packet error ratio для WiFi 2.4 GHz.

    Используем простую logistic: при rss > -65 dB loss ≈ 0; при rss < -90 dB
    loss → 1; в середине плавный переход. Параметры подобраны чтобы
    coverage = 65% radio map (как в smoke run) давал ~5-15% средний loss.
    """
    # logistic centered at -78 dB, slope = 0.5
    return 1.0 / (1.0 + math.exp(0.5 * (rss_db + 78.0)))


def rss_to_extra_delay_ms(rss_db: float) -> float:
    """Чем хуже сигнал, тем больше delay (retransmits, queueing).
    Простая модель: 0 ms при RSS > -60 dB, до +200 ms при RSS < -90 dB."""
    if rss_db >= -60.0:
        return 0.0
    if rss_db <= -90.0:
        return 200.0
    return 200.0 * (-60.0 - rss_db) / 30.0


def tail_flight_positions(events_path: Path, from_start: bool = False):
    """Generator: yield последнюю UAV-позицию (lat, lon, alt_rel_m) из
    events.jsonl. Делает tail -f аналог: при появлении новых строк
    парсит `event_type=flight` и `position.{lat,lon,alt_rel_m}`.

    `from_start=True` — читать journal с начала (полезно для replay/smoke
    на завершённых прогонах).
    """
    fp = None
    while True:
        if not events_path.exists():
            time.sleep(0.1)
            continue
        if fp is None:
            fp = events_path.open("r", encoding="utf-8")
            if not from_start:
                fp.seek(0, os.SEEK_END)   # real-time: только новые события
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
        yield (
            float(pos["lat"]),
            float(pos["lon"]),
            float(pos.get("alt_rel_m", 0.0)),
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True, help="Путь к events.jsonl")
    ap.add_argument("--radio-map", default="",
                    help="Путь к .npz (offline lookup). Игнорируется при --rt-online")
    ap.add_argument("--rt-online", action="store_true",
                    help="Live Sionna RT PathSolver вместо offline radio map "
                         "(требует --rt-scene)")
    ap.add_argument("--rt-scene", default="scene/iris_runway.xml",
                    help="Mitsuba scene для live ray tracing (используется при --rt-online)")
    ap.add_argument("--rt-tx", default="0,-60,1.5",
                    help="Transmitter position 'x,y,z' для live RT")
    ap.add_argument("--rt-max-depth", type=int, default=2,
                    help="Max reflection depth для PathSolver (выше = точнее, медленнее)")
    ap.add_argument("--out", default="/tmp/sionna_channel.json")
    ap.add_argument("--interval-ms", type=int, default=100)
    ap.add_argument("--max-seconds", type=float, default=0.0,
                    help="Auto-stop после N секунд (0 = forever)")
    ap.add_argument("--replay", action="store_true",
                    help="Прочитать events.jsonl с начала (smoke на завершённых прогонах)")
    args = ap.parse_args()

    # Channel model: offline radio map (default) либо live PathSolver.
    rm: object
    if args.rt_online:
        tx_xyz = tuple(float(v) for v in args.rt_tx.split(","))
        if len(tx_xyz) != 3:
            raise SystemExit("--rt-tx must be x,y,z")
        rm = LiveRTChannel(
            scene_path=Path(args.rt_scene),
            tx_position=tx_xyz,           # type: ignore[arg-type]
            max_depth=args.rt_max_depth,
        )
    else:
        if not args.radio_map:
            raise SystemExit("--radio-map required when --rt-online not set")
        rm = RadioMap(Path(args.radio_map))
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    start = time.time()
    last_write = 0.0
    interval_s = args.interval_ms / 1000.0
    last_pos = None

    print(f"==> publishing to {out_path} every {args.interval_ms} ms "
          f"(reading {args.events})")

    for lat, lon, alt in tail_flight_positions(Path(args.events), from_start=args.replay):
        last_pos = (lat, lon, alt)
        now = time.time()
        if now - last_write < interval_s:
            continue
        last_write = now

        x, y = haversine_xy_m(lat, lon)
        rt_t0 = time.time()
        rss_db, path_loss_db = rm.lookup(x, y, alt)   # type: ignore[arg-type]
        rt_latency_ms = (time.time() - rt_t0) * 1000.0
        loss_ratio = rss_to_loss_ratio(rss_db)
        extra_delay_ms = rss_to_extra_delay_ms(rss_db)

        record = {
            "wall_time": now,
            "wall_dt": now - start,
            "uav_lat": lat,
            "uav_lon": lon,
            "uav_alt_rel_m": alt,
            "uav_x_north": x,
            "uav_y_east": y,
            "rss_db": rss_db,
            "path_loss_db": path_loss_db,
            "loss_ratio": loss_ratio,
            "extra_delay_ms": extra_delay_ms,
            "channel_model": "rt_online" if args.rt_online else "radio_map_lookup",
            "channel_latency_ms": rt_latency_ms,
        }
        tmp_path = out_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(record, separators=(",", ":")))
        tmp_path.replace(out_path)   # atomic rename

        if args.max_seconds and (now - start) > args.max_seconds:
            print("max-seconds reached")
            return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
