"""Чтение JSONL-журнала прогона."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def load_events(log_path: Path) -> list[dict[str, Any]]:
    """Читает events.jsonl построчно. Пропускает пустые строки и битый JSON
    с предупреждением (битый JSON допустим только из-за обрыва записи)."""
    events: list[dict[str, Any]] = []
    with log_path.open("r", encoding="utf-8") as f:
        for line_no, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"WARN: битая строка {line_no} в {log_path}: {exc}")
    return events


def group_by_event_type(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ev in events:
        groups[ev.get("event_type", "<unknown>")].append(ev)
    return dict(groups)


def find_run_dir(arg: str) -> Path:
    """Принимает или путь к каталогу прогона, или путь к events.jsonl."""
    p = Path(arg)
    if p.is_file():
        return p.parent
    if p.is_dir():
        return p
    raise FileNotFoundError(f"Не найдено: {arg}")
