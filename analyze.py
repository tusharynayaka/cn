#!/usr/bin/env python3
"""Summarise results/*.csv and plot baseline vs static vs dynamic."""
import glob, os, re
import pandas as pd
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt

frames = []
for f in glob.glob('results/*.csv'):
    fname = os.path.basename(f)[:-4]
    m = re.match(r'^(baseline|static|dynamic)_(idle|congested)_(c\d+)$', fname)
    if not m:
        continue
    mode, scen, client = m.groups()
    df = pd.read_csv(f)
    df['mode'], df['scenario'], df['client'] = mode, scen, client
    frames.append(df)

if not frames:
    print("No valid CSV results found.")
    exit(1)

d = pd.concat(frames, ignore_index=True)
ok = d[d.status == 'ok']
s = ok.groupby(['scenario', 'mode']).agg(
    rtt_mean=('rtt_ms', 'mean'),
    rtt_p95=('rtt_ms', lambda x: x.quantile(.95)),
    offset_err_mean=('offset_err_ms', 'mean'),
    offset_err_p95=('offset_err_ms', lambda x: x.quantile(.95)),
    clock_err_mean=('clock_err_ms', 'mean')
)
s['loss_pct'] = d.assign(l=d.status.eq('lost')).groupby(['scenario', 'mode']).l.mean() * 100
s = s.round(3)
print(s.to_string())
s.to_csv('summary.csv')

for m_col, title in [('rtt_mean', 'Mean RTT (ms)'), 
                    ('offset_err_mean', 'Mean offset-estimation error (ms)'),
                    ('clock_err_mean', 'Mean residual clock error (ms)'), 
                    ('loss_pct', 'Sync packet loss (%)')]:
    s[m_col].unstack('mode').plot.bar(rot=0, title=title)
    plt.ylabel(title)
    plt.tight_layout()
    plt.savefig(f'plot_{m_col}.png', dpi=150)
    plt.close()

print('Saved summary.csv and plot_*.png')
