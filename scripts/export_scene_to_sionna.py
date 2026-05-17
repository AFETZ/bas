#!/usr/bin/env python3
"""Этап 2.1.b: программный билдер Mitsuba 3 XML-сцены для Sionna RT.

В отличие от полного SDF→Mitsuba парсера (сложно: SDF разветвлённый, с
include'ами моделей, mesh'ами из COLLADA, etc.), мы строим **упрощённую
сцену** с теми же ключевыми элементами что и `iris_runway.sdf`:

  * ground_plane: 1500×200 м, материал itu_concrete (бетон runway)
  * 3 box-здания по бокам runway как препятствия для радиосигнала
    (демонстрируют occlusion для off-runway позиций UAV)
  * 1 metal-box ("ангар") в начале runway

Этого достаточно чтобы:
  1) показать корреляцию loss с позицией UAV (за зданием — высокий loss,
     над runway — LoS),
  2) иметь физически реалистичные материалы (Sionna RT'у ITU-R материалы
     задают ε_r, tan δ → правильные коэффициенты отражения),
  3) обойтись без mesh-converter'а COLLADA→OBJ.

Запуск:
  ./sionna_env/bin/python scripts/export_scene_to_sionna.py \
      --out scene/iris_runway.xml

Использование в Sionna:
  scene = rt.load_scene("scene/iris_runway.xml")
"""
from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path


# Координаты в стиле Gazebo: X — север (вдоль runway), Y — восток, Z — вверх.
# Origin (0, 0, 0) = центр runway, на земле.
# Iris в Gazebo стартует в (0, 0, 0.195), GCS условно ставим в (0, -30, 1.5)
# (рядом, не на runway).

RUNWAY_LENGTH_M = 1500.0
RUNWAY_WIDTH_M = 200.0

# Препятствия: (имя, x_центр, y_центр, z_центр_низ, sx, sy, sz, material).
# x идёт вдоль runway, здания смещены вбок (y!=0) чтобы UAV пролетал между ними.
OBSTACLES = [
    ("hangar",   -200.0, -60.0,  0.0,  30.0, 20.0, 15.0, "itu_metal"),
    ("tower_a",   100.0,  70.0,  0.0,  10.0, 10.0, 30.0, "itu_concrete"),
    ("tower_b",   300.0, -70.0,  0.0,  10.0, 10.0, 25.0, "itu_concrete"),
    ("building",  500.0,  60.0,  0.0,  40.0, 30.0, 12.0, "itu_brick"),
]


def _bsdf(material: str) -> str:
    """ITU-R radio material BSDF (Sionna 1.x specific tag).

    `material` -- один из {concrete, metal, brick, glass, wood, wet_ground}.
    Sionna lookup'ит ε_r и tan δ из ITU-R recommendations.
    """
    return (
        f'  <bsdf type="itu-radio-material" id="{material}">\n'
        f'    <string name="type" value="{material}"/>\n'
        f'    <float name="thickness" value="0.3"/>\n'
        f'  </bsdf>'
    )


def make_mitsuba_xml() -> str:
    """Собирает Mitsuba 3 scene XML для Sionna RT 1.x.

    Используются ITU-R радио-материалы (`itu-radio-material` BSDF), а shape'ы
    -- type=`cube` (включая тонкий cube для runway вместо rectangle, чтобы
    Sionna parser не игнорировал).
    """
    parts: list[str] = []
    parts.append('<?xml version="1.0" encoding="utf-8"?>')
    parts.append('<scene version="2.1.0">')
    parts.append('')
    parts.append('<!-- Integrator -->')
    parts.append('  <integrator type="path">')
    parts.append('    <integer name="max_depth" value="-1"/>')
    parts.append('  </integrator>')
    parts.append('')
    parts.append('<!-- ITU-R radio materials -->')
    for m in ("concrete", "metal", "brick", "glass"):
        parts.append(_bsdf(m))
    parts.append('')

    # Runway: тонкая бетонная плита (Z=0.5 м для надёжного hit при ray-trace).
    parts.append(
        f'<!-- Runway: бетонная плита {RUNWAY_LENGTH_M:.0f}x{RUNWAY_WIDTH_M:.0f} м -->'
    )
    parts.append(textwrap.dedent(f'''\
          <shape type="cube" id="mesh-runway">
            <transform name="to_world">
              <scale x="{RUNWAY_LENGTH_M/2:.1f}" y="{RUNWAY_WIDTH_M/2:.1f}" z="0.25"/>
              <translate x="0.0" y="0.0" z="-0.25"/>
            </transform>
            <ref id="concrete" name="bsdf"/>
          </shape>'''))
    parts.append('')

    # Препятствия.
    parts.append('<!-- Препятствия (occlusion для off-runway позиций UAV) -->')
    for name, cx, cy, cz_bot, sx, sy, sz, mat in OBSTACLES:
        material_id = mat.removeprefix("itu_")  # itu_metal -> metal
        parts.append(textwrap.dedent(f'''\
              <shape type="cube" id="mesh-{name}">
                <transform name="to_world">
                  <scale x="{sx/2:.1f}" y="{sy/2:.1f}" z="{sz/2:.1f}"/>
                  <translate x="{cx:.1f}" y="{cy:.1f}" z="{cz_bot + sz/2:.1f}"/>
                </transform>
                <ref id="{material_id}" name="bsdf"/>
              </shape>'''))
    parts.append('')
    parts.append('</scene>')
    return '\n'.join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--out", default="scene/iris_runway.xml",
        help="Куда записать Mitsuba XML",
    )
    ap.add_argument(
        "--verify", action="store_true",
        help="После записи попробовать загрузить через Sionna и напечатать "
             "число object'ов сцены",
    )
    args = ap.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    xml = make_mitsuba_xml()
    out_path.write_text(xml, encoding="utf-8")
    print(f"Mitsuba scene XML: {out_path} ({len(xml)} bytes)")
    print(f"  runway: {RUNWAY_LENGTH_M:.0f}x{RUNWAY_WIDTH_M:.0f} м, itu_concrete")
    print(f"  препятствий: {len(OBSTACLES)}")
    for name, cx, cy, _, sx, sy, sz, mat in OBSTACLES:
        print(f"    {name:10s} ({cx:+7.1f}, {cy:+7.1f}) — "
              f"{sx:.0f}x{sy:.0f}x{sz:.0f} м, {mat}")

    if args.verify:
        print("\n==> verifying with Sionna load_scene")
        import mitsuba as mi  # type: ignore
        if mi.variant() is None:
            mi.set_variant("llvm_ad_mono_polarized")
        import sionna.rt as rt  # type: ignore
        scene = rt.load_scene(str(out_path))
        print(f"  scene objects: {len(scene.objects)}")
        for k, obj in scene.objects.items():
            print(f"    - {k}: {obj}")
        print("  load OK")

    return 0


if __name__ == "__main__":
    sys.exit(main())
