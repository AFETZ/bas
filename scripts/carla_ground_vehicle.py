#!/usr/bin/env python3
"""Наземный транспорт через CARLA — альтернативный движок к ArduRover SITL.

Даёт пользователю ВЫБОР движка наземной техники (ТЗ упоминает CARLA среди основ
среды моделирования). Два под-режима:

  live      — реальный CARLA сервер :2000 (пакет carla 0.9.12 из conda-env
              `msvan3t_carla`, python 3.7). Spawn vehicle, ручное управление
              `carla.VehicleControl(throttle/steer/brake)`, позиция машины →
              ИССГР. Требует запущенный CARLA UE4 сервер (GPU-хост).
  kinematic — велосипедная (bicycle) модель на чистом stdlib. Работает в любом
              python (в т.ч. .venv 3.12 на CI без GPU/сервера). Та же
              геопривязка и тот же выход в ИССГР, что и live.

  auto      — пробует live (импорт carla + connect :2000); если сервера нет —
              падает в kinematic. По умолчанию.

Оба под-режима upsert-ят машину в ИССГР как объект
`operational_situation.ground_vehicle.{wheeled,offroad}` — В ТОЧНОСТИ тот же
формат, что и `rover_to_issgr_publisher.py` для ArduRover. Поэтому витрина и
цифровой двойник показывают наземную машину одинаково, независимо от движка.

Геопривязка единая для обоих режимов: машина выдаёт локальное смещение
(восток_м, север_м) от референс-точки → lat/lon. Референс по умолчанию = home
ArduRover, поэтому CARLA-машина появляется там же, где ездил бы rover (рядом с
БАС в двойнике).

Usage:
  # kinematic (без CARLA сервера, для CI/демо):
  ./.venv/bin/python scripts/carla_ground_vehicle.py --mode kinematic \
      --issgr-url http://127.0.0.1:8770 --seconds 40

  # live (нужен запущенный CARLA сервер):
  ~/miniforge3/envs/msvan3t_carla/bin/python scripts/carla_ground_vehicle.py \
      --mode live --carla-host 127.0.0.1 --carla-port 2000 --map Town10HD_Opt
"""
from __future__ import annotations

import argparse
import json
import math
import socket
import subprocess
import sys
import time
import urllib.request

# ---------------------------------------------------------------------------
# Классы наземного транспорта (совместимо с rover_to_issgr_publisher.py).
# wheeled = дорожный, offroad = вне дорог.
# ---------------------------------------------------------------------------
FRAME_TO_CLASS = {
    "wheeled": "operational_situation.ground_vehicle.wheeled",
    "offroad": "operational_situation.ground_vehicle.offroad",
}
# Подбор CARLA blueprint под класс: дорожный = седан, вне дорог = 4x4.
FRAME_TO_CARLA_BP = {
    "wheeled": ["vehicle.tesla.model3", "vehicle.audi.tt", "vehicle.*"],
    "offroad": ["vehicle.jeep.wrangler_rubicon", "vehicle.tesla.cybertruck",
                "vehicle.*"],
}

# Референс по умолчанию — home ArduRover (Канберра), чтобы CARLA-машина
# появлялась рядом с БАС в едином двойнике.
REF_LAT = -35.363262
REF_LON = 149.165237
REF_ALT = 584.0

# Велосипедная модель (типовой легковой автомобиль).
WHEELBASE_M = 2.8
MAX_STEER_RAD = 0.6          # ~34° на упоре руля
ACCEL_MAX_MPS2 = 4.0         # при throttle=1.0
BRAKE_MAX_MPS2 = 8.0         # при brake=1.0
DRAG_PER_S = 0.06            # линейное сопротивление (создаёт V_max ~ accel/drag)

OBJ_UUID = "00000000-0000-0000-0000-0000000000a2"   # стабильный → upsert
# (rover использует ...a1; CARLA-машина — отдельный объект ...a2)


def resolve_carla_host(host: str) -> str:
    """`auto` → IP Windows-хоста из WSL2. В NAT-режиме это default gateway
    (CARLA сервер на Windows слушает :2000, WSL достаёт его по gateway-IP).
    В mirrored-режиме gateway недоступен → localhost. Любое явное значение
    возвращается как есть."""
    if host != "auto":
        return host
    try:
        out = subprocess.check_output(
            ["ip", "route", "show", "default"], text=True, timeout=3)
        parts = out.split()
        if "via" in parts:
            return parts[parts.index("via") + 1]
    except Exception:
        pass
    return "127.0.0.1"


