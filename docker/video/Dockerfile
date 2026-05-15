# Видео-канал: GStreamer + Python binding.
# Образ общий для sender и receiver — конкретный пайплайн выбирается ENTRYPOINT'ом
# в compose. Минимизируем зависимости, но плагины нужны полные:
#   plugins-good  — udpsrc/udpsink, rtph264pay/depay, appsink, tee
#   plugins-bad   — fallback для некоторых RTP-payloader'ов
#   plugins-ugly  — x264enc (H.264 encoder)
#   libav         — avdec_h264 (для receiver, чтобы валидность H.264 потока проверять)
#
# Базируемся на debian:bookworm-slim (gstreamer 1.22, тот же что в gazebo контейнере) —
# меньше шанс ABI-разночтений с ardupilot_gazebo gst-плагинами в 1.5.2.b.

FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-gi \
        python3-gst-1.0 \
        gstreamer1.0-tools \
        gstreamer1.0-plugins-base \
        gstreamer1.0-plugins-good \
        gstreamer1.0-plugins-bad \
        gstreamer1.0-plugins-ugly \
        gstreamer1.0-libav \
        gir1.2-gstreamer-1.0 \
        gir1.2-gst-plugins-base-1.0 \
        ca-certificates \
        tini \
    && rm -rf /var/lib/apt/lists/*

# Скрипты подмонтируем volume'ом (как делает compose) — не копируем COPY'ем
# чтобы не пере-собирать образ при каждой правке pipeline'а.

ENV PYTHONUNBUFFERED=1
ENV GST_DEBUG=2

WORKDIR /work

# tini как PID 1 — корректный SIGTERM в long-running gst pipeline'ах.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "-c", "print('video image; specify command in compose')"]
