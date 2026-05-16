# Gazebo Sim Harmonic + ardupilot_gazebo плагин
#
# Reference:
# - https://gazebosim.org/docs/harmonic/install_ubuntu
# - https://github.com/ArduPilot/ardupilot_gazebo

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Europe/Moscow

# Шаг 1: базовые системные пакеты (без gz-* - их репо ещё не подключён).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates wget curl gnupg lsb-release \
        git build-essential cmake pkg-config \
        rapidjson-dev libeigen3-dev libxml2-dev \
    && rm -rf /var/lib/apt/lists/*

# Шаг 2: подключаем официальный репозиторий OSRF для Gazebo Harmonic.
RUN wget -qO - https://packages.osrfoundation.org/gazebo.gpg \
        | gpg --dearmor -o /usr/share/keyrings/pkgs-osrf-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg] http://packages.osrfoundation.org/gazebo/ubuntu-stable $(lsb_release -cs) main" \
        > /etc/apt/sources.list.d/gazebo-stable.list

# Шаг 3: ставим Harmonic и dev-заголовки для сборки плагина.
# libopencv-dev + gstreamer-*-dev нужны ardupilot_gazebo для видеостриминга.
# Также ставим RUNTIME плагины gstreamer (good/bad/ugly/libav) — GstCameraPlugin
# открывает pipeline videotestsrc-style "videoconvert ! x264enc ! rtph264pay !
# udpsink" внутри Gazebo-процесса, и без этих плагинов он молча не пушит RTP.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gz-harmonic \
        libgz-sim8-dev libgz-cmake3-dev \
        libopencv-dev \
        libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
        gstreamer1.0-tools \
        gstreamer1.0-plugins-base \
        gstreamer1.0-plugins-good \
        gstreamer1.0-plugins-bad \
        gstreamer1.0-plugins-ugly \
        gstreamer1.0-libav \
        libdebuginfod1 \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Шаг 4: собираем плагин ardupilot_gazebo (мост Gazebo <-> ArduPilot SITL).
WORKDIR /work
RUN git clone --depth 1 https://github.com/ArduPilot/ardupilot_gazebo.git \
    && cd ardupilot_gazebo \
    && mkdir build && cd build \
    && cmake .. -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    && make -j$(nproc)

ENV GZ_SIM_SYSTEM_PLUGIN_PATH=/work/ardupilot_gazebo/build
ENV GZ_SIM_RESOURCE_PATH=/work/ardupilot_gazebo/models:/work/ardupilot_gazebo/worlds

# Headless по умолчанию (-s = server only, -r = run, -v4 = verbose).
CMD ["gz", "sim", "-s", "-r", "-v4", "/work/gazebo/worlds/basic.sdf"]
