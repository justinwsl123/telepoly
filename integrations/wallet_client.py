"""跨 Bot 钱包客户端 · 把这个文件原样拷到 KickAI / 其他 bot 项目里即用。

依赖：httpx（如果没有可改成 requests）。

用法：
    from wallet_client import TelePolyWallet
    wallet = TelePolyWallet(base_url="https://telepoly.up.railway.app",
                            api_key=os.environ["TELEPOLY_WALLET_API_KEY"])

    # 用户首次接入：
    wallet.ensure_user(tg_user_id=123456, username="alice")

    # 查余额（USDT × 10^6 micro）：
    bal = wallet.balance(tg_user_id=123456)

    # 扣款（带幂等键防重）：
    import uuid
    wallet.charge(tg_user_id=123456, amount_micro=15_000_000,
                  idempotency_key=f"kickai_paywall_{order_id}",
                  reason="kickai_paywall")
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx


class WalletApiError(Exception):
    pass


@dataclass
class TelePolyWallet:
    base_url: str
    api_key: str
    timeout: float = 8.0

    def _headers(self) -> dict:
        return {"X-Wallet-Api-Key": self.api_key, "Content-Type": "application/json"}

    def _post(self, path: str, body: dict) -> dict:
        url = f"{self.base_url.rstrip('/')}{path}"
        with httpx.Client(timeout=self.timeout) as c:
            r = c.post(url, json=body, headers=self._headers())
        if r.status_code >= 400:
            raise WalletApiError(f"{r.status_code}: {r.text}")
        return r.json()

    def _get(self, path: str) -> dict:
        url = f"{self.base_url.rstrip('/')}{path}"
        with httpx.Client(timeout=self.timeout) as c:
            r = c.get(url, headers=self._headers())
        if r.status_code >= 400:
            raise WalletApiError(f"{r.status_code}: {r.text}")
        return r.json()

    def balance(self, tg_user_id: int) -> int:
        """返回 micro USDT 余额。用户不存在时返回 0。"""
        data = self._get(f"/api/wallet/balance/{tg_user_id}")
        return int(data.get("balance_micro", 0))

    def ensure_user(self, *, tg_user_id: int, username: str | None = None,
                    first_name: str | None = None, lang: str = "en") -> dict:
        return self._post("/api/wallet/ensure_user", {
            "tg_user_id": tg_user_id, "username": username,
            "first_name": first_name, "lang": lang,
        })

    def charge(self, *, tg_user_id: int, amount_micro: int,
               idempotency_key: str, reason: str, note: str | None = None) -> dict:
        return self._post("/api/wallet/charge", {
            "tg_user_id": tg_user_id, "amount_micro": amount_micro,
            "idempotency_key": idempotency_key, "reason": reason, "note": note,
        })

    def credit(self, *, tg_user_id: int, amount_micro: int,
               idempotency_key: str, reason: str, note: str | None = None) -> dict:
        return self._post("/api/wallet/credit", {
            "tg_user_id": tg_user_id, "amount_micro": amount_micro,
            "idempotency_key": idempotency_key, "reason": reason, "note": note,
        })
