#!/usr/bin/env python3
"""Demo 2 runner: topology + 3 narrated test cases + live per-host traffic view.

Topology (s1 port numbers follow link order):
  h1 (time server) --- s1 port 1   plain link; 10 Mbps bottleneck + 2 HTB queues set on s1-eth1
  h2 (client 1)    --- s1 port 2   10 ms each way, 10 Mbps
  h3 (client 2)    --- s1 port 3   15 ms each way, 10 Mbps
  h4 (flood)       --- s1 port 4    2 ms each way, 100 Mbps (so the flood really reaches s1)
"""
import csv
import os
import shutil
import subprocess
import sys
import threading
import time
from subprocess import PIPE, STDOUT, DEVNULL

from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController
from mininet.link import TCLink, Link
from mininet.log import setLogLevel

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
MODE_FILE = "/tmp/qos_mode"
SERVER_IP = "10.0.0.1"
ITERATIONS = 20
FLOOD_MBPS = 15                               # > 10 Mbps bottleneck, but leaves some replies alive
CLIENT_SKEW_MS = {"h2": 250, "h3": -120}      # known clock errors to recover
HOSTS = ["h1", "h2", "h3", "h4"]

BANNERS = {
    "1-normal": ("CASE 1: NORMAL TRAFFIC",
                 "Only h2 and h3 are syncing with h1. Expect RTT ~20 ms (h2) and ~30 ms (h3), error ~0."),
    "2-congestion-noSDN": ("CASE 2: CONGESTION, SDN OFF",
                 f"h4 floods h1 at {FLOOD_MBPS} Mbps into a 10 Mbps server link; the controller detects it "
                 "but is NOT allowed to install the priority rule.\n"
                 "    Expect: queue fills -> RTT ~1 s, large sync error, some replies lost."),
    "3-congestion-SDN": ("CASE 3: CONGESTION, SDN ON",
                 f"Same {FLOOD_MBPS} Mbps flood. Controller detects >5 Mbps and installs a priority rule:\n"
                 "    UDP/12345 -> queue 1, so sync packets skip the flood's queue.\n"
                 "    Expect: RTT back to ~20/30 ms, error ~0, even though the flood is still running."),
}


def say(msg):
    print(f">>> {msg}", flush=True)


def fmt(mbps):
    """5-char rate: 15.2M / ' 950k' / '    0'"""
    if mbps >= 0.95:
        return f"{mbps:4.1f}M"
    if mbps >= 0.0005:
        return f"{mbps * 1000:4.0f}k"
    return "    0"


def set_mode(mode):
    with open(MODE_FILE, "w") as f:
        f.write(mode)


def setup_bottleneck_qos():
    """10 Mbps cap on the server-facing port with a priority queue (1) and a
    best-effort queue (0). Lower 'priority' value = served first."""
    cmd = ("ovs-vsctl set port s1-eth1 qos=@q -- "
           "--id=@q create qos type=linux-htb other-config:max-rate=10000000 "
           "queues:0=@q0 queues:1=@q1 -- "
           "--id=@q0 create queue other-config:min-rate=1000000 "
           "other-config:max-rate=10000000 other-config:priority=1 -- "
           "--id=@q1 create queue other-config:min-rate=2000000 "
           "other-config:max-rate=10000000 other-config:priority=0")
    subprocess.run(cmd, shell=True, check=True)