def probe_tcp(host: str, port: int, timeout: float = 2.0) -> bool:
    """Быстрая проверка, что CARLA RPC-порт достижим (иначе carla.Client висит
    10 с и даёт невнятный таймаут — а тут сразу ясно про firewall/сервер)."""
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False


def enu_to_latlon(ref_lat: float, ref_lon: float,
                  east_m: float, north_m: float) -> tuple[float, float]:
    """Локальное ENU-смещение (метры) → широта/долгота (flat-earth, см-точность
    на километровых дистанциях демо)."""
    dlat = north_m / 111_320.0
    dlon = east_m / (111_320.0 * math.cos(math.radians(ref_lat)))
    return ref_lat + dlat, ref_lon + dlon


def upsert_vehicle(issgr_url: str, name: str, issgr_class: str, sysid: int,
                   lat: float, lon: float, alt: float, heading: float,
                   speed_mps: float, mode: str) -> bool:
    """Upsert наземной машины в ИССГР (формат идентичен rover publisher)."""
    payload = {
        "id": {"domain": "bas", "system": "carla", "object_uuid": OBJ_UUID},
        "name": name, "sysid": sysid,
        "issgr_class": issgr_class,
        "pose": {"latitude_deg": lat, "longitude_deg": lon,
                 "altitude_m": alt, "heading_deg": heading},
        "armed": True, "flight_mode": mode,
        "battery_v": 12.4,
        "velocity_ned": [round(speed_mps, 2), 0.0, 0.0],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        issgr_url.rstrip("/") + "/collections/uavs/items",
        data=data, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=3.0).read()
        return True
    except Exception:
        return False


def manual_control(t: float) -> tuple[float, float]:
    """Ручное управление: газ вперёд + S-маневр рулём (видна траектория).

    Возвращает (throttle[0..1], steer[-1..1]). Эмулирует оператора, который
    держит газ и плавно водит рулём — то же поведение, что rover_manual_drive.
    """
    throttle = 0.6
    # Мягкий руль: на реальной дороге CARLA сильный S-манёвр (0.4) за ~3с
    # уводит машину в здание и она застревает. 0.08 = лёгкие правки курса,
    # машина едет вперёд и в kinematic (открытое поле) рисует пологую кривую.
    steer = 0.08 * math.sin(t * 0.25)
    return throttle, steer


class KinematicCar:
    """Велосипедная модель машины. Состояние в локальном ENU (восток, север)."""

    def __init__(self) -> None:
        self.east = 0.0
        self.north = 0.0
        self.yaw = 0.0          # рад, 0 = на восток
        self.v = 0.0            # м/с

    def step(self, throttle: float, steer: float, brake: float,
             dt: float) -> None:
        accel = throttle * ACCEL_MAX_MPS2 - brake * BRAKE_MAX_MPS2
        accel -= DRAG_PER_S * self.v
        self.v = max(0.0, self.v + accel * dt)
        beta = max(-1.0, min(1.0, steer)) * MAX_STEER_RAD
        self.yaw += (self.v / WHEELBASE_M) * math.tan(beta) * dt
        self.east += self.v * math.cos(self.yaw) * dt
        self.north += self.v * math.sin(self.yaw) * dt

    @property
    def heading_deg(self) -> float:
        # ENU yaw (0=восток, против часовой) → компасный курс (0=север, по часовой)
        return (90.0 - math.degrees(self.yaw)) % 360.0


