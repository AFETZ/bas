#!/usr/bin/env python3
"""AirSim scene builder smoke — generate settings.json + populate stub.

Запускает stub server, прогоняет airsim_scene_builder через ping +
populate_scene, verifies:
  1. settings.json содержит правильное количество vehicles/cameras/sensors
     и _BasUrbanScene block с N catalog объектов;
  2. Spawn log писется и содержит N успешных entries;
  3. simListSceneObjects returns base + spawned;
  4. SDF parser работает на iris_runway_urban.sdf.
"""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path("/home/afetz/bas-prototype")
sys.path.insert(0, str(REPO / "scripts"))

from airsim_scene_builder import (   # noqa: E402
    URBAN_SCENE_CATALOG, emit_settings_json, parse_sdf_models,
    populate_scene, destroy_scene, scene_stats,
)
from airsim_client import AirSimRpcClient   # noqa: E402


STUB_PORT = 41560   # отличный от prod 41451
STUB_LOG = Path("/tmp/_airsim_scene_smoke_stub.log")
SPAWN_LOG_CLIENT = Path("/tmp/_airsim_scene_smoke_spawn_client.jsonl")
SPAWN_LOG_SERVER = Path("/tmp/_airsim_scene_smoke_spawn_server.jsonl")


def _free_port_open(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect(("127.0.0.1", port))
        s.close()
        return True
    except OSError:
        return False


def main() -> int:
    # --- 1. settings.json ---
    print("===== [1] settings.json generation =====")
    settings = emit_settings_json()
    assert settings["SettingsVersion"] == 2.0
    assert settings["SimMode"] == "Multirotor"
    assert "Iris1" in settings["Vehicles"]
    v = settings["Vehicles"]["Iris1"]
    assert "front_center_cam" in v["Cameras"]
    assert "fpv_cam" in v["Cameras"]
    assert "Lidar1" in v["Sensors"]
    bus = settings["_BasUrbanScene"]
    assert bus["object_count"] == len(URBAN_SCENE_CATALOG)
    assert bus["object_count"] >= 20, \
        f"expected ≥20 scene objects, got {bus['object_count']}"
    assert bus["source_sdf"] == "gazebo/worlds/iris_runway_urban.sdf"
    print(f"  settings.json valid; {bus['object_count']} catalog objects, "
          f"{len(v['Cameras'])} cameras, {len(v['Sensors'])} sensors")

    # --- 2. SDF parser ---
    print("\n===== [2] SDF parser =====")
    sdf_path = REPO / "gazebo/worlds/iris_runway_urban.sdf"
    if sdf_path.exists():
        sdf_models = parse_sdf_models(sdf_path)
        assert len(sdf_models) > 0
        building_like = [m for m in sdf_models if m["boxes"]]
        print(f"  SDF parsed: {len(sdf_models)} <model> blocks, "
              f"{len(building_like)} с box geometry")
    else:
        print(f"  SDF not found at {sdf_path}, skip")

    # --- 3. Scene stats ---
    print("\n===== [3] Scene stats =====")
    s = scene_stats()
    print(f"  total={s['total_objects']}  by_cat={s['by_category']}  "
          f"vol_m3={s['total_volume_m3']:.0f}")
    assert s["total_objects"] >= 20
    assert "building" in s["by_category"]
    assert "tree" in s["by_category"]
    assert "vehicle" in s["by_category"]

    # --- 4. Spawn через airsim_stub_server ---
    print("\n===== [4] Spawn via stub server =====")
    for p in (SPAWN_LOG_CLIENT, SPAWN_LOG_SERVER, STUB_LOG):
        if p.exists():
            p.unlink()
    # Стартуем stub server в subprocess.
    stub = subprocess.Popen(
        ["python3", str(REPO / "scripts/airsim_stub_server.py"),
         "--port", str(STUB_PORT),
         "--spawn-log", str(SPAWN_LOG_SERVER)],
        stdout=open(STUB_LOG, "wb"), stderr=subprocess.STDOUT,
        env={**os.environ, "PATH": "/usr/bin:/bin",
             "PYTHONPATH": str(REPO / ".venv/lib/python3.12/site-packages")},
    )
    try:
        # Wait for port open.
        for _ in range(30):
            if _free_port_open(STUB_PORT):
                break
            time.sleep(0.1)
        else:
            print("  stub didn't bind; aborting", file=sys.stderr)
            return 1
        print(f"  stub up on :{STUB_PORT}")

        client = AirSimRpcClient(host="127.0.0.1", port=STUB_PORT, timeout_s=2.0)
        assert client.call("ping") is True
        # Verify simListSceneObjects baseline.
        base = client.call("simListSceneObjects")
        assert isinstance(base, list)
        print(f"  baseline list: {len(base)} objects")

        # Populate.
        recs = populate_scene(client, spawn_log=SPAWN_LOG_CLIENT)
        ok = sum(1 for r in recs if r["ok"])
        print(f"  populated: {ok}/{len(recs)} ok")
        assert ok == len(URBAN_SCENE_CATALOG), \
            f"only {ok}/{len(URBAN_SCENE_CATALOG)} spawned successfully"

        # Client-side spawn log (наш populate_scene writes).
        assert SPAWN_LOG_CLIENT.exists()
        c_lines = SPAWN_LOG_CLIENT.read_text().strip().splitlines()
        assert len(c_lines) == len(URBAN_SCENE_CATALOG), \
            f"client log has {len(c_lines)} lines, expected {len(URBAN_SCENE_CATALOG)}"
        for ln in c_lines:
            rec = json.loads(ln)
            for k in ("object_name", "asset_name", "pose_ned", "scale",
                      "category", "material", "ok"):
                assert k in rec, f"client log missing key {k!r}: {rec}"
            assert rec["object_name"].startswith("BasUrban_")

        # Server-side spawn log (stub server writes for each RPC).
        assert SPAWN_LOG_SERVER.exists()
        s_lines = SPAWN_LOG_SERVER.read_text().strip().splitlines()
        assert len(s_lines) == len(URBAN_SCENE_CATALOG), \
            f"server log has {len(s_lines)} lines, expected {len(URBAN_SCENE_CATALOG)}"
        print(f"  client log={len(c_lines)} lines, server log={len(s_lines)} lines")

        # Verify simListSceneObjects now contains spawn list.
        after = client.call("simListSceneObjects")
        spawned_in_list = [o for o in after if isinstance(o, str)
                           and o.startswith("BasUrban_")]
        print(f"  post-spawn list: {len(after)} total, "
              f"{len(spawned_in_list)} BasUrban_*")
        assert len(spawned_in_list) == len(URBAN_SCENE_CATALOG)

        # Destroy roundtrip.
        n = destroy_scene(client)
        after_destroy = client.call("simListSceneObjects")
        spawned_after = [o for o in after_destroy if isinstance(o, str)
                         and o.startswith("BasUrban_")]
        print(f"  destroyed: {n}, post-destroy BasUrban_* count={len(spawned_after)}")
        assert n == len(URBAN_SCENE_CATALOG)
        assert len(spawned_after) == 0
    finally:
        stub.terminate()
        stub.wait(timeout=5)

    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
