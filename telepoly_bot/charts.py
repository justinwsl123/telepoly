"""Polymarket-inspired event card rendered to PNG via matplotlib.

Layout (top → bottom):
  ┌────────────────────────────────────────────────┐
  │  💰  1,234.56 USDT          LIVE · 03:42 left   │  ← header band
  │  TOTAL POOL                                     │
  ├────────────────────────────────────────────────┤
  │  🎯 <event title, wrapped>                      │  ← title band
  │                                                 │
  │     67%        YES probability ▌▌▌▌▌▌▌▌░░       │  ← gauge band
  │                                                 │
  ├────────────────────────────────────────────────┤
  │  ╱╲╱╲ (timeline of YES probability over time)   │  ← mini chart
  └────────────────────────────────────────────────┘

Design language: Apple HIG / Polymarket — soft greys, vivid systemGreen/Red
accents, generous whitespace, big bold pool number to emphasize the
parimutuel nature ("the more money on one side, the worse the odds").
"""
from __future__ import annotations

import io
import textwrap
from datetime import datetime, timezone
from typing import Sequence

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec


# Apple system colors
COLOR_YES   = "#34c759"   # systemGreen
COLOR_NO    = "#ff3b30"   # systemRed
COLOR_BG    = "#ffffff"
COLOR_PANEL = "#f5f5f7"
COLOR_GRID  = "#d2d2d7"
COLOR_TEXT  = "#1d1d1f"
COLOR_MUTED = "#86868b"
COLOR_LIVE  = "#ff3b30"


def _fmt_pool(usdt: float) -> str:
    """Render the pool number compactly (e.g. 12,345.6 → '12,345' / 1.2M)."""
    if usdt >= 1_000_000:
        return f"{usdt / 1_000_000:.2f}M"
    if usdt >= 10_000:
        return f"{usdt / 1000:.1f}k"
    if usdt >= 1:
        return f"{usdt:,.0f}"
    return f"{usdt:.2f}"


def _fmt_countdown(close_at: datetime | None) -> str:
    """Return a Polymarket-style 'live · 3h 42m left' string."""
    if close_at is None:
        return "LIVE"
    now = datetime.now(timezone.utc)
    close = close_at.replace(tzinfo=timezone.utc) if close_at.tzinfo is None else close_at
    delta = close - now
    secs = int(delta.total_seconds())
    if secs <= 0:
        return "CLOSED"
    if secs >= 86_400:
        return f"{secs // 86_400}d {secs % 86_400 // 3600}h left"
    if secs >= 3600:
        return f"{secs // 3600}h {secs % 3600 // 60}m left"
    return f"{secs // 60}m left"


