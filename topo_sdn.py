#!/usr/bin/env python3
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController, OVSController
from mininet.cli import CLI
from mininet.log import setLogLevel, info
from mininet.link import TCLink

def create_topology():
    net = Mininet(switch=OVSSwitch, link=TCLink, controller=OVSController)

    info('*** Adding Controller\n')
    c0 = net.addController('c0')

    info('*** Adding Server and Clients\n')
    s1_server = net.addHost('h1', ip='10.0.0.1/24') # Time Reference Server
    c1 = net.addHost('h2', ip='10.0.0.2/24')        # Client 1
    c2 = net.addHost('h3', ip='10.0.0.3/24')        # Client 2
    bg_host = net.addHost('h4', ip='10.0.0.4/24')   # Congestion Generator Host

    info('*** Adding Switch\n')
    sw1 = net.addSwitch('s1')

    info('*** Creating Links with Controlled Propagation Delay\n')
    # Link server with 5ms delay
    net.addLink(s1_server, sw1, delay='5ms', bw=10)
    # Link clients with 10ms and 15ms delay
    net.addLink(c1, sw1, delay='10ms', bw=10)
    net.addLink(c2, sw1, delay='15ms', bw=10)
    # Link congestion generator
    net.addLink(bg_host, sw1, delay='2ms', bw=10)

    info('*** Starting Network\n')
    net.start()

    info('*** Configuring OVS QoS Prioritization Flow Rules\n')
    # OpenFlow rule: UDP Port 12345 (Time Sync Traffic) assigned higher priority (65535)
    sw1.cmd('ovs-ofctl add-flow s1 priority=65535,udp,tp_dst=12345,actions=normal')
    # Default traffic rule lower priority
    sw1.cmd('ovs-ofctl add-flow s1 priority=100,ip,actions=normal')

    CLI(net)
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    create_topology()
