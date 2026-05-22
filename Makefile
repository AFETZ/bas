# bas-prototype — common targets
#
# Использовать GNU make. Запускать из корня репо.

REPO_ROOT := $(shell pwd)
VENV := $(REPO_ROOT)/.venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: help bootstrap bootstrap-full clean clean-logs venv test smoke \
        smoke-1.5.2 smoke-1.7 smoke-1.8 smoke-2.4 \
        demo demo-fpv demo-rf demo-qgc demo-multi demo-airsim demo-auto \
        lint syntax docs check ci-local

help:
	@echo "BAS Prototype — Makefile targets"
	@echo ""
	@echo "Setup:"
	@echo "  bootstrap         — sudo bash scripts/bootstrap.sh (minimal)"
	@echo "  bootstrap-full    — sudo bash scripts/bootstrap.sh --full (с Sionna + AirSim)"
	@echo "  venv              — только Python venv + pip install"
	@echo ""
	@echo "Smoke tests:"
	@echo "  smoke             — all smoke tests (1.5.2 + 1.7 + 1.8)"
	@echo "  smoke-1.5.2       — mission + RTP video"
	@echo "  smoke-1.7         — LoRa Serial"
	@echo "  smoke-1.8         — MAVROS"
	@echo "  smoke-2.4         — Web GCS RF demo headless"
	@echo ""
	@echo "Demos (интерактивные):"
	@echo "  demo              — auto_demo recorder (Playwright + ffmpeg)"
	@echo "  demo-fpv          — FPV + RF live"
	@echo "  demo-rf           — RF demo (obstacles + RSSI)"
	@echo "  demo-qgc          — QGroundControl bridge"
	@echo "  demo-multi        — Multi-UAV (2 SITL)"
	@echo "  demo-airsim       — Cosys-AirSim overlay (stub mode)"
	@echo ""
	@echo "Quality:"
	@echo "  lint              — shellcheck + python flake8"
	@echo "  syntax            — bash -n + python -m py_compile"
	@echo "  check             — lint + syntax"
	@echo "  ci-local          — что прогоняет GitHub Actions локально"
	@echo ""
	@echo "Misc:"
	@echo "  clean-logs        — очистить logs/ и output/"
	@echo "  clean             — clean-logs + venv + cosys-airsim"
	@echo "  docs              — печатает links к docs/"

# ---------------------------------------------------------------- bootstrap
bootstrap:
	sudo bash scripts/bootstrap.sh

bootstrap-full:
	sudo bash scripts/bootstrap.sh --full

venv:
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e ./orchestrator -e ./analyzer
	.venv/bin/pip install msgpack playwright pymavlink mavproxy pyyaml requests
	.venv/bin/playwright install chromium

# ---------------------------------------------------------------- smoke
smoke: smoke-1.5.2 smoke-1.7 smoke-1.8

smoke-1.5.2:
	sudo bash scripts/run_stage_1_5_2_mission.sh wifi_good

smoke-1.7:
	sudo bash scripts/run_stage_1_7_lora_serial.sh

smoke-1.8:
	sudo bash scripts/run_stage_1_8_mavros.sh baseline_wifi

smoke-2.4:
	sudo env BAS_GAZEBO_GUI=0 bash scripts/run_stage_2_4_rf_demo.sh

# ---------------------------------------------------------------- demos
demo:
	sudo bash scripts/run_stage_2_4_auto_demo.sh

demo-fpv:
	sudo bash scripts/run_stage_2_4_fpv_rf_demo.sh

demo-rf:
	sudo bash scripts/run_stage_2_4_rf_demo.sh

demo-qgc:
	sudo bash scripts/run_stage_2_4_qgc_demo.sh

demo-multi:
	sudo bash scripts/run_stage_2_4_multi_uav_demo.sh

demo-airsim:
	sudo bash scripts/run_stage_2_2_airsim_overlay.sh

demo-airsim-linux:
	sudo env BAS_AIRSIM_MODE=linux bash scripts/run_stage_2_2_airsim_overlay.sh

demo-airsim-windows:
	sudo env BAS_AIRSIM_MODE=windows bash scripts/run_stage_2_2_airsim_overlay.sh

demo-rt-online:
	sudo bash scripts/run_stage_2_4_rt_online_demo.sh

# ---------------------------------------------------------------- quality
lint:
	@echo ">>> shellcheck on bash scripts"
	@find scripts -name "*.sh" -not -path "*/__pycache__/*" -print0 \
	  | xargs -0 -n1 shellcheck -e SC1091,SC2086,SC2155 2>/dev/null || true
	@echo ">>> python flake8 (optional)"
	@command -v $(VENV)/bin/flake8 >/dev/null && \
	  $(VENV)/bin/flake8 --max-line-length=120 scripts/*.py orchestrator/ || \
	  echo "flake8 not installed, skip"

syntax:
	@echo ">>> bash -n on all .sh"
	@for f in scripts/*.sh scripts/run_stage_*.sh; do \
	  bash -n "$$f" && echo "  OK $$f" || echo "  FAIL $$f"; \
	done
	@echo ">>> python AST"
	@for f in scripts/*.py; do \
	  $(PY) -c "import ast; ast.parse(open('$$f').read())" 2>/dev/null \
	    && echo "  OK $$f" || echo "  FAIL $$f"; \
	done

check: syntax lint

ci-local: syntax
	@echo ">>> Stub-mode AirSim smoke"
	@($(PY) scripts/airsim_stub_server.py --port 41452 --pose-log /tmp/ci_stub.jsonl > /tmp/ci_stub.log 2>&1 &) ; \
	  sleep 2 ; \
	  $(PY) scripts/airsim_client.py --port 41452 ; \
	  pkill -f "airsim_stub_server.*41452" 2>/dev/null || true
	@echo ">>> Web GCS demo mode probe"
	@($(PY) scripts/gcs_web_ui_server.py --demo --port 18765 > /tmp/ci_ui.log 2>&1 &) ; \
	  sleep 3 ; \
	  curl -s -m 3 http://127.0.0.1:18765/api/health | head -c 100 ; \
	  echo "" ; \
	  pkill -f "gcs_web_ui_server.*18765" 2>/dev/null || true

# ---------------------------------------------------------------- clean
clean-logs:
	rm -rf logs/* output/* /tmp/bas_*.json /tmp/blocks*.log /tmp/airsim_*

clean: clean-logs
	rm -rf .venv sionna_env
	rm -rf ~/cosys-airsim

# ---------------------------------------------------------------- docs
docs:
	@echo "Documentation entrypoints:"
	@echo "  README.md                        — overview + быстрый старт"
	@echo "  docs/INSTALL.md                  — пошаговая установка"
	@echo "  docs/QUICKSTART.md               — каталог команд"
	@echo "  docs/ARCHITECTURE.md             — полная архитектура"
	@echo "  docs/DEMOS.md                    — wrappers + env vars"
	@echo "  docs/STAGES.md                   — этапы разработки"
	@echo "  docs/TROUBLESHOOTING.md          — частые проблемы"
	@echo "  docs/CONTRIBUTING.md             — guidelines"
	@echo "  docs/architecture.md             — исторический отчёт"
	@echo "  docs/tz_compliance.md            — матрица ТЗ"
	@echo "  docs/roadmap.md                  — backlog (всё closed)"
	@echo "  docs/stage_*.md                  — детальные планы каждого stage"
	@echo "  CHANGELOG.md                     — история релизов"
