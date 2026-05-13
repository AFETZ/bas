#!/usr/bin/env bash
set -e
LATEST="$(ls -td /home/afetz/bas-prototype/logs/202*/ | head -1)"
echo "dir=$LATEST"
wc -l "${LATEST}events.jsonl" 2>&1
echo "--- last 20 events ---"
tail -20 "${LATEST}events.jsonl"
