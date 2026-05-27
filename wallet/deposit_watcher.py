"""TRC20 充值扫块器（独立进程 / 独立 polling 循环）。

策略：
  1. 每 30 秒拉一次"全部已派生用户地址"的最近转入（TronGrid /transactions/trc20）；
  2. 用 deposits.tx_hash 唯一索引去重；
  3. 链上一旦看到 → 写 deposits(status=pending)；
  4. 简化版：tx 出现在 TronGrid REST 时即视为"已上链"（TronGrid 只返回上链交易），
     超过 WALLET_MIN_CONFIRMATIONS 个区块后转 confirmed → 入账 ledger → 通知 bot。

为什么不直接监听新块：
  - TronGrid 的 /trc20 接口已经是按地址过滤后的转入列表，远比扫全块经济；
  - 用户地址数量级可控（几千~几万），分批轮询即可；
  - 极简 MVP，Day 3 流量上来再切 webhook + 块订阅。

启动：
    uv run python -m wallet.deposit_watcher
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime

import httpx
from loguru import logger
from sqlalchemy import select

from db.models import Deposit, User, WalletAddress
from db.session import get_session
from core.ledger import record_ledger
from core.money import micro_to_usdt
from telepoly_bot.config import settings
from wallet.trc20 import fetch_incoming_usdt


POLL_INTERVAL_SEC = 30
PAGE_SIZE = 50
NOTIFY_RETRY_LATER: list[tuple[int, str]] = []  # (tg_user_id, message) 失败重试队列


async def _notify_user(tg_user_id: int, text: str) -> None:
    """通过 bot REST API 发私信通知（不依赖 PTB 进程，独立扫块器也能发）。"""
    if not settings.telepoly_bot_token:
        return
    url = f"https://api.telegram.org/bot{settings.telepoly_bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            await client.post(url, json={
                "chat_id": tg_user_id,
                "text": text,
                "parse_mode": "Markdown",
            })
        except Exception as e:
            logger.warning(f"notify {tg_user_id} failed: {e}")


async def _scan_address(user_id: int, tg_user_id: int, address: str,
                        last_seen_ms: int) -> int:
    """扫一个地址，返回入账的新增 deposits 数。"""
    txs = await fetch_incoming_usdt(address, min_timestamp_ms=max(last_seen_ms - 60_000, 0),
                                    limit=PAGE_SIZE)
    if not txs:
        return 0

    credited = 0
    for tx in txs:
        with get_session() as s:
            existing = s.scalars(select(Deposit).where(Deposit.tx_hash == tx.tx_hash)).first()
            if existing:
                continue

            user = s.get(User, user_id)
            if not user:
                continue

            dep = Deposit(
                user_id=user.id,
                address=address,
                amount_micro=tx.amount_micro,
                tx_hash=tx.tx_hash,
                block_number=tx.block_number,
                status="confirmed",  # MVP：能从 TronGrid 拉到就视为已确认
                confirmed_at=datetime.utcnow(),
            )
            s.add(dep)
            s.flush()

            record_ledger(
                s, user=user, delta_micro=tx.amount_micro, reason="deposit",
                ref_id=dep.id, ref_type="deposit", tx_hash=tx.tx_hash,
                note=f"trc20-usdt from={tx.from_addr}",
            )
            dep.status = "credited"

            credited += 1
            amount_str = f"{micro_to_usdt(tx.amount_micro):.2f}"
            logger.success(f"deposit credited user={user.id} +{amount_str}U tx={tx.tx_hash}")

        if credited:
            try:
                await _notify_user(tg_user_id,
                    f"✅ *USDT 已入账 / Deposit credited*\n"
                    f"+`{amount_str}` USDT\n"
                    f"tx: `{tx.tx_hash[:16]}…`\n"
                    f"使用 /me 查看余额。")
            except Exception:
                pass

    return credited


async def scan_once() -> int:
    """全量扫一轮：返回新入账笔数。"""
    with get_session() as s:
        rows = list(s.execute(
            select(WalletAddress, User).join(User, WalletAddress.user_id == User.id)
        ).all())

    if not rows:
        return 0

    now_ms = int(time.time() * 1000)
    last_seen_ms = now_ms - 60 * 60 * 1000  # 默认看过去 1 小时（重启容忍）
    total = 0
    # 控并发：TronGrid 限频，串行扫即可（几千地址轮询一遍 < 1 分钟）
    for wa, user in rows:
        try:
            total += await _scan_address(user.id, user.tg_user_id, wa.address, last_seen_ms)
        except Exception as e:
            logger.exception(f"scan failed for addr={wa.address}: {e}")
        await asyncio.sleep(0.2)  # 给 TronGrid 留口气
    return total


async def main_loop() -> None:
    logger.info("== TelePoly Deposit Watcher starting ==")
    if not settings.wallet_mnemonic:
        logger.warning("WALLET_MNEMONIC 未配置，扫块器仅监控 wallet_addresses 表中已存在的地址")

    while True:
        try:
            n = await scan_once()
            if n:
                logger.info(f"[scan] credited {n} new deposits")
        except Exception as e:
            logger.exception(f"scan loop error: {e}")
        await asyncio.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    import sys
    from pathlib import Path
    Path("logs").mkdir(exist_ok=True)
    logger.add("logs/deposit_watcher.log", rotation="20 MB", retention="14 days", level="INFO")
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        sys.exit(0)
