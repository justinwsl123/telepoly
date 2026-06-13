"""热钱包公开余额查询 · 透明度功能。

向用户公示：所有竞猜资金汇入同一个公开 TRC20 地址，任何人可在 Tronscan 验证。

依赖：
  WALLET_HOT_ADDRESS — 热钱包 TRC20 地址（与 wallet/trc20.py 共用）
  TRONGRID_API_KEY   — 可选，提升限流上限

使用 TronGrid REST API 查询 TRC20 USDT 余额：
  GET https://api.trongrid.io/v1/accounts/{address}
  响应中 data[0].trc20 是 [{contract_address: amount_str}, ...]
"""
from __future__ import annotations

import httpx
from loguru import logger

from telepoly_bot.config import settings

# 主网 USDT-TRC20 合约（与 wallet/trc20.py 同）
USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"


def get_hot_wallet_usdt_balance() -> dict:
    """
    查询热钱包的链上 USDT 余额。

    返回：
      {
        "address":       "TXxx...",
        "balance_usdt":  1234.56,         # float USDT
        "balance_micro": 1234560000,      # int micro
        "tronscan_url":  "https://tronscan.org/#/address/TXxx...",
        "ok":            True,
      }
    失败时 ok=False，balance_usdt=0。
    """
    address = settings.wallet_hot_address
    tronscan_url = f"https://tronscan.org/#/address/{address}" if address else ""

    base = {
        "address":       address or "",
        "balance_usdt":  0.0,
        "balance_micro": 0,
        "tronscan_url":  tronscan_url,
        "ok":            False,
    }

    if not address:
        logger.warning("wallet_public: WALLET_HOT_ADDRESS not set")
        return base

    headers = {"Accept": "application/json"}
    if settings.trongrid_api_key:
        headers["TRON-PRO-API-KEY"] = settings.trongrid_api_key

    try:
        with httpx.Client(timeout=10) as client:
            r = client.get(
                f"https://api.trongrid.io/v1/accounts/{address}",
                headers=headers,
            )
            r.raise_for_status()
            data = r.json().get("data", [])

        if not data:
            # 全新地址，余额为 0
            base["ok"] = True
            return base

        account = data[0]
        trc20_list = account.get("trc20", [])
        # trc20_list 是 [{contract_address: amount_str}, ...]
        balance_raw = 0
        for item in trc20_list:
            amt_str = item.get(USDT_CONTRACT)
            if amt_str is not None:
                balance_raw = int(amt_str)
                break

        base["balance_micro"] = balance_raw
        base["balance_usdt"]  = round(balance_raw / 1_000_000, 6)
        base["ok"] = True
        return base

    except Exception as e:
        logger.warning(f"wallet_public: TronGrid query failed: {e}")
        return base