def run_kinematic(args: argparse.Namespace) -> int:
    """Bicycle-модель: ручное вождение + upsert в ИССГР. Без CARLA сервера."""
    issgr_class = FRAME_TO_CLASS[args.frame]
    print(f"[carla-gv] mode=kinematic frame={args.frame} "
          f"class={issgr_class} → {args.issgr_url}", flush=True)
    car = KinematicCar()
    dt = max(0.02, args.period_s)
    t0 = time.time()
    n = 0
    max_dist = 0.0
    while time.time() - t0 < args.seconds:
        t = time.time() - t0
        throttle, steer = manual_control(t)
        car.step(throttle, steer, brake=0.0, dt=dt)
        lat, lon = enu_to_latlon(args.ref_lat, args.ref_lon, car.east, car.north)
        dist = math.hypot(car.east, car.north)
        max_dist = max(max_dist, dist)
        if upsert_vehicle(args.issgr_url, args.name, issgr_class, args.sysid,
                          lat, lon, REF_ALT, car.heading_deg, car.v, "MANUAL"):
            n += 1
            if n % 20 == 0:
                print(f"[carla-gv] {n} upserts; ({lat:.6f},{lon:.6f}) "
                      f"v={car.v:.1f} m/s dist={dist:.1f}m", flush=True)
        time.sleep(dt)
    print(f"[carla-gv] kinematic done — {n} upserts, max_dist={max_dist:.1f}m",
          flush=True)
    return 0 if max_dist > 10.0 else 1


