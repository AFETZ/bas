# Contributing

## Архитектурные соглашения

### Структура коммитов

Conventional commits + русский body:

```
feat(stage-X.Y): краткое английское summary

Detailed Russian body explaining что и почему.
Multiple paragraphs OK для сложных commits.

Verified: какой-то конкретный smoke результат с числами.
Closes/addresses: ссылка на task/issue.
```

Types:
- `feat` — новая фича
- `fix` — баг fix
- `docs` — только документация
- `refactor` — без поведенческих изменений
- `chore` — gitignore, lint, packaging
- `test` — тесты
- `ci` — GitHub Actions

### Wrapper convention

Все `scripts/run_stage_*.sh`:

```bash
#!/usr/bin/env bash
# Stage X.Y — краткое описание что демонстрирует.
#
# Архитектура:
#   <короткая mermaid-style диаграмма>
#
# Reference patterns (если адаптировано из чужого репозитория):
#   * https://github.com/...
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Env по умолчанию (все через ${VAR:-default} pattern)
export BAS_GAZEBO_GUI="${BAS_GAZEBO_GUI:-0}"
# ...

# Делегировать базовому wrapper'у (mavproxy_gcs для Stage 2.4)
exec bash "${SCRIPT_DIR}/run_stage_2_4_mavproxy_gcs.sh" ui
```

### Code style

- Python: PEP 8, type hints где осмысленно. Без формальной mypy строгости.
- Bash: `set -euo pipefail`, `${VAR:-default}` для env, кавычки везде.
- JS: ES2020+, no transpiler, run в браузере напрямую. К camelCase.
- C++ (ns-3): следовать ns-3 codestyle (4-space indent).

### Логирование

```jsonl
{"event_type":"component","component":"name","phase":"phase_name","wall_time":...,"sim_time":...,...}
{"event_type":"flight","position":{"lat":...,"lon":...,"alt_rel_m":...,...},...}
{"event_type":"network","flow_id":"control","bytes_tx":...,"packets_rx":...,...}
{"event_type":"scenario","status":"success","reason":"mission_landed"}
```

Каждое событие должно иметь `event_type`, `wall_time` (или `sim_time` для ns-3),
`run_id`. Стиль ns-3 events отличается от orchestrator (камelCase vs snake_case
в полях), это исторически — не unify без причины.

## Workflow

### Новая фича

1. Создать ветку: `git checkout -b feature/stage-X.Y-thing`
2. Сделать changes, обязательно с docs update (relevant `docs/stage_*.md`)
3. Add wrapper `scripts/run_stage_*_demo.sh` если применимо
4. Smoke test:
   ```bash
   sudo bash scripts/run_stage_*_demo.sh   # должен пройти без error
   ```
5. Commit + push
6. PR с описанием:
   - Какую задачу из roadmap/backlog закрывает
   - Что verified (smoke output)
   - Какие env переменные добавлены

### Bug fix

1. Воспроизвести: какой stage, какие env, что ломается
2. Fix + добавить в `docs/stage_*_known_issues.md` или TROUBLESHOOTING.md
3. Verify smoke
4. Commit

### Документация

Изменения docs/ можно мерджить без full smoke. Но проверь:
- Все ссылки рабочие (`docs/<file>.md` через relative paths)
- Mermaid диаграммы рендерятся (можно `mermaid.live` проверить)
- Команды копипастятся (нет невидимых символов из IDE)

## Code review checklist

- [ ] `bash -n` syntax check на всех изменённых `.sh`
- [ ] `python -m py_compile` на всех изменённых `.py`
- [ ] Если новый wrapper — добавлен в `docs/DEMOS.md`
- [ ] Если новая env переменная — добавлена в `docs/QUICKSTART.md`
- [ ] Если изменён `events.jsonl` schema — обновлён analyzer
- [ ] Smoke test от соответствующего stage прошёл
- [ ] Commit message по convention
- [ ] PR описание содержит verified output

## Стиль PR / Issue

Issues и PR на русском или английском — оба ок. Body:

```markdown
## Что
Краткое описание в 1-2 строки.

## Почему
Контекст: какая задача / backlog item / regression.

## Как
Технический подход.

## Verified
Конкретный output / numbers / screenshot.

## Refs
- Issue #N
- Backlog roadmap.md item X
```

## License

Research / academic prototype без явной open-source лицензии. Использование
кода для академических целей в рамках гранта ПВАТС УЛ САПР разрешено.
Production использование без согласования с авторами не допускается.

Сторонние компоненты сохраняют свои лицензии:
- ArduPilot: GPLv3
- Gazebo Harmonic: Apache 2.0
- ns-3: GPLv2
- Sionna: Apache 2.0
- Cosys-AirSim: MIT
- bluenviron/mavp2p: MIT
- Mesa Dozen: MIT
- MAVProxy: GPLv3
- ROS2 / MAVROS: Apache 2.0 / BSD
