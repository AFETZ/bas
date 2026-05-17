"""CLI для сравнения двух прогонов (этап 1.6)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .compare import load_labeled_run, to_compare_csv, to_compare_markdown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bas-analyzer-compare",
        description="Сравнительный анализ двух прогонов (этап 1.6, WiFi vs LoRa).",
    )
    parser.add_argument("run_a", help="Путь к каталогу первого прогона.")
    parser.add_argument("run_b", help="Путь к каталогу второго прогона.")
    parser.add_argument(
        "--label-a", default="A",
        help="Подпись для первого прогона в отчёте (например, wifi_good).",
    )
    parser.add_argument(
        "--label-b", default="B",
        help="Подпись для второго прогона (например, degraded_lora).",
    )
    parser.add_argument(
        "--out-dir", type=Path, required=True,
        help="Куда сложить comparison.md и comparison.csv.",
    )
    args = parser.parse_args(argv)

    a = load_labeled_run(args.run_a, args.label_a)
    b = load_labeled_run(args.run_b, args.label_b)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    md_path = args.out_dir / "comparison.md"
    csv_path = args.out_dir / "comparison.csv"

    md_path.write_text(to_compare_markdown(a, b), encoding="utf-8")
    csv_path.write_text(to_compare_csv(a, b), encoding="utf-8")

    print(f"comparison.md → {md_path}")
    print(f"comparison.csv → {csv_path}")
    print()
    print(md_path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
