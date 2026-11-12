#!/usr/bin/env python3
"""
Dynamic SDN QoS controller for time-sync traffic (Ryu, OpenFlow 1.3).

Polls port counters every second. When ingress on any switch port exceeds
START_MBPS it installs a high-priority flow that sends UDP/12345 traffic to
the server through OVS queue 1 (the priority HTB queue on the server-facing
port). When load falls below STOP_MBPS the flow is removed.

/tmp/qos_mode containing "off" disables the rule (detection still logged);
the demo uses this for the "congestion without SDN" case.
"""
import os
import time

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, CONFIG_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib import hub

SERVER_IP = "10.0.0.1"
SERVER_PORT_NO = 1        # s1 port facing h1 (first link added in the topology)
SYNC_UDP_PORT = 12345
PRIORITY_QUEUE = 1        # queue 1 = priority HTB queue on s1-eth1
START_MBPS = 5.0
STOP_MBPS = 2.0
POLL_SECONDS = 1
MODE_FILE = "/tmp/qos_mode"
EVENT_LOG = "qos_events.csv"


class DynamicQoSController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.datapaths = {}
        self.prev = {}            # (dpid, port) -> (rx_bytes, time)
        self.congested = False
        self.qos_active = False
        with open(EVENT_LOG, "w") as f:
            f.write("ts,event\n")
        hub.spawn(self._monitor)

    # ---------- helpers ----------
    def _event(self, name):
        with open(EVENT_LOG, "a") as f:
            f.write(f"{time.time()},{name}\n")

    @staticmethod
    def _qos_enabled():
        try:
            with open(MODE_FILE) as f:
                return f.read().strip() != "off"
        except OSError:
            return True

    def _priority_match(self, parser):
        return parser.OFPMatch(eth_type=0x0800, ipv4_dst=SERVER_IP,
                               ip_proto=17, udp_dst=SYNC_UDP_PORT)

    def add_flow(self, dp, priority, match, actions):
        ofp, parser = dp.ofproto, dp.ofproto_parser
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(parser.OFPFlowMod(datapath=dp, priority=priority,
                                      match=match, instructions=inst))

    def install_priority_rule(self, dp):
        parser = dp.ofproto_parser
        actions = [parser.OFPActionSetQueue(PRIORITY_QUEUE),
                   parser.OFPActionOutput(SERVER_PORT_NO)]
        self.add_flow(dp, 65535, self._priority_match(parser), actions)

    def remove_priority_rule(self, dp):
        ofp, parser = dp.ofproto, dp.ofproto_parser
        dp.send_msg(parser.OFPFlowMod(
            datapath=dp, table_id=ofp.OFPTT_ALL, command=ofp.OFPFC_DELETE,
            out_port=ofp.OFPP_ANY, out_group=ofp.OFPG_ANY,
            match=self._priority_match(parser)))

    # ---------- switch connect ----------
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        dp = ev.msg.datapath
        parser = dp.ofproto_parser
        self.datapaths[dp.id] = dp
        # table-miss: ordinary L2 switching (also handles ARP)
        self.add_flow(dp, 0, parser.OFPMatch(),
                      [parser.OFPActionOutput(dp.ofproto.OFPP_NORMAL)])
        self.logger.info("[*] Switch DPID %s connected, table-miss -> NORMAL", dp.id)

    # ---------- telemetry ----------
    def _monitor(self):
        while True:
            for dp in list(self.datapaths.values()):
                parser = dp.ofproto_parser
                dp.send_msg(parser.OFPPortStatsRequest(dp, 0, dp.ofproto.OFPP_ANY))
            hub.sleep(POLL_SECONDS)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply_handler(self, ev):
        dp = ev.msg.datapath
        now = time.time()
        peak, peak_port = None, None

        for stat in ev.msg.body:
            if stat.port_no > 0xFFFFFF00:      # skip LOCAL
                continue
            key = (dp.id, stat.port_no)
            if key in self.prev:
                b0, t0 = self.prev[key]
                dt = now - t0
                if dt > 0:
                    mbps = (stat.rx_bytes - b0) * 8 / dt / 1e6
                    if peak is None or mbps > peak:
                        peak, peak_port = mbps, stat.port_no
            self.prev[key] = (stat.rx_bytes, now)

        if peak is None:                       # first sample, no rate yet
            return

        # one decision per reply, based on the busiest ingress port
        if not self.congested and peak >= START_MBPS:
            self.congested = True
            self.logger.warning("[!] CONGESTION DETECTED on port %s: %.1f Mbps",
                                peak_port, peak)
            self._event("congestion_start")
        elif self.congested and peak < STOP_MBPS:
            self.congested = False
            self.logger.info("[*] Traffic normalized (%.1f Mbps)", peak)
            self._event("congestion_end")

        want_rule = self.congested and self._qos_enabled()
        if want_rule and not self.qos_active:
            self.install_priority_rule(dp)
            self.qos_active = True
            self.logger.info("[*] SDN: priority rule installed "
                             "(UDP/%d -> queue %d, prio 65535)",
                             SYNC_UDP_PORT, PRIORITY_QUEUE)
            self._event("sdn_rule_on")
        elif not want_rule and self.qos_active:
            self.remove_priority_rule(dp)
            self.qos_active = False
            self.logger.info("[*] SDN: priority rule removed")
            self._event("sdn_rule_off")
        elif self.congested and not self._qos_enabled() and not self.qos_active:
            pass  # congestion present but SDN deliberately disabled (case 2)
