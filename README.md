# TelePoly

> Telegram 上的每日 USDT 预测市场。
> 每天一道题，事件结束前都能押 YES/NO，结算后赢家按彩池比例分钱。

详细产品文档见 **[`TelePoly.md`](./TelePoly.md)**。

---

## 状态

```
启动：2026-05-27
目标：D-Day 2026-06-11 世界杯开赛
当前：MVP 开发中（Day 1）
```

## 快速启动（本地）

```bash
# 1. 安装依赖（首次，需先装 uv: https://docs.astral.sh/uv/）
uv sync

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，至少填：
#   TELEPOLY_BOT_TOKEN
#   TELEPOLY_BOT_USERNAME
#   OWNER_TG_IDS
#   ANNOUNCE_CHANNEL_ID

# 3. 初始化数据库
uv run python -m db.init

# 4. 启动主 Bot
uv run python -m telepoly_bot.main
```

## 部署

直接 push 到 [`justinwsl123/telepoly`](https://github.com/justinwsl123/telepoly)，Railway 自动构建（NIXPACKS）+ 启动 `start.sh`。
环境变量在 Railway 项目页配置，**所有敏感值（特别是 `WALLET_MNEMONIC`）必须用 Railway Secrets 注入**。

## 目录

```
telepoly_bot/   # 主 Bot 入口 + handlers
core/           # 业务核心（事件 / 下注 / 结算 / 记账）
db/             # ORM + migration
wallet/         # TRC20 USDT 钱包（HD 派生 + 扫块 + 出款）
scheduler/      # 定时任务（封盘 / 推送 / 扫块）
integrations/   # TeleGrowth / Aiberm / @Wallet
admin_web/      # 运营后台（Day 2）
data/           # SQLite 持久化（不入 Git）
logs/           # 运行日志（不入 Git）
tests/
```

## 角色分工

- **老板**：选题、运营、配 Token / 钱包、决策。
- **AI 合伙人**：全部代码、文案、产品迭代。

详见 [`TelePoly.md`](./TelePoly.md)。