class Monitor(threading.Thread):
    """Once a second: rate per host. out = sent by the host, in = received by the host
    (measured at the host's switch port: host out = switch rx, host in = switch tx)."""

    def __init__(self, path):
        super().__init__(daemon=True)
        self.path = path
        self.phase = "setup"
        self.running = True

    @staticmethod
    def _read(n):
        base = f"/sys/class/net/s1-eth{n}/statistics/"
        with open(base + "rx_bytes") as f:
            rx = int(f.read())
        with open(base + "tx_bytes") as f:
            tx = int(f.read())
        return rx, tx

    def run(self):
        prev = {n: self._read(n) for n in range(1, 5)}
        t_prev = t0 = time.time()
        with open(self.path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ts", "t", "phase"] +
                       [f"{h}_{d}" for h in HOSTS for d in ("tx", "rx")])
            while self.running:
                time.sleep(1)
                now = time.time()
                dt = now - t_prev
                tx, rx, row = {}, {}, []
                for n, h in enumerate(HOSTS, 1):
                    r, t = self._read(n)
                    tx[h] = (r - prev[n][0]) * 8 / dt / 1e6
                    rx[h] = (t - prev[n][1]) * 8 / dt / 1e6
                    prev[n] = (r, t)
                    row += [round(tx[h], 6), round(rx[h], 6)]
                t_prev = now
                w.writerow([f"{now:.3f}", f"{now - t0:.1f}", self.phase] + row)
                f.flush()

                notes = []
                if tx["h4"] > 1:
                    notes.append("h4 FLOODING h1")
                if rx["h1"] > 9.5:
                    notes.append("server link FULL (10M)")
                if tx["h2"] > 0.0003 or tx["h3"] > 0.0003:
                    notes.append("time-sync probes")
                cells = " | ".join(f"{h}: out {fmt(tx[h])} in {fmt(rx[h])}" for h in HOSTS)
                print(f"[net {now - t0:4.0f}s] {cells}"
                      + (f"   <-- {', '.join(notes)}" if notes else ""), flush=True)


def pump(proc, name):
    for line in proc.stdout:
        print(f"[{name}] {line.rstrip()}", flush=True)


def show_switch_evidence(s1):
    say("Switch evidence: flows matching UDP/12345 (n_packets = sync packets that used the priority rule)")
    print(s1.cmd("ovs-ofctl -O OpenFlow13 dump-flows s1 | grep 12345 "
                 "|| echo '    (no priority flow installed)'").rstrip(), flush=True)
    say("Switch evidence: queues on s1 port 1 (queue 1 = sync, queue 0 = flood; "
        "'errors' = dropped packets)")
    print(s1.cmd("ovs-ofctl -O OpenFlow13 queue-stats s1 1").rstrip(), flush=True)


def case_result(tag):
    parts = []
    for h in ("h2", "h3"):
        with open(os.path.join(HERE, f"sync_{tag}_{h}.csv")) as f:
            rows = list(csv.DictReader(f))
        ok = [r for r in rows if r["Delay_ms"]]
        if ok:
            rtt = sum(float(r["Delay_ms"]) for r in ok) / len(ok)
            err = sum(abs(float(r["Error_ms"])) for r in ok) / len(ok)
            parts.append(f"{h}: {len(ok)}/{len(rows)} replies, mean RTT {rtt:.0f} ms, "
                         f"mean |sync error| {err:.2f} ms")
        else:
            parts.append(f"{h}: 0/{len(rows)} replies")
    say("RESULT  " + "   ||   ".join(parts))


