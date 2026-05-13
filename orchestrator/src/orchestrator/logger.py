"""Единый JSONL-журнал прогона.

Схема событий соответствует таблице 5 архитектурного документа Физулина А.В.:
поля purpose-specific для каждого контура (scenario / flight / control_telemetry /
network / payload / sync). Все события несут общие поля event_type, run_id, scenario_id,
sim_time, wall_time.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, TextIO


# Какие event_type ожидает анализатор. Используем при валидации.
EVENT_TYPES = frozenset({
    "run_start",
    "run_end",
    "scenario",
    "flight",
    "control_telemetry",
    "network",
    "payload",
    "sync",
    "component",
})


class EventLogger:
    """Потокобезопасный JSONL-логгер с буферизацией.

    Файл открывается на запись; каждое событие — отдельная строка JSON.
    Flush явный через flush() либо в close().
    """

    def __init__(self, log_path: Path, run_id: str, scenario_id: str) -> None:
        self.log_path = log_path
        self.run_id = run_id
        self.scenario_id = scenario_id
        self._lock = threading.Lock()
        self._start_wall = time.time()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._fp: TextIO = log_path.open("w", encoding="utf-8")

    def emit(self, event_type: str, **fields: Any) -> None:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"Неизвестный event_type: {event_type!r}")
        now = time.time()
        sim_time = fields.pop("sim_time", None)
        record: dict[str, Any] = {
            "event_type": event_type,
            "run_id": self.run_id,
            "scenario_id": self.scenario_id,
            "wall_time": now,
            "wall_dt": now - self._start_wall,
        }
        if sim_time is not None:
            record["sim_time"] = sim_time
        record.update(fields)
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            self._fp.write(line)
            self._fp.write("\n")

    def flush(self) -> None:
        with self._lock:
            self._fp.flush()

    def close(self) -> None:
        with self._lock:
            if not self._fp.closed:
                self._fp.flush()
                self._fp.close()

    def __enter__(self) -> "EventLogger":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()