def run_live(args: argparse.Namespace) -> int:
    """Реальный CARLA сервер: spawn vehicle + ручное VehicleControl + ИССГР.

    Импорт carla ЛЕНИВЫЙ (пакет есть только в env msvan3t_carla py3.7).
    """
    try:
        import carla  # noqa: PLC0415  (lazy: py3.7-only Boost.Python binding)
    except Exception as e:
        print(f"[carla-gv] carla недоступен ({e}); запускайте под "
              f"msvan3t_carla python или используйте --mode kinematic",
              flush=True)
        return 2

    issgr_class = FRAME_TO_CLASS[args.frame]
    host = resolve_carla_host(args.carla_host)
    print(f"[carla-gv] mode=live connect {host}:{args.carla_port} "
          f"(--carla-host {args.carla_host}) frame={args.frame}", flush=True)

    # Preflight: порт достижим? (Windows CARLA + WSL2 NAT — нужен firewall-allow
    # на :2000, иначе carla.Client молча висит весь таймаут.)
    if not probe_tcp(host, args.carla_port):
        print(f"[carla-gv] {host}:{args.carla_port} недостижим. Проверьте: "
              f"(1) CARLA сервер запущен; (2) Windows Firewall пускает порт "
              f"{args.carla_port}; (3) для WSL2 NAT — сервер слушает 0.0.0.0",
              flush=True)
        return 2

    client = carla.Client(host, args.carla_port)
    client.set_timeout(20.0)
    try:
        # reload/load → ЧИСТЫЙ мир в async-состоянии. Критично: если прошлый
        # прогон оборвался (timeout/SIGTERM) не восстановив async, мир остаётся
        # в synchronous и новый клиент не может прогнать физику — машина стоит.
        world = (client.load_world(args.map) if args.map
                 else client.reload_world())
    except Exception as e:
        print(f"[carla-gv] нет CARLA сервера на "
              f"{args.carla_host}:{args.carla_port} ({e})", flush=True)
        return 2

    original = world.get_settings()
    vehicle = None
    try:
        settings = world.get_settings()
        settings.synchronous_mode = True
        # 0.05с — стабильная физика колёс. CARLA substepping требует
        # fixed_delta <= max_substep_delta(0.01) × max_substeps(10) = 0.1;
        # ровно на пределе (0.1) физика машины не интегрируется и она стоит.
        settings.fixed_delta_seconds = 0.05
        world.apply_settings(settings)

        bp_lib = world.get_blueprint_library()
        blueprint = None
        for cand in FRAME_TO_CARLA_BP[args.frame]:
            found = bp_lib.filter(cand)
            if found:
                blueprint = found[0]
                break
        if blueprint is None:
            print("[carla-gv] нет vehicle blueprint", flush=True)
            return 1

        spawn_points = world.get_map().get_spawn_points()
        if not spawn_points:
            print("[carla-gv] на карте нет spawn points", flush=True)
            return 1
        vehicle = world.spawn_actor(blueprint, spawn_points[0])
        # Warmup: дать машине осесть на дорогу (спавн чуть над землёй) и только
        # потом снять origin — иначе get_location() сразу после spawn даёт (0,0)
        # и появляется ложное смещение.
        for _ in range(20):
            world.tick()
        origin = vehicle.get_location()
        print(f"[carla-gv] spawned {blueprint.id} на "
              f"({origin.x:.1f},{origin.y:.1f})", flush=True)

        t0 = time.time()
        last_pub = 0.0
        n = 0
        max_dist = 0.0
        stuck_since = None       # когда газуем, но скорость ~0
        recover_until = 0.0      # до этого времени — задний ход (вы-застревание)
        while time.time() - t0 < args.seconds:
            t = time.time() - t0
            now = time.time()
            if now < recover_until:
                # Упёрлись (город CARLA) → задний ход с поворотом, чтобы
                # выехать и продолжить ручное движение вперёд.
                vehicle.apply_control(carla.VehicleControl(
                    throttle=0.5, steer=0.6, brake=0.0, reverse=True))
            else:
                throttle, steer = manual_control(t)
                vehicle.apply_control(carla.VehicleControl(
                    throttle=throttle, steer=steer, brake=0.0))
            world.tick()

            vel = vehicle.get_velocity()
            speed = math.sqrt(vel.x ** 2 + vel.y ** 2 + vel.z ** 2)
            # Детект застревания: газуем вперёд, но скорость ~0 дольше 1.2с.
            if now >= recover_until:
                if speed < 0.7:
                    if stuck_since is None:
                        stuck_since = now
                    elif now - stuck_since > 1.2:
                        recover_until = now + 1.2
                        stuck_since = None
                else:
                    stuck_since = None

            # Публикуем в ИССГР не каждый tick, а раз в period_s.
            if now - last_pub < args.period_s:
                continue
            last_pub = now
            loc = vehicle.get_location()
            # CARLA left-handed (x вперёд, y вправо). Для ИССГР-демо мапим
            # x→восток, -y→север (точная ориентация некритична для движения).
            east_m = loc.x - origin.x
            north_m = -(loc.y - origin.y)
            lat, lon = enu_to_latlon(args.ref_lat, args.ref_lon,
                                     east_m, north_m)
            hdg = vehicle.get_transform().rotation.yaw % 360.0
            dist = math.hypot(east_m, north_m)
            max_dist = max(max_dist, dist)
            if upsert_vehicle(args.issgr_url, args.name, issgr_class,
                              args.sysid, lat, lon, REF_ALT, hdg, speed,
                              "MANUAL"):
                n += 1
                if n % 10 == 0:
                    print(f"[carla-gv] {n} upserts; ({lat:.6f},{lon:.6f}) "
                          f"v={speed:.1f} m/s dist={dist:.1f}m", flush=True)
        print(f"[carla-gv] live done — {n} upserts, max_dist={max_dist:.1f}m",
              flush=True)
        return 0 if max_dist > 5.0 else 1
    finally:
        if vehicle is not None:
            try:
                vehicle.destroy()
            except Exception:
                pass
        try:
            world.apply_settings(original)   # вернуть async-режим серверу
        except Exception:
            pass


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="CARLA наземный транспорт → ИССГР")
    ap.add_argument("--mode", default="auto",
                    choices=["auto", "live", "kinematic"])
    ap.add_argument("--frame", default="wheeled", choices=list(FRAME_TO_CLASS))
    ap.add_argument("--issgr-url", default="http://127.0.0.1:8770")
    ap.add_argument("--seconds", type=float, default=40.0)
    ap.add_argument("--period-s", type=float, default=0.1)
    ap.add_argument("--name", default="Carla-Ground-1")
    ap.add_argument("--sysid", type=int, default=3)
    ap.add_argument("--ref-lat", type=float, default=REF_LAT)
    ap.add_argument("--ref-lon", type=float, default=REF_LON)
    ap.add_argument("--carla-host", default="127.0.0.1")
    ap.add_argument("--carla-port", type=int, default=2000)
    ap.add_argument("--map", default="", help="CARLA map (пусто=текущий мир)")
    args = ap.parse_args(argv)

    if args.mode == "kinematic":
        return run_kinematic(args)
    if args.mode == "live":
        return run_live(args)
    # auto: пробуем live, при отсутствии сервера/пакета — kinematic.
    rc = run_live(args)
    if rc == 2:
        print("[carla-gv] live недоступен → fallback kinematic", flush=True)
        return run_kinematic(args)
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
