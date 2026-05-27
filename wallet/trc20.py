"""TronGrid + tronpy 薄封装。

设计：
  - 扫块用 TronGrid REST（公开 + 配 API key 限流更宽）：拉某地址近期 trc20 转入流水；
  - 出款用 tronpy SDK：拼 trigger_smart_contract 转 USDT。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import httpx
from loguru import logger

from telepoly_bot.config import settings


# 主网 USDT-TRC20 合约
USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
USDT_DECIMALS = 6  # 1 USDT = 1_000_000，对应我们 micro 单位


@dataclass
class IncomingTRC20:
    tx_hash: str
    from_addr: str
    to_addr: str
    amount_micro: int
    block_number: int
    timestamp_ms: int


def _trongrid_headers() -> dict:
    h = {"Accept": "application/json"}
    if settings.trongrid_api_key:
        h["TRON-PRO-API-KEY"] = settings.trongrid_api_key
    return h


async def fetch_incoming_usdt(address: str, *, min_timestamp_ms: int | None = None,
                              limit: int = 50) -> list[IncomingTRC20]:
    """
    拉一个地址最近的 USDT-TRC20 转入。
    TronGrid endpoint:
      GET /v1/accounts/{address}/transactions/trc20
        ?contract_address=USDT_CONTRACT&only_to=true&limit=N&min_timestamp=ms
    """
    url = f"https://api.trongrid.io/v1/accounts/{address}/transactions/trc20"
    params = {
        "contract_address": USDT_CONTRACT,
        "only_to": "true",
        "limit": limit,
    }
    if min_timestamp_ms:
        params["min_timestamp"] = min_timestamp_ms

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(url, params=params, headers=_trongrid_headers())
            r.raise_for_status()
            data = r.json().get("data", [])
        except Exception as e:
            logger.warning(f"trongrid fetch failed for {address}: {e}")
            return []

    out: list[IncomingTRC20] = []
    for tx in data:
        if tx.get("type") != "Transfer":
            continue
        token = tx.get("token_info") or {}
        if token.get("address") != USDT_CONTRACT:
            continue
        try:
            amt = int(tx["value"])  # 已经是 micro（USDT 6 decimals）
        except (KeyError, ValueError):
            continue
        out.append(IncomingTRC20(
            tx_hash=tx["transaction_id"],
            from_addr=tx.get("from"),
            to_addr=tx.get("to"),
            amount_micro=amt,
            block_number=tx.get("block_timestamp", 0),  # TronGrid 用 timestamp 替代 block_number
            timestamp_ms=tx.get("block_timestamp", 0),
        ))
    return out


def send_trc20_usdt(to_address: str, amount_micro: int, private_key_hex: str) -> str:
    """
    用 tronpy 发 USDT 转账，返回 tx_hash。
    ⚠️ 私钥参数仅在出款进程内传，外部接口禁止暴露。
    """
    from tronpy import Tron
    from tronpy.keys import PrivateKey

    client = Tron(network="mainnet")
    priv = PrivateKey(bytes.fromhex(private_key_hex))
    sender = priv.public_key.to_base58check_address()

    contract = client.get_contract(USDT_CONTRACT)
    txn = (
        contract.functions.transfer(to_address, amount_micro)
        .with_owner(sender)
        .fee_limit(15_000_000)  # 15 TRX 上限，正常一笔 ≈ 13 TRX
        .build()
        .sign(priv)
    )
    receipt = txn.broadcast().wait()
    tx_hash = receipt.get("id") or txn.txid
    logger.info(f"USDT sent to={to_address} amount={amount_micro} tx={tx_hash}")
    return tx_hash
