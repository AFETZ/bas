# ns-3 (real-time, TapBridge, наш сценарий two_channel)
#
# Reference:
# - https://www.nsnam.org/docs/installation/html/quick-start.html
# - https://www.nsnam.org/docs/release/3.40/models/html/tap.html
#
# Собираем ns-3.40 (LTS). Сборка занимает ~15-20 минут.

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Europe/Moscow

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates wget curl git \
        python3 python3-pip python3-dev \
        build-essential cmake pkg-config \
        g++ gcc \
        libgsl-dev libxml2-dev libsqlite3-dev \
        libssl-dev libpcap-dev tcpdump \
        bridge-utils uml-utilities iproute2 \
        socat \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /work
ARG NS3_VERSION=3.40
RUN wget -q https://www.nsnam.org/releases/ns-allinone-${NS3_VERSION}.tar.bz2 \
    && tar xjf ns-allinone-${NS3_VERSION}.tar.bz2 \
    && rm ns-allinone-${NS3_VERSION}.tar.bz2 \
    && mv ns-allinone-${NS3_VERSION}/ns-${NS3_VERSION} /work/ns3-src

WORKDIR /work/ns3-src

# Этап 1.7: ns-3 lorawan module от signetlabdei/Università di Padova.
# Это академический стандарт для LoRaWAN моделирования (50+ paper'ов).
# Commit 6babcbe -- последний на ns-3.40 (родитель 5ce32fa "Update supported
# ns-3 version to ns-3.41"). Поддерживает SF7-12, BW 125/250/500 kHz,
# CR 4/5-4/8, ADR, Class A/B/C.
ARG LORAWAN_COMMIT=6babcbe
RUN git clone https://github.com/signetlabdei/lorawan.git /work/ns3-src/src/lorawan \
    && cd /work/ns3-src/src/lorawan \
    && git checkout ${LORAWAN_COMMIT}

RUN ./ns3 configure --enable-examples --enable-tests --build-profile=optimized \
    && ./ns3 build -j$(nproc)

# Наш сценарий копируется как volume в /work/ns3/scenarios.
# Запуск через ./ns3 run scratch/two_channel.
WORKDIR /work
ENV PATH=/work/ns3-src/build:$PATH

CMD ["/bin/bash", "-c", "cp /work/ns3/scenarios/two_channel.cc /work/ns3-src/scratch/ && cd /work/ns3-src && ./ns3 run two_channel"]
