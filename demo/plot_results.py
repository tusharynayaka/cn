#!/usr/bin/env python3
"""Plots + summary for the time-sync demo.

  sync_story.png       one time-aligned figure: traffic / RTT per probe / sync error
  summary_bars.png     mean RTT, mean |error|, reply rate per case and client
  traffic_timeline.png per-host (h1..h4) send/receive rates
  summary_results.csv  numbers behind the plots
"""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CASES = [("normal", "1. Normal"), ("unprio", "2. Congestion\nno SDN"), ("sdn", "3. Congestion\n+ dynamic SDN")]
CASE_NAME = {t: n.replace("\n", " ") for t, n in CASES}
PHASES = {"1-normal": ("Case 1: normal traffic", "#d6e6f5", "normal"),
          "2-congestion-noSDN": ("Case 2: congestion, SDN OFF", "#f8d7d7", "unprio"),
          "3-congestion-SDN": ("Case 3: congestion, SDN ON", "#d9efd2", "sdn")}
CLIENTS = {"h2": ("tab:blue", "o"), "h3": ("tab:orange", "s")}
LOST_Y = 3000


def load(tag, client):
    return pd.read_csv(os.path.join(HERE, f"sync_{tag}_{client}.csv"))


def stats(df):
    ok = df.dropna(subset=["Delay_ms"])
    return dict(replies=len(ok), total=len(df),
                mean_rtt=ok["Delay_ms"].mean() if len(ok) else np.nan,
                max_rtt=ok["Delay_ms"].max() if len(ok) else np.nan,
                mean_err=ok["Error_ms"].abs().mean() if len(ok) else np.nan,
                max_err=ok["Error_ms"].abs().max() if len(ok) else np.nan)


def summary():
    rows = []
    for tag, _ in CASES:
        for c in CLIENTS:
            s = stats(load(tag, c))
            rows.append({"case": CASE_NAME[tag], "client": c,
                         "replies": f"{s['replies']}/{s['total']}",
                         "mean_rtt_ms": s["mean_rtt"], "max_rtt_ms": s["max_rtt"],
                         "mean_abs_err_ms": s["mean_err"], "max_abs_err_ms": s["max_err"]})
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(HERE, "summary_results.csv"), index=False)
    print(df.to_string(index=False, float_format=lambda v: f"{v:.2f}"))


def timeline():
    tl = pd.read_csv(os.path.join(HERE, "traffic_timeline.csv"))
    body = tl[tl["phase"] != "setup"].copy()          # drop pingAll/setup traffic
    T0 = body["ts"].min()
    body["t"] = body["ts"] - T0
    grp = (body["phase"] != body["phase"].shift()).cumsum()
    spans = [[g["phase"].iloc[0], g["t"].min(), g["t"].max()] for _, g in body.groupby(grp)]
    for i in range(len(spans) - 1):                    # make spans contiguous
        spans[i][2] = spans[i + 1][1]
    sdn = []
    ev_path = os.path.join(HERE, "qos_events.csv")
    if os.path.exists(ev_path):
        on = None
        for _, e in pd.read_csv(ev_path).iterrows():
            t = e["ts"] - T0
            if e["event"] == "sdn_rule_on":
                on = t
            elif e["event"] == "sdn_rule_off" and on is not None:
                sdn.append((on, t))
                on = None
        if on is not None:
            sdn.append((on, body["t"].max()))
    return body, T0, spans, sdn


def shade(axes, spans, sdn):
    for phase, a, b in spans:
        for ax in axes:
            ax.axvspan(a, b, color=PHASES[phase][1], alpha=0.6, lw=0)
    for a, b in sdn:
        for ax in axes:
            ax.axvspan(a, b, facecolor="none", edgecolor="green", hatch="//", alpha=0.35, lw=0)


