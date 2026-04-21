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
  "players": ["南怀北瑾丨少头脑", "瓦莉拉"],
  "startedAt": "2026-04-09T23:30:00Z"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | string | `"timeout"`（超时）或 `"abandoned"`（掉线） |
| `gameUuid` | string | 对局 UUID |
| `players` | array | 需要补录的玩家 displayName 列表 |
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

返回对阵图数据（从 tournament_groups 集合读取）。

**响应：** 同 `_build_bracket_data()` 结构，包含 tournaments → rounds → groups → players。

---

### `POST /api/tournament/create`

管理员创建赛事并分配分组。

**认证：** 需管理员登录（session）

**请求体：**
```json
{
  "tournamentName": "2026 春季赛",
  "rounds": [
    {
      "round": 1,
      "boN": 3,
      "groups": [
        {
          "groupIndex": 1,
          "players": [
            {"battleTag": "xxx#1234", "accountIdLo": "12345", "displayName": "xxx", "heroCardId": "...", "heroName": "..."},
            ...
          ],
          "nextRoundGroupId": 1
        }
      ]
    }
  ]
}
```

| 字段 | 说明 |
|------|------|
| `tournamentName` | 赛事名称 |
| `rounds[].round` | 轮次编号 |
| `rounds[].boN` | 本轮局数（BO3=3, BO5=5） |
| `rounds[].groups[].groupIndex` | 组号（1-based） |
| `rounds[].groups[].players` | 8 个玩家（不足 8 人自动补空位） |
| `rounds[].groups[].nextRoundGroupId` | 晋级目标组号 |

**响应：** `{"ok": true, "tournamentName": "...", "groupsCreated": N}`

---

### `GET /api/tournament/group/<group_id>`

获取单个分组详情。

**响应：** tournament_groups 文档完整数据（含 boN、gamesPlayed、players.totalPoints、players.games[]）。
