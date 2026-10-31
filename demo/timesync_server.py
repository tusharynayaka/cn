import socket
import time
import struct

SERVER_HOST = '0.0.0.0'  # Listens on all interfaces in h1's namespace
SERVER_PORT = 12345

def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    
    # Allow quick address/port reuse in network namespaces
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
    sock.bind((SERVER_HOST, SERVER_PORT))
    print(f"[*] Time Server listening on UDP port {SERVER_PORT}...")

    while True:
        try:
            data, addr = sock.recvfrom(1024)
            t2 = time.time()  # T2: Server Receive Time
            
            if len(data) < 8:
                continue
            
            # Unpack T1 from client payload
            t1 = struct.unpack('!d', data[:8])[0]
            
            t3 = time.time()  # T3: Server Transmit Time
            
            # Send back T1, T2, T3
            response = struct.pack('!ddd', t1, t2, t3)
            sock.sendto(response, addr)
            print(f"[+] Responded to {addr}")
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[!] Error: {e}")

    sock.close()

if __name__ == "__main__":
    main()
