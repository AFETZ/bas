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
    args = parser.parse_args(argv)

    run_dir = run_scenario(
        scenario_id=args.scenario_id,
        project_root=args.project_root,
        stub=not args.real,
    )
    print(f"Прогон завершён. Логи: {run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
