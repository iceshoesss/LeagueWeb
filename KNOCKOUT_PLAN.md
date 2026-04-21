# feat/knockout — 淘汰赛版本开发计划

> 基于 main 分支，改造联赛网站为淘汰赛赛制。插件(HDT_BGTracker)不需要改动。

## 现状

- main 分支是积分赛版本：排行榜 + 报名队列 + 等待队列 + 进行中对局
- 对阵图 HTML 样式已完成（bracket.html 纯前端 mock）

## 改造目标

- 首页从"排行榜+队列+对局"改为**纯对阵图**
- 8 组 × 8 人 = 64 人，每组前 4 名晋级
- 8 组 → 4 组 → 2 组 → 决赛 8 人
- 匹配机制：按预分配的 tournament_group 匹配（不再用自由等待队列）
- 自动晋级：两组都打完后自动创建下一轮分组

## 数据结构

### 新增集合：`tournament_groups`

```json
{
  "_id": ObjectId,
  "round": 1,                    // 轮次 1/2/3/4
  "groupIndex": 1,               // 组号 1-8/1-4/1-2/1
  "status": "waiting",           // waiting / active / done
  "players": [
    {
      "battleTag": "xxx#1234",
      "accountIdLo": "1708070391",
      "displayName": "xxx",
      "placement": null,         // 1-8，null=未提交
      "points": null,            // 积分
      "qualified": false         // 是否晋级
    }
  ],
  "startedAt": null,             // ISO string
  "endedAt": null,
  "nextRoundGroupId": null       // 晋级目标组 ObjectId
}
```

### 改动：`league_matches`

新增字段：
- `tournamentGroupId` — 关联 tournament_groups._id
- `tournamentRound` — 轮次

原有字段不变，兼容积分赛历史数据。

### 不变的集合

- `player_records` — 玩家记录+验证码，不变
- `league_players` — 注册选手，不变
- `league_admins` — 管理员，不变

## API 接口

### 新增

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/bracket` | GET | 返回对阵图数据（所有轮次所有组） |
| `/api/tournament/create` | POST | 管理员创建赛事（指定64人分组） |
| `/api/tournament/group/<id>` | GET | 单组详情 |

### 改动

| 端点 | 改动说明 |
|------|----------|
| `POST /api/plugin/check-league` | 匹配逻辑从"waiting_queue 重叠"改为"查玩家所在 tournament_group" |
| `POST /api/plugin/update-placement` | 提交后检测该组是否全部完成 → 触发晋级逻辑 |

### 退役（页面不再需要，API 保留兼容）

- `GET /api/queue` / `POST /api/queue/join` / `POST /api/queue/leave`
- `GET /api/waiting-queue`
- `GET /api/active-games`
- SSE 端点

## 页面改动

| 页面 | 改动 |
|------|------|
| `/` 首页 | 改为对阵图（bracket.html 模板） |
| `/admin` | 新增「赛事管理」Tab：创建赛事、分配分组 |
| 其他页面 | 不变 |

## 开发阶段

### Phase 1 — 数据层 + API
- [x] 创建 feat/knockout 分支
- [x] 定义 tournament_groups 数据结构
- [x] `GET /api/bracket` 接口（mock 数据）
- [x] 对阵图模板集成到 Flask（数据驱动布局 + 折叠 + 连线）

### Phase 2 — 匹配改造
- [ ] 改造 check-league：按组匹配
- [ ] 改造 update-placement：组级别结算
- [ ] 自动晋级逻辑：两组完成 → 创建下一轮组

### Phase 3 — 管理后台
- [ ] 创建赛事表单（64 人分组）
- [ ] 赛事管理 Tab（查看/编辑/强制结束）
- [ ] 分组编辑（调整玩家到不同组）

### Phase 4 — 首页整合
- [ ] 对阵图接入真实数据
- [ ] 去掉排行榜/报名/队列区块
- [ ] SSE 推送对阵图状态变化

### Phase 5 — 边界处理
- [ ] 弃赛/空位处理（轮空/递补）
- [ ] 并行开打支持
- [ ] 历史赛事归档
