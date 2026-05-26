#!/usr/bin/env bash
echo "=== Sionna venv packages ==="
/home/afetz/bas-prototype/sionna_env/bin/pip list 2>&1 | grep -iE 'mitsuba|drjit|sionna|tensorflow|numpy'
echo
echo "=== Ubuntu release ==="
lsb_release -a 2>&1
echo
echo "=== apt-cache libnvidia-gl ==="
apt-cache search libnvidia-gl 2>&1 | head -20
echo
echo "=== apt-cache libnvidia-compute ==="
apt-cache policy libnvidia-gl-535 libnvidia-gl-545 libnvidia-gl-550 libnvidia-gl-555 libnvidia-gl-560 libnvidia-gl-565 libnvidia-gl-570 libnvidia-gl-575 libnvidia-gl-580 libnvidia-gl-585 libnvidia-gl-590 libnvidia-gl-595 2>&1 | grep -E 'Installed|Candidate|libnvidia' | head -40
echo
echo "=== Driver via nvidia-smi ==="
nvidia-smi --query-gpu=name,driver_version,cuda_version --format=csv | head -3
