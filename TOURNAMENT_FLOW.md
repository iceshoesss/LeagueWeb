# 淘汰赛流程说明

## 1. 管理员创建赛事

管理员指定：
- 赛事名称（如"2026 春季赛"）
- 第一轮分组（8 组 × 8 人）
- 每轮 BO 几（BO3/BO5/BO7）

分组由系统按 `ceil(groupIndex/2)` 自动配对，管理员无需手动指定晋级目标。

---

## 2. 匹配（check-league）

玩家进游戏，插件在第一轮战斗结束（STEP 13）时自动调用 `check-league`，传本局 8 个玩家的 `accountIdLo`。

匹配逻辑：

```
查所有 tournament_groups（status=waiting 或 active，且 gamesPlayed < boN）
  → 逐个比较 accountIdLo 集合
  → 完全匹配 → isLeague=true，创建对局记录
  → 没匹配到 → isLeague=false，不记录
```

匹配成功后，该组状态从 `waiting` 变为 `active`。

---

## 3. 对局结束（update-placement）

游戏结束，插件逐个提交每个玩家的排名（1-8）。

- 7 人提交后，系统自动推算第 8 人排名
- 8 人齐全后，对局结束

**BO 累计**：

```
每个玩家的本局积分 → 写入 league_matches（players[].points）
gamesPlayed + 1（tournament_groups 仅记元数据）

if gamesPlayed < boN:
  status = "waiting"  → 等下一局，回到步骤 2
if gamesPlayed == boN:
  status = "done"  → 触发晋级检查
```

注意：totalPoints 和排名**不再存储在 tournament_groups 中**，而是从 league_matches 按 tournamentGroupId 聚合计算。tournament_groups 只存身份信息和元数据（boN/status/gamesPlayed）。

---

## 4. 晋级

BO 全部打完后，系统检查本轮所有组是否都完成：

- 未全部完成 → 等其他组打完
- 全部完成 → 按 `ceil(groupIndex/2)` 分桶，每桶取前 4 名（从 league_matches 聚合 totalPoints 降序），合并成 8 人，创建下一轮

```
Round 1: 8 组 × BO3
  A组前4 + B组前4 → Round 2 ①组
  C组前4 + D组前4 → Round 2 ②组
  E组前4 + F组前4 → Round 2 ③组
  G组前4 + H组前4 → Round 2 ④组

Round 2: 4 组 × BO3
  ①组前4 + ②组前4 → Round 3 ①组
  ③组前4 + ④组前4 → Round 3 ②组

Round 3: 2 组 × BO5
  ①组前4 + ②组前4 → Round 4 决赛

Round 4: 决赛 × BO7
  → 打完 → 冠军诞生
```

最后一轮打完后没有下一轮，赛事结束。

---

## 积分规则

| 排名 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|------|---|---|---|---|---|---|---|---|
| 积分 | 9 | 7 | 6 | 5 | 4 | 3 | 2 | 1 |

公式：`points = placement == 1 ? 9 : max(1, 9 - placement)`

BO N 赛制下每局积分不变，N 局累加为总分，按总分排名决定晋级。
