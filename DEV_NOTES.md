# LeagueWeb 开发日志

---

## QQ 机器人集成计划（待开发）

### 目标

通过 QQ 机器人实现：
1. **查询排名** — 群内发送指令查询排行榜/选手详情
2. **管理员补录** — 管理员通过机器人补录问题对局排名
3. **问题对局通知** — 对局超时/掉线时自动通知相关玩家

### 架构

```
QQ群 ↔ QQ机器人 ↔ HTTP API ↔ Flask ↔ MongoDB
```

机器人作为独立服务运行，通过 HTTP API 与 Flask 通信。不需要 WebSocket，现有 SSE 也不需要改。

### 需要的改动

#### 1. Webhook 通知（Flask 侧）

- 新增环境变量 `WEBHOOK_URL`（QQ 机器人的接收地址）
- 在问题对局发生时（超时、部分掉线），POST 通知到 webhook URL
- payload 包含对局信息 + 玩家列表（battleTag）

#### 2. QQ 号绑定机制（Flask 侧）

采用 **方案 A**：在 `league_players` 上加字段

```json
{
  "battleTag": "衣锦夜行#1000",
  "bindCode": "A3F8",
  "bindCodeExpire": "2026-04-17T08:30:00Z"
}
```

- `bindCode`：一次性绑定码，有效期 5 分钟
- `bindCodeExpire`：过期时间
- 绑定成功后清除这两个字段

流程：
1. 玩家在网站点击「绑定 QQ」→ 生成临时绑定码
2. 玩家在 QQ 机器人输入 `/绑定 A3F8`
3. 机器人调 API 验证 → 匹配到 battleTag → 机器人写入本地映射表
4. 绑定码用完即废

**机器人侧**维护 QQ 号 ↔ battleTag 映射表，不存入 Flask 数据库。

#### 3. 新增 API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/bind-code` | POST | 登录用户生成绑定码（返回 code） |
| `/api/bind-code/verify` | POST | 机器人验证绑定码（返回 battleTag） |

机器人调用第二个端点时，需要带机器人自己的认证 token（环境变量 `BOT_API_KEY`）。

### 涉及的数据集合

| 集合 | 改动 |
|------|------|
| `league_players` | 新增 `bindCode`、`bindCodeExpire` 字段（临时） |

不需要新建集合，不需要改动现有字段。
