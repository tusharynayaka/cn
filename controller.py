"""Ryu OpenFlow1.3 controller: learning switch + time-sync traffic prioritisation."""
import json, os, time
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, DEAD_DISPATCHER, CONFIG_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, udp, ether_types
from ryu.lib import hub

MODE = os.environ.get('SDN_MODE', 'dynamic')
NTP_PORT = 5005
BW = 10e6
HIGH, LOW = 0.60, 0.30
POLL = 1.0
BNECK_FILE = '/tmp/bottleneck.json'

class TimeSyncController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.mac_to_port, self.datapaths = {}, {}
        self.ntp_flows = {}
        self.priority_on = (MODE == 'static')
        self.rate, self.last, self.low_cnt = {}, {}, 0
        self._bn, self._mtime = {}, 0
        with open('/tmp/sdn_mode.txt', 'w') as f:
            f.write(MODE)
        self.logger.info('=== SDN_MODE = %s ===', MODE)
        if MODE == 'dynamic':
            hub.spawn(self._monitor)

    def _bneck(self):
        try:
            m = os.path.getmtime(BNECK_FILE)
            if m != self._mtime:
                self._bn = {int(k): v for k, v in json.load(open(BNECK_FILE)).items()}
                self._mtime = m
        except Exception:
            pass
        return self._bn

    def _add_flow(self, dp, prio, match, actions, idle=0):
        ofp, p = dp.ofproto, dp.ofproto_parser
        inst = [p.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(p.OFPFlowMod(datapath=dp, priority=prio, match=match,
                                 instructions=inst, idle_timeout=idle))

    def _ntp_actions(self, dp, out_port):
        p = dp.ofproto_parser
        acts = []
        if self.priority_on and out_port == self._bneck().get(dp.id):
            acts.append(p.OFPActionSetQueue(1))
        acts.append(p.OFPActionOutput(out_port))
        return acts

    def _add_ntp_flow(self, dp, in_port, src, dst, out_port, field):
        p = dp.ofproto_parser
        match = p.OFPMatch(in_port=in_port, eth_src=src, eth_dst=dst,
                           eth_type=0x0800, ip_proto=17, **{field: NTP_PORT})
        self._add_flow(dp, 20, match, self._ntp_actions(dp, out_port))

    def _set_priority(self, flag, rate):
        self.priority_on = flag
        self.logger.info('[DYNAMIC] bottleneck %.2f Mbps -> time-sync priority %s',
                         rate / 1e6, 'ON' if flag else 'OFF')
        for (dpid, in_port, src, dst, out_port, field) in list(self.ntp_flows.values()):
            dp = self.datapaths.get(dpid)
            if dp:
                self._add_ntp_flow(dp, in_port, src, dst, out_port, field)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def features_handler(self, ev):
        dp = ev.msg.datapath
        p, ofp = dp.ofproto_parser, dp.ofproto
        self._add_flow(dp, 0, p.OFPMatch(),
                       [p.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)])

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def state_change(self, ev):
        dp = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[dp.id] = dp
        else:
            self.datapaths.pop(dp.id, None)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in(self, ev):
        msg, dp = ev.msg, ev.msg.datapath
        ofp, p = dp.ofproto, dp.ofproto_parser
        in_port = msg.match['in_port']
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if not eth or eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return
        src, dst = eth.src, eth.dst
        self.mac_to_port.setdefault(dp.id, {})[src] = in_port
        out_port = self.mac_to_port[dp.id].get(dst, ofp.OFPP_FLOOD)
        actions = [p.OFPActionOutput(out_port)]

        if out_port != ofp.OFPP_FLOOD:
            u = pkt.get_protocol(udp.udp)
            if u and NTP_PORT in (u.src_port, u.dst_port) and MODE != 'baseline':
                field = 'udp_dst' if u.dst_port == NTP_PORT else 'udp_src'
                self.ntp_flows[(dp.id, in_port, src, dst, field)] = \
                    (dp.id, in_port, src, dst, out_port, field)
                self.logger.info('[APP] time-sync flow s%s %s -> %s detected', dp.id, src, dst)
                self._add_ntp_flow(dp, in_port, src, dst, out_port, field)
            else:
                match = p.OFPMatch(in_port=in_port, eth_dst=dst, eth_src=src)
                self._add_flow(dp, 10, match, actions, idle=60)

        data = msg.data if msg.buffer_id == ofp.OFP_NO_BUFFER else None
        dp.send_msg(p.OFPPacketOut(datapath=dp, buffer_id=msg.buffer_id,
                                   in_port=in_port, actions=actions, data=data))

    def _monitor(self):
        while True:
            hub.sleep(POLL)
            for dp in list(self.datapaths.values()):
                port = self._bneck().get(dp.id)
                if port is not None:
                    dp.send_msg(dp.ofproto_parser.OFPPortStatsRequest(dp, 0, port))

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def stats_reply(self, ev):
        dp, now = ev.msg.datapath, time.time()
        port = self._bneck().get(dp.id)
        for st in ev.msg.body:
            if st.port_no != port:
                continue
            prev = self.last.get(dp.id)
            self.last[dp.id] = (now, st.tx_bytes)
            if prev and now > prev[0]:
                self.rate[dp.id] = (st.tx_bytes - prev[1]) * 8 / (now - prev[0])
        if not self.rate:
            return
        r = max(self.rate.values())
        if not self.priority_on and r > HIGH * BW:
            self.low_cnt = 0
            self._set_priority(True, r)
        elif self.priority_on and r < LOW * BW:
            self.low_cnt += 1
            if self.low_cnt >= 3:
                self._set_priority(False, r)
        else:
            self.low_cnt = 0
