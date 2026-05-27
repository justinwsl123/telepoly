"""金额工具：USDT ↔ micro (×10^6) 整数。

铁律：DB / 业务计算一律用 micro 整数；只在展示给用户时才转 Decimal。
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

MICRO = 1_000_000


def usdt_to_micro(amount: float | str | Decimal) -> int:
    d = Decimal(str(amount)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    return int(d * MICRO)


def micro_to_usdt(micro: int) -> Decimal:
    return (Decimal(micro) / MICRO).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def fmt_usdt(micro: int, suffix: str = " USDT") -> str:
    return f"{micro_to_usdt(micro):.2f}{suffix}"
