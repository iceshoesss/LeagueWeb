# LeagueWeb API 文档

Base URL: `http://<服务器IP>:5000`

所有 JSON 响应均返回 `application/json`。时间字段统一为 UTC 格式（带 `Z` 后缀）。

---

## 排行榜

### `GET /api/players`

获取排行榜（所有选手聚合数据）。

**参数：** 无

**响应示例：**
```json
[
  {
    "_id": "南怀北瑾丨少头脑#5267",
    "battleTag": "南怀北瑾丨少头脑#5267",
    "displayName": "南怀北瑾丨少头脑",
    "accountIdLo": "1708070391",
    "totalPoints": 142,
    "leagueGames": 20,
    "wins": 9,
    "chickens": 5,
    "avgPlacement": 2.8,
    "winRate": 0.45,
    "chickenRate": 0.25,
    "lastGameAt": "2026-04-08T22:30:00Z"
  }
]
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `battleTag` | string | 完整 BattleTag（唯一标识） |
| `displayName` | string | 显示名（不含 #tag） |
| `accountIdLo` | string | 暴雪账号唯一 ID |
| `totalPoints` | int | 累计积分 |
| `leagueGames` | int | 联赛场次 |
| `wins` | int | 前四次数 |
| `chickens` | int | 吃鸡次数 |
| `avgPlacement` | float | 平均排名 |
| `winRate` | float | 胜率（0~1，前四=胜） |
| `chickenRate` | float | 吃鸡率（0~1） |
| `lastGameAt` | string | 最后一局时间（UTC） |

---

## 选手详情

### `GET /api/players/<battleTag>`

获取单个选手的详细信息。注意 URL 中的 `#` 需要编码为 `%23`。

**示例：**
```
GET /api/players/%E5%8D%97%E6%80%80%E5%8C%97%E7%91%BE%E4%B8%A8%E5%B0%91%E5%A4%B4%E8%84%91%235267
```

**响应：** 同排行榜单条格式。选手不存在时返回 `404 {"error": "选手不存在"}`。

---

## 对局列表

### `GET /api/matches`

获取最近已完成的对局（默认 10 条，排除 timeout/abandoned）。

**参数：** 无

**响应示例：**
```json
[
  {
    "_id": "6612abc...",
    "gameUuid": "888fc109-8a0c-42d8-8b21-fcee26708e8f",
    "region": "CN",
    "mode": "solo",
    "startedAt": "2026-04-08T20:15:00Z",
    "endedAt": "2026-04-08T21:05:00Z",
    "players": [
      {
        "accountIdLo": "1708070391",
        "battleTag": "南怀北瑾丨少头脑#5267",
        "displayName": "南怀北瑾丨少头脑",
        "heroCardId": "TB_BaconShop_HERO_56",
        "heroName": "阿莱克丝塔萨",
        "placement": 1,
        "points": 9
      }
    ]
  }
]
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `gameUuid` | string | 对局唯一标识 |
| `region` | string | 服务器区域（CN/US/EU） |
| `mode` | string | `solo` 或 `duo` |
| `startedAt` | string | 开始时间（UTC） |
| `endedAt` | string | 结束时间（UTC） |
| `players` | array | 8 个玩家，按排名升序排列 |
| `players[].accountIdLo` | string | 暴雪账号唯一 ID |
| `players[].battleTag` | string | 完整 BattleTag |
| `players[].displayName` | string | 显示名 |
| `players[].placement` | int | 排名 1-8 |
| `players[].points` | int | 积分（1st=9, 2nd=7, ..., 8th=1） |
| `players[].heroCardId` | string | 英雄卡牌 ID |
| `players[].heroName` | string | 英雄中文名 |

**英雄头像 URL 模板：**
```
https://art.hearthstonejson.com/v1/256x/{heroCardId}.jpg
```

---

## 对局详情

### `GET /api/match/<gameUuid>`

获取单场对局的完整信息。

**响应：** 同对局列表单条格式。`players` 按排名升序排列（null 排最后）。

**错误：** `404 {"error": "对局不存在"}`

> 网页版对局详情页地址为 `GET /match/<gameUuid>`（返回 HTML）。

---

## 正在进行的对局

### `GET /api/active-games`

获取当前进行中的联赛对局（80 分钟内未结束）。

**响应示例：**
```json
[
  {
    "_id": "6612def...",
    "gameUuid": "aabbccdd-1234-5678-...",
    "startedAt": "2026-04-09T23:30:00Z",
    "startedAtEpoch": 1744231800,
    "players": [
      {
        "displayName": "南怀北瑾丨少头脑",
        "heroCardId": "TB_BaconShop_HERO_56",
        "heroName": "阿莱克丝塔萨",
        "placement": null,
        "points": null,
        "accountIdLo": "1708070391",
        "battleTag": "南怀北瑾丨少头脑#5267"
      }
    ]
  }
]
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `gameUuid` | string | 对局唯一标识 |
| `startedAt` | string | 开始时间（UTC） |
| `startedAtEpoch` | int | 开始时间（Unix 秒），前端可直接算计时 |
| `players` | array | 8 个玩家 |
| `players[].placement` | int/null | 已提交排名，未提交为 null |
| `players[].points` | int/null | 已提交积分，未提交为 null |