def run_case(net, mon, name, tag, flood, sdn):
    h2, h3, h4, s1 = net.get("h2"), net.get("h3"), net.get("h4"), net.get("s1")
    mon.phase = name
    set_mode("on" if sdn else "off")
    title, expect = BANNERS[name]
    print("\n" + "=" * 100, flush=True)
    say(title)
    say(expect)
    print("=" * 100, flush=True)

    flood_p = None
    if flood:
        say(f"Starting flood: h4 -> h1, {FLOOD_MBPS} Mbps UDP (watch h4 'out' and h1 'in')")
        flood_p = h4.popen(["iperf", "-c", SERVER_IP, "-u", "-b", f"{FLOOD_MBPS}M",
                            "-t", "300"], stdout=DEVNULL, stderr=DEVNULL)
        say("Waiting 6 s so the queue fills and the controller can react...")
        time.sleep(6)
    else:
        time.sleep(2)

    say(f"Starting time sync on h2 and h3 ({ITERATIONS} probes each, 2 s timeout)")
    procs = []
    for h in (h2, h3):
        out = os.path.join(HERE, f"sync_{tag}_{h.name}.csv")
        p = h.popen([PY, "-u", os.path.join(HERE, "timesync_client.py"), SERVER_IP, out,
                     "--skew-ms", str(CLIENT_SKEW_MS[h.name]),
                     "--iterations", str(ITERATIONS)],
                    stdout=PIPE, stderr=STDOUT, universal_newlines=True)
        t = threading.Thread(target=pump, args=(p, h.name), daemon=True)
        t.start()
        procs.append((p, t))
    for p, t in procs:
        p.wait()
        t.join()

    say("Time sync finished.")
    if flood:
        show_switch_evidence(s1)
        say("Stopping flood")
        flood_p.terminate()
        flood_p.wait()
    case_result(tag)
    say("Pausing 6 s to let queues drain and the controller release the rule...")
    time.sleep(6)


def main():
    setLogLevel("warning")
    if not shutil.which("iperf"):
        sys.exit("iperf (iperf2) not found: install it (Arch: pacman -S iperf2)")

    net = Mininet(switch=OVSSwitch, link=TCLink, controller=RemoteController)
    net.addController("c0", controller=RemoteController, ip="127.0.0.1", port=6653)
    h1 = net.addHost("h1", ip="10.0.0.1/24")
    h2 = net.addHost("h2", ip="10.0.0.2/24")
    h3 = net.addHost("h3", ip="10.0.0.3/24")
    h4 = net.addHost("h4", ip="10.0.0.4/24")
    s1 = net.addSwitch("s1", protocols="OpenFlow13")

    net.addLink(h1, s1, cls=Link)                       # port 1 (bottleneck set below)
    net.addLink(h2, s1, delay="10ms", bw=10)            # port 2
    net.addLink(h3, s1, delay="15ms", bw=10)            # port 3
    net.addLink(h4, s1, delay="2ms", bw=100)            # port 4

    mon, server_p, iperf_p, server_log = None, None, None, None
    try:
        say("Starting network...")
        net.start()
        setup_bottleneck_qos()
        try:
            net.waitConnected(timeout=15)
        except AttributeError:
            time.sleep(3)
        time.sleep(1)
        net.pingAll()                                   # learn MACs
        # Static ARP: ARP refreshes are not UDP/12345, so under congestion they would
        # be dropped in the flooded queue and silently kill the sync replies.
        net.staticArp()

        print("\nLEGEND  [net Ns] = traffic per host, measured at its switch port, every second.\n"
              "        out = sent BY the host, in = received BY the host.   k = kbit/s, M = Mbit/s\n"
              "        [h2]/[h3] = sync client output.   [!]/[*] = Ryu controller messages.\n",
              flush=True)
        mon = Monitor(os.path.join(HERE, "traffic_timeline.csv"))
        mon.start()

        server_log = open(os.path.join(HERE, "server.log"), "w")
        server_p = h1.popen([PY, "-u", os.path.join(HERE, "timesync_server.py")],
                            stdout=server_log, stderr=STDOUT)
        iperf_p = h1.popen(["iperf", "-s", "-u"], stdout=DEVNULL, stderr=DEVNULL)
        say("h1 is running the time server (UDP/12345) and an iperf sink")
        time.sleep(2)

        run_case(net, mon, "1-normal", "normal", flood=False, sdn=False)
        run_case(net, mon, "2-congestion-noSDN", "unprio", flood=True, sdn=False)
        run_case(net, mon, "3-congestion-SDN", "sdn", flood=True, sdn=True)
    finally:
        if mon:
            mon.running = False
            time.sleep(1.2)
        for p in (server_p, iperf_p):
            if p:
                p.terminate()
        if server_log:
            server_log.close()
        net.stop()


if __name__ == "__main__":
    main()
