#!/usr/bin/env python3
"""UDP time-sync client (NTP-style 4-timestamp exchange).

Mininet hosts all share one system clock, so the true offset is 0 and
"synchronization error" would be unmeasurable. This client therefore runs
on a deliberately skewed local clock (--skew-ms). The true offset to be
recovered is then exactly -skew, and:

    error = estimated_offset - true_offset = (d_forward - d_reverse) / 2

i.e. the error is caused purely by path asymmetry (queueing under congestion).
"""
import argparse
import csv
import socket
import struct
import time


def sync_time(server_ip, port, iterations, log_file, skew_s, interval, timeout):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, 0xB8)  # DSCP EF

    def clock():
        return time.time() + skew_s

    true_offset_ms = -skew_s * 1000.0
    print(f"Sync with {server_ip}:{port} | injected clock error {skew_s*1000:+.0f} ms "
          f"(true offset = {true_offset_ms:+.1f} ms)", flush=True)

    with open(log_file, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Iteration", "T1", "T2", "T3", "T4", "Delay_ms",
                    "Offset_ms", "True_Offset_ms", "Error_ms"])

        for i in range(1, iterations + 1):
            t1 = clock()
            sock.sendto(struct.pack("!d", t1), (server_ip, port))
            deadline = time.time() + timeout
            done = False

            while not done:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                sock.settimeout(remaining)
                try:
                    data, _ = sock.recvfrom(1024)
                except socket.timeout:
                    break
                t4 = clock()
                if len(data) < 24:
                    continue
                echo_t1, t2, t3 = struct.unpack("!ddd", data[:24])
                if echo_t1 != t1:      # late reply to an earlier probe: ignore
                    continue

                delay_ms = ((t4 - t1) - (t3 - t2)) * 1000.0
                offset_ms = ((t2 - t1) + (t3 - t4)) / 2.0 * 1000.0
                error_ms = offset_ms - true_offset_ms

                print(f"Iter {i:02d} | RTT {delay_ms:8.2f} ms | offset {offset_ms:9.2f} ms "
                      f"| error {error_ms:8.2f} ms", flush=True)
                w.writerow([i, t1, t2, t3, t4, delay_ms, offset_ms,
                            true_offset_ms, error_ms])
                done = True

            if not done:
                print(f"Iter {i:02d} | TIMEOUT (no reply in {timeout:.1f}s)", flush=True)
                w.writerow([i, t1, "", "", "", "", "", true_offset_ms, ""])  # keep T1 so the plot can place the lost probe in time

            f.flush()
            time.sleep(interval)

    sock.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("server", nargs="?", default="10.0.0.1")
    ap.add_argument("out", nargs="?", default="sync_results.csv")
    ap.add_argument("--port", type=int, default=12345)
    ap.add_argument("--iterations", type=int, default=20)
    ap.add_argument("--skew-ms", type=float, default=0.0,
                    help="inject a known local clock error (ms)")
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--timeout", type=float, default=2.0)
    a = ap.parse_args()
    sync_time(a.server, a.port, a.iterations, a.out, a.skew_ms / 1000.0,
              a.interval, a.timeout)
