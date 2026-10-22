#!/usr/bin/env python3
"""Mininet topology + experiment runner.  Run with: sudo python3 run_experiment.py
Topology:  srv,n2 -- s1 ==(10 Mbps bottleneck)== s2 -- c1,c2,c3,n1
"""
import argparse, json, os, subprocess, time
from functools import partial
from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink, Link
from mininet.cli import CLI
from mininet.log import setLogLevel, info

BASE = os.path.dirname(os.path.abspath(__file__))
BW = 10_000_000

def sh(cmd):
    subprocess.run(cmd, shell=True, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def setup_qos(intf):
    """HTB with 2 queues on the bottleneck port: q1 = high priority (time sync), q0 = default."""
    cmd = (f"ovs-vsctl -- set port {intf} qos=@q -- "
           f"--id=@q create qos type=linux-htb other-config:max-rate={BW} queues:0=@q0 queues:1=@q1 -- "
           f"--id=@q0 create queue other-config:min-rate=1000000 other-config:max-rate={BW} other-config:priority=1 -- "
           f"--id=@q1 create queue other-config:min-rate=3000000 other-config:max-rate={BW} other-config:priority=0")
    subprocess.run(cmd, shell=True, check=True)

def build(delay, jitter, loss):
    net = Mininet(controller=None, link=TCLink, autoSetMacs=True,
                  switch=partial(OVSKernelSwitch, protocols='OpenFlow13'))
    net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6653)
    s1, s2 = net.addSwitch('s1'), net.addSwitch('s2')
    srv = net.addHost('srv', ip='10.0.0.1/24')
    n2 = net.addHost('n2', ip='10.0.0.22/24')
    n1 = net.addHost('n1', ip='10.0.0.21/24')
    cl = [net.addHost(f'c{i}', ip=f'10.0.0.1{i}/24') for i in (1, 2, 3)]
    net.addLink(srv, s1)
    net.addLink(n2, s1)
    bott = net.addLink(s1, s2, cls=Link)                  # shaped by OVS QoS, not tc
    for c in cl:                                          # controlled delay / jitter / loss
        kw = dict(delay=f'{delay}ms')
        if jitter: kw['jitter'] = f'{jitter}ms'
        if loss:   kw['loss'] = loss
        net.addLink(c, s2, **kw)
    net.addLink(n1, s2)
    return net, s1, s2, bott

def run_scenario(net, mode, name, duration, interval, congest):
    clients = [net.get(x) for x in ('c1', 'c2', 'c3')]
    n1, n2 = net.get('n1'), net.get('n2')
    skews, drifts = [0.250, -0.120, 0.500], [50, -30, 100]
    count = int(duration / interval)
    os.makedirs(os.path.join(BASE, 'results'), exist_ok=True)
    info(f'\n*** Scenario: {name} (mode={mode}, {duration}s)\n')
    for h, sk, dr in zip(clients, skews, drifts):
        out = os.path.join(BASE, 'results', f'{mode}_{name}_{h.name}.csv')
        h.sendCmd(f'python3 {BASE}/timesync_client.py --server 10.0.0.1 --name {h.name} '
                  f'--skew {sk} --drift {dr} --count {count} --interval {interval} --out {out}')
    if congest:   # saturating UDP background traffic in both directions across the bottleneck
        n1.sendCmd(f'iperf -c 10.0.0.22 -u -b 12M -p 5201 -t {duration}')
        n2.sendCmd(f'iperf -c 10.0.0.21 -u -b 12M -p 5202 -t {duration}')
    for h in clients:
        h.waitOutput()
    if congest:
        n1.waitOutput(); n2.waitOutput()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--duration', type=int, default=40)
    ap.add_argument('--interval', type=float, default=0.5)
    ap.add_argument('--delay', type=int, default=5, help='access-link delay (ms)')
    ap.add_argument('--jitter', type=int, default=2, help='access-link jitter (ms)')
    ap.add_argument('--loss', type=float, default=0, help='access-link loss (%%)')
    ap.add_argument('--scenarios', default='idle,congested')
    ap.add_argument('--cli', action='store_true', help='drop to Mininet CLI at the end')
    a = ap.parse_args()
    mode = open('/tmp/sdn_mode.txt').read().strip() if os.path.exists('/tmp/sdn_mode.txt') else 'dynamic'

    setLogLevel('info')
    sh('ovs-vsctl --all destroy qos -- --all destroy queue')
    net, s1, s2, bott = build(a.delay, a.jitter, a.loss)
    net.start()
    try:
        net.waitConnected(timeout=20)
    except Exception:
        time.sleep(5)
    json.dump({int(s1.dpid, 16): s1.ports[bott.intf1], int(s2.dpid, 16): s2.ports[bott.intf2]},
              open('/tmp/bottleneck.json', 'w'))
    setup_qos(bott.intf1.name); setup_qos(bott.intf2.name)
    time.sleep(1)
    net.pingAll()

    net.get('srv').cmd(f'python3 {BASE}/timesync_server.py > /tmp/server.log 2>&1 &')
    net.get('n2').cmd('iperf -s -u -p 5201 > /dev/null 2>&1 &')
    net.get('n1').cmd('iperf -s -u -p 5202 > /dev/null 2>&1 &')
    time.sleep(1)

    for sc in a.scenarios.split(','):
        run_scenario(net, mode, sc, a.duration, a.interval, congest=(sc != 'idle'))
        time.sleep(8)          # let the controller see the link go idle again

    if a.cli:
        CLI(net)
    sh('pkill -f timesync_server; pkill iperf')
    net.stop()
    sh('ovs-vsctl --all destroy qos -- --all destroy queue')

if __name__ == '__main__':
    main()
