"""HD 钱包派生 · TRC20 (TRON, BIP44 coin_type=195)。

铁律：
  - WALLET_MNEMONIC 仅来自 Railway secrets / 本地 .env，永不入 Git / 日志；
  - 派生路径 m/44'/195'/0'/0/0 = 平台主热钱包（出款）；
  - m/44'/195'/0'/0/N (N>=1) = 第 N 号用户专属充值地址；
  - derive_index 与 user_id 的映射存 wallet_addresses 表，用 user.id 自增分配。

API：
  - get_or_create_user_address(session, user) → WalletAddress
  - hot_wallet_address() → 平台主热钱包地址
  - hot_wallet_private_key() → 主热钱包出款私钥（仅出款进程使用）
"""
from __future__ import annotations

import os
from functools import lru_cache

from bip_utils import (
    Bip39MnemonicValidator, Bip39SeedGenerator, Bip44, Bip44Coins, Bip44Changes,
)
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.models import User, WalletAddress
from telepoly_bot.config import settings


HOT_WALLET_INDEX = 0


def _validate_mnemonic(mnemonic: str) -> None:
    if not Bip39MnemonicValidator().IsValid(mnemonic):
        raise ValueError("WALLET_MNEMONIC 不是有效的 BIP39 助记词")


@lru_cache(maxsize=1)
def _master() -> Bip44:
    """从 .env 助记词构造 BIP44 master（缓存）。"""
    mnemonic = settings.wallet_mnemonic.strip()
    if not mnemonic:
        raise RuntimeError("WALLET_MNEMONIC 未配置")
    _validate_mnemonic(mnemonic)
    seed = Bip39SeedGenerator(mnemonic).Generate()
    bip = Bip44.FromSeed(seed, Bip44Coins.TRON).Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT)
    return bip


def derive_address(index: int) -> tuple[str, str]:
    """返回 (address, private_key_hex)。"""
    bip = _master().AddressIndex(index)
    address = bip.PublicKey().ToAddress()  # T 开头的 base58 TRON 地址
    priv_hex = bip.PrivateKey().Raw().ToHex()
    return address, priv_hex


def hot_wallet_address() -> str:
    """主热钱包（出款来源）。"""
    if settings.wallet_hot_address:
        return settings.wallet_hot_address  # 允许显式覆盖（多签 / 冷热分离）
    addr, _ = derive_address(HOT_WALLET_INDEX)
    return addr


def hot_wallet_private_key() -> str:
    """⚠️ 仅出款进程调用；写日志时绝不能打印此值。"""
    _, priv = derive_address(HOT_WALLET_INDEX)
    return priv


def get_or_create_user_address(session: Session, user: User) -> WalletAddress:
    """幂等获取用户专属充值地址；不存在则派生新 derive_index。"""
    existing = session.scalars(
        select(WalletAddress).where(WalletAddress.user_id == user.id)
    ).first()
    if existing:
        return existing

    # 取当前最大 derive_index + 1（>=1，0 留给热钱包）
    max_idx = session.scalar(select(func.max(WalletAddress.derive_index))) or HOT_WALLET_INDEX
    new_idx = max(int(max_idx) + 1, 1)
    address, _ = derive_address(new_idx)

    wa = WalletAddress(user_id=user.id, derive_index=new_idx, address=address)
    session.add(wa)
    session.flush()
    logger.info(f"derived deposit address for user={user.id} idx={new_idx} addr={address}")
    return wa
