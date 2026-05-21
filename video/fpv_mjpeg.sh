#!/usr/bin/env bash
# Stage 2.4 FPV: принимает RTP H.264 от Gazebo iris_with_gimbal GstCameraPlugin
# (UDP loopback внутри bas-uav netns), декодирует, перекодирует в JPEG и отдаёт
# multipart MJPEG поток через TCP 0.0.0.0:8766 (доступно с хоста через
# 10.10.0.2:8766). gcs_web_ui_server.py проксирует это в /camera.mjpg.
#
# Запускается в bas-fpv-mjpeg контейнере (image bas/video:dev), который
# монтирует ./video → /work/video.
set -euo pipefail

: "${BAS_FPV_UDP_PORT:=5600}"
: "${BAS_FPV_MJPEG_PORT:=8766}"
: "${BAS_FPV_WIDTH:=640}"
: "${BAS_FPV_HEIGHT:=480}"
: "${BAS_FPV_FPS:=15}"
: "${BAS_FPV_QUALITY:=70}"

echo "[fpv] udpsrc:${BAS_FPV_UDP_PORT} -> mjpeg tcpserver:${BAS_FPV_MJPEG_PORT} (${BAS_FPV_WIDTH}x${BAS_FPV_HEIGHT}@${BAS_FPV_FPS}fps q=${BAS_FPV_QUALITY})"

# Запускаем gst-launch как одно процессов целевое приложение. Pipeline собран
# одной строкой намеренно: bash + YAML multi-line + backslash-continuation
# дают сбойную интерполяцию (видели "port=5600 caps=..." как одно значение
# для свойства port). Single-line + кавычки вокруг caps решают всё.
exec gst-launch-1.0 -v \
    udpsrc port=${BAS_FPV_UDP_PORT} caps="application/x-rtp, media=video, encoding-name=H264, payload=96" ! rtpjitterbuffer latency=80 drop-on-latency=true ! rtph264depay ! avdec_h264 ! videoconvert ! videoscale ! "video/x-raw,width=${BAS_FPV_WIDTH},height=${BAS_FPV_HEIGHT}" ! videorate ! "video/x-raw,framerate=${BAS_FPV_FPS}/1" ! jpegenc quality=${BAS_FPV_QUALITY} ! multipartmux boundary=spionkop ! tcpserversink host=0.0.0.0 port=${BAS_FPV_MJPEG_PORT} sync=false recover-policy=keyframe
