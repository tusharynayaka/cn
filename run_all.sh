#!/bin/bash
# Runs baseline, static and dynamic experiments, then analysis.
cd "$(dirname "$0")"
rm -rf results; mkdir results
source ~/ryu-env/bin/activate
for MODE in baseline static dynamic; do
  echo "################ MODE: $MODE ################"
  SDN_MODE=$MODE ryu-manager controller.py > ryu_$MODE.log 2>&1 &
  RYU=$!
  sleep 5
  sudo python3 run_experiment.py --duration 40
  kill $RYU; sleep 1
  sudo mn -c > /dev/null 2>&1
done
sudo chown -R "$USER" results
deactivate
python3 analyze.py
