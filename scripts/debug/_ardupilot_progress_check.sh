#!/usr/bin/env bash
# Status check for background ArduPilot install.
echo "=== Install log (last 30 lines) ==="
tail -30 /home/afetz/bas-prototype/logs/ardupilot_install/install.log 2>/dev/null || echo "(no log yet)"
echo
echo "=== Running processes ==="
ps -eo pid,etime,cmd | grep -E "install_ardupilot|waf|git" | grep -v grep | head -20
echo
echo "=== Disk usage of $HOME/ardupilot ==="
du -sh /home/afetz/ardupilot 2>/dev/null || echo "(directory not yet created)"
echo
echo "=== Build artifacts ==="
ls -la /home/afetz/ardupilot/build/sitl/bin/arducopter 2>/dev/null || echo "(no arducopter binary yet)"
