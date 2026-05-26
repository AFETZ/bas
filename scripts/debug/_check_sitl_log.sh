#!/usr/bin/env bash
LOGDIR=$(ls -td /tmp/_real_sitl_smoke_* 2>/dev/null | head -1)
echo "=== latest: $LOGDIR ==="
tail -60 "$LOGDIR/sitl.log" 2>&1 | grep -E "PreArm|Roll/Pitch|inconsist|EKF3|Need|fail|origin|Field|GPS" | tail -30
