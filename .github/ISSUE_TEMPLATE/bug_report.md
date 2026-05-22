---
name: Bug report
about: Что-то сломалось / не воспроизводится / неожиданное поведение
labels: bug
---

## Симптом

Что произошло одним предложением. Не пиши "не работает" — пиши конкретно
("после TAKEOFF дрон не реагирует на WASD" / "smoke 1.7 RC=1, no events.jsonl"
и т.п.).

## Воспроизведение

```bash
# Команда(ы) которые запускал
sudo bash scripts/run_stage_X_Y_demo.sh
```

## Ожидание vs реальность

- **Ожидал**: ...
- **Реальность**: ...

## Окружение

```
git log -1 --oneline
uname -a
lsb_release -a
docker info | head -10
```

## Логи

Прикрепи tarball последнего `logs/<run>/`, и output последних ~50 строк из:

```
sudo journalctl -u docker.service --since "10 min ago" | tail -50
sudo dmesg | tail -30
```

## Что уже пробовал

Cleanup / restart / другая команда / прочёл TROUBLESHOOTING.md?
