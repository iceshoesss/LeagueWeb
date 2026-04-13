# HDT_BGTracker 联赛网站 API 文档

Base URL: `http://<服务器IP>:5000`

所有 JSON 响应均返回 `application/json`。

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
    "lastGameAt": "2026-04-08T22:30:00"
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
| `lastGameAt` | string | 最后一局时间 |

---

## 选手详情

### `GET /api/players/<battleTag>`

获取单个选手的详细信息。注意 URL 中的 `#` 需要编码为 `%23`。

**示例：**
```
GET /api/players/%E5%8D%97%E6%80%80%E5%8C%97%E7%91%BE%E4%B8%A8%E5%B0%91%E5%A4%B4%E8%84%91%235267
```

**响应：** 同排行榜单条格式，选手不存在时返回 `404 {"error": "选手不存在"}`。

---

## 对局列表

### `GET /api/matches`

获取最近已完成的对局（默认 10 条）。

**参数：** 无

**响应示例：**
```json
[
  {
    "_id": "6612abc...",
    "gameUuid": "888fc109-8a0c-42d8-8b21-fcee26708e8f",
    "region": "CN",
    "mode": "solo",
    "startedAt": "2026-04-08T20:15:00",
    "endedAt": "2026-04-08T21:05:00",
    "status": "completed",
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
| `startedAt` | string | 开始时间 |
| `endedAt` | string | 结束时间 |
| `players` | array | 8 个玩家，按排名升序排列 |
| `players[].placement` | int | 排名 1-8 |
| `players[].points` | int | 积分（1st=9, 2nd=7, ..., 8th=1） |
| `players[].heroCardId` | string | 英雄卡牌 ID（可用于拼头像 URL） |
| `players[].heroName` | string | 英雄中文名 |

**英雄头像 URL 模板：**
```
https://art.hearthstonejson.com/v1/256x/{heroCardId}.jpg
```

---

## 对局详情

### `GET /api/match/<gameUuid>`

获取单场对局的完整信息。

**响应示例：**
```json
{
  "_id": "6612abc...",
  "gameUuid": "888fc109-8a0c-42d8-8b21-fcee26708e8f",
  "region": "CN",
  "mode": "solo",
  "startedAt": "2026-04-08T20:15:00",
  "endedAt": "2026-04-08T21:05:00",
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
```

`players` 按排名升序排列（null 排最后）。对局不存在时返回 `404 {"error": "对局不存在"}`。

> 网页版对局详情页地址为 `GET /match/<gameUuid>`（返回 HTML）。

---

## 正在进行的对局

### `GET /api/active-games`

获取当前进行中的联赛对局。

**响应示例：**
```json
[
  {
    "_id": "6612def...",
    "gameUuid": "aabbccdd-1234-5678-...",
    "startedAt": "2026-04-09T23:30:00",
    "startedAtEpoch": 1744231800,
    "players": [
      { "displayName": "南怀北瑾丨少头脑", "heroCardId": "TB_BaconShop_HERO_56", "heroName": "阿莱克丝塔萨", "placement": null, "points": null, "accountIdLo": "1708070391", "battleTag": "南怀北瑾丨少头脑#5267" },
      { "displayName": "疾风剑豪", "heroCardId": "BG20_HERO_202", "heroName": "阮大师", "placement": 3, "points": 6, "accountIdLo": "12345678", "battleTag": "疾风剑豪#1234" }
    ]
  }
]
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `gameUuid` | string | 对局唯一标识 |
| `startedAt` | string | 开始时间 |
| `startedAtEpoch` | int | 开始时间（Unix 秒），前端可直接用来算计时 |
| `players` | array | 8 个玩家 |
| `players[].placement` | int/null | 已提交排名为 int，未提交为 null |
| `players[].points` | int/null | 已提交积分为 int，未提交为 null |

---

## 报名队列

### `GET /api/queue`

获取当前报名队列。

**响应示例：**
```json
[
  { "_id": "6612...", "name": "衣锦夜行", "joinedAt": "2026-04-09T23:00:00" },
  { "_id": "6613...", "name": "瓦莉拉", "joinedAt": "2026-04-09T23:01:00" }
]
```

### `POST /api/queue/join`

加入报名队列。

**请求体：**
```json
{ "name": "衣锦夜行" }
```

**响应：**
```json
{ "ok": true, "name": "衣锦夜行", "moved": false }
```

| 字段 | 说明 |
|------|------|
| `moved` | `true` = 满 8 人已移入等待组；`false` = 仍在报名队列中 |

**错误：** 重复报名返回 `400 {"error": "已在报名队列中"}`

### `POST /api/queue/leave`

退出报名队列或等待队列。

**请求体：**
```json
{ "name": "衣锦夜行" }
```

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
      { "name": "衣锦夜行" },
      { "name": "瓦莉拉" },
      { "name": "墨衣" },
      { "name": "安德罗妮" },
      { "name": "驴鸽" },
      { "name": "异灵术" },
      { "name": "岛猫" },
      { "name": "赤小兔" }
    ],
    "createdAt": "2026-04-09T23:10:00"
  }
]
```

- `players` 数组长度为 8 时 = 等待开赛
- 插件检测到 8 人匹配后自动删除该组

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

**错误：**
- `404` 未找到游戏记录（需先用插件打一局）
- `400` 验证码不正确

### `POST /api/login`

登录（验证码同注册时的）。

**请求体：** 同 register

**响应：** `{"ok": true, "battleTag": "...", "displayName": "..."}`

**错误：**
- `404` 未找到记录
- `403` 验证码不正确

### `POST /api/logout`

退出登录（清除 session）。

**响应：** `{"ok": true}`

### `GET /api/verify?battleTag=xxx`

检查某 BattleTag 是否已注册验证。

**响应：** `{"verified": true, "displayName": "南怀北瑾丨少头脑"}` 或 `{"verified": false}`

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

**响应：** `{"ok": true, "updated": 8}`

**错误：**
- `400` 排名数据不完整或有重复
- `404` 对局不存在

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

| 集合 | 说明 |
|------|------|
| `bg_ratings` | 插件上传的玩家分数记录（含验证码） |
| `league_matches` | 联赛对局（8 人完整数据） |
| `league_queue` | 报名队列 |
| `league_waiting_queue` | 等待组（满 8 人自动创建） |
| `league_players` | 已注册选手 |

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
