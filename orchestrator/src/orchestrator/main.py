"""CLI оркестратора."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .run import run_scenario


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bas-orchestrator",
        description="Сценарный оркестратор первого прототипа среды моделирования БАС.",
    )
    parser.add_argument(
        "scenario_id",
        help="ID сценария (имя файла без расширения в configs/scenarios/).",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[3],
        help="Корень проекта bas-prototype (по умолчанию определяется автоматически).",
    )
    parser.add_argument(
        "--real",
        action="store_true",
        help="Запустить с реальными компонентами через Docker (требует собранных образов).",
    )
    parser.add_argument(
        "--mavlink-endpoint",
        default="tcp:127.0.0.1:5760",
        help="MAVLink endpoint для подключения к SITL (по умолчанию loopback). "
             "Для этапа 1.5.1+ через ns-3 указать tcp:10.10.0.2:5760. "
             "Для --mavlink-backend=mavros формат fcu_url: udp://@:14550.",
    )
    parser.add_argument(
        "--mavlink-backend",
        choices=["pymavlink", "mavros"],
        default="pymavlink",
        help="Выбор MAVLink backend: pymavlink (default, текущая реализация) "
             "или mavros (этап 1.8: ROS2 humble + MAVROS 2.14 через docker bas/mavros:dev). "
             "Backend влияет только на --real путь; stub-режим использует свою эмуляцию.",
    )
    parser.add_argument(
        "--external-compose",
        action="store_true",
        help="Контейнеры подняты внешним скриптом. Оркестратор НЕ делает compose up/down "
             "и не ждёт open порт 5760 — сразу подключается к --mavlink-endpoint.",
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=None,
        help="Использовать заданный каталог прогона (а не генерировать). "
             "Нужно когда host-скрипт уже создал каталог под run_id.",
    )
    args = parser.parse_args(argv)

    run_dir = run_scenario(
        scenario_id=args.scenario_id,
        project_root=args.project_root,
        stub=not args.real,
        mavlink_endpoint=args.mavlink_endpoint,
        mavlink_backend=args.mavlink_backend,
        external_compose=args.external_compose,
        run_dir_override=args.run_dir,
    )
    print(f"Прогон завершён. Логи: {run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
