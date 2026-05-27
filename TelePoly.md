# TelePoly · Telegram 上的每日预测市场

> "Polymarket 的玩法 × Telegram 的渠道 × 我们的合规牌照"
> 每天一道题，事件结束之前都能押注，全 USDT 结算。
>
> 启动：2026-05-27（今天）
> 目标：D-Day 2026-06-11 世界杯开赛前上线，今晚 MVP 跑通即开运营。

---

## 0. TL;DR（30 秒读懂）

- **产品**：一个 Telegram Bot（@TelePoly_xxx_Bot）每天 09:00 在频道发 1 道预测题，用户在 bot 里押 YES / NO，到事件截止时间封盘，运营手动结算，赢家按彩池比例分钱（平台抽 5%）。
- **机制**：彩池对赌（Parimutuel） — 我们 0 风险，输家钱进赢家口袋，我们只抽手续费。
- **支付**：纯 USDT-TRC20 自托管钱包 + @Wallet 兜底。**不走法币**。
- **获客**：复用 `TeleGrowth` 的 affiliate 表 / 多 Channel 矩阵 / Multi-Agent 框架。
- **多 Bot 矩阵**：单代码 + 多 BOT_TOKEN 多实例（参考 TeleSportAI 的 KickAI / Arena 双 Bot 模式）。
- **部署**：Railway → [`justinwsl123/telepoly`](https://github.com/justinwsl123/telepoly)。

---

## 1. 核心玩法规则（产品 PRD）

### 1.1 一个事件的完整生命周期

```
[draft] 运营出题
   ↓ /admin publish
[open]  开盘 → bot 推送到频道 + 主 bot 私聊订阅用户
   ↓ 用户在事件截止时间前可不限次数下注
[locked] 截止时间到 → bot 自动封盘（不再接受新单）
   ↓ 等官方/oracle 结果
[settled] 运营 /admin settle <event_id> <yes|no|void> + 证据链接
   ↓ 自动按比例分钱 + 入账到用户余额 + 频道公告
[archived] 归档进历史
```

### 1.2 彩池对赌（Parimutuel）数学规则

设 `P_yes` = YES 池总额，`P_no` = NO 池总额，`F` = 平台手续费率（默认 **5%**）。

事件结算后（假设 YES 赢）：
```
total_pool   = P_yes + P_no
fee          = total_pool × F
payout_pool  = total_pool − fee = P_yes + (P_no × (1 − F))   # 数学等价
                                                              # 但实现上一律按总池抽

每个 YES 方用户的回报：
  user_payout = payout_pool × (user_bet / P_yes)

净收益：
  user_pnl    = user_payout − user_bet
```

**实时赔率（用户下注前 bot 显示的"如果现在结算"预估）**：

```
implied_yes_odds = (P_yes + P_no) / P_yes          # 比如 2.5x = 押 1U 赢 1.5U
implied_no_odds  = (P_yes + P_no) / P_no
displayed_yes   = implied_yes_odds × (1 − F)       # 扣手续费后展示给用户
displayed_no    = implied_no_odds  × (1 − F)
```

### 1.3 边界规则（写死在代码里，不靠运营记忆）

| 规则 | 默认值 | 备注 |
|---|---|---|
| 平台手续费 | 5% | `EVENT_FEE_BPS=500` 可单事件覆盖 |
| 最低下注 | 1.00 USDT | |
| 单人单事件最大下注 | ≤ 总池的 30% | 防鲸鱼操盘；超出强制拒绝 |
| 一方无人下注 | 自动 void → 全额退款 | 比如全押 YES，没人押 NO |
| 事件被取消/无法判定 | `/admin settle <id> void` → 全额退款 | |
| 已下注不可撤单 | — | 简化 MVP；二期可以加"反向对冲" |
| 币种 | USDT (TRC20)，最小单位 0.01 | 内部全部用整数微分 (×1,000,000) 存 |
| 支持多选 | ❌ MVP 只做二元 YES/NO | 多选放二期 |

### 1.4 用户旅程

```
新用户
  /start →  欢迎页 + 今日事件卡片 + [入金]按钮
        →  生成专属 TRC20 充值地址（HD wallet 派生）
        →  扫块脚本 5 min 内入账 → bot 主动通知

下注
  点 [押 YES 1.5x] → 弹输入金额 → 二次确认 → 扣余额 → 进 YES 池
  →  bot 推送 receipt（份额 / 当前赔率 / 预估回报）

封盘后
  /me 看仓位 + 等待结算
  
结算
  bot 主动推送：事件结果 / 你押对/错 / 入账 X USDT / 当前余额

提现
  /withdraw → 输入金额 + 链上地址 → 二次确认 → 自动出款（>$200 走人工审核队列）
```

---

## 2. 技术架构

### 2.1 技术栈（与 TeleSportAI 对齐，AI 写起来最快）

```
语言        Python 3.11+
Bot 框架    python-telegram-bot 21.x
LLM 网关    Aiberm（出题辅助、文案生成；可选）
DB          SQLite（启动期）→ PostgreSQL（>10k 用户后）
ORM         SQLAlchemy 2.x
任务调度    APScheduler（封盘定时、扫块、每日推送）
钱包        tronpy（TRC20-USDT 链上交互）
HD 派生     bip-utils（一个种子派生 N 个用户充值地址）
日志        loguru
环境管理    uv
部署        Railway（NIXPACKS） + 持久化 Volume
```

### 2.2 模块切分

```
TelePoly/
├── README.md                        # 项目导航（指向本文件）
├── TelePoly.md                      # 本文件（PRD + 技术 + 上线手册）
├── pyproject.toml
├── uv.lock
├── .env.example
├── .gitignore
├── nixpacks.toml
├── railway.json
├── start.sh                         # Railway 启动脚本（多进程 supervisor）
│
├── telepoly_bot/                    # 主 Bot（对外）
│   ├── __init__.py
│   ├── main.py                      # 入口：bot polling
│   ├── config.py                    # pydantic-settings 加载 .env
│   ├── handlers/
│   │   ├── start.py                 # /start 欢迎 + 今日事件
│   │   ├── event.py                 # 查看事件 / 押注交互
│   │   ├── wallet.py                # 充值地址 / 余额 / /withdraw
│   │   ├── me.py                    # 个人仓位 / 历史
│   │   ├── admin.py                 # /admin publish / lock / settle
│   │   └── referral.py              # /invite 拿专属 link（接 TeleGrowth）
│   ├── keyboards.py                 # InlineKeyboardMarkup 集中管理
│   ├── i18n/                        # 中英双语（运营 channel 中文，主 bot 英文为主）
│   └── texts.py                     # 文案模板
│
├── core/                            # 业务核心（与 bot UI 解耦）
│   ├── events.py                    # 事件 CRUD + 状态机
│   ├── betting.py                   # 下注 / 实时赔率 / 边界检查
│   ├── settlement.py                # 结算引擎（核心数学）
│   ├── ledger.py                    # 双式记账（每笔钱有借贷凭证）
│   └── policies.py                  # 风控（最大单注 / 反洗钱 / 黑名单）
│
├── wallet/                          # USDT 钱包模块
│   ├── hd.py                        # bip-utils HD 派生用户地址
│   ├── deposit_watcher.py           # 扫块入账（独立进程）
│   ├── withdraw.py                  # 出款（手动审核 + 链上发交易）
│   └── trc20.py                     # tronpy 封装
│
├── db/
│   ├── __init__.py
│   ├── models.py                    # SQLAlchemy ORM
│   ├── session.py
│   └── migrations/                  # alembic 或自写 .py 脚本
│
├── scheduler/
│   ├── jobs.py                      # APScheduler 任务定义
│   │   - 每分钟：检查到点封盘
│   │   - 每 30s：扫 TRC20 块
│   │   - 每天 09:00：自动推送当日新事件到频道
│   └── runner.py
│
├── integrations/
│   ├── telegrowth.py                # 共享 affiliate / contacts / 漏斗回流
│   ├── aiberm.py                    # LLM 出题文案辅助（可选）
│   └── wallet_bot.py                # @Wallet（CryptoBot）兜底通道
│
├── admin_web/                       # 运营后台（FastAPI + Jinja2，复用 TeleGrowth 的风格）
│   ├── main.py                      # 8080 端口
│   ├── routes/
│   │   ├── events.py                # 出题 / 发题 / 封盘 / 结算
│   │   ├── users.py                 # 余额查 / 黑名单
│   │   ├── treasury.py              # 平台钱包总览 / 提现审核
│   │   └── matrix.py                # 多 Bot 矩阵管理（每个 Bot 一行）
│   └── templates/
│
├── data/                            # SQLite 文件（持久卷，不进 Git）
└── logs/
```

### 2.3 数据库 Schema（核心表）

```sql
-- 用户
CREATE TABLE users (
  id INTEGER PRIMARY KEY,
  tg_user_id BIGINT UNIQUE NOT NULL,
  bot_id TEXT NOT NULL,                -- 矩阵中是哪个 bot 创建的
  source_channel TEXT,                 -- 来自哪个 Channel（接 TeleGrowth）
  affiliate_code TEXT,                 -- 推荐码（接 TeleGrowth）
  username TEXT,
  lang TEXT DEFAULT 'en',
  created_at TIMESTAMP,
  status TEXT DEFAULT 'active'         -- active / banned
);

-- 事件
CREATE TABLE events (
  id INTEGER PRIMARY KEY,
  bot_id TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT,
  yes_label TEXT DEFAULT 'YES',
  no_label TEXT DEFAULT 'NO',
  cover_url TEXT,
  open_at TIMESTAMP,                   -- 开盘时间（默认推送时刻）
  close_at TIMESTAMP NOT NULL,         -- 截止时间（自动封盘）
  state TEXT NOT NULL,                 -- draft / open / locked / settled / void
  outcome TEXT,                        -- yes / no / void（结算后填）
  evidence_url TEXT,                   -- 结算依据公开链接
  fee_bps INTEGER DEFAULT 500,         -- 5% = 500 bps
  pool_yes_micro BIGINT DEFAULT 0,     -- 整数微分 (×1,000,000)
  pool_no_micro  BIGINT DEFAULT 0,
  created_by INTEGER,                  -- 运营 user_id
  created_at TIMESTAMP,
  settled_at TIMESTAMP
);

-- 下注流水（不可改）
CREATE TABLE bets (
  id INTEGER PRIMARY KEY,
  event_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  side TEXT NOT NULL,                  -- yes / no
  amount_micro BIGINT NOT NULL,
  odds_at_bet REAL,                    -- 下注瞬间显示的赔率（用于 receipt 展示）
  payout_micro BIGINT DEFAULT 0,       -- 结算后填
  status TEXT DEFAULT 'placed',        -- placed / won / lost / refunded
  created_at TIMESTAMP
);

-- 余额账本（双式记账：每笔钱必有 from / to）
CREATE TABLE ledger (
  id INTEGER PRIMARY KEY,
  user_id INTEGER,                     -- NULL 表示平台钱包
  delta_micro BIGINT NOT NULL,         -- 正进负出
  reason TEXT NOT NULL,                -- deposit / bet_place / bet_payout / bet_refund / withdraw / fee
  ref_id INTEGER,                      -- 关联 bet_id / deposit_id / withdraw_id
  tx_hash TEXT,                        -- 链上交易哈希（如有）
  balance_after_micro BIGINT,          -- 快照（便于审计）
  created_at TIMESTAMP
);

-- 充值
CREATE TABLE deposits (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  address TEXT NOT NULL,               -- 用户专属 TRC20 地址
  amount_micro BIGINT NOT NULL,
  tx_hash TEXT UNIQUE NOT NULL,
  block_number BIGINT,
  confirmed_at TIMESTAMP,
  status TEXT                          -- pending / confirmed / credited
);

-- 提现
CREATE TABLE withdrawals (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  to_address TEXT NOT NULL,
  amount_micro BIGINT NOT NULL,
  fee_micro BIGINT DEFAULT 1000000,    -- 1 USDT 网络费（可调）
  status TEXT,                         -- pending / approved / rejected / sent / confirmed
  tx_hash TEXT,
  approved_by INTEGER,                 -- 运营审核人
  created_at TIMESTAMP,
  sent_at TIMESTAMP
);

-- HD 钱包派生地址池
CREATE TABLE wallet_addresses (
  id INTEGER PRIMARY KEY,
  user_id INTEGER UNIQUE NOT NULL,
  derive_index INTEGER UNIQUE NOT NULL,
  address TEXT UNIQUE NOT NULL,
  created_at TIMESTAMP
);

-- 事件历史指标（封盘瞬间快照）
CREATE TABLE event_snapshots (
  event_id INTEGER PRIMARY KEY,
  total_bets INTEGER,
  total_users INTEGER,
  pool_yes_micro BIGINT,
  pool_no_micro BIGINT,
  fee_micro BIGINT,
  snapshot_at TIMESTAMP
);
```

> **关键点**：所有金额一律用 `BIGINT micro`（USDT × 10^6）存储，禁止用 float。结算引擎用 `Decimal` 计算后再转回。

---

## 3. USDT 支付通道

### 3.1 TRC20 自托管（主通道）

**为什么 TRC20**：手续费几乎为 0（约 1 USDT/笔）、确认快（3 秒一块）、TG 用户最普及。

**HD 钱包架构**：
```
平台主钱包种子（BIP39 Mnemonic，存 .env 加密）
  ├── m/44'/195'/0'/0/0  → 主热钱包（出款）
  ├── m/44'/195'/0'/0/1  → 用户 1 充值地址
  ├── m/44'/195'/0'/0/2  → 用户 2 充值地址
  └── ...
```

**入账流程**：
1. `wallet.deposit_watcher` 进程每 30s 扫一次 TronGrid API（拿全部派生地址的最新 TRC20-USDT 交易）；
2. 命中 → 写 `deposits`（status=pending）→ 等 19 个块确认 → 转 confirmed；
3. confirmed 后写 `ledger` 入账 → bot 主动私信通知用户。

**出款流程**：
1. 用户 `/withdraw` → 创建 `withdrawals` (status=pending)；
2. ≤ $200 自动批准；> $200 进运营 web 审核队列；
3. 批准 → 从主热钱包发 TRC20 转账 → 拿到 tx_hash → 状态推 sent → 等链上确认。

**安全红线**：
- 主钱包热钱包余额永远 ≤ 总用户余额 × 30%；其余在冷钱包（多签）。
- 助记词只走 `.env` + Railway secrets，**永远不入 Git / 日志 / 截图**。
- 出款脚本独立进程，bot 进程拿不到出款私钥（最小权限）。

### 3.2 @Wallet (CryptoBot) 兜底（次通道）

对"不会装钱包"的小白用户，提供一键调起 @Wallet 内付款。
集成方式：CryptoBot API（[crypto-pay-api](https://help.crypt.bot/crypto-pay-api)） — 平台抽 0%，比 NOWPayments 便宜，唯一限制是用户必须用 Telegram 内置钱包。

UX：`/deposit` → 出现两个按钮 [TRC20 自有钱包] / [用 @Wallet 一键付款]。

---

## 4. 多 Bot 矩阵 & TeleGrowth 联动

### 4.1 单代码 + 多实例（参考 TeleSportAI 的 KickAI/Arena）

`start.sh` 里按 `BOT_TOKEN_LIST` 拉起多个 polling 进程：
```bash
TELEPOLY_BOTS="
  main:7xxx:TelePoly_Bot:en
  cn:8xxx:TelePoly_CN_Bot:zh
  africa:9xxx:TelePoly_Africa_Bot:en
"
```
每个实例共享同一份 DB / 钱包 / core 业务逻辑，仅 `bot_id`、文案模板、Channel 不同。
**好处**：写一份代码，铺 N 个 Channel；运营端在 admin_web 一个表格里看所有 bot 数据。

### 4.2 与 TeleGrowth 联动

- **affiliate 共享**：`/invite` 生成的 deep link 带 `?start=ref_<code>` → 写入 TeleGrowth 同库的 `affiliates` 表 → 一个推广员同时给 KickAI / TelePoly / 未来其他产品计佣（40% 一级 + 8% 二级，与现有规则一致）。
- **contacts 触达**：TeleGrowth 的 10 万 contacts 池 → TelePoly 上线后用同一套 Multi-Agent 框架做精准触达（Soul/Safety/Skill 三件套）。
- **漏斗回流**：TelePoly 的 `placed_first_bet` / `first_deposit` / `first_payout` 事件 → TeleGrowth `analytics` 收口 → 北极星 Dashboard 统一看。
- **Multi-Agent 接管运营**：未来出题、文案、客服可以让 TeleGrowth 现有的 7 个 Agent 之一专门负责 TelePoly。

---

## 5. Day-1（今天）开发 Checklist

> 目标：今晚跑通"开题 → 用户押注 → 封盘 → 结算"主流程；提现可放明天。

```
🔥 必须今天完成（上线最小集合）
├── [P0] uv 项目初始化 + 依赖（pyproject.toml）
├── [P0] .env.example / .gitignore / README.md
├── [P0] db/models.py 全部表 + 初始化脚本
├── [P0] core/events.py（CRUD + 状态机）
├── [P0] core/betting.py（下注 + 实时赔率 + 边界检查）
├── [P0] core/settlement.py（分钱核心，单测覆盖）
├── [P0] core/ledger.py（记账）
├── [P0] handlers/start.py + event.py + me.py + admin.py
├── [P0] keyboards.py（YES/NO 内联键盘）
├── [P0] scheduler：到点封盘 + 每天 09:00 推今日事件
├── [P0] handlers/wallet.py：展示充值地址（先人工对账，扫块明天接）
├── [P0] start.sh + nixpacks.toml + railway.json（部署 Railway）
└── [P0] 推到 https://github.com/justinwsl123/telepoly

⏰ 明天接（非阻塞上线运营）
├── [P1] wallet/deposit_watcher.py（TRC20 自动扫块入账）
├── [P1] wallet/withdraw.py（人工审核出款队列）
├── [P1] @Wallet 兜底通道
├── [P1] admin_web/（运营后台）
├── [P1] integrations/telegrowth.py（affiliate 接通）
└── [P2] AI 出题辅助 / 多语言 / 多 Bot 拉起
```

### 5.1 关于"今天上线运营"

只要 P0 跑通：
1. 你（运营）今天就出第一个事件 → /admin publish 推送到 1 个种子 Channel；
2. 用户充值先**人工对账**（用户在 bot 内点[我已转账] → 提交 tx_hash → 你在 admin_web 一键入账）；这能撑过头 100 个用户；
3. 24 小时内把 deposit_watcher 跑起来 → 切自动入账；
4. 边运营边接 TeleGrowth。

---

## 6. 部署到 Railway

### 6.1 仓库

```
git init
git remote add origin https://github.com/justinwsl123/telepoly.git
git push -u origin main
```

### 6.2 Railway 配置

`railway.json`：
```json
{
  "$schema": "https://railway.app/railway.schema.json",
  "build": { "builder": "NIXPACKS" },
  "deploy": {
    "startCommand": "bash start.sh",
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 10
  }
}
```

`nixpacks.toml`（参考 TeleSportAI）：
```toml
[phases.setup]
nixPkgs = ["python311", "uv"]

[phases.install]
cmds = ["uv sync --frozen --no-dev"]

[start]
cmd = "bash start.sh"
```

`start.sh`（多进程 supervisor）：
```bash
#!/usr/bin/env bash
set -e
DATA_DIR=/app/data && mkdir -p "$DATA_DIR"
VENV_PY=/opt/venv/bin/python

# DB migration
$VENV_PY -m db.migrate || true

# Bot polling
$VENV_PY -m telepoly_bot.main &
BOT_PID=$!

# Deposit watcher（明天接）
# $VENV_PY -m wallet.deposit_watcher &

# Admin Web（明天接）
# $VENV_PY -m uvicorn admin_web.main:app --host 0.0.0.0 --port "${PORT:-8080}" &

trap "kill $BOT_PID 2>/dev/null" SIGTERM SIGINT
wait -n
```

### 6.3 Railway 必填环境变量

| Key | 说明 |
|---|---|
| `TELEPOLY_BOT_TOKEN` | 主 Bot Token（@BotFather 创建） |
| `TELEPOLY_BOT_USERNAME` | 主 Bot 用户名（不带 @） |
| `OWNER_TG_IDS` | 运营 TG ID（逗号分隔，用来 /admin） |
| `ANNOUNCE_CHANNEL_ID` | 自动推送的频道 ID（-100xxx） |
| `WALLET_MNEMONIC` | TRC20 HD 助记词（**Railway secret，永不入 Git**） |
| `TRONGRID_API_KEY` | TronGrid API Key |
| `LLM_API_KEY` / `LLM_API_BASE` / `LLM_MODEL` | Aiberm 网关（可选，用于出题辅助） |
| `TELEGROWTH_DB_URL` | 共享 affiliate 库（如分库部署） |
| `CRYPTO_PAY_TOKEN` | @Wallet CryptoBot Token（可选） |
| `MIN_BET_USDT` | 默认 1 |
| `MAX_BET_RATIO` | 默认 0.30（单人 ≤ 池子 30%） |
| `EVENT_FEE_BPS` | 默认 500（5%） |

### 6.4 持久化

Railway 加一个 Volume 挂到 `/app/data`，SQLite 文件 + 日志都放这里。

---

## 7. 安全 & 合规约定

```
🔴 永远不能做：
  - WALLET_MNEMONIC / BOT_TOKEN 写进代码或入 Git
  - 对未实名/未通过风控的用户提供 KYC 跳过
  - 在 bot 文案里使用法币术语（"$" "USD" "CNY"）→ 一律 USDT
  - 在中国大陆 IP 主动推广（合规）
  - 对未成年用户开放（/start 强制确认 ≥18）

🟢 必须这么做：
  - 所有金额 BIGINT micro 存储，Decimal 计算
  - 每笔资金流动写 ledger（双式记账）
  - 单事件单人 ≤ 池子 30%，硬性拦截
  - 出款 > $200 进人工审核
  - 主热钱包 ≤ 总余额 30%，其余冷钱包多签
  - 每天 23:55 自动跑账：sum(ledger) 必须 = 链上余额 ± 阈值
  - .env / data/ / logs/ / *.mnemonic 全部 .gitignore
```

---

## 8. 上线后运营节奏（第 1 周）

```
Day 1（今天）
  20:00  代码上线 Railway，推第一个测试事件给自己 + 1 个朋友
  22:00  发出第一个真事件（建议选短周期 < 24h，让用户当晚就能看结算）

Day 2-3
  - 接 deposit_watcher 自动入账
  - 接 admin_web 出款审核
  - 同一篇 Channel 帖子做 A/B：图卡 vs 纯文案

Day 4-7
  - 接 TeleGrowth affiliate（让早鸟用户当推广员）
  - 第二个 BOT_TOKEN 拉第二个 Channel（CN 中文版）
  - 每日复盘：DAU / 池子规模 / 平台手续费收入 / 留存

Day 8+ → 世界杯（6/11）
  - 每天事件主题向世界杯倾斜
  - 联动 KickAI（赛前情报） → "TelePoly 押 + KickAI 看分析" 组合包
  - 上 AI 出题（用 Aiberm 拿 ChatGPT 出 10 个候选，运营选 1）
```

---

## 9. 后续优化方向（v2 / v3）

> 这些**不在今天范围**，仅记录路线图，避免现在分心。

**v2（上线后第 2-4 周）**
- 多选事件（不止 YES/NO，可以是 4 选 1）
- 实时下注后即时返回"我的预估排名" → 玩家粘性
- 提前止盈（用户在事件结算前可以以当前赔率把仓位卖回池子，平台抽 1%）
- 推荐裂变奖励金可直接当下注金（提高使用粘性）

**v3（>1 万 DAU 后）**
- 切 PostgreSQL + Redis（事件并发下注的池子读写）
- CPMM 自动做市（升级到 Polymarket 同款股票模型）
- Mini App（Telegram WebApp 内嵌网页版下注界面，复杂事件可视化）
- 多链（BSC-USDT / TON-USDT，给 TG 原生用户用 TON）
- 事件订阅 + 推送精准化（基于历史下注偏好）

---

## 10. 角色分工

- **老板（你）**：选题、出第一道题、配 .env、Railway 创建项目、@BotFather 建 bot、把第一波种子用户拉进 Channel。
- **AI 合伙人（我）**：全部代码、文案、产品 PRD、运营节奏复盘、监控告警。

---

**核心原则**：今天做的每一行代码，都问自己——"这能让今晚就有人在 bot 里押第一注吗？"
不能 → 推到明天。能 → 立刻写。
