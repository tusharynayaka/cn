#!/usr/bin/env python3
"""UDP time-sync client with a simulated skewed/drifting clock."""
import argparse, csv, socket, struct, time

FMT = '!BIddd'
REQ, RESP = 1, 2

class SimClock:
    def __init__(self, skew, drift_ppm):
        self.skew = skew
        self.drift = drift_ppm * 1e-6
        self.t0 = time.time()
        self.adj = 0.0

    def now(self):
        t = time.time()
        return t + self.skew + self.drift * (t - self.t0) + self.adj

    def error(self):
        return self.now() - time.time()

    def step(self, theta):
        self.adj += theta

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--server', required=True)
    ap.add_argument('--port', type=int, default=5005)
    ap.add_argument('--name', default='client')
    ap.add_argument('--skew', type=float, default=0.25)
    ap.add_argument('--drift', type=float, default=50)
    ap.add_argument('--count', type=int, default=60)
    ap.add_argument('--interval', type=float, default=0.5)
    ap.add_argument('--timeout', type=float, default=1.0)
    ap.add_argument('--sync-every', type=int, default=4)
    ap.add_argument('--out', default='client.csv')
    a = ap.parse_args()

    clock = SimClock(a.skew, a.drift)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(a.timeout)
    window = []

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
                rtt = (t4 - t1) - (t3 - t2)
                theta = ((t2 - t1) + (t3 - t4)) / 2.0
                true_theta = -clock.error()
                off_err = abs(theta - true_theta)
                
                w.writerow([f'{time.time():.3f}', a.name, seq, 'ok', 
                            f'{rtt*1000:.3f}', f'{theta*1000:.3f}', 
                            f'{off_err*1000:.3f}', f'{abs(clock.error())*1000:.3f}'])
                window.append((rtt, theta))
                if len(window) >= a.sync_every:
                    best_theta = min(window, key=lambda x: x[0])[1]
                    clock.step(best_theta)
                    window.clear()
            else:
                w.writerow([f'{time.time():.3f}', a.name, seq, 'lost', '', '', '',
                            f'{abs(clock.error())*1000:.3f}'])
            f.flush()
            time.sleep(max(0, a.interval - (time.time() - start)))

if __name__ == '__main__':
    main()
