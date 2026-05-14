"""CLI анализатора."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .load import find_run_dir, load_run_events
from .metrics import compute
from .report import to_markdown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bas-analyzer",
        description="Анализатор журналов прогонов: метрики и markdown-отчёт.",
    )
    parser.add_argument(
        "run_path",
        help="Путь к каталогу прогона (logs/<run_id>) либо к events.jsonl.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Куда сохранить markdown-отчёт. По умолчанию <run_dir>/report.md",
    )
    args = parser.parse_args(argv)

    run_dir = find_run_dir(args.run_path)
    log_path = run_dir / "events.jsonl"
    if not log_path.exists():
        print(f"Не найден журнал событий: {log_path}", file=sys.stderr)
        return 2

    events = load_run_events(run_dir)
    report = compute(events)
    md = to_markdown(report)

    out_path = args.out or (run_dir / "report.md")
    out_path.write_text(md, encoding="utf-8")
    print(md)
    print(f"\nОтчёт сохранён: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
