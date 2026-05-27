"""统一配置加载（pydantic-settings + .env）。"""
from __future__ import annotations

from functools import cached_property
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ---- Bot ----
    telepoly_bot_token: str = Field(default="", alias="TELEPOLY_BOT_TOKEN")
    telepoly_bot_username: str = Field(default="TelePoly_Bot", alias="TELEPOLY_BOT_USERNAME")
    bot_id: str = Field(default="main", alias="BOT_ID")  # 矩阵中每个实例自己的标识
    bot_lang: str = Field(default="", alias="BOT_LANG")  # 强制覆盖 default_lang，矩阵实例用
    owner_tg_ids: str = Field(default="", alias="OWNER_TG_IDS")
    announce_channel_id: str = Field(default="", alias="ANNOUNCE_CHANNEL_ID")

    # ---- 业务规则 ----
    min_bet_usdt: float = Field(default=1.0, alias="MIN_BET_USDT")
    max_bet_ratio: float = Field(default=0.30, alias="MAX_BET_RATIO")
    event_fee_bps: int = Field(default=500, alias="EVENT_FEE_BPS")
    default_lang: str = Field(default="en", alias="DEFAULT_LANG")

    # ---- DB ----
    database_url: str = Field(default="sqlite:///./data/telepoly.db", alias="DATABASE_URL")

    # ---- 钱包 ----
    wallet_mnemonic: str = Field(default="", alias="WALLET_MNEMONIC")
    trongrid_api_key: str = Field(default="", alias="TRONGRID_API_KEY")
    wallet_hot_address: str = Field(default="", alias="WALLET_HOT_ADDRESS")
    wallet_min_confirmations: int = Field(default=19, alias="WALLET_MIN_CONFIRMATIONS")
    wallet_auto_approve_limit_usdt: float = Field(default=200.0, alias="WALLET_AUTO_APPROVE_LIMIT_USDT")

    # ---- LLM ----
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_api_base: str = Field(default="https://aiberm.com/v1", alias="LLM_API_BASE")
    llm_model: str = Field(default="openai/gpt-5.5", alias="LLM_MODEL")

    # ---- 三方 ----
    crypto_pay_token: str = Field(default="", alias="CRYPTO_PAY_TOKEN")
    telegrowth_db_url: str = Field(default="", alias="TELEGROWTH_DB_URL")
    telepoly_bot_matrix: str = Field(default="", alias="TELEPOLY_BOT_MATRIX")

    @cached_property
    def owner_ids(self) -> set[int]:
        return {int(x.strip()) for x in self.owner_tg_ids.split(",") if x.strip()}

    @property
    def min_bet_micro(self) -> int:
        return int(self.min_bet_usdt * 1_000_000)


settings = Settings()
