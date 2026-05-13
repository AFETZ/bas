# ArduPilot SITL + ardupilot_gazebo плагин
#
# Reference:
# - https://ardupilot.org/dev/docs/sitl-with-gazebo.html
# - https://github.com/ArduPilot/ardupilot_gazebo
#
# Сборка ArduPilot из исходников занимает ~30 минут. После первой удачной
# сборки кешируйте слои.

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Europe/Moscow

# Префиксуем tzdata, чтобы install-prereqs (вызывающий sudo apt) не зависал на prompt.
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && ln -fs /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

RUN apt-get update && apt-get install -y --no-install-recommends \
        sudo git ca-certificates wget curl \
        python3 python3-pip python3-venv \
        build-essential cmake pkg-config \
        libtool autoconf automake \
        ccache rsync \
    && rm -rf /var/lib/apt/lists/*

# Пользователь для совместимости с install-prereqs-ubuntu.sh.
# Дополнительно: sudo сохраняет DEBIAN_FRONTEND через env_keep, чтобы apt из install-prereqs
# не запрашивал интерактивные prompt'ы (например tzdata).
RUN useradd -m -s /bin/bash -G sudo ardupilot \
    && echo "ardupilot ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers \
    && echo 'Defaults env_keep += "DEBIAN_FRONTEND TZ"' >> /etc/sudoers

USER ardupilot
WORKDIR /home/ardupilot

# Клонируем ArduPilot.
# TODO: зафиксировать тег (Copter-4.5+) - сейчас используется master.
RUN git clone --recurse-submodules --depth 1 https://github.com/ArduPilot/ardupilot.git

WORKDIR /home/ardupilot/ardupilot

# Установка зависимостей ArduPilot. install-prereqs-ubuntu.sh использует sudo и apt.
RUN USER=ardupilot Tools/environment_install/install-prereqs-ubuntu.sh -y

# Сборка SITL для ArduCopter (для других платформ - waf configure --board=...).
RUN bash -lc "./waf configure --board sitl && ./waf copter"

WORKDIR /work

# Точка входа: запускает SITL и слушает MAVLink на 5760/14550.
# Реальный sim_vehicle.py использует Python venv из install-prereqs.
CMD ["/home/ardupilot/ardupilot/Tools/autotest/sim_vehicle.py", \
     "-v", "ArduCopter", "-f", "gazebo-iris", "--no-mavproxy", \
     "-w", "--add-param-file=/work/ardupilot/parameters/default.parm"]