def story_figure(body, T0, spans, sdn):
    data = {}
    for tag, _ in CASES:
        for c in CLIENTS:
            df = load(tag, c)
            skew_s = -df["True_Offset_ms"].iloc[0] / 1000.0
            df["t"] = (df["T1"] - skew_s) - T0          # back to real (shared) time
            data[(tag, c)] = df

    fig, axes = plt.subplots(3, 1, sharex=True, figsize=(15, 11),
                             gridspec_kw={"height_ratios": [1, 1.5, 1.1]})
    a_tr, a_rtt, a_err = axes
    shade(axes, spans, sdn)

    # 1) traffic
    a_tr.plot(body["t"], body["h4_tx"], color="tab:red", lw=2, label="h4 (flood generator): sent")
    a_tr.plot(body["t"], body["h1_rx"], color="tab:purple", lw=2, label="h1 (time server): received")
    a_tr.axhline(10, color="gray", ls=":", lw=1)
    a_tr.text(0.3, 10.4, "server link capacity = 10 Mbit/s", fontsize=8, color="dimgray")
    a_tr.set_ylim(0, max(body["h4_tx"].max() * 1.2, 12))
    a_tr.set_ylabel("Mbit/s")
    a_tr.set_title("1) Where the traffic is: h4 floods, the server link (h1) saturates", loc="left", fontsize=11)
    a_tr.legend(loc="center left", bbox_to_anchor=(1.01, 0.5), fontsize=9)

    # 2) RTT per probe  /  3) error per probe
    used = set()
    for (tag, c), df in data.items():
        color, marker = CLIENTS[c]
        ok = df.dropna(subset=["Delay_ms"])
        lab = f"{c}: reply received" if c not in used else None
        used.add(c)
        a_rtt.plot(ok["t"], ok["Delay_ms"], color=color, marker=marker, ms=5, lw=1, label=lab)
        a_err.plot(ok["t"], ok["Error_ms"], color=color, marker=marker, ms=5, lw=1)
        lost = df[df["Delay_ms"].isna() & df["t"].notna()]
        if len(lost):
            a_rtt.scatter(lost["t"], [LOST_Y] * len(lost), marker="x", color="red", s=45, zorder=5,
                          label="probe LOST (no reply in 2 s)" if "lost" not in used else None)
            used.add("lost")
    a_rtt.set_yscale("log")
    a_rtt.set_ylim(3, 6000)
    a_rtt.axhline(LOST_Y * 0.7, color="red", ls=":", lw=0.8)
    a_rtt.set_ylabel("RTT (ms, log scale)")
    a_rtt.set_title("2) Time-sync round-trip delay, one marker per probe", loc="left", fontsize=11)
    a_rtt.legend(loc="center left", bbox_to_anchor=(1.01, 0.5), fontsize=9)

    a_err.set_yscale("symlog", linthresh=1)
    top = max(df["Error_ms"].abs().max() for df in data.values() if df["Error_ms"].notna().any())
    a_err.set_ylim(-1.5, max(top * 6, 10))
    a_err.axhline(0, color="k", lw=0.8)
    a_err.set_ylabel("error (ms, symlog)")
    a_err.set_xlabel("Time since start of test (s)      hatched green = SDN priority rule installed")
    a_err.set_title("3) Synchronisation error = estimated offset - true offset", loc="left", fontsize=11)

    # labels and numbers per phase
    for phase, a, b in spans:
        label, _, tag = PHASES[phase]
        mid = (a + b) / 2
        a_tr.text(mid, 0.96, label, transform=a_tr.get_xaxis_transform(), ha="center",
                  va="top", fontsize=10, weight="bold")
        lines, errs = [], []
        for c in CLIENTS:
            s = stats(data[(tag, c)])
            lines.append(f"{c}: {s['replies']}/{s['total']} replies" +
                         (f", mean RTT {s['mean_rtt']:.0f} ms" if s["replies"] else ""))
            errs.append(f"{c} {s['mean_err']:.2f} ms" if s["replies"] else f"{c} n/a")
        a_rtt.text(mid, 0.03, "\n".join(lines), transform=a_rtt.get_xaxis_transform(),
                   ha="center", va="bottom", fontsize=9,
                   bbox=dict(boxstyle="round", fc="white", ec="gray", alpha=0.9))
        a_err.text(mid, 0.55, "mean |error|:  " + "   ".join(errs), transform=a_err.get_xaxis_transform(),
                   ha="center", va="center", fontsize=9,
                   bbox=dict(boxstyle="round", fc="white", ec="gray", alpha=0.9))

    a_err.set_xlim(0, body["t"].max())
    plt.tight_layout(rect=(0, 0, 0.84, 1))
    out = os.path.join(HERE, "sync_story.png")
    plt.savefig(out, dpi=120)
    print(f"[*] saved {out}")


