"""赔率走势图渲染 · matplotlib → PNG bytes。

设计：
  - Apple HIG 风：浅灰背景、柔和填充、圆角 spine、SF 字体兜底；
  - 双区域填充：YES 蓝、NO 红，按 yes_share 在 50% 基线上下浮动；
  - 顶部紧迫感标语："Odds shifted Δx% in 1h" — 制造下注冲动。
"""
from __future__ import annotations

import io
from datetime import datetime
from typing import Sequence

import matplotlib
matplotlib.use("Agg")  # 无界面后端，必须在 pyplot 之前
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from core.money import micro_to_usdt


# Apple system colors
COLOR_YES = "#34c759"      # systemGreen
COLOR_NO  = "#ff3b30"      # systemRed
COLOR_BG  = "#f5f5f7"
COLOR_GRID = "#d2d2d7"
COLOR_TEXT = "#1d1d1f"


def render_pool_timeline(
    points: Sequence[dict],
    *,
    title: str = "",
    fee_bps: int = 500,
    width: float = 8.0,
    height: float = 3.6,
    dpi: int = 130,
) -> bytes:
    """
    points: [{"t": iso, "yes": micro, "no": micro, "yes_share": 0..1}, ...]
    返回 PNG bytes，可直接 send_photo。
    """
    if not points:
        return _placeholder("No bets yet — be the first.")

    times = [datetime.fromisoformat(p["t"]) for p in points]
    yes_share = [p["yes_share"] * 100 for p in points]
    no_share = [(1 - p["yes_share"]) * 100 for p in points]
    total_pool = [p["total"] / 1_000_000 for p in points]  # USDT

    fig, ax = plt.subplots(figsize=(width, height), dpi=dpi)
    fig.patch.set_facecolor(COLOR_BG)
    ax.set_facecolor(COLOR_BG)

    # 双区域填充：YES 在 50% 基线之上，NO 之下
    ax.fill_between(times, 50, [50 + (s - 50) for s in yes_share],
                    where=[s >= 50 for s in yes_share],
                    color=COLOR_YES, alpha=0.28, linewidth=0)
    ax.fill_between(times, 50, [50 + (s - 50) for s in yes_share],
                    where=[s < 50 for s in yes_share],
                    color=COLOR_NO, alpha=0.28, linewidth=0)
    ax.plot(times, yes_share, color=COLOR_YES, linewidth=2.0, label="YES probability")
    ax.axhline(50, color=COLOR_GRID, linewidth=0.8, linestyle="--", alpha=0.6)

    ax.set_ylim(0, 100)
    ax.set_ylabel("YES implied probability (%)", fontsize=9, color=COLOR_TEXT)
    ax.tick_params(colors=COLOR_TEXT, labelsize=8)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=6))

    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(COLOR_GRID)

    ax.grid(True, axis="y", color=COLOR_GRID, linewidth=0.5, alpha=0.5)

    # 顶部紧迫感标语
    if len(points) >= 2:
        delta = (points[-1]["yes_share"] - points[0]["yes_share"]) * 100
        if abs(delta) >= 1:
            arrow = "↑" if delta > 0 else "↓"
            color = COLOR_YES if delta > 0 else COLOR_NO
            ax.text(0.02, 0.95, f"YES {arrow} {abs(delta):.1f}% since open",
                    transform=ax.transAxes, fontsize=10, fontweight="bold",
                    color=color, va="top")

    # 右上角池子总额
    ax.text(0.98, 0.95, f"Pool: {total_pool[-1]:.2f} USDT",
            transform=ax.transAxes, fontsize=9, color=COLOR_TEXT,
            va="top", ha="right", alpha=0.7)

    if title:
        fig.suptitle(title[:80], fontsize=11, color=COLOR_TEXT, y=0.995, ha="left", x=0.05)

    fig.tight_layout(pad=1.2)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=COLOR_BG, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def _placeholder(text: str) -> bytes:
    fig, ax = plt.subplots(figsize=(8, 3.6), dpi=130)
    fig.patch.set_facecolor(COLOR_BG)
    ax.set_facecolor(COLOR_BG)
    ax.text(0.5, 0.5, text, ha="center", va="center",
            color="#86868b", fontsize=14, transform=ax.transAxes)
    ax.set_axis_off()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=COLOR_BG, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()
