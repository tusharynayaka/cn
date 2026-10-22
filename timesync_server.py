#!/usr/bin/env python3
"""UDP time server (NTP-like). Replies with T2 (receive) and T3 (transmit) timestamps."""
import argparse, socket, struct, threading, time
from collections import defaultdict

FMT = '!BIddd'          # type, seq, t1, t2, t3  (29 bytes)
REQ, RESP = 1, 2

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bind', default='0.0.0.0')
    ap.add_argument('--port', type=int, default=5005)
    ap.add_argument('--proc-delay-ms', type=float, default=0.0, help='emulate server load')
    a = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((a.bind, a.port))
    counts = defaultdict(int)

    def report():
        while True:
            time.sleep(5)
            print(f'[server] requests served: {dict(counts)}', flush=True)
    threading.Thread(target=report, daemon=True).start()

    print(f'[server] listening on {a.bind}:{a.port}', flush=True)
    while True:
        data, addr = sock.recvfrom(1024)
        t2 = time.time()                       # receive timestamp
        try:
            typ, seq, t1, _, _ = struct.unpack(FMT, data)
        except struct.error:
            continue                           # ignore malformed packets
        if typ != REQ:
            continue
        if a.proc_delay_ms:
            time.sleep(a.proc_delay_ms / 1000)
        t3 = time.time()                       # transmit timestamp
        sock.sendto(struct.pack(FMT, RESP, seq, t1, t2, t3), addr)
        counts[addr[0]] += 1

if __name__ == '__main__':
    main()
