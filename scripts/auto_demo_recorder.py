#!/usr/bin/env python3
"""Stage 2.4 Auto Demo Recorder.

Один command даёт готовое демо-видео + screenshots + Markdown отчёт для
grant отчётности и регрессионного visual diff между прогонами.

Цепочка:
    run_stage_2_4_*_demo.sh stack (Gazebo + SITL + ns-3 + Web GCS) уже
    запущен (wrapper делает это до вызова recorder).

    AutoDemoRecorder делает:
      1. Wait для готовности /api/health и появления mavlink state.
      2. Стартует ffmpeg subprocess который пишет /camera.mjpg → fpv.mp4
         (если FPV доступен).
      3. Открывает headless Chromium через Playwright, navigate to Web GCS,
         включает video recording (контекст-level).
      4. Шлёт серию /api/command + /api/goto согласно жёсткой траектории:
         GUIDED → ARM → TAKEOFF → GO TO forward → GO TO hangar (LOS) →
         GO TO behind hangar (NLOS) → GO TO return → LAND.
         Между шагами polls /api/state до достижения цели либо timeout.
      5. На ключевых моментах берёт screenshot (Web GCS canvas).
      6. После LAND собирает все артефакты + парсит report.md если есть.
      7. Генерирует logs/<run_id>/demo_report.md с:
         - Сборная timeline скриншотов
         - Ссылки на видео (Web GCS, FPV)
         - Метрики (waypoints reached, distance, max RSSI loss)
         - Voice-of-truth events (events.jsonl key milestones)

Пользователю выдаётся:
    logs/<run_id>/demo_report.md
    logs/<run_id>/web_gcs.webm        ← Playwright capture
    logs/<run_id>/fpv.mp4             ← ffmpeg capture (опционально)
    logs/<run_id>/screenshots/        ← key moments

Pattern базируется на установившейся практике headless E2E recording
(Playwright video record + parallel ffmpeg для side-streams). Без специфичных
зависимостей — Playwright + ffmpeg + stdlib.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]


# -----------------------------------------------------------------------------
# Trajectory: упорядоченный список (action, params, описание, время ожидания
# чтобы дать дрону физически приблизиться к цели).
#
# Используем координаты "вокруг ангара" (rf_demo world):
#   * ангар "Hangar" в (45, 0) размер 20×32×18м (см. RF_OBSTACLES в gcs_web_ui_server)
#   * GCS mast в (0, -60)
#
# Цель — пролететь так, чтобы UI зафиксировал и LOS-зону, и NLOS-spike-и.
# -----------------------------------------------------------------------------
@dataclass
class DemoStep:
    label: str
    api: str                    # "command" или "goto"
    payload: dict[str, Any]
    wait_s: float = 2.0         # минимум подождать после команды
    reach_check: bool = False   # если True — ждать local position в radius пока не timeout
    reach_north: float | None = None
    reach_east: float | None = None
    reach_alt: float | None = None
    reach_tol_m: float = 4.0
    reach_timeout_s: float = 30.0
    screenshot: str | None = None   # если задан — имя файла без расширения
    fpv_screenshot: bool = False     # сделать FPV snapshot


DEFAULT_TRAJECTORY: list[DemoStep] = [
    DemoStep("guided", "command", {"action": "guided"}, wait_s=2.0),
    DemoStep("arm", "command", {"action": "arm"}, wait_s=3.0,
             screenshot="01_armed"),
    DemoStep("takeoff_10m", "command", {"action": "takeoff", "altitude": 10.0},
             wait_s=12.0, screenshot="02_takeoff", fpv_screenshot=True),
    DemoStep("forward_to_runway", "goto", {"north": 30.0, "east": 0.0},
             wait_s=2.0, reach_check=True, reach_north=30.0, reach_east=0.0,
             reach_tol_m=5.0, reach_timeout_s=35.0, screenshot="03_forward"),
    DemoStep("approach_hangar_los", "goto", {"north": 55.0, "east": -25.0},
             wait_s=2.0, reach_check=True, reach_north=55.0, reach_east=-25.0,
             reach_tol_m=5.0, reach_timeout_s=35.0,
             screenshot="04_hangar_los", fpv_screenshot=True),
    DemoStep("behind_hangar_nlos", "goto", {"north": 55.0, "east": 25.0},
             wait_s=2.0, reach_check=True, reach_north=55.0, reach_east=25.0,
             reach_tol_m=5.0, reach_timeout_s=40.0,
             screenshot="05_hangar_nlos", fpv_screenshot=True),
    DemoStep("flyover_hangar", "goto", {"north": 30.0, "east": 0.0},
             wait_s=2.0, reach_check=True, reach_north=30.0, reach_east=0.0,
             reach_tol_m=5.0, reach_timeout_s=35.0, screenshot="06_return"),
    DemoStep("home", "goto", {"north": 0.0, "east": 0.0},
             wait_s=2.0, reach_check=True, reach_north=0.0, reach_east=0.0,
             reach_tol_m=4.0, reach_timeout_s=35.0, screenshot="07_home"),
    DemoStep("land", "command", {"action": "land"}, wait_s=10.0,
             screenshot="08_land"),
    DemoStep("disarm_safety", "command", {"action": "disarm"}, wait_s=2.0,
             screenshot="09_disarmed"),
]


# -----------------------------------------------------------------------------
# Helpers: HTTP к Web GCS.
# -----------------------------------------------------------------------------
def http_get_json(url: str, timeout: float = 3.0) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post_json(url: str, payload: dict[str, Any], timeout: float = 5.0) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wait_ui_ready(ui_url: str, timeout_s: float = 60.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            health = http_get_json(f"{ui_url}/api/health")
            if health.get("ok"):
                print(f"[recorder] UI ready: {ui_url}", flush=True)
                return
        except Exception:
            pass
        time.sleep(1.0)
    raise SystemExit(f"UI did not become ready at {ui_url} within {timeout_s}s")


def wait_state_predicate(
    ui_url: str,
    predicate,
    timeout_s: float,
    label: str,
) -> dict[str, Any]:
    """Polling /api/state до тех пор пока predicate(state) не True."""
    deadline = time.time() + timeout_s
    last_state = None
    while time.time() < deadline:
        try:
            state = http_get_json(f"{ui_url}/api/state")
            last_state = state
            if predicate(state):
                return state
        except Exception:
            pass
        time.sleep(0.4)
    print(f"[recorder] WARN: {label} not reached within {timeout_s}s",
          flush=True)
    return last_state or {}


# -----------------------------------------------------------------------------
# FPV recording helper: ffmpeg subprocess.
# -----------------------------------------------------------------------------
class FpvRecorder:
    def __init__(self, source_url: str, out_path: Path, duration_s: float):
        self.source_url = source_url
        self.out_path = out_path
        self.duration_s = duration_s
        self.proc: subprocess.Popen | None = None

    def start(self) -> bool:
        # Probe: если эндпоинт не отвечает 200/multipart — пропускаем
        # запись. socat в bas-uav netns форкает child процесс на каждое
        # UDP подключение и может race-condition'ить на старте — поэтому
        # делаем несколько retry с паузой (gst-pipeline ещё warm-up).
        last_err: Exception | None = None
        for attempt in range(6):
            try:
                with urllib.request.urlopen(self.source_url, timeout=4.0) as r:
                    ct = r.headers.get("Content-Type", "")
                    if "multipart" in ct:
                        last_err = None
                        break
                    print(f"[recorder] FPV upstream not multipart ({ct}, "
                          f"attempt {attempt+1}/6)", flush=True)
            except Exception as exc:
                last_err = exc
                print(f"[recorder] FPV probe attempt {attempt+1}/6: {exc}",
                      flush=True)
            time.sleep(3.0)
        if last_err is not None:
            print(f"[recorder] FPV upstream not reachable after 6 attempts;"
                  f" skipping FPV record", flush=True)
            return False

        # ffmpeg -i http://.../camera.mjpg -c copy -t <duration> out.mp4
        # mjpeg → mp4 (via copy) даёт MotionJPEG-in-MP4 контейнер. Совместимо
        # с QuickTime/VLC/Chrome video player.
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "warning",
            "-y",
            "-f", "mjpeg", "-use_wallclock_as_timestamps", "1",
            "-i", self.source_url,
            "-c:v", "copy",
            "-t", str(int(self.duration_s)),
            str(self.out_path),
        ]
        print(f"[recorder] ffmpeg FPV → {self.out_path.name}", flush=True)
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        return True

    def stop(self) -> None:
        if not self.proc:
            return
        # Дать ffmpeg flush time, потом терминировать.
        try:
            self.proc.terminate()
            self.proc.wait(timeout=4.0)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=2.0)
        self.proc = None


# -----------------------------------------------------------------------------
# Main orchestration.
# -----------------------------------------------------------------------------
@dataclass
class RunSummary:
    started_at: float
    finished_at: float = 0.0
    steps: list[dict[str, Any]] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)
    fpv_path: str | None = None
    web_video_path: str | None = None
    notes: list[str] = field(default_factory=list)


def execute_demo(
    ui_url: str,
    log_dir: Path,
    trajectory: list[DemoStep],
    duration_budget_s: float,
) -> RunSummary:
    from playwright.sync_api import sync_playwright

    screenshots_dir = log_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    video_dir = log_dir / "video"
    video_dir.mkdir(parents=True, exist_ok=True)

    summary = RunSummary(started_at=time.time())

    # FPV recorder в параллель (если доступен).
    fpv = FpvRecorder(
        source_url=f"{ui_url}/camera.mjpg",
        out_path=video_dir / "fpv.mjpeg.mp4",
        duration_s=duration_budget_s,
    )
    if fpv.start():
        summary.fpv_path = str(fpv.out_path.relative_to(log_dir))
    else:
        summary.notes.append("FPV stream недоступен в этом сценарии — "
                             "fpv.mp4 не записан (запусти с BAS_GCS_FPV=1).")

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=[
                "--no-sandbox",            # WSL2 sandbox issues
                "--disable-dev-shm-usage", # /dev/shm may be small
            ])
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                record_video_dir=str(video_dir),
                record_video_size={"width": 1440, "height": 900},
            )
            page = context.new_page()
            page.set_default_timeout(8000)
            print(f"[recorder] open {ui_url}", flush=True)
            page.goto(ui_url, wait_until="domcontentloaded")

            # Дать UI первый refresh tick (250-650 мс).
            page.wait_for_timeout(1500)
            page.screenshot(path=str(screenshots_dir / "00_initial.png"))
            summary.screenshots.append("00_initial.png")

            # Цикл по trajectory.
            for idx, step in enumerate(trajectory):
                step_start = time.time()
                print(f"[recorder] step {idx+1}/{len(trajectory)}: {step.label}",
                      flush=True)
                try:
                    if step.api == "command":
                        resp = http_post_json(
                            f"{ui_url}/api/command", step.payload,
                        )
                    elif step.api == "goto":
                        resp = http_post_json(
                            f"{ui_url}/api/goto", step.payload,
                        )
                    else:
                        raise ValueError(f"unknown api {step.api}")
                    step_record = {
                        "label": step.label, "api": step.api,
                        "payload": step.payload, "response": resp,
                        "started_dt": step_start - summary.started_at,
                    }
                except Exception as exc:
                    step_record = {
                        "label": step.label, "api": step.api,
                        "payload": step.payload, "error": str(exc),
                        "started_dt": step_start - summary.started_at,
                    }

                # Минимальное ожидание стабилизации.
                time.sleep(step.wait_s)

                # Если шаг требует достижения позиции — polling.
                if step.reach_check and step.reach_north is not None:
                    rn, re_ = step.reach_north, step.reach_east or 0.0
                    tol = step.reach_tol_m

                    def reached(st: dict[str, Any]) -> bool:
                        ne = st.get("local") or {}
                        nv = ne.get("north")
                        ev = ne.get("east")
                        if nv is None or ev is None:
                            return False
                        d = ((nv - rn) ** 2 + (ev - re_) ** 2) ** 0.5
                        return d <= tol

                    last = wait_state_predicate(
                        ui_url, reached, step.reach_timeout_s, step.label,
                    )
                    step_record["reached_state"] = {
                        "armed": last.get("armed"),
                        "mode": last.get("mode"),
                        "local": last.get("local"),
                    }

                # Screenshot для report'а.
                if step.screenshot:
                    shot_path = screenshots_dir / f"{step.screenshot}.png"
                    try:
                        page.screenshot(path=str(shot_path))
                        summary.screenshots.append(shot_path.name)
                        step_record["screenshot"] = shot_path.name
                    except Exception as exc:
                        step_record["screenshot_error"] = str(exc)

                # FPV отдельный кадр (просто статичный JPEG из multipart).
                if step.fpv_screenshot:
                    try:
                        fpv_shot = screenshots_dir / f"{step.screenshot}_fpv.jpg"
                        # Захват первого кадра из MJPEG через ffmpeg.
                        subprocess.run([
                            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                            "-f", "mjpeg",
                            "-i", f"{ui_url}/camera.mjpg",
                            "-frames:v", "1",
                            str(fpv_shot),
                        ], timeout=6.0, check=False)
                        if fpv_shot.exists():
                            summary.screenshots.append(fpv_shot.name)
                            step_record["fpv_screenshot"] = fpv_shot.name
                    except Exception as exc:
                        step_record["fpv_screenshot_error"] = str(exc)

                summary.steps.append(step_record)

            # Финальный snapshot.
            page.screenshot(path=str(screenshots_dir / "99_final.png"))
            summary.screenshots.append("99_final.png")

            # Завершить video — Playwright дописывает webm после close.
            context.close()
            browser.close()

            # Найти Playwright webm файл (он именуется случайно).
            webm_files = sorted(video_dir.glob("*.webm"))
            if webm_files:
                renamed = video_dir / "web_gcs.webm"
                webm_files[0].rename(renamed)
                summary.web_video_path = str(renamed.relative_to(log_dir))
    finally:
        fpv.stop()
        summary.finished_at = time.time()

    return summary


# -----------------------------------------------------------------------------
# Report assembly.
# -----------------------------------------------------------------------------
def parse_existing_report(log_dir: Path) -> dict[str, str]:
    """Извлекает несколько ключевых строк из существующего report.md."""
    report = log_dir / "report.md"
    if not report.exists():
        return {}
    out: dict[str, str] = {}
    for line in report.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        for key in ("Waypoint", "Дистанция", "Финальный режим",
                    "Длительность миссии", "Максимальная высота",
                    "Максимальная скорость", "Статус сценария"):
            if key in line:
                out[key] = line
                break
    return out


def emit_demo_report(log_dir: Path, summary: RunSummary) -> Path:
    rep_path = log_dir / "demo_report.md"
    existing = parse_existing_report(log_dir)
    duration = summary.finished_at - summary.started_at

    lines: list[str] = [
        f"# Stage 2.4 Auto Demo — `{log_dir.name}`",
        "",
        f"- Сценарий записан: **{time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime(summary.started_at))}**",
        f"- Длительность записи: **{duration:.1f} с**",
        f"- Шагов выполнено: **{len(summary.steps)}**",
        f"- Скриншотов: **{len(summary.screenshots)}**",
        "",
        "## Артефакты",
        "",
    ]
    if summary.web_video_path:
        lines += [
            f"- Web GCS video: [{summary.web_video_path}]({summary.web_video_path})",
        ]
    if summary.fpv_path:
        lines += [
            f"- FPV onboard video: [{summary.fpv_path}]({summary.fpv_path})",
        ]
    if not summary.web_video_path and not summary.fpv_path:
        lines += ["_Видео не записано._"]
    lines += [""]

    if existing:
        lines += [
            "## Метрики из orchestrator report",
            "",
        ]
        for v in existing.values():
            lines += [f"- {v}"]
        lines += [""]

    lines += [
        "## Timeline",
        "",
        "| # | Шаг | API | Payload | Reached | Screenshot |",
        "|---|---|---|---|---|---|",
    ]
    for i, st in enumerate(summary.steps, 1):
        payload_str = json.dumps(st.get("payload", {}), ensure_ascii=False)
        reached = st.get("reached_state", {})
        reached_str = (
            f"N={reached.get('local', {}).get('north'):.1f} "
            f"E={reached.get('local', {}).get('east'):.1f}"
            if reached.get("local") and
               isinstance(reached["local"].get("north"), (int, float))
            else "—"
        )
        shot = st.get("screenshot", "")
        fpv_shot = st.get("fpv_screenshot", "")
        shot_str = f"`{shot}`" + (f" + `{fpv_shot}`" if fpv_shot else "")
        lines += [
            f"| {i} | {st['label']} | {st['api']} | `{payload_str}` | {reached_str} | {shot_str} |",
        ]
    lines += [""]

    if summary.screenshots:
        lines += [
            "## Скриншоты ключевых моментов",
            "",
        ]
        for shot in summary.screenshots:
            lines += [
                f"### {shot}",
                "",
                f"![{shot}](screenshots/{shot})",
                "",
            ]

    if summary.notes:
        lines += ["## Примечания", ""]
        for note in summary.notes:
            lines += [f"- {note}"]
        lines += [""]

    rep_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[recorder] demo report → {rep_path}", flush=True)
    return rep_path


# -----------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Stage 2.4 Auto Demo Recorder")
    ap.add_argument("--ui-url", default="http://127.0.0.1:8765",
                    help="Базовый URL Web GCS")
    ap.add_argument("--log-dir", required=True,
                    help="Папка прогона (logs/<run_id>/) для артефактов")
    ap.add_argument("--ui-ready-timeout", type=float, default=90.0,
                    help="Сколько ждать /api/health")
    ap.add_argument("--duration-budget-s", type=float, default=240.0,
                    help="Верхняя планка длительности demo (для ffmpeg -t)")
    args = ap.parse_args()

    log_dir = Path(args.log_dir).resolve()
    log_dir.mkdir(parents=True, exist_ok=True)

    if shutil.which("ffmpeg") is None:
        print("[recorder] WARN: ffmpeg not installed; FPV/Web video disabled",
              file=sys.stderr, flush=True)

    # Wait until UI отвечает.
    wait_ui_ready(args.ui_url, args.ui_ready_timeout)

    # Запускаем сценарий.
    summary = execute_demo(
        ui_url=args.ui_url,
        log_dir=log_dir,
        trajectory=DEFAULT_TRAJECTORY,
        duration_budget_s=args.duration_budget_s,
    )

    # Собираем отчёт.
    emit_demo_report(log_dir, summary)

    return 0


if __name__ == "__main__":
    sys.exit(main())
