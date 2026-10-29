import matplotlib.pyplot as plt
import pandas as pd
import sys

def plot_data(normal_csv, congestion_csv):
    df_norm = pd.read_csv(normal_csv)
    df_cong = pd.read_csv(congestion_csv)

    plt.figure(figsize=(10, 5))

    # Plot RTT Delay comparison
    plt.subplot(1, 2, 1)
    plt.plot(df_norm['Iteration'], df_norm['Delay_ms'], label='Normal Traffic', marker='o')
    plt.plot(df_cong['Iteration'], df_cong['Delay_ms'], label='Under Congestion', marker='x')
    plt.title('Round-Trip Delay (ms)')
    plt.xlabel('Iteration')
    plt.ylabel('Delay (ms)')
    plt.legend()
    plt.grid(True)

    # Plot Clock Offset comparison
    plt.subplot(1, 2, 2)
    plt.plot(df_norm['Iteration'], df_norm['Offset_ms'], label='Normal Traffic', marker='o')
    plt.plot(df_cong['Iteration'], df_cong['Offset_ms'], label='Under Congestion', marker='x')
    plt.title('Estimated Clock Offset (ms)')
    plt.xlabel('Iteration')
    plt.ylabel('Offset (ms)')
    plt.legend()
    plt.grid(True)

    plt.tight_layout()
    plt.savefig('sync_performance_comparison.png')
    print("[*] Performance comparison plot saved to 'sync_performance_comparison.png'")

if __name__ == "__main__":
    norm = sys.argv[1] if len(sys.argv) > 1 else "sync_normal.csv"
    cong = sys.argv[2] if len(sys.argv) > 2 else "sync_congested.csv"
    plot_data(norm, cong)
