#!/usr/bin/env bash
# Local pre-flight that mirrors the GitHub CI jobs as closely as possible.
set -u
cd ~/bas-prototype || exit 2
PY=/home/afetz/bas-prototype/.venv/bin/python

echo "=== [1] bash -n ==="
for f in $(find scripts -name '*.sh'); do bash -n "$f" || echo "BAD $f"; done
echo "bash -n done"

echo; echo "=== [2] compileall (syntax gate) ==="
$PY -m compileall -q scripts orchestrator/src && echo "compileall OK"

echo; echo "=== [3] yaml sanity ==="
$PY - <<'PYEOF'
import glob, yaml
files = (glob.glob("configs/**/*.yml", recursive=True)
         + glob.glob("configs/**/*.yaml", recursive=True)
         + glob.glob("*.yml") + glob.glob(".github/workflows/*.yml"))
for f in sorted(set(files)):
    yaml.safe_load(open(f)); print("ok", f)
PYEOF

echo; echo "=== [4] CI-sim: run_all_smokes with sionna_env hidden (expect 12/12) ==="
moved=0
if [ -d sionna_env ]; then mv sionna_env .sionna_env_hidden && moved=1; fi
bash scripts/run_all_smokes.sh; rc=$?
[ "$moved" = 1 ] && mv .sionna_env_hidden sionna_env
echo "regression rc=$rc"

echo; echo "=== [5] CI dep-set test (fresh venv, exact CI deps) ==="
rm -rf /tmp/civenv && python3 -m venv /tmp/civenv
/tmp/civenv/bin/pip install -q --upgrade pip
/tmp/civenv/bin/pip install -q msgpack numpy pyproj requests mavproxy 2>&1 | tail -1
/tmp/civenv/bin/pip install -q -e ./orchestrator 2>&1 | tail -1
fails=0
for s in _large_map_smoke _multirotor_dynamics_smoke _onboard_db_smoke \
         _parallel_smoke _cyber_smoke _sync_loopback_smoke; do
  out=$(/tmp/civenv/bin/python scripts/$s.py 2>&1)
  if echo "$out" | grep -q "ALL CHECKS PASSED"; then
    echo "  $s PASS"
  else
    echo "  $s FAIL"; echo "$out" | tail -4 | sed 's/^/      /'; fails=$((fails+1))
  fi
done
echo "dep-set fails=$fails"
