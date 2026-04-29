# feat/knockout — 淘汰赛版本开发计划

> 基于 main 分支，改造联赛网站为淘汰赛赛制。插件(HDT_BGTracker)不需要改动。

## 改造目标

- 首页改为**纯对阵图**
- 预分组 BO N 赛制（每轮可配置不同 BO）
- 匹配机制：按预分配的 tournament_group 匹配（Lo 集合子集匹配，支持少人开打）
- 自动晋级：同轮所有组打完后自动创建下一轮分组

## 数据结构

### `tournament_groups`

```json
{
  "tournamentName": "2026 春季赛",
  "round": 1,
  "groupIndex": 1,
  "status": "waiting",        // waiting / active / done
  "boN": 3,
  "gamesPlayed": 1,
  "players": [{ "battleTag", "accountIdLo", "displayName", ... }],
  "nextRoundGroupId": null,
  "startedAt": null,
  "endedAt": null
}
```

排名数据不存储在 tournament_groups 中，从 league_matches 按 tournamentGroupId 聚合计算。

### `league_matches` 新增字段

- `tournamentGroupId` — 关联 tournament_groups._id
- `tournamentRound` — 轮次

### 不变的集合

- `player_records`、`league_players`、`league_admins`

## 匹配逻辑

BO 系列赛**不走 league_waiting_queue**，直接在 tournament_groups 内部匹配：

```
check-league → 查 tournament_groups（status=waiting + gamesPlayed < boN）
  → 组内有效 Lo ⊆ 游戏 Lo（issubset，最少 5 个有效 Lo）
  → 匹配到 → 创建 league_matches（带 tournamentGroupId）
  → 没匹配到 → 查 league_waiting_queue（积分赛）
```

- 支持少人开打（5-8 人），缺失位由 bot 填充
- 手机玩家（无 Lo）不影响匹配，相当于隐形人
- 自动推算仅 7/8 触发，少人情况需管理员手动补录或等全员提交

## 积分规则

| 排名 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|------|---|---|---|---|---|---|---|---|
| 积分 | 9 | 7 | 6 | 5 | 4 | 3 | 2 | 1 |

BO N 下每局积分不变，N 局累加。

## 开发阶段

### Phase 1 — 数据层 + API ✅
- [x] 创建 feat/knockout 分支
- [x] 定义 tournament_groups 数据结构
- [x] `GET /api/bracket` 接口
- [x] 对阵图模板（数据驱动布局 + 折叠 + SVG 连线）

### Phase 2 — 匹配改造 ✅
- [x] check-league：按 tournament_groups 组匹配（Lo 集合子集匹配）
- [x] update-placement：BO 累计积分 + 组级别结算
- [x] 自动晋级：同轮全部 done → 创建下一轮分组

### Phase 3 — 管理后台 ✅
- [x] 创建赛事表单（搜索选择选手，支持上千人）
- [x] 赛事管理 Tab（查看/编辑分组/BO/删除赛事）
- [x] 分组编辑（调整玩家到不同组）
- [x] 确定性随机烟牌（SHA256 seed + Fisher-Yates）
- [x] 管理员手动添加选手（手机玩家/无插件玩家）
- [x] QQ 绑定码（导航栏全局入口）

### Phase 4 — 首页整合 ✅
- [x] 首页改为对阵图（去掉排行榜/队列/对局）
- [x] SSE 推送对阵图状态变化（`/api/events/bracket`）
- [x] 积分赛首页备份为 `index_league.html`

### Phase 5 — 边界处理 ✅
- [x] 少人开打支持（5-8 人，issubset 匹配）
- [x] 手机玩家支持（无 Lo 不影响匹配，管理员手动补录排名）
- [x] 赛事报名系统（报名 + 1024 名额上限 + 替补队列 + 定时截止）
- [x] 赛事归档（tournaments 集合 + archive/unarchive API + 历史赛事页面 + 迁移脚本）

## 待办

- [x] 赛事归档
- [ ] CSRF 防护（优先级低，等 HTTPS 后再考虑）
- [ ] ~~HTTPS（Cloudflare Tunnel）~~ 备案中，暂不做
- [ ] 安全加固 — 密码系统（见下方，比赛结束后实施）

## 安全加固计划 — 密码系统（比赛后）

### 问题

当前 `upload-rating` 接口既写数据又返回验证码，攻击者反编译 DLL 拿到 API key 后，
可以调用 `upload-rating` 传任意 playerId + accountIdLo 获取验证码，冒充任意玩家登录。

### 方案

验证码改为随机一次性，新增密码系统：

1. `upload-rating` 返回**随机验证码**（一次性，5 分钟过期）
2. 玩家首次在网站注册时输入验证码 + **设置密码**，验证码作废
3. 后续登录使用 **BattleTag + 密码**，不再需要验证码

### 改动点

**服务端（LeagueWeb）：**
- `upload-rating`：验证码改为随机生成，写入 `player_records` 并加 `expireAt` 字段（TTL 索引自动清理）
- `POST /api/register`：验证码 + 密码 → 注册成功后验证码作废，密码哈希存入 `league_players`
- `POST /api/login`：改为 BattleTag + 密码登录
- `player_records` 新增字段：`verificationCodeExpire`（datetime）

**插件（HDT_BGTracker / bg_tool）：**
- 插件侧无须改动（验证码仍然从 upload-rating 响应获取）
- 首次注册后验证码作废，后续登录用密码

### 防护效果

- 攻击者有 API key → 能生成验证码 → 但 5 分钟过期
- 玩家自己注册后设密码 → 验证码作废 → 攻击者无法接管
- 密码不在插件/二进制中，攻击者拿不到
