#!/usr/bin/env bash
cd /home/afetz/bas-prototype
bash scripts/run_sionna_live.sh -- sionna_env/bin/python scripts/_sionna_scenes_smoke.py \
    > /tmp/scenes_smoke.out 2>/tmp/scenes_smoke.err
echo "=== exit $? ==="
cat /tmp/scenes_smoke.out
echo "=== stderr errors ==="
grep -iE "error|traceback|exception|assert" /tmp/scenes_smoke.err | head -20 || echo "(none)"
