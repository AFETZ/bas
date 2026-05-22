<!--
Pull Request шаблон. Заполняй секции, удаляй ненужные.
См. docs/CONTRIBUTING.md для conventions.
-->

## Что

Краткое описание в 1-2 строки.

## Почему

Контекст: какая задача / backlog item / regression.

## Как

Технический подход (без копий кода — diff и так в PR).

## Verified

```
вывод/числа/результат smoke теста
```

Например:
```
sudo bash scripts/run_stage_1_5_2_mission.sh wifi_good
→ 7/7 waypoints, 252.7m, video_rx.mp4 = 16M, RC=0
```

## Refs

- Closes #N
- Backlog: roadmap.md → пункт X
- Related: docs/stage_X_Y_*.md

## Checklist

- [ ] `make syntax` проходит
- [ ] Relevant `docs/` обновлены (если применимо)
- [ ] Wrapper добавлен в `docs/DEMOS.md` (если новый)
- [ ] CHANGELOG.md обновлён (если значимое изменение)
- [ ] Smoke от соответствующего stage прошёл
