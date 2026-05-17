# Gazebo Sim Harmonic + ardupilot_gazebo плагин
#
# Reference:
# - https://gazebosim.org/docs/harmonic/install_ubuntu
# - https://github.com/ArduPilot/ardupilot_gazebo

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Europe/Moscow
# Keep the simulator ABI reproducible across OSRF apt updates. This pin is not
# the camera/gimbal FDM fix; the working demo path is selected by world/model.
ARG GZ_SIM8_VERSION=8.10.0-1~jammy

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

# Шаг 3: ставим Harmonic и dev-заголовки для сборки плагина, затем pin'им
# gz-sim8 на проверенную minor-версию для воспроизводимых rebuild'ов.
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
    && arch="$(dpkg --print-architecture)" \
    && mkdir -p /tmp/gz-sim8-pin \
    && cd /tmp/gz-sim8-pin \
    && base="https://osrf-distributions.s3.us-east-1.amazonaws.com/gz-sim/releases" \
    && for pkg in \
        "libgz-sim8_${GZ_SIM8_VERSION}_${arch}.deb" \
        "libgz-sim8-plugins_${GZ_SIM8_VERSION}_${arch}.deb" \
        "libgz-sim8-dev_${GZ_SIM8_VERSION}_${arch}.deb" \
        "gz-sim8-cli_${GZ_SIM8_VERSION}_${arch}.deb" \
        "python3-gz-sim8_${GZ_SIM8_VERSION}_${arch}.deb"; do \
            curl --retry 5 --retry-delay 2 --retry-connrefused -fsSLO "${base}/${pkg}"; \
        done \
    && apt-get install -y --allow-downgrades ./*.deb \
    && apt-mark hold libgz-sim8 libgz-sim8-plugins libgz-sim8-dev gz-sim8-cli python3-gz-sim8 \
    && test "$(gz sim --versions)" = "${GZ_SIM8_VERSION%%-*}" \
    && cd / \
    && rm -rf /tmp/gz-sim8-pin \
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
