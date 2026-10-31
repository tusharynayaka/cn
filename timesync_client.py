import socket
import time
import struct
import sys
import csv

def sync_time(server_ip, port=12345, iterations=20, log_file="sync_results.csv"):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2.0)
    
    # Set DSCP value for Quality of Service (QoS)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, 0xB8)

    print(f"[*] Starting synchronization with Server {server_ip}:{port}...")

    with open(log_file, mode='w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["Iteration", "T1", "T2", "T3", "T4", "Delay_ms", "Offset_ms"])

        for i in range(iterations):
            t1 = time.time()  # T1: Client Send Time
            payload = struct.pack('!d', t1)
            
            try:
                sock.sendto(payload, (server_ip, port))
                data, _ = sock.recvfrom(1024)
                t4 = time.time()  # T4: Client Receive Time

                if len(data) >= 24:
                    recv_t1, t2, t3 = struct.unpack('!ddd', data[:24])
                    
                    delay = (t4 - recv_t1) - (t3 - t2)
                    offset = ((t2 - recv_t1) + (t3 - t4)) / 2.0

                    delay_ms = delay * 1000.0
                    offset_ms = offset * 1000.0

                    print(f"Iter {i+1:02d} | RTT Delay: {delay_ms:7.3f} ms | Clock Offset: {offset_ms:8.3f} ms")
                    writer.writerow([i+1, recv_t1, t2, t3, t4, delay_ms, offset_ms])
            except socket.timeout:
                print(f"Iter {i+1:02d} | Timeout waiting for server response.")
            
            time.sleep(0.5)

    sock.close()

if __name__ == "__main__":
    ip = sys.argv[1] if len(sys.argv) > 1 else "10.0.0.1"
    out = sys.argv[2] if len(sys.argv) > 2 else "sync_results.csv"
    sync_time(ip, log_file=out)
