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
import re
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


ROLE = {"h1": "TIME SERVER", "h2": "CLIENT 1", "h3": "CLIENT 2", "h4": "FLOOD GEN"}
ORDER = ["h4", "h2", "h3", "h1"]          # senders first, server last
PORT = {"h1": 1, "h2": 2, "h3": 3, "h4": 4}
BAR_W = 22
IS_TTY = sys.stdout.isatty()
LAST_PROBE = {}                            # host -> latest sync-client line
DASH_LINES = 0                             # lines drawn last frame (for in-place redraw)


def bar(mbps, full=16.0):
    """sqrt-scaled bar so 1 kbit/s sync probes stay visible next to a 16 Mbit/s flood."""
    if mbps < 0.0003:
        return " " * BAR_W
    n = max(1, round(((min(mbps, full) / full) ** 0.5) * BAR_W))
    return ("█" * n).ljust(BAR_W)


def queue_stats(s1_name="s1"):
    """Per-queue tx counters of the server-facing port: {queue: (bytes, errors)}."""
    try:
        out = subprocess.run(["ovs-ofctl", "-O", "OpenFlow13", "queue-stats", s1_name, "1"],
                             capture_output=True, text=True, timeout=2).stdout
    except Exception:
        return {}
    q = {}
    for m in re.finditer(r"queue (\d+):.*?bytes=(\d+).*?errors=(\d+)", out):
        q[int(m.group(1))] = (int(m.group(2)), int(m.group(3)))
    return q


def rate(mbps):
    if mbps >= 0.95:
        return f"{mbps:.1f} Mbps"
    return f"{mbps * 1000:.0f} kbps"


def plain_probe(line):
    """'Iter 07 | RTT 1229.38 ms | ... | error 604.49 ms' -> plain English."""
    if not line:
        return "waiting..."
    if "TIMEOUT" in line:
        return "no reply (packet lost in the jam)"
    m = re.search(r"RTT\s+([\d.]+) ms.*error\s+(-?[\d.]+) ms", line)
    if not m:
        return line
    return f"reply took {float(m.group(1)):.0f} ms, clock still wrong by {abs(float(m.group(2))):.0f} ms"


def draw(title, t, sdn, tx, rx, qrate, qdrops):
    """Simple live view, redrawn in place (one block per second if not a terminal)."""
    global DASH_LINES
    full = rx["h1"] > 9.5
    if not full:
        verdict = "Road is free. Time requests get through fast and clocks are accurate."
    elif sdn:
        verdict = "Road is jammed, BUT time requests use the priority lane -> still fast and accurate."
    else:
        verdict = "Road is jammed. Time requests wait in the same queue as the flood -> slow, lost, wrong clocks."
    W = 78
    L = []
    L.append(f" {title}   (running {t:.0f}s)")
    L.append(" " + "─" * W)
    L.append(" ALL HOSTS: DATA EACH ONE GENERATES (sends) AND RECEIVES")
    L.append(f"   {'HOST':<20}{'GENERATES (sends)':<{BAR_W + 12}}RECEIVES")
    names = {"h1": "h1 time server", "h2": "h2 client 1", "h3": "h3 client 2", "h4": "h4 flood sender"}
    for h in ("h1", "h2", "h3", "h4"):
        note = ""
        if h == "h4" and tx[h] > 1:
            note = "  <- flooding h1!"
        if h == "h1" and rx[h] > 9.5:
            note = "  <- FULL"
        L.append(f"   {names[h]:<20}{bar(tx[h])} {rate(tx[h]):>10}   {bar(rx[h])} {rate(rx[h]):>10}{note}")
    L.append("")
    L.append(" THE SERVER'S ROAD (max 10 Mbps)")
    L.append(f"   traffic arriving        {bar(rx['h1'])} {rate(rx['h1']):>10}" + ("   <- FULL" if full else ""))
    L.append(f"   priority lane (sync)    {bar(qrate.get(1, 0))} {rate(qrate.get(1, 0)):>10}   lost: {qdrops.get(1, 0)}")
    L.append(f"   normal lane (flood)     {bar(qrate.get(0, 0))} {rate(qrate.get(0, 0)):>10}   lost: {qdrops.get(0, 0)}")
    L.append("")
    L.append(f" WHAT THIS MEANS: {verdict}")
    L.append(f" LATEST TIME-SYNC RESULT (normal = ~20-30 ms, error ~0):")
    L.append(f"   h2: {plain_probe(LAST_PROBE.get('h2'))}")
    L.append(f"   h3: {plain_probe(LAST_PROBE.get('h3'))}")
    L.append("")
    if IS_TTY:
        if DASH_LINES:
            sys.stdout.write(f"\033[{DASH_LINES}A")
        sys.stdout.write("".join("\033[2K" + ln + "\n" for ln in L))
        sys.stdout.flush()
        DASH_LINES = len(L)
    else:
        print("\n".join(L), flush=True)


class Monitor(threading.Thread):
    """Once a second: rate per host link (measured at s1's port) + queue rates on s1:1.
    out/tx = sent BY the host, in/rx = received BY the host
    (host out = switch rx_bytes, host in = switch tx_bytes)."""

    def __init__(self, path):
        super().__init__(daemon=True)
        self.path = path
        self.phase = "setup"
        self.title = "SETUP"
        self.sdn = False
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
        qprev = queue_stats()
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

                qnow = queue_stats()
                qrate = {k: max(0, (qnow[k][0] - qprev.get(k, (0, 0))[0]) * 8 / dt / 1e6) for k in qnow}
                qdrops = {k: qnow[k][1] for k in qnow}
                qprev = qnow
                draw(self.title, now - t0, self.sdn, tx, rx, qrate, qdrops)


def pump(proc, name):
    """Keep only the latest sync-client line per host; the dashboard displays it."""
    for line in proc.stdout:
        line = line.rstrip()
        if line.startswith("Iter"):
            LAST_PROBE[name] = line
        if not IS_TTY:
            print(f"[{name}] {line}", flush=True)


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
    mon.title = title
    mon.sdn = sdn
    LAST_PROBE.clear()
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

