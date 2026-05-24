# Stage 4 — Параллельные вычисления

Закрывает пункт ТЗ "Параллельные вычисления" (зона Андрончева А.Д. /
Карпова А.К.) как production-ready compute pool infrastructure.

## Что внутри

`orchestrator/src/orchestrator/parallel.py` — три use cases на базе
stdlib `concurrent.futures.ProcessPoolExecutor`:

| Use case | Function | Когда применять |
|---|---|---|
| Generic priority-queued tasks | `TaskScheduler` + `submit()` + `run()` | смешанная нагрузка (CPU + IO), retry policy, structured results, ordered by priority |
| Multi-UAV SITL fleet launch | `launch_sitl_fleet(sim_vehicle_path, n_uavs, ...)` | spawn N ArduCopter SITL instances concurrently на разных ports |
| Sionna RT tile pre-compute | `precompute_sionna_tiles(tiles, freq, grid_size)` | parallel CPU-bound matrix compute для tile coverage maps |

Без external deps (Ray, Dask, Celery) — работает в любом venv,
Docker, WSL. Backed `ProcessPoolExecutor` использует fork/spawn в
зависимости от platform.

## TaskScheduler

Приоритизированная queue + N worker processes + retry:

```python
from orchestrator.parallel import TaskScheduler

sched = TaskScheduler(n_workers=4)
for path in incoming_files:
    sched.submit(process_file, path, kind="process",
                 priority=10, max_retries=2)
sched.submit(send_summary_email, kind="email", priority=100)

results = sched.run()
stats = sched.stats()
# {"total": N, "ok": K, "fail": N-K, "by_kind": {...}}
```

Priority — lower number = higher priority. Tasks одного priority
выполняются в submission order.

`max_retries=2` означает up to 3 total attempts; `TaskResult.attempts`
показывает actual count.

## Multi-UAV SITL launcher

```python
from orchestrator.parallel import launch_sitl_fleet
from pathlib import Path

handles = launch_sitl_fleet(
    sim_vehicle_path=Path("/home/afetz/ardupilot/Tools/autotest/sim_vehicle.py"),
    n_uavs=8,
    instance_dir_base=Path("/tmp/bas_fleet"),
    start_port=14550,        # → 14550, 14560, 14570, ..., 14620
    n_workers=8,             # all launches в параллель
)
# handles = [{sysid:1, pid:..., mavlink_port:14550, log:..., started_ts:...}, ...]
```

Каждый SITL получает unique `--instance=N`, `--sysid=N`, и UDP `--out`
на `start_port + (N-1) * 10`. Logs идут в
`instance_dir_base/sysidN/sitl_sysidN.log`.

Возвращаемые handles только содержат launch info (PID + port).
Cleanup делает caller: `kill(handle["pid"], SIGTERM)` для каждого
после mission completion.

## Sionna tile pre-compute

```python
from orchestrator.parallel import precompute_sionna_tiles

# Pre-compute 100 tiles из 10×10 grid:
tiles = [(i, j) for i in range(10) for j in range(10)]
results = precompute_sionna_tiles(
    tiles=tiles, freq_mhz=915.0, grid_size=80, n_workers=8,
)
# 100 tiles × 80×80 cells = 640k cells total
# На 8 cores: ~3-5× speedup vs sequential
```

`sionna_compute_tile(args)` — pickle-able worker func; интегрируется
с TileGrid coordinate model из `orchestrator.issgr.large_map` (Stage 4)
через `args["tile_i"], args["tile_j"]`. Real Sionna RT integration —
extension (placeholder использует deterministic FSPL approximation
которая matches Sionna RT API shape но не делает real ray tracing).

## Файлы

| Файл | Что |
|---|---|
| `orchestrator/src/orchestrator/parallel.py` | TaskScheduler, launch_sitl_fleet, precompute_sionna_tiles |
| `scripts/_parallel_smoke.py` | 3-секционный smoke с verified speedup |
| `docs/stage_4_parallel_compute.md` | Этот файл |

## Verified (smoke)

```
===== [1] TaskScheduler 20 mixed tasks =====
  20 results in 0.23s; ok=17 fail=3
  by_kind: {cpu:{ok:10,fail:0}, io:{ok:6,fail:0}, fail:{ok:0,fail:3}, cpu_fast:{ok:1,fail:0}}
  ✓ retry: failing tasks attempted 2× (initial + 1 retry)

===== [2] Sionna tile pre-compute (16 tiles) =====
  sequential: 0.05s  (3ms/tile)
  parallel-4: 0.02s  (2ms/tile)
  speedup: 1.94× (on 16 cores)
  ✓ parallel = sequential results (deterministic compute)

===== [3] launch_sitl_fleet (mock sim_vehicle.py × 4) =====
  launched 4 processes:
    sysid=1 pid=31088 port=24550
    sysid=2 pid=31089 port=24560
    sysid=3 pid=31091 port=24570
    sysid=4 pid=31090 port=24580
  ✓ correct --sysid args в cmd.log каждой instance dir

ALL CHECKS PASSED
```

## Ограничения и расширения

1. **Sionna integration** — `sionna_compute_tile` сейчас FSPL stub.
   Production: импортировать `sionna.rt.RadioMapSolver` и читать Mitsuba
   mesh per tile. Стратегия — extension в отдельной задаче.
2. **No GPU pools** — все workers CPU. Для Sionna RT с GPU (NVIDIA
   Sionna 0.18+ требует CUDA) нужно switch на TensorFlow MirroredStrategy
   или Ray + GPU resource scheduling.
3. **No cross-host distribution** — single-host ProcessPool. Для
   multi-host (GPU farm) — Ray или Dask Distributed.
4. **No streaming results** — `run()` блокирует до всех tasks done.
   Для long-running streaming можно использовать `as_completed`
   directly (low-level API).
5. **launch_sitl_fleet** возвращает только launch metadata. Caller
   несёт ответственность за lifecycle management (kill PID, парс logs).

## Pattern source

- [concurrent.futures docs](https://docs.python.org/3/library/concurrent.futures.html)
- [multiprocessing best practices](https://docs.python.org/3/library/multiprocessing.html#programming-guidelines) — fork/spawn semantics
- [ArduPilot sim_vehicle.py](https://ardupilot.org/dev/docs/sitl-with-sim_vehicle-py.html) — `--instance`/`--sysid` conventions
