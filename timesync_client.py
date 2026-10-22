#!/usr/bin/env python3
"""UDP time-sync client with a simulated skewed/drifting clock.
All Mininet hosts share one real clock, so each client gets an artificial
clock error; that lets us measure the true synchronization error."""
import argparse, csv, socket, struct, time

FMT = '!BIddd'
REQ, RESP = 1, 2

class SimClock:
    def __init__(self, skew, drift_ppm):
        self.skew, self.drift, self.t0, self.adj = skew, drift_ppm * 1e-6, time.time(), 0.0
    def now(self):
        t = time.time()
        return t + self.skew + self.drift * (t - self.t0) + self.adj
    def error(self):                    # client clock minus true time
        return self.now() - time.time()
    def step(self, theta):              # apply NTP-style correction
        self.adj += theta

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--server', required=True)
    ap.add_argument('--port', type=int, default=5005)
    ap.add_argument('--name', default='client')
    ap.add_argument('--skew', type=float, default=0.25, help='initial clock error (s)')
    ap.add_argument('--drift', type=float, default=50, help='clock drift (ppm)')
    ap.add_argument('--count', type=int, default=60)
    ap.add_argument('--interval', type=float, default=0.5)
    ap.add_argument('--timeout', type=float, default=1.0)
    ap.add_argument('--sync-every', type=int, default=4, help='apply best of N samples')
    ap.add_argument('--out', default='client.csv')
    a = ap.parse_args()

    clock = SimClock(a.skew, a.drift)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(a.timeout)
    window = []                                   # (delay, theta)

    with open(a.out, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['wall_time', 'name', 'seq', 'status', 'rtt_ms', 'theta_ms',
                    'offset_err_ms', 'clock_err_ms'])
        for seq in range(a.count):
            start = time.time()
            t1 = clock.now()
            sock.sendto(struct.pack(FMT, REQ, seq, t1, 0, 0), (a.server, a.port))
            ok = False
            try:
                while True:
                    data, _ = sock.recvfrom(1024)
                    t4 = clock.now()
                    typ, rseq, _, t2, t3 = struct.unpack(FMT, data)
                    if typ == RESP and rseq == seq:
                        ok = True
                        break
            except socket.timeout:
                pass

            if ok:
                delay = (t4 - t1) - (t3 - t2)             # round-trip delay
                theta = ((t2 - t1) + (t3 - t4)) / 2       # estimated offset (server - client)
                true_theta = -clock.error()               # real offset
                off_err = abs(theta - true_theta)
                w.writerow([f'{time.time():.3f}', a.name, seq, 'ok', f'{delay*1000:.3f}',
                            f'{theta*1000:.3f}', f'{off_err*1000:.3f}',
                            f'{abs(clock.error())*1000:.3f}'])
                window.append((delay, theta))
                if len(window) >= a.sync_every:           # min-delay filter, then correct clock
                    clock.step(min(window)[1])
                    window.clear()
            else:
                w.writerow([f'{time.time():.3f}', a.name, seq, 'lost', '', '', '',
                            f'{abs(clock.error())*1000:.3f}'])
            f.flush()
            time.sleep(max(0, a.interval - (time.time() - start)))
    print(f'[{a.name}] done -> {a.out}')

if __name__ == '__main__':
    main()
