#!/usr/bin/env bash
# Установка Docker Engine + compose plugin в WSL Ubuntu 24.04.
# Запускать ВНУТРИ WSL: bash scripts/install_docker.sh
set -euo pipefail

if command -v docker >/dev/null 2>&1; then
  echo "Docker уже установлен: $(docker --version)"
  exit 0
fi

echo "[1/5] Обновляем apt и ставим базовые пакеты..."
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg lsb-release

echo "[2/5] Подключаем официальный репозиторий Docker..."
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
  sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

echo "[3/5] Ставим Docker Engine, CLI и compose plugin..."
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

echo "[4/5] Добавляем пользователя в группу docker (новый shell для применения)..."
sudo usermod -aG docker "$USER" || true

echo "[5/5] В WSL Docker не стартует сам - заводим сервис вручную..."
# WSL: используем service вместо systemctl, если systemd не включён.
if command -v systemctl >/dev/null 2>&1 && systemctl is-system-running >/dev/null 2>&1; then
  sudo systemctl enable docker
  sudo systemctl start docker
else
  sudo service docker start || true
  echo "Подсказка: для автозапуска включите systemd в /etc/wsl.conf:"
  echo "  [boot]"
  echo "  systemd=true"
fi

echo
echo "Готово. Откройте новый WSL-shell или выполните 'newgrp docker', затем проверьте:"
echo "  docker run --rm hello-world"
