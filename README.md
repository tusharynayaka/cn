# Network Time Synchronization System

Computer Networks mini project (UE25CS243A), Team 23.

## What is this?

Every computer has its own clock, and no two clocks agree perfectly. This project makes several "client" computers ask a "server" computer for the correct time, using small UDP messages, and then fix their own clocks. It is a simplified version of how NTP works.

Then we do the interesting part: we jam the network on purpose and watch time sync break. After that we use an SDN controller to give time-sync messages a priority lane, and watch it work again.

## The idea in plain words

Think of the server's network link as a road with a speed limit of 10 Mbps.

- Normal day: only a few small time-sync cars on the road. Everything is fast.
- Traffic jam: one host (h4) sends 15 Mbps of useless data to the server. The road is full. Time-sync cars wait in the same long queue, arrive late or never, and the clocks end up wrong.
- Priority lane: the SDN controller notices the jam and opens a priority lane just for time-sync packets. They skip the queue, and sync is accurate again, even though the jam is still going on.

## The four hosts

- h1: the time server (also receives the flood)
- h2: client 1, 10 ms from the switch each way
- h3: client 2, 15 ms from the switch each way
- h4: the flood sender (creates the traffic jam)

All four connect to one virtual switch (s1) controlled by a Ryu SDN controller. Everything runs on one computer inside Mininet.

## How a clock is fixed

The client sends a message and records four times:

- T1: client sends the request (client clock)
- T2: server receives it (server clock)
- T3: server sends the reply (server clock)
- T4: client receives the reply (client clock)

From these:

- Round-trip delay = (T4 - T1) - (T3 - T2). This is how long the trip took, not counting the server's thinking time.
- Clock offset = ((T2 - T1) + (T3 - T4)) / 2. This is how far the client clock is from the server clock.

The offset is only correct if the trip there and the trip back take the same time. When a jam delays one direction more than the other, the estimate goes wrong. That wrongness is the "synchronization error" we measure.

In Mininet all hosts share one real clock, so the true offset would be zero and there would be nothing to measure. So each client deliberately runs a wrong clock (h2 is 250 ms off, h3 is 120 ms behind). We know the true answer, so we can calculate exactly how wrong the estimate is.

## What is in the folder

- timesync_server.py: the time server (UDP port 12345)
- timesync_client.py: the client; runs 20 sync attempts and saves results to a CSV
- sdn_controller.py: the Ryu controller; watches traffic and turns the priority lane on or off
- demo2_runner.py: builds the network, runs the three test cases and shows the live display
- run_demo2.sh: the one command that does everything
- plot_results.py: makes the graphs and summary after the run
- Result files created by a run: sync_*.csv, traffic_timeline.csv, qos_events.csv, summary_results.csv, server.log, and the PNG graphs

## What you need (Arch Linux)

- Mininet (AUR package), Open vSwitch, and iperf2 (note: iperf2, not iperf3)
- Python 3 with matplotlib for the graphs
- Ryu installed in a virtual environment called ryu-env inside this folder

```
sudo pacman -S openvswitch iperf2 python-matplotlib
yay -S mininet
sudo systemctl enable --now ovs-vswitchd ovsdb-server
python -m venv ryu-env
./ryu-env/bin/pip install ryu
```

Ryu is old and can break on the newest Python versions. If installing or starting it fails, use an older Python (3.10 or 3.9) for the virtual environment and install eventlet 0.30.2 along with it.

## How to run

From this folder, in a real terminal (not piped to a file):

```
sudo ./run_demo2.sh
```

The whole run takes about two minutes. It starts the controller, runs the three test cases one after another, then makes the graphs.

## What you will see on screen

A live display refreshes every second. It shows:

- A table of all four hosts with the data each one generates (sends) and receives.
- The server's road: how full the 10 Mbps link is, and how much goes through the priority lane and the normal lane, with a count of lost packets.
- A one-line explanation of what is happening.
- The latest sync result for each client.

The three test cases:

1. Normal traffic. Only h2 and h3 syncing.
2. Congestion, SDN off. h4 floods the server. The controller notices but is not allowed to help.
3. Congestion, SDN on. Same flood, but the controller opens the priority lane for time-sync packets.

## Results from our run

Normal traffic: all 20 of 20 replies, delay about 20 ms (h2) and 30 ms (h3), error about 0.03 ms.

Congestion without SDN: only 8 of 20 replies for h2 and 5 of 20 for h3, delay about 1.2 seconds, error about 604 ms. Time sync is effectively broken.

Congestion with SDN: back to 20 of 20 replies, delay about 20 and 30 ms, error about 0.03 ms. The flood is still running, but it no longer matters to the sync traffic.

The graphs are saved as traffic_timeline.png (traffic per host over time), sync_story.png (delay and error over time) and summary_bars.png (the three cases side by side).

## How the SDN part works

The controller asks the switch for traffic counts every second. If any port receives more than 5 Mbps, it declares congestion and tells the switch: "send anything going to the server on UDP port 12345 through queue 1". Queue 1 is a priority queue on the server link, set up with Open vSwitch. When traffic drops below 2 Mbps, the rule is removed.

## Things to know

- The congestion and the delays are simulated on one machine. Real networks will behave differently in detail.
- Static ARP is used so address lookups are not lost in the jam and do not confuse the results.
- If a run is interrupted, clean up with `sudo mn -c` before running again.
- Result files are overwritten each run.
