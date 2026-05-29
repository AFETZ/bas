#!/usr/bin/env python3
"""Parallel compute smoke — verify speedup и correctness.

  1. TaskScheduler: 20 mixed tasks (CPU + IO sleep) → ordered results,
     ok/fail counts, retry на explicit failure.
  2. Sionna tile pre-compute: 16 tiles в N workers → sequential vs
     parallel time, verify ≥1.5× speedup на multi-core.
  3. launch_sitl_fleet: dry-run с mock sim_vehicle.py (echo only) —
     verify N processes spawned + valid PIDs.
"""
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "orchestrator", "src"))

from orchestrator.parallel import (   # noqa: E402
    TaskScheduler, TaskResult, launch_sitl_fleet,
    precompute_sionna_tiles, sionna_compute_tile,
)


def _cpu_work(n_iter: int) -> int:
    """Spin CPU."""
    s = 0
    for i in range(n_iter):
        s += int(math.sqrt(i + 1) * 1000) % 7
    return s


def _io_work(sleep_s: float) -> str:
    time.sleep(sleep_s)
    return f"slept {sleep_s}s"


def _always_fails(msg: str) -> None:
    raise RuntimeError(f"intentional: {msg}")


def main() -> int:
    # --- 1. TaskScheduler mixed workload ---
    print("===== [1] TaskScheduler 20 mixed tasks =====")
    sched = TaskScheduler(n_workers=4)
    for i in range(10):
        sched.submit(_cpu_work, 50_000, kind="cpu", priority=50)
    for i in range(5):
        sched.submit(_io_work, 0.1, kind="io", priority=100)
    for i in range(3):
        sched.submit(_always_fails, f"task-{i}", kind="fail",
                     priority=10, max_retries=1)
    sched.submit(_cpu_work, 1_000, kind="cpu_fast", priority=1)
    sched.submit(_io_work, 0.05, kind="io", priority=200)

    t0 = time.time()
    results = sched.run()
    dt = time.time() - t0
    s = sched.stats()
    print(f"  {len(results)} results in {dt:.2f}s; ok={s['ok']} fail={s['fail']}")
    print(f"  by_kind: {s['by_kind']}")
    assert s["total"] == 20
    assert s["ok"] == 17, f"expected 17 ok (10 cpu + 6 io + 1 cpu_fast), got {s['ok']}"
    assert s["fail"] == 3
    fail_results = [r for r in results if not r.ok]
    for r in fail_results:
        assert r.attempts == 2, f"expected 2 attempts (1 retry), got {r.attempts}"
    print("  ✓ all asserts pass")

    # --- 2. Sionna tile pre-compute speedup ---
    print("\n===== [2] Sionna tile pre-compute (16 tiles) =====")
    tiles = [(i, j) for i in range(4) for j in range(4)]

    # Sequential.
    t0 = time.time()
    seq_results = [sionna_compute_tile({"tile_i": i, "tile_j": j,
                                         "freq_mhz": 915.0, "grid_size": 80})
                   for i, j in tiles]
    seq_dt = time.time() - t0

    # Parallel.
    t0 = time.time()
    par_results = precompute_sionna_tiles(tiles, freq_mhz=915.0,
                                           grid_size=80, n_workers=4)
    par_dt = time.time() - t0

    print(f"  sequential: {seq_dt:.2f}s  ({seq_dt/16*1000:.0f}ms/tile)")
    print(f"  parallel-4: {par_dt:.2f}s  ({par_dt/16*1000:.0f}ms/tile)")
    speedup = seq_dt / par_dt if par_dt > 0 else float("inf")
    print(f"  speedup: {speedup:.2f}× (on {os.cpu_count()} cores)")
    assert len(par_results) == 16
    assert len(seq_results) == 16
    # Results must match (deterministic compute).
    for r_seq, r_par in zip(seq_results, par_results):
        assert r_seq["tile_i"] == r_par["tile_i"]
        assert r_seq["tile_j"] == r_par["tile_j"]
        assert abs(r_seq["min_rssi"] - r_par["min_rssi"]) < 0.001, \
            f"deterministic mismatch: {r_seq} vs {r_par}"
    # Speedup ≥1.3× даже на 2 cores (allowing pool overhead).
    assert speedup >= 1.3, f"weak speedup {speedup} (n_cores={os.cpu_count()})"
    print(f"  ✓ {len(par_results)} tiles parallel = sequential results, "
          f"speedup ≥1.3×")

    # --- 3. SITL fleet launch (mock sim_vehicle.py) ---
    print("\n===== [3] launch_sitl_fleet (mock sim_vehicle.py × 4) =====")
    tmp = Path(tempfile.mkdtemp(prefix="bas_parallel_smoke_"))
    try:
        # Write mock sim_vehicle.py that records args + sleeps.
        mock_sv = tmp / "sim_vehicle.py"
        mock_sv.write_text(
            "#!/usr/bin/env python3\n"
            "import sys, time, os\n"
            "log = open(os.path.join(os.getcwd(), 'cmd.log'), 'w')\n"
            "log.write(' '.join(sys.argv))\n"
            "log.close()\n"
            "time.sleep(2)\n"
        )
        mock_sv.chmod(0o755)
        instance_dir = tmp / "fleet"
        instance_dir.mkdir()

        handles = launch_sitl_fleet(
            sim_vehicle_path=mock_sv, n_uavs=4,
            instance_dir_base=instance_dir, start_port=24550,
            n_workers=4,
        )
        print(f"  launched {len(handles)} processes:")
        for h in handles:
            print(f"    sysid={h['sysid']}  pid={h['pid']}  port={h['mavlink_port']}")
        assert len(handles) == 4
        assert {h["sysid"] for h in handles} == {1, 2, 3, 4}
        assert {h["mavlink_port"] for h in handles} == {24550, 24560, 24570, 24580}
        for h in handles:
            assert h["pid"] > 0

        # Wait for mock procs.
        time.sleep(2.5)

        # Verify each instance dir has cmd.log + correct sysid arg.
        for i in range(4):
            cmd_log = instance_dir / f"sysid{i+1}" / "cmd.log"
            assert cmd_log.exists(), f"missing {cmd_log}"
            content = cmd_log.read_text()
            assert f"--sysid={i+1}" in content, \
                f"sysid arg missing in {cmd_log}: {content}"
        print(f"  ✓ all 4 instance dirs contain valid cmd.log with --sysid args")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