---

## 报名队列

### `GET /api/queue`

获取当前报名队列。

**响应示例：**
```json
[
  { "_id": "6612...", "name": "衣锦夜行", "joinedAt": "2026-04-09T23:00:00Z", "lastSeen": "2026-04-09T23:05:00Z" },
  { "_id": "6613...", "name": "瓦莉拉", "joinedAt": "2026-04-09T23:01:00Z", "lastSeen": "2026-04-09T23:04:00Z" }
]
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 玩家 BattleTag |
| `joinedAt` | string | 加入时间（UTC） |
| `lastSeen` | string | 最后活跃时间（UTC），10 分钟超时自动踢出 |

### `POST /api/queue/join`

加入报名队列（需登录，从 session 读取玩家身份）。

**请求体：** 无需传参（或传空 `{}`）

**响应：**
```json
{ "ok": true, "name": "衣锦夜行", "moved": false }
```

| 字段 | 说明 |
|------|------|
| `name` | 玩家 BattleTag |
| `moved` | `true` = 满 8 人已移入等待组；`false` = 仍在报名队列中 |

**错误：**
- `401` 未登录
- `400 {"error": "已在报名队列中"}`
- `400 {"error": "已在等待队列中"}`

### `POST /api/queue/leave`

退出报名队列或等待队列（需登录，从 session 读取玩家身份）。

**请求体：** 无需传参

**响应：** `{"ok": true, "name": "衣锦夜行"}`

---

## 等待队列

### `GET /api/waiting-queue`

获取所有等待中的对局组（每满 8 人创建一组）。

**响应示例：**
```json
[
  {
    "_id": "6614...",
    "players": [
      { "name": "衣锦夜行", "accountIdLo": "1708070391" },
      { "name": "瓦莉拉", "accountIdLo": "12345678" }
    ],
    "createdAt": "2026-04-09T23:10:00Z"
  }
]
```

- `players[].name` — 玩家 BattleTag
- `players[].accountIdLo` — 暴雪账号唯一 ID
- `players` 数组长度为 8 时 = 等待开赛
- 等待组 20 分钟超时自动解散

---

## 登录 / 注册

### `POST /api/register`

注册（首次登录）。输入 BattleTag + 插件生成的验证码。

**请求体：**
```json
{
  "battleTag": "南怀北瑾丨少头脑#5267",
  "verificationCode": "A1B2C3D4"
}
```

**响应：** `{"ok": true, "battleTag": "...", "displayName": "..."}`

注册成功后自动登录（设置 session）。

**错误：**
- `400` BattleTag 或验证码为空
- `404` 未找到游戏记录（需先用插件打一局）
- `400` 验证码不正确

### `POST /api/login`

登录（验证码同注册时的）。

**请求体：** 同 register

**响应：** `{"ok": true, "battleTag": "...", "displayName": "..."}`

**错误：**
- `400` BattleTag 或验证码为空
- `404` 未找到记录
- `403` 验证码不正确

### `POST /api/logout`

退出登录（清除 session，同时自动退出所有队列）。

**响应：** `{"ok": true}`

### `GET /api/verify?battleTag=xxx`

检查某 BattleTag 是否已注册验证。

**响应：** `{"verified": true, "displayName": "南怀北瑾丨少头脑"}` 或 `{"verified": false}`

---

## 绑定码

### `POST /api/bind-code`

登录用户生成绑定码，用于 QQ 机器人关联游戏账号与 QQ 号。

**认证：** 需登录（session）

**请求体：** 无需传参

**响应：**
```json
{ "ok": true, "code": "A3F8D2", "expireMinutes": 5 }
```

| 字段 | 说明 |
|------|------|
| `code` | 6 位绑定码，5 分钟内有效 |
| `expireMinutes` | 有效期（分钟） |

**错误：**
- `401` 未登录
- `503` `BOT_API_KEY` 未配置，绑定功能未启用

### `POST /api/bind-code/verify`

机器人验证绑定码，返回玩家身份。

**认证：** 需 `BOT_API_KEY`（请求体中传入）

**请求体：**
```json
{
  "botKey": "你的BOT_API_KEY",
  "code": "A3F8D2"
}
```

**响应：**
```json
{ "ok": true, "battleTag": "南怀北瑾丨少头脑#5267", "displayName": "南怀北瑾丨少头脑" }
```

**错误：**
- `403` botKey 认证失败
- `400` 绑定码为空
- `404` 绑定码不存在或已过期

---

## 补录排名

### `POST /api/match/<gameUuid>/update-placement`

手动补录问题对局的排名（需登录）。

**请求体：**
```json
{
  "placements": {
    "1708070391": 1,
    "12345678": 2,
    "11111111": 3,
    "22222222": 4,
    "33333333": 5,
    "44444444": 6,
    "55555555": 7,
    "66666666": 8
  }
}
```

- key 是 `accountIdLo`（字符串）
- value 是排名 1-8，必须刚好 8 个且不重复
- 已有排名的玩家会被跳过（锁定）

**响应：** `{"ok": true, "updated": 5, "skipped_locked": 3}`

| 字段 | 说明 |
|------|------|
| `updated` | 本次更新的玩家数 |
| `skipped_locked` | 因已有排名而跳过的玩家数 |

**错误：**
- `401` 未登录
- `403` 非管理员只能补录自己的排名
- `400` 排名数据不完整、有重复、或所有玩家已锁定
- `404` 对局不存在

---

## 插件专用 API

以下端点供 C# HDT 插件调用，替代直连 MongoDB。

**所有插件请求必须带 `X-HDT-Plugin` header，值为插件版本号（如 `0.5.5`）。**
服务端通过 `MIN_PLUGIN_VERSION` 环境变量控制最低版本，低于此版本的插件将被拒绝（403）。

**如配置了 `PLUGIN_API_KEY`，插件请求还需带 `Authorization: Bearer <key>` header。**

### `POST /api/plugin/upload-rating`

插件上报分数并获取验证码。**无需登录认证（需插件版本号 header，配置 `PLUGIN_API_KEY` 后还需 Bearer token）。**

**请求头：** `Content-Type: application/json`

**请求体：**
```json
{
  "playerId": "南怀北瑾丨少头脑#5267",
  "accountIdLo": "1708070391",
  "rating": 6500,
  "mode": "solo",
  "gameUuid": "888fc109-...",
  "region": "CN"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `playerId` | string | ✅ | 完整 BattleTag |
| `accountIdLo` | string | | 暴雪账号唯一 ID |
| `rating` | number | ✅ | 当前分数 |
| `mode` | string | | `solo`（默认）或 `duo` |
| `gameUuid` | string | | 对局 UUID |
| `region` | string | | 服务器区域，默认 `CN` |

**响应：**
```json
{ "ok": true, "verificationCode": "A1B2C3D4" }
```

- 首次上传返回 `verificationCode`，后续上传也返回已有验证码
- 速率限制：每 playerId 每 60 秒最多 10 次

### `POST /api/plugin/check-league`

检查是否为联赛对局（STEP 13 时调用）。**无需登录认证（需插件版本号 header，配置 `PLUGIN_API_KEY` 后还需 Bearer token）。**

**请求体：**
```json
{
  "playerId": "南怀北瑾丨少头脑#5267",
  "gameUuid": "888fc109-...",
  "accountIdLo": "1708070391",
  "accountIdLoList": ["1708070391", "12345678", "..."],
  "players": {
    "1708070391": {
      "battleTag": "南怀北瑾丨少头脑#5267",
      "displayName": "南怀北瑾丨少头脑",
      "heroCardId": "TB_BaconShop_HERO_56",
      "heroName": "阿莱克丝塔萨"
    }
  },
  "mode": "solo",
  "region": "CN",
  "startedAt": "2026-04-09T23:30:00Z"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `gameUuid` | string | ✅ | 对局 UUID（格式：`xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`） |
| `accountIdLoList` | array | ✅ | 本局 8 个玩家的 accountIdLo 列表 |
| `playerId` | string | | 当前玩家 BattleTag |
| `accountIdLo` | string | | 当前玩家 accountIdLo |
| `players` | object | | 详细玩家信息（key 为 accountIdLo） |
| `mode` | string | | `solo` 或 `duo` |
| `region` | string | | 服务器区域 |
| `startedAt` | string | | 开始时间 |

**响应：**
```json
{ "isLeague": true, "verificationCode": "A1B2C3D4" }
```

- `isLeague`: 是否匹配到联赛等待组
- `verificationCode`: 确保玩家在 player_records 中有记录
- 匹配成功时自动创建 `league_matches` 文档并删除等待组

### `POST /api/plugin/update-placement`

更新联赛对局排名（游戏结束时调用）。**无需登录认证（需插件版本号 header，配置 `PLUGIN_API_KEY` 后还需 Bearer token）。**

**请求体：**
```json
{
  "playerId": "南怀北瑾丨少头脑#5267",
  "gameUuid": "888fc109-...",
  "accountIdLo": "1708070391",
  "placement": 3
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `gameUuid` | string | ✅ | 对局 UUID |
| `accountIdLo` | string | ✅ | 当前玩家 accountIdLo |
| `placement` | int | ✅ | 排名 1-8 |
| `playerId` | string | | 用于速率限制 |

**响应：**
```json
{ "ok": true, "finalized": false }
```

| 字段 | 说明 |
|------|------|
| `finalized` | `true` = 全部 8 人已提交（含自动推算），对局结束并写入 `endedAt` |

> 当 7 人提交后，第 8 人的排名会自动推算（剩余的唯一数字），`finalized` 直接返回 `true`。

**错误：**
- `400` 参数不完整或格式无效
- `404` 对局不存在
- `404` 玩家不在此对局中
- `409` 该玩家已提交过排名
- `429` 请求过于频繁

---

## SSE 实时推送

以下端点使用 Server-Sent Events 推送实时数据变化，前端通过 `EventSource` 连接。

连接每 120 秒自动断开（防僵尸连接），客户端自动重连。每 30 秒发送心跳注释行。

| 端点 | 数据 | 说明 |
|------|------|------|
| `/api/events/active-games` | 进行中对局列表 | 有变化时推送完整列表 |
| `/api/events/queue` | 报名队列 | 有变化时推送完整列表 |
| `/api/events/waiting-queue` | 等待组列表 | 有变化时推送完整列表 |
| `/api/events/matches` | 最近 5 场已完成对局 | 有新对局结束时推送 |
| `/api/events/problem-matches` | `{"count": N}` | 问题对局数量变化 |

**响应格式：** `Content-Type: text/event-stream`

```
data: [{"gameUuid":"...","startedAtEpoch":1744231800,"players":[...]}]

: heartbeat
```

---

## 数据结构速查

### 积分规则

| 排名 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|------|---|---|---|---|---|---|---|---|
| 积分 | 9 | 7 | 6 | 5 | 4 | 3 | 2 | 1 |

公式：`points = placement == 1 ? 9 : max(1, 9 - placement)`

### 对局状态

| `status` 字段 | 说明 |
|--------------|------|
| （不存在） | 正常完成 |
| `"timeout"` | 超时（80 分钟） |
| `"abandoned"` | 部分玩家掉线 |

### MongoDB 集合

| 集合 | 写入方 | 说明 |
|------|--------|------|
| `player_records` | Flask API（`/api/plugin/upload-rating`） | 玩家记录（含验证码、accountIdLo） |
| `league_matches` | Flask API（`check-league` + `update-placement`） | 联赛对局（8 人完整数据） |
| `league_queue` | Flask 网站 | 报名队列（10 分钟超时踢出） |
| `league_waiting_queue` | Flask 网站 + `check-league` | 等待组（满 8 人自动创建，20 分钟超时解散） |
| `league_players` | Flask 网站 | 已注册选手（含 `lastSeen` 活跃追踪） |

### 队列超时

| 队列 | 超时 | 行为 |
|------|------|------|
| `league_queue` | 10 分钟 | 自动踢出 |
| `league_waiting_queue` | 20 分钟 | 解散组，不再回到报名队列 |

---

## Webhook 通知

当问题对局发生时（超时、掉线），服务端会主动 POST 通知到配置的 `WEBHOOK_URL`。

**触发条件：**
- 超时对局：对局超过 80 分钟未结束，所有玩家均未提交排名 → 标记 `status: "timeout"`
- 掉线对局：部分玩家已提交但超过 80 分钟仍未全部提交 → 标记 `status: "abandoned"`

**请求格式：**
```json
{
  "type": "timeout",
  "gameUuid": "888fc109-...",
  "players": [{"battleTag": "南怀北瑾丨少头脑#5267", "displayName": "南怀北瑾丨少头脑"}, {"battleTag": "瓦莉拉#1234", "displayName": "瓦莉拉"}],
  "startedAt": "2026-04-09T23:30:00Z"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | string | `"timeout"`（超时）或 `"abandoned"`（掉线） |
| `gameUuid` | string | 对局 UUID |
| `players` | array | 需要补录的玩家列表，每项含 `battleTag`（带 #tag）和 `displayName` |
| `startedAt` | string | 对局开始时间（UTC） |

**说明：**
- `timeout` 类型包含全部 8 位玩家（均未提交）
- `abandoned` 类型仅包含 `placement` 为 null 的玩家（未提交排名的）
- webhook 由后台清理线程触发（间隔由 `CLEANUP_INTERVAL` 控制，非页面访问触发）
- 每局只会通知一次（标记后不再重复匹配）

---

## 英雄头像

拼接 `heroCardId` 获取头像：

```
https://art.hearthstonejson.com/v1/256x/{heroCardId}.jpg
```

示例：
```
https://art.hearthstonejson.com/v1/256x/TB_BaconShop_HERO_56.jpg
```

> 注意：这是 256×256 正方形图，原图可能是横条（tiles 格式），用于头像需要 CSS 裁剪聚焦脸部。


---

## 淘汰赛 API

### `GET /api/bracket`

返回对阵图数据（从 tournament_groups 集合读取，5 秒缓存）。

**响应：** tournaments → rounds → groups → players 结构，含 label（A1、B1 等）、排名、积分。

---

### `GET /api/tournaments`

获取所有赛事列表。

**认证：** 需管理员登录（session）

**响应：**
```json
[
  {
    "name": "2026 春季赛",
    "totalGroups": 112,
    "statusCounts": {"waiting": 80, "done": 32},
    "rounds": [1]
  }
]
```

---

### `POST /api/tournament/create`

管理员创建赛事并分配分组。

**认证：** 需管理员登录（session）

**请求体：**
```json
{
  "tournamentName": "2026 春季赛",
  "layout": "bracket",
  "rounds": [
    {
      "round": 1,
      "boN": 3,
      "groups": [
        {
          "groupIndex": 1,
          "players": [
            {"battleTag": "xxx#1234", "accountIdLo": "12345", "displayName": "xxx", "heroCardId": "...", "heroName": "..."}
          ]
        }
      ]
    }
  ]
}
```

| 字段 | 说明 |
|------|------|
| `tournamentName` | 赛事名称 |
| `layout` | `"bracket"`（淘汰赛）或 `"grid"`（海选平铺），默认 `"bracket"` |
| `rounds[].round` | 轮次编号 |
| `rounds[].boN` | 本轮局数（BO1~BO7） |
| `rounds[].groups[].groupIndex` | 组号（1-based） |
| `rounds[].groups[].players` | 最多 8 个玩家（不足自动补空位） |

**响应：** `{"ok": true, "tournamentName": "...", "groupsCreated": N, "layout": "bracket"}`

---

### `GET /api/tournament/manage/<tournament_name>`

获取赛事全部分组数据（含排名聚合）。

**认证：** 需管理员登录（session）

**响应：**
```json
{
  "name": "2026 春季赛",
  "groups": [
    {
      "_id": "6612...",
      "tournamentName": "2026 春季赛",
      "round": 1,
      "groupIndex": 1,
      "status": "waiting",
      "boN": 3,
      "gamesPlayed": 1,
      "players": [
        {
          "battleTag": "xxx#1234",
          "accountIdLo": "12345",
          "displayName": "xxx",
          "heroCardId": "...",
          "heroName": "...",
          "totalPoints": 7,
          "games": [7],
          "qualified": false,
          "eliminated": false,
          "empty": false
        }
      ],
      "layout": "bracket",
      "createdAt": "2026-04-21T20:00:00Z",
      "startedAt": null,
      "endedAt": null
    }
  ]
}
```

---

### `GET /api/tournament/group/<group_id>`

获取单个分组详情（含排名聚合）。

**响应：** tournament_groups 文档完整数据。

---

### `PUT /api/tournament/group/<group_id>/update`

编辑分组（BO 数和/或玩家列表）。只能编辑未开始的分组（waiting + gamesPlayed=0）。

**认证：** 需管理员登录（session）

**请求体：**
```json
{
  "boN": 5,
  "players": [
    {"battleTag": "xxx#1234", "accountIdLo": "12345", "displayName": "xxx", "heroCardId": "", "heroName": ""}
  ]
}
```

| 字段 | 说明 |
|------|------|
| `boN` | BO 数（1-20），可选 |
| `players` | 玩家列表（不足 8 人自动补空位），可选 |

**错误：** `400` 已开始的分组不能编辑

---

### `POST /api/tournament/shuffle`

确定性随机洗牌（SHA256 seed + Fisher-Yates）。

**认证：** 需管理员登录（session）

**请求体：**
```json
{
  "seed": "2026春季赛海选",
  "players": [{"battleTag": "xxx#1234", "accountIdLo": "12345", "displayName": "xxx"}, ...]
}
```

**响应：**
```json
{"ok": true, "seed": "2026春季赛海选", "players": [...]}
```

`players` 为洗牌后的数组，顺序可复现。

---

### `DELETE /api/tournament/<tournament_name>`

删除赛事。普通管理员只能删除未开始的赛事，超级管理员可强制删除（同时清理关联的 league_matches）。

**认证：** 需管理员登录（session）

**响应：** `{"ok": true, "deleted": N}`

---

### `GET /api/tournament/qualifier-pool`

获取指定赛事的晋级者 + 种子选手池。

**认证：** 需管理员登录（session）

**参数：** `?tournament=赛事名称`（必填）

**响应：**
```json
{
  "qualifiers": 32,
  "seeds": 4,
  "total": 36,
  "players": [
    {"battleTag": "xxx#1234", "accountIdLo": "12345", "displayName": "xxx", "heroCardId": "...", "heroName": "..."}
  ]
}
```

---

### `POST /api/tournament/generate-next`

从源赛事晋级者 + 种子选手自动生成新赛事分组。确定性洗牌（seed = 新赛事名称）。

**认证：** 需管理员登录（session）

**请求体：**
```json
{
  "sourceTournament": "2026 春季赛海选",
  "tournamentName": "2026 春季赛 512强",
  "boN": 5
}
```

**响应：**
```json
{
  "ok": true,
  "tournamentName": "2026 春季赛 512强",
  "qualifiers": 32,
  "seeds": 4,
  "total": 36,
  "groupsCreated": 4
}
```

**错误：** `400` 晋级者+种子不足 16 人

---

## 赛事报名

报名上限 1024 人，超出自动进替补队列。正选退出后替补按顺序自动补上。
截止时间通过环境变量 `ENROLL_DEADLINE` 配置（ISO 时间格式），截止后禁止新报名和退赛。

### `POST /api/enroll`

报名参赛（需登录）。

**请求体：** 无需传参

**响应：**
```json
{ "ok": true, "status": "enrolled", "position": 42, "message": "报名成功" }
```

| 字段 | 说明 |
|------|------|
| `status` | `enrolled`（正选）或 `waitlist`（替补） |
| `position` | 队列位置 |

**错误：**
- `401` 未登录
- `400` 已报名 / 已截止

### `POST /api/enroll/withdraw`

退赛（需登录，截止前可退）。正选退出后替补自动补上。

**请求体：** 无需传参

**响应：** `{"ok": true, "message": "已退赛"}`

**错误：**
- `401` 未登录
- `400` 未报名 / 已截止

### `GET /api/enroll/status`

查看自己的报名状态（需登录）。

**响应：**
```json
{
  "enrolled": true,
  "status": "enrolled",
  "position": 42,
  "enrollAt": "2026-04-23T10:00:00Z",
  "cap": 1024,
  "deadline": "2026-05-01T20:00:00+08:00"
}
```

未报名时返回 `{"enrolled": false, "cap": 1024, "enrolledCount": N, "deadline": "..."}`

### `GET /api/enrollments`

查看报名列表（公开）。

**响应：**
```json
{
  "cap": 1024,
  "enrolledCount": 500,
  "waitlistCount": 30,
  "deadline": "2026-05-01T20:00:00+08:00",
  "deadlineReached": false,
  "players": [
    { "battleTag": "xxx#1234", "displayName": "xxx", "status": "enrolled", "position": 1, "enrollAt": "..." },
    { "battleTag": "yyy#5678", "displayName": "yyy", "status": "waitlist", "position": 1025, "enrollAt": "..." }
  ]
}
```

### `GET /api/admin/enrolled`

管理员查看报名列表（含 accountIdLo，用于创建赛事分组）。需管理员登录。

**响应：** 同 `/api/enrollments`，players 额外包含 `accountIdLo` 字段。

---

## 管理后台 API

以下端点需管理员登录（session）。超级管理员端点额外标注。

### `GET /api/admin/stats`

管理面板总览数据。

**响应：** 聚合统计（选手数、对局数、队列数等）。

### `GET /api/admin/matches`

管理面板对局列表（分页）。

**参数：** `?page=1&per_page=20&status=all`

**响应：** `{"matches": [...], "total": N, "page": 1, "totalPages": 5}`

### `GET /api/admin/players`

管理面板选手列表（分页 + 搜索）。

**参数：** `?page=1&search=关键词`

**响应：** `{"players": [...], "total": N, "page": 1, "totalPages": 5}`

### `GET /api/admin/players-all`

获取全部已注册选手（不分页，用于创建赛事选择器）。

**响应：**
```json
[
  {"battleTag": "xxx#1234", "displayName": "xxx", "accountIdLo": "12345"}
]
```

### `GET /api/admin/enrolled-players`

获取报名选手列表（含 accountIdLo，批量查询优化）。

**参数：** `?limit=N`（可选，取前 N 人）

**响应：** 同 `/api/admin/players-all`。

### `POST /api/admin/player/add`

管理员手动添加选手（手机玩家/无插件玩家）。accountIdLo 用 battleTag 作为伪 Lo。

**请求体：** `{"battleTag": "xxx#1234", "displayName": "xxx"}`

**响应：** `{"ok": true, "battleTag": "xxx#1234", "displayName": "xxx"}`

### `PUT /api/admin/player/<battleTag>/seed`

设置/取消种子选手（toggle）。

**响应：** `{"ok": true, "isSeed": true}`

### `GET /api/admin/seed-players`

获取所有种子选手列表。

**响应：** `[{"battleTag": "...", "displayName": "...", "accountIdLo": "..."}]`

### `POST /api/admin/match/<gameUuid>/force-end`

强制结束对局（标记为 timeout）。

**响应：** `{"ok": true}`

### `POST /api/admin/match/<gameUuid>/force-abandon`

强制标记对局掉线（标记为 abandoned）。

**响应：** `{"ok": true}`

### `POST /api/admin/match/<gameUuid>/reset`

重置对局状态（清除 endedAt 和 status，回到进行中）。

**响应：** `{"ok": true}`

### `PUT /api/admin/match/<gameUuid>/edit-placement`

修改已完成对局的排名（覆盖已有排名）。

**请求体：**
```json
{
  "placements": {
    "1708070391": 1,
    "12345678": 2
  }
}
```

- key 是 accountIdLo，value 是新排名 1-N
- 排名必须覆盖所有玩家且不重复

**响应：** `{"ok": true}`

### `DELETE /api/match/<gameUuid>`

删除对局。需管理员登录。

**响应：** `{"ok": true, "gameUuid": "..."}`

### `POST /api/admin/group/<group_id>/advance`

手动晋级：管理员指定晋级者（最多 4 人）。

**请求体：**
```json
{
  "players": ["1708070391", "12345678"]
}
```

- `players` 是 accountIdLo 列表
- bracket 布局：创建/填入下一轮分组
- grid 布局（海选）：只标记 qualified/eliminated，不创建下一轮

**响应：** `{"ok": true, "advanced": 4}`

### `POST /api/admin/group/<group_id>/manual-record`

纯手工补录：为分组创建完整对局记录（插件失效时使用）。创建 match 记录 + 计算积分 + 触发晋级。

**请求体：**
```json
{
  "placements": {
    "1708070391": 1,
    "12345678": 2,
    "11111111": 3,
    "22222222": 4
  }
}
```

- key 是 accountIdLo，value 是排名 1-N
- 必须覆盖组内所有非空玩家

**响应：** `{"ok": true, "gameUuid": "...", "gamesPlayed": 2}`

### `POST /api/admin/queue/remove`

从报名队列移除玩家。

**请求体：** `{"name": "玩家BattleTag"}`

### `POST /api/admin/waiting/remove`

从等待组移除玩家。

**请求体：** `{"name": "玩家BattleTag"}`

### `GET /api/admin/admins`

获取管理员列表。**需超级管理员。**

**响应：** `[{"_id": "...", "battleTag": "...", "addedAt": "...", "addedBy": "...", "isSuperAdmin": false}]`

### `POST /api/admin/admins/add`

添加管理员。**需超级管理员。**

**请求体：** `{"battleTag": "xxx#1234"}`

### `POST /api/admin/admins/remove`

移除管理员。**需超级管理员。** 不能移除自己或超级管理员。

**请求体：** `{"battleTag": "xxx#1234"}`
