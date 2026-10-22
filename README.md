# Network Time Synchronization System – UDP + Mininet + Ryu SDN

## Files
| File | Purpose |
|---|---|
| setup_fix.sh | Installs/fixes Mininet, OVS, iperf, Ryu (Python 3.9 venv) |
| controller.py | Ryu controller (learning switch + time-sync QoS; modes baseline/static/dynamic) |
| timesync_server.py / timesync_client.py | UDP NTP-style server / client (T1..T4, delay, offset) |
| run_experiment.py | Mininet topology, QoS queues, delay/loss injection, congestion, experiments |
| run_all.sh | Runs all 3 modes end to end |
| analyze.py | Summary table + plots |

## Topology
srv,n2 — s1 ==10 Mbps bottleneck== s2 — c1,c2,c3,n1  (n1/n2 = iperf congestion generators)

## Run
    ./setup_fix.sh                      # once
    ./run_all.sh                        # ~7 min, produces summary.csv + plot_*.png

Manual (2 terminals):
    # T1
    source ~/ryu-env/bin/activate
    SDN_MODE=dynamic ryu-manager controller.py
    # T2
    sudo python3 run_experiment.py --duration 40 --scenarios idle,congested --cli
Extra robustness test: `--loss 5` (5% packet loss), `--delay 50 --jitter 20` (high delay).

## Algorithm
offset = ((T2-T1)+(T3-T4))/2, delay = (T4-T1)-(T3-T2). Clients have artificial skew/drift,
so the true error is known. Min-delay filter over 4 samples; clock stepped by best offset.
