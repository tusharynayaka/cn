#!/bin/bash
# Usage: sudo ./run_demo2.sh
cd "$(dirname "$0")" || exit 1
[ "$EUID" -eq 0 ] || { echo "Run with sudo"; exit 1; }
command -v iperf >/dev/null || { echo "iperf (iperf2) not found"; exit 1; }

mn -c >/dev/null 2>&1
rm -f sync_*.csv traffic_timeline.csv qos_events.csv server.log
echo on > /tmp/qos_mode

echo "=== STEP 1: Starting Ryu controller ==="
./ryu-env/bin/ryu-manager sdn_controller.py --ofp-tcp-listen-port 6653 &
CTRL=$!
trap 'kill $CTRL 2>/dev/null; mn -c >/dev/null 2>&1' EXIT
for i in $(seq 20); do ss -ltn | grep -q ':6653 ' && break; sleep 0.5; done

echo "=== STEP 2: Running test cases in Mininet ==="
python3 demo2_runner.py

echo "=== STEP 3: Plots and summary ==="
[ -n "$SUDO_USER" ] && chown "$SUDO_USER": sync_*.csv traffic_timeline.csv qos_events.csv server.log 2>/dev/null
if [ -n "$SUDO_USER" ]; then sudo -u "$SUDO_USER" python3 plot_results.py; else python3 plot_results.py; fi
echo "=== DONE ==="
