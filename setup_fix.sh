#!/bin/bash
# Fixes Mininet / iperf / Open vSwitch / Ryu on Ubuntu 22.04 (and 24.04).
set -e
sudo apt update
sudo apt install -y mininet openvswitch-switch iperf iperf3 python3-pandas python3-matplotlib \
                    software-properties-common
sudo systemctl enable --now openvswitch-switch
sudo mn -c || true

# Ryu does not work with Python >= 3.10 -> use an isolated Python 3.9 virtualenv
if ! command -v python3.9 >/dev/null; then
  sudo add-apt-repository -y ppa:deadsnakes/ppa
  sudo apt update
  sudo apt install -y python3.9 python3.9-venv python3.9-distutils
fi
python3.9 -m venv ~/ryu-env
source ~/ryu-env/bin/activate
pip install --upgrade pip wheel "setuptools<58"
pip install eventlet==0.30.2 ryu
ryu-manager --version
echo "OK. Use:  source ~/ryu-env/bin/activate"
