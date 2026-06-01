#!/usr/bin/env bash
# Видеообзор стенда (слайдшоу) из скриншотов интерфейсов → MP4 + GIF.
#
# Headless WSL без дисплея не пишет экранное видео надёжно (Playwright video
# даёт пустой кадр, screenshot в цикле виснет на бесконечном MJPEG/анимации
# баннера). Поэтому видеообход собирается из УЖЕ снятых скриншотов всех
# интеграций — порядок слайдов = нарратив обхода стенда.
#
# Запуск:  bash scripts/make_walkthrough_video.sh
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2
ASSETS=docs/assets
DUR="${BAS_SLIDE_DUR:-2.8}"     # секунд на слайд
W=1280; H=720; BG=0x0d0f12

# Порядок слайдов = маршрут осмотра.
SLIDES=(
  master_demo_admin    # стенд: все модули живы + кибер-баннер
  grand_pult           # пульт: все 6 интеграций сразу
  admin_control        # A — управление дроном из витрины
  geofence_pult        # B — облёт запретных зон
  cv_admin_detections  # C — CV-детекты на карте витрины
  cyber_admin_alert    # D — кибер-атака на витрине
  rf_heatmap_pult      # E — RF-покрытие на пульте
  airsim_fpv_pult      # E2 — камера AirSim в окне FPV
  grand_vitrina        # витрина: всё вместе
)

inputs=(); filters=""; idx=0
for name in "${SLIDES[@]}"; do
    f="${ASSETS}/${name}.png"
    if [ ! -f "$f" ]; then echo "skip missing $f"; continue; fi
    inputs+=(-loop 1 -t "$DUR" -i "$f")
    filters+="[${idx}:v]scale=${W}:${H}:force_original_aspect_ratio=decrease,"
    filters+="pad=${W}:${H}:(ow-iw)/2:(oh-ih)/2:color=${BG},setsar=1,fps=25[v${idx}];"
    idx=$((idx + 1))
done
if [ "$idx" -lt 2 ]; then echo "too few slides ($idx)"; exit 1; fi

concat=""
for ((i = 0; i < idx; i++)); do concat+="[v${i}]"; done
concat+="concat=n=${idx}:v=1:a=0[out]"

MP4="${ASSETS}/walkthrough.mp4"
GIF="${ASSETS}/walkthrough.gif"

echo "slides: ${idx} × ${DUR}s → $(echo "$idx * $DUR" | bc 2>/dev/null || echo "~$((idx*3))")s"
ffmpeg -y "${inputs[@]}" -filter_complex "${filters}${concat}" \
    -map "[out]" -c:v libx264 -pix_fmt yuv420p -movflags +faststart \
    -crf 23 -preset veryfast "$MP4" >/tmp/ff_slide.log 2>&1
if [ ! -s "$MP4" ]; then echo "MP4 FAILED"; tail -5 /tmp/ff_slide.log; exit 1; fi
echo "mp4: $(du -h "$MP4" | cut -f1)"

ffmpeg -y -i "$MP4" \
    -vf "fps=8,scale=720:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse" \
    "$GIF" >/tmp/ff_slidegif.log 2>&1
echo "gif: $(du -h "$GIF" 2>/dev/null | cut -f1)"
echo SLIDESHOW_DONE
