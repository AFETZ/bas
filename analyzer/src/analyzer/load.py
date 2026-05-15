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


def load_run_events(run_dir: Path) -> list[dict[str, Any]]:
    """Читает основной журнал и, если есть, журнал ns-3 в том же каталоге."""
    events = load_events(run_dir / "events.jsonl")
    ns3_path = run_dir / "ns3_events.jsonl"
    if ns3_path.exists():
        events.extend(load_events(ns3_path))
    return events


def load_video_events(run_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Читает video_tx.jsonl и video_rx.jsonl (если есть) из каталога прогона.

    Возвращает (tx_events, rx_events). Если файлов нет — возвращает ([], []).
    Это позволяет analyzer'у работать совместимо с прогонами 1.4 / 1.5.1
    (без видео) и с 1.5.2+ (с видео).
    """
    tx_path = run_dir / "video_tx.jsonl"
    rx_path = run_dir / "video_rx.jsonl"
    tx_events = load_events(tx_path) if tx_path.exists() else []
    rx_events = load_events(rx_path) if rx_path.exists() else []
    return tx_events, rx_events


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