def render_pool_timeline(
    points: Sequence[dict],
    *,
    title: str = "",
    fee_bps: int = 500,
    close_at: datetime | None = None,
    width: float = 9.0,
    height: float = 5.6,
    dpi: int = 140,
) -> bytes:
    """Render the headline event card as PNG bytes.

    `points` is the timeline produced by `core.snapshots.fetch_timeline`:
    [{"t": iso, "yes": micro, "no": micro, "yes_share": 0..1, "total": micro}, ...]
    """
    yes_share = points[-1]["yes_share"] if points else 0.5
    total_micro = points[-1]["total"] if points else 0
    total_usdt = total_micro / 1_000_000

    fig = plt.figure(figsize=(width, height), dpi=dpi, facecolor=COLOR_BG)
    gs = GridSpec(
        3, 1,
        height_ratios=[1.0, 1.4, 1.4],
        hspace=0.05,
        left=0.06, right=0.96, top=0.94, bottom=0.10,
    )
    ax_head = fig.add_subplot(gs[0]); ax_head.set_axis_off()
    ax_gauge = fig.add_subplot(gs[1]); ax_gauge.set_axis_off()
    ax_chart = fig.add_subplot(gs[2])

    # ---------- Header band: giant pool + live badge ----------
    # Note: stick to ASCII + simple Unicode here — DejaVu Sans (matplotlib's
    # default Linux font) has no color-emoji glyphs.
    ax_head.text(
        0.0, 0.65, _fmt_pool(total_usdt),
        transform=ax_head.transAxes, fontsize=44, fontweight="bold",
        color=COLOR_TEXT, va="center", ha="left",
    )
    ax_head.text(
        0.0, 0.12, "USDT  ·  TOTAL POOL",
        transform=ax_head.transAxes, fontsize=11, fontweight="bold",
        color=COLOR_MUTED, va="center", ha="left",
    )

    # LIVE pill + countdown (top-right)
    live_pill = mpatches.FancyBboxPatch(
        (0.84, 0.62), 0.13, 0.30,
        boxstyle="round,pad=0.02,rounding_size=0.04",
        transform=ax_head.transAxes,
        facecolor=COLOR_LIVE, edgecolor="none",
    )
    ax_head.add_patch(live_pill)
    ax_head.text(
        0.905, 0.77, "● LIVE",
        transform=ax_head.transAxes, fontsize=10, fontweight="bold",
        color="white", va="center", ha="center",
    )
    ax_head.text(
        0.905, 0.30, _fmt_countdown(close_at),
        transform=ax_head.transAxes, fontsize=9, color=COLOR_MUTED,
        va="center", ha="center",
    )

    # ---------- Gauge band: title + big YES% + horizontal bar ----------
    wrapped_title = "\n".join(textwrap.wrap(title or "Market", width=58)[:2])
    ax_gauge.text(
        0.0, 0.92, wrapped_title,
        transform=ax_gauge.transAxes, fontsize=15, fontweight="bold",
        color=COLOR_TEXT, va="top", ha="left",
    )

    yes_pct = yes_share * 100
    yes_color = COLOR_YES if yes_share >= 0.5 else COLOR_NO

    ax_gauge.text(
        0.0, 0.10, f"{yes_pct:.0f}%",
        transform=ax_gauge.transAxes, fontsize=46, fontweight="bold",
        color=yes_color, va="bottom", ha="left",
    )
    ax_gauge.text(
        0.22, 0.18, "YES probability",
        transform=ax_gauge.transAxes, fontsize=11, color=COLOR_MUTED,
        va="bottom", ha="left",
    )

    # Horizontal probability bar
    bar_bg = mpatches.FancyBboxPatch(
        (0.22, 0.05), 0.74, 0.08,
        boxstyle="round,pad=0,rounding_size=0.04",
        transform=ax_gauge.transAxes,
        facecolor=COLOR_PANEL, edgecolor="none",
    )
    ax_gauge.add_patch(bar_bg)
    bar_fill = mpatches.FancyBboxPatch(
        (0.22, 0.05), 0.74 * yes_share, 0.08,
        boxstyle="round,pad=0,rounding_size=0.04",
        transform=ax_gauge.transAxes,
        facecolor=yes_color, edgecolor="none",
    )
    ax_gauge.add_patch(bar_fill)

    # ---------- Mini timeline ----------
    if points and len(points) >= 2:
        times = [datetime.fromisoformat(p["t"]) for p in points]
        ys = [p["yes_share"] * 100 for p in points]
        ax_chart.set_facecolor(COLOR_BG)
        ax_chart.fill_between(times, 50, ys,
                              where=[v >= 50 for v in ys],
                              color=COLOR_YES, alpha=0.20, linewidth=0,
                              interpolate=True)
        ax_chart.fill_between(times, 50, ys,
                              where=[v < 50 for v in ys],
                              color=COLOR_NO, alpha=0.20, linewidth=0,
                              interpolate=True)
        ax_chart.plot(times, ys, color=yes_color, linewidth=2.2)
        ax_chart.axhline(50, color=COLOR_GRID, linewidth=0.8, linestyle="--", alpha=0.6)
        ax_chart.set_ylim(0, 100)
        ax_chart.tick_params(colors=COLOR_MUTED, labelsize=8)
        ax_chart.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        ax_chart.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=5))
        for spine in ("top", "right"):
            ax_chart.spines[spine].set_visible(False)
        for spine in ("left", "bottom"):
            ax_chart.spines[spine].set_color(COLOR_GRID)
        ax_chart.grid(True, axis="y", color=COLOR_GRID, linewidth=0.4, alpha=0.4)

        delta = (points[-1]["yes_share"] - points[0]["yes_share"]) * 100
        if abs(delta) >= 1:
            arrow = "↑" if delta > 0 else "↓"
            ax_chart.text(
                0.99, 0.93, f"YES {arrow} {abs(delta):.1f}% since open",
                transform=ax_chart.transAxes, fontsize=9, fontweight="bold",
                color=COLOR_YES if delta > 0 else COLOR_NO,
                va="top", ha="right",
            )
    else:
        ax_chart.set_facecolor(COLOR_BG)
        ax_chart.text(0.5, 0.5,
                      "No bets yet — be the first to set the odds.",
                      ha="center", va="center",
                      color=COLOR_MUTED, fontsize=11,
                      transform=ax_chart.transAxes)
        ax_chart.set_axis_off()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=COLOR_BG, bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)
    return buf.getvalue()
