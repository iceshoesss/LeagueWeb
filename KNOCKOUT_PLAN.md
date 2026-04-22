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

### Phase 5 — 边界处理 🔶
- [x] 少人开打支持（5-8 人，issubset 匹配）
- [x] 手机玩家支持（无 Lo 不影响匹配，管理员手动补录排名）
- [ ] 赛事报名系统（报名 + 名额上限 + 替补队列）
- [ ] 赛事归档（当前单赛事场景暂不需要）

## 待办

- [ ] 赛事报名系统（报名入口 + 1024 人上限 + 替补）
- [ ] 赛事归档（多赛事场景）
- [ ] CSRF 防护
- [ ] HTTPS（Cloudflare Tunnel）
