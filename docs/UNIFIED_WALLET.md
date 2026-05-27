# 跨 Bot 共享余额 · 集成手册

> **TL;DR**：TelePoly 是钱包权威源，KickAI 等其他 Bot 通过 HTTPS API 共用同一份用户余额。
> 用户在任何一个 Bot 里充值，所有矩阵 Bot 都能直接花。

---

## 1. 架构

```
                ┌───────────────────────────┐
                │   TelePoly admin_web      │
                │   (FastAPI · Railway)     │
                │                           │
   POST /api/wallet/ensure_user            │
   POST /api/wallet/charge   ─ idempotent  │
   POST /api/wallet/credit   ─ idempotent  │
   GET  /api/wallet/balance/<tg_id>        │
                │                           │
                │  → core.ledger 双式记账   │
                │  → users.balance_micro    │
                └───────────────────────────┘
                          ▲
              X-Wallet-Api-Key (HTTP header)
                          │
       ┌──────────────────┼──────────────────┐
       │                  │                  │
   ┌───┴───┐          ┌───┴───┐          ┌───┴───┐
   │KickAI │          │TelePoly│          │未来 Bot│
   │ Bot   │          │ Bot   │          │       │
   └───────┘          └───────┘          └───────┘
```

- **TRC20 入金**：仍只在 TelePoly 侧（HD 派生地址 + deposit_watcher），其他 bot 不操心；
- **下注 / 消费 / 退款**：每个 bot 直接调 charge / credit；
- **幂等性**：每个写操作必须带 `idempotency_key`（建议 UUID 或 `<bot_id>_<order_id>` 格式），
  服务端会拒绝同 key 二次写入，自动返回首次结果。

## 2. 服务端配置（TelePoly 侧）

`.env` / Railway secrets：

```
WALLET_API_KEY=<random 32 char token>     # 高熵随机串，例如 openssl rand -hex 32
```

`MINIAPP_BASE_URL` 同时也是钱包 API 的 base URL，例如 `https://telepoly.up.railway.app`。

## 3. 客户端集成（KickAI 侧）

### 3.1 拷文件

把 `integrations/wallet_client.py` 直接复制到 KickAI 项目，例如 `kickai_bot/wallet_client.py`。
唯一依赖：`httpx`（KickAI 已经在用）。

### 3.2 配置环境变量

KickAI `.env`：

```
TELEPOLY_WALLET_BASE=https://telepoly.up.railway.app
TELEPOLY_WALLET_API_KEY=<同上 WALLET_API_KEY>
```

### 3.3 KickAI 关键替换点

#### 用户开户

KickAI 用户 `/start` 第一次进入时：

```python
from wallet_client import TelePolyWallet
wallet = TelePolyWallet(
    base_url=os.environ["TELEPOLY_WALLET_BASE"],
    api_key=os.environ["TELEPOLY_WALLET_API_KEY"],
)

wallet.ensure_user(
    tg_user_id=update.effective_user.id,
    username=update.effective_user.username,
    first_name=update.effective_user.first_name,
    lang=(update.effective_user.language_code or "en")[:2],
)
```

#### 查余额（替代 KickAI 自己的 user.balance 字段）

```python
balance_micro = wallet.balance(tg_user_id=user.tg_id)
balance_usdt = balance_micro / 1_000_000
```

#### Paywall 扣款

KickAI 卖订阅 / 单场 SKU 时：

```python
import uuid
order_id = str(uuid.uuid4())
try:
    res = wallet.charge(
        tg_user_id=user.tg_id,
        amount_micro=int(price_usdt * 1_000_000),
        idempotency_key=f"kickai_paywall_{order_id}",
        reason="kickai_paywall",
        note=f"sku={sku_code}",
    )
    # 充值成功 → 给用户开通服务
    grant_subscription(user.tg_id, sku_code)
except WalletApiError as e:
    if "402" in str(e):
        await update.message.reply_text("余额不足，先去 @TelePoly_Bot /deposit 充值。")
    else:
        await update.message.reply_text(f"支付失败：{e}")
```

#### 退款

```python
wallet.credit(
    tg_user_id=user.tg_id,
    amount_micro=refund_micro,
    idempotency_key=f"kickai_refund_{original_order_id}",
    reason="kickai_refund",
    note="user requested refund within 7d",
)
```

#### 推广奖励 / 福利金

```python
wallet.credit(
    tg_user_id=user.tg_id,
    amount_micro=2_000_000,  # 2 USDT
    idempotency_key=f"kickai_signup_bonus_{user.tg_id}",
    reason="kickai_signup_bonus",
)
```

## 4. 错误码

| HTTP | 含义 | 客户端处理 |
|---|---|---|
| 401 | api key 不对 | 检查 `WALLET_API_KEY` 一致性 |
| 402 | 余额不足（仅 charge） | 提示用户去 TelePoly 充值 |
| 404 | 用户不存在 | 先调 ensure_user 再重试 |
| 503 | 服务端未配 WALLET_API_KEY | 联系运营 |

## 5. 风控建议

- KickAI 侧不该有"硬编码大额扣款"，所有金额必须从 SKU 价目表读取，不允许用户在请求里指定；
- 幂等键 = `<bot_id>_<场景>_<业务id>`，永远可重放；
- 客户端调用建议加 1-2 次重试 + 指数退避（httpx Transport 自带）；
- TelePoly 侧的 ledger 已经审计每笔，不需要 KickAI 重复记账。

## 6. 渐进式上线建议

1. **Phase 1（共存）**：KickAI 保留旧的本地 balance 字段，新用户走 TelePoly 钱包；旧用户继续旧逻辑。
2. **Phase 2（迁移）**：写一次性脚本，把 KickAI 旧用户余额 `credit` 到 TelePoly 钱包，KickAI 清零旧字段。
3. **Phase 3（全量）**：KickAI 删掉本地 balance 表，所有读写走 API。

## 7. 监控

- Railway logs：`grep "external_charge\|external_credit" logs/telepoly.log`
- admin_web `/users` 页面看 ledger 历史
- 每周复盘：`SELECT reason, SUM(delta_micro) FROM ledger GROUP BY reason` 验证各场景金流符合预期