def bars_figure():
    fig, axes = plt.subplots(1, 3, figsize=(16, 5))
    metrics = [("mean_rtt", "Mean round-trip delay (ms, log)", True),
               ("mean_err", "Mean |sync error| (ms, log)", True),
               ("pct", "Replies received (%)", False)]
    w = 0.36
    for ax, (key, title, log) in zip(axes, metrics):
        for j, (c, (color, _)) in enumerate(CLIENTS.items()):
            vals = []
            for tag, _n in CASES:
                s = stats(load(tag, c))
                vals.append(100.0 * s["replies"] / s["total"] if key == "pct" else s[key])
            xs = np.arange(len(CASES)) + (j - 0.5) * w
            bars = ax.bar(xs, [0 if np.isnan(v) else v for v in vals], w, color=color, label=c)
            for x, v in zip(xs, vals):
                if not np.isnan(v):
                    ax.text(x, v * (1.12 if log else 1) + (0 if log else 1), (f"{v:.2f}" if v < 1 else f"{v:.1f}" if v < 100 else f"{v:.0f}"),
                            ha="center", va="bottom", fontsize=9)
        ax.set_xticks(range(len(CASES)))
        ax.set_xticklabels([n for _, n in CASES])
        ax.set_title(title)
        ax.grid(True, axis="y", alpha=0.4)
        if log:
            ax.set_yscale("log")
        else:
            ax.set_ylim(0, 115)
    axes[0].legend(title="client")
    plt.tight_layout()
    out = os.path.join(HERE, "summary_bars.png")
    plt.savefig(out, dpi=120)
    print(f"[*] saved {out}")


def host_figure(body, spans, sdn):
    hosts = [("h1", "h1 (time server)"), ("h2", "h2 (client 1)"),
             ("h3", "h3 (client 2)"), ("h4", "h4 (flood generator)")]
    fig, axes = plt.subplots(4, 1, sharex=True, figsize=(14, 10))
    shade(axes, spans, sdn)
    for ax, (h, title) in zip(axes, hosts):
        small = h in ("h2", "h3")
        k = 1000.0 if small else 1.0
        ax.plot(body["t"], body[f"{h}_tx"] * k, label="sent by host", color="tab:orange")
        ax.plot(body["t"], body[f"{h}_rx"] * k, label="received by host", color="tab:purple")
        ax.set_ylabel("kbit/s" if small else "Mbit/s")
        ax.set_title(title + ("  (sync probes only; gaps = lost probes/replies)" if small else ""),
                     loc="left", fontsize=10)
        ax.grid(True, alpha=0.4)
    axes[0].legend(loc="center left", bbox_to_anchor=(1.01, 0.5), fontsize=8)
    for phase, a, b in spans:
        axes[0].text((a + b) / 2, 0.93, PHASES[phase][0], transform=axes[0].get_xaxis_transform(),
                     ha="center", va="top", fontsize=9, weight="bold")
    axes[-1].set_xlabel("Time (s)      hatched green = SDN priority rule installed")
    axes[-1].set_xlim(0, body["t"].max())
    plt.tight_layout(rect=(0, 0, 0.9, 1))
    out = os.path.join(HERE, "traffic_timeline.png")
    plt.savefig(out, dpi=120)
    print(f"[*] saved {out}")


if __name__ == "__main__":
    summary()
    bars_figure()
    body, T0, spans, sdn = timeline()
    story_figure(body, T0, spans, sdn)
    host_figure(body, spans, sdn)
