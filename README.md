# LeagueWeb

酒馆战棋联赛网站 — 排行榜、对局记录、淘汰赛对阵图、插件 API。

配套 C# HDT 插件：[HDT_BGTracker](https://github.com/iceshoesss/HDT_BGTracker)

## 项目结构

```
LeagueWeb/
├── app.py              # Flask 后端 API + 页面路由 + 插件端点 + SSE 推送
├── templates/          # Jinja2 模板（Tailwind CSS + ECharts CDN）
│   ├── base.html       # 基础布局
│   ├── bracket.html    # 淘汰赛对阵图（数据驱动 + 折叠 + SVG 连线）
│   ├── index.html      # 首页（排行榜 + 对局 + 队列）
│   └── ...
├── scripts/            # 测试、迁移、工具脚本
│   ├── test_bot_slot.py    # 7 人淘汰赛 bot 空位测试
│   ├── test_knockout.py    # 淘汰赛全流程测试（8 人）
│   ├── test_league.py      # 积分赛全流程测试
│   ├── mock_qualifier.py   # 模拟海选赛事数据
│   ├── toggle-test-mode.py # 切换测试/正常模式
│   ├── enroll_all.py       # 批量报名
│   ├── export_enrollments.py # 导出报名名单
│   ├── migrate_mobile_lo.py  # 手机玩家 Lo 迁移
│   └── migrate_rename_collection.py # 集合重命名迁移
├── Dockerfile
├── docker-compose.yml  # Docker 部署（Flask + MongoDB）
├── API.md              # 接口文档（含插件 API + 淘汰赛 API）
├── KNOCKOUT_PLAN.md    # 淘汰赛开发计划
├── gunicorn.conf.py    # Gunicorn 配置（gevent worker）
├── manage_admins.py    # 管理员管理工具
└── requirements.txt
```

## 快速启动

```bash
docker build -t league-web:latest .
docker compose up -d
```

访问 http://localhost:5000

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MONGO_URL` | `mongodb://mongo:27017` | MongoDB 连接地址 |
| `DB_NAME` | `hearthstone` | 数据库名 |
| `FLASK_SECRET_KEY` | 随机生成 | Session 签名密钥，生产环境建议固定设置 |
| `SITE_NAME` | `酒馆战棋联赛` | 网站名称（导航栏 + 页面标题） |
| `SITE_LOGO` | `🍺` | 网站 Logo，支持 emoji 或图片 URL |
| `MIN_PLUGIN_VERSION` | `0.5.5` | 最低插件版本，低于此版本的插件请求将被拒绝（403） |
| `PLUGIN_API_KEY` | _(空)_ | 插件 API Key，配置后插件请求必须带 `Authorization: Bearer <key>`；为空则跳过校验 |
| `WEBHOOK_URL` | _(空)_ | QQ 机器人 webhook 地址；为空则不发通知 |
| `BOT_API_KEY` | _(空)_ | 机器人调用 API 的认证 token |
| `CLEANUP_INTERVAL` | `60` | 后台清理间隔（秒） |

## 常用命令

```bash
docker compose logs -f web     # 看日志
docker compose down            # 停止
docker compose restart web     # 重启
```

## 两套赛制

本站支持两套独立的赛制，共用同一数据库：

### 积分赛（main 分支）

自由组局模式：
- 玩家在网站报名 → `league_queue` → 满 8 人 → `league_waiting_queue`
- 插件 check-league 匹配 waiting_queue → 创建 `league_matches`
- 排行榜按累计积分排名

### 淘汰赛（feat/knockout 分支）

预分组 BO N 模式：
- 管理员创建赛事，预分配 8 人一组（`tournament_groups`）
- 每组打 N 局（BO3/BO5/BO7），每轮可配置不同
- **不走 waiting_queue**，check-league 直接按 Lo 集合匹配 tournament_groups
- 每局结束累加积分，N 局全部打完按总分排名，前 4 晋级
- 同轮所有组完成后自动创建下一轮分组

**插件不需要改动**——插件只上报 8 个 Lo，判断逻辑全在 Flask 侧。

## BO N 赛制

### 概念

每组可以打 N 局（BO3/BO5/BO7），按 N 局总分排名。

- `boN`：本组打几局（管理员创建赛事时指定，可每轮不同）
- `gamesPlayed`：已完成局数
- `players[].totalPoints`：N 局累计积分
- `players[].games[]`：每局得分明细，如 `[7, 5, 9]`

### 匹配流程

```
玩家进游戏 → 插件 STEP 13 → POST /api/plugin/check-league
  → 先查 tournament_groups（status=waiting + gamesPlayed < boN + Lo 集合匹配）
  → 匹配到 → isLeague=true，创建 league_matches（带 tournamentGroupId）
  → 没匹配到 → 查 league_waiting_queue（积分赛匹配）
  → 都没匹配到 → isLeague=false

游戏结束 → POST /api/plugin/update-placement
  → 更新排名 + 积分
  → 如果关联了 tournament_group → 累加 totalPoints, gamesPlayed+1
  → gamesPlayed < boN → status=waiting（等下一局）
  → gamesPlayed == boN → status=done → 晋级逻辑触发
```

### 积分规则

| 排名 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|------|---|---|---|---|---|---|---|---|
| 积分 | 9 | 7 | 6 | 5 | 4 | 3 | 2 | 1 |

BO N 赛制下每局积分不变，N 局累加为总分。

### 移动端玩家

手机玩家无法使用插件，获取不到 accountIdLo。目前讨论的解决方案：
1. 管理员手动注册时填入 Lo（从同局有插件的玩家获取）
2. 匹配阈值降为 5/8 人匹配即可

## MongoDB 集合

| 集合 | 用途 | 模式 |
|------|------|------|
| `player_records` | 玩家记录 + 验证码 | 通用 |
| `league_matches` | 对局记录 | 通用（淘汰赛新增 `tournamentGroupId`、`tournamentRound`） |
| `league_players` | 已注册选手 | 通用 |
| `league_queue` | 报名队列 | 积分赛 |
| `league_waiting_queue` | 等待组 | 积分赛 |
| `league_admins` | 管理员 | 通用 |
| `tournament_groups` | 淘汰赛分组 | 淘汰赛 |
| `tournament_enrollments` | 赛事报名 | 淘汰赛 |

### tournament_groups 结构

```json
{
  "tournamentName": "2026 春季赛",
  "round": 1,
  "groupIndex": 1,
  "status": "waiting",        // waiting / active / done
  "boN": 3,                   // 本组打几局
  "gamesPlayed": 1,           // 已完成局数
  "players": [
    {
      "battleTag": "xxx#1234",
      "accountIdLo": "1708070391",
      "displayName": "xxx",
      "heroCardId": "TB_BaconShop_HERO_56",
      "heroName": "阿莱克丝塔萨",
      "totalPoints": 7,       // BO 累计积分
      "games": [7],           // 每局得分明细
      "placement": null,      // 最终排名（done 后计算）
      "points": null,         // 最终总分
      "qualified": false,     // 是否晋级
      "eliminated": false,
      "empty": false
    }
  ],
  "nextRoundGroupId": 1,      // 晋级目标组号
  "startedAt": null,
  "endedAt": null
}
```

## 版本号

当前版本：`v0.4.0`（定义在 `app.py` → `WEB_VERSION`）

> 积分赛（main）和淘汰赛（feat/knockout）版本号互不关联，各自递增。

| 分支 | 系统 | 当前版本 |
|------|------|----------|
| `main` | 积分赛 | v0.5.2 |
| `feat/knockout` | 淘汰赛 | v0.13.0 |

修改版本号只需改 `app.py` 中的 `WEB_VERSION = "x.y.z"`，页面底部自动显示。

版本号规则：`主版本.次版本.修订号`
- **修订号 +1** — 修 bug
- **次版本 +1** — 加新功能
- **主版本 +1** — 大改/重构/正式发布

## 更新日志

### v0.13.0 (2026-04-25) — 赛事管理页大数据量优化
- **搜索筛选**：顶部搜索框，支持按组号、标签（A1）、轮次、玩家名即时筛选（200ms 防抖）
- **自适应折叠**：单轮赛事（海选）按排折叠（A1~D1 为一排，4 列布局）；多轮赛事（淘汰赛）按轮次折叠（2 列布局）
- **懒渲染**：搜索组件仅在组卡片可见时初始化，解决 112 组 × 8 人 = 896 个组件同时创建导致的卡顿
- **编号对齐对阵图**：管理页面分组标签改为 A1、B1、C1 等格式，与对阵图一致
- **按钮文字优化**：补录/晋级按钮去掉图标，直接显示文字
- **弹窗加宽**：`max-w-7xl`，适配 4 列网格

### v0.12.0 (2026-04-25) — 分组标签优化 + 对阵图缓存 + 导出增强
- **分组标签改为 ABCD 4 字母循环**：一个裁判管 4 组，标签从 A1-H1 改为 A1-D1
- **对阵图数据缓存**：`build_bracket_data()` 加 5 秒内存缓存，状态变化时主动失效，解决对局多时页面卡顿
- **导出报名名单增强**：新增 `--with-lo` 模式（从 league_players/player_records 查询 Lo）、`--missing-lo` 查找缺 Lo 玩家

### v0.11.0 (2026-04-25) — 报名锁定 + 自动分组
- **截止后名单锁定**：报名截止后禁止新报名和退赛，替补名单保留不标记 expired
- **取前N人自动分组**：海选赛取前 N 人后自动创建 N/8 个分组，不用手动添加
- **使用指南下载链接**：bg_tool 和 HDT 插件添加蓝奏云下载地址

### v0.10.5 (2026-04-25) — 手动补录 + 修改排名
- **纯手工补录对局结果**：插件失效时管理员手动录入排名，创建 match 记录 + 计算积分 + 触发晋级
- **对局管理支持修改排名**：已完成的对局显示「修改」按钮，解锁所有排名下拉框，覆盖已有排名后聚合管道自动重算
- **手动晋级按钮简化**：去掉 `manualAdvance` 标志，等待中/进行中的组直接显示按钮
- **海选赛手动晋级修复**：不创建下一轮分组，只标记 qualified/eliminated
- **手动晋级限制 4 人**：前端 + 后端双重校验
- **手动晋级弹窗显示完整 battleTag**：带 #tag 方便辨识

### v0.10.4 (2026-04-25) — 手动晋级简化
- **手动晋级按钮改为针对等待中/进行中的组**：已完成的组已自动晋级，不需要手动操作
- 去掉  标志：不再需要预设复选框，直接在组卡片上显示按钮
- 手动晋级后自动把源组标记为 done

### v0.10.3 (2026-04-25) — 淘汰赛 bot 空位修复
- **修复 bot 空位进入 match 导致自动推算错误**：check-league 创建 match 时 `isdigit()` 过滤 bot 空位（accountIdLo 非数字），不再放入 players
- **自动推算条件恢复**：从 `len(remaining)==len(null_indices)` 改回 `len(null_indices)==1`，只在剩 1 个空位时触发
- **排行榜聚合排除历史 bot 数据**：`$nin` 过滤加 `"None"` 字符串
- 测试/迁移脚本移入 `scripts/` 目录

### v0.10.2 (2026-04-24) — 淘汰赛 BO 连续对局修复
- **修复 gamesPlayed 提前递增导致 BO2+ 匹配失败**：check-league 创建 match 时不再 $inc gamesPlayed，改为 update-placement 对局结束时才递增
- **修复自动推算只补 1 个空位**：改为剩余空位数 == 剩余排名数时全部补上，支持 6 人测试（2 个待定空位自动推算）

### v0.10.0 (2026-04-24) — 服务端生成 gameUuid（淘汰赛）
- **淘汰赛 gameUuid 改为服务端生成**：修复同一局不同玩家 bg_tool 生成不同 UUID 导致匹配失败的 bug
- 第一个玩家 check-league 匹配淘汰赛组后，服务端用 `uuid4()` 生成 UUID，通过 upsert 创建 match
- 后续玩家 check-league 通过 upsert 找到已有 match，返回同一个 UUID
- 所有玩家（5~8 人）都能成功 check-league，不再受 BO 数限制
- check-league 响应新增 `gameUuid` 字段，客户端使用服务端返回的 UUID
- 积分赛仍使用客户端 gameUuid，不受影响

### v0.9.4 (2026-04-24) — 报名进度环修复
- 进度环改为显示所有报名人数（正选+替补），分母保持 1024

### v0.9.3 (2026-04-24) — 进度环分母修复
- 进度环分母从写死 1024 改为动态 ENROLL_SLOTS（896）

### v0.9.2 (2026-04-24) — BO1 淘汰赛后续玩家匹配修复
- 修复 BO1 淘汰赛后续玩家 check-league 匹配失败：前置检查已有 match + fallback 日志

### v0.9.1 (2026-04-24) — 正选名额 + 取前N人修复
- 正选名额 896 人，超出进替补，cap 仍显示 1024
- 取前N人改用 enrollAt 排序，修复退赛导致 position 空洞少取人

### v0.9.0 (2026-04-24) — 多赛事按创建时间排序
- 多个 bracket 赛事时只显示最后创建的（512强出现后隐藏海选 bracket）
- 创建赛事时写入 `createdAt` 字段，排序依据从 `startedAt` 改为 `createdAt`
- mock_qualifier.py 的 createdAt 固定为早期时间，避免干扰排序

### v0.8.0 (2026-04-24) — 512强自动隐藏海选
- 有 bracket 布局赛事时自动隐藏 grid 海选网格

### v0.7.0 (2026-04-24) — 种子选手 + 海选晋级洗牌 + 平铺网格取前N人
- 选手管理页新增种子选手 toggle 按钮（league_players.isSeed 字段）
- 创建赛事新增「🎲 海选晋级洗牌」：从已完成赛事晋级者 + 种子选手池洗牌分配
- 创建平铺网格赛事新增「取前N人」：输入 N 自动取报名前 N 人缩小选手池
- 新增 `/api/tournament/qualifier-pool` 接口获取晋级者+种子选手池
- `/api/admin/enrolled-players` 支持 `limit` 参数

### v0.5.0 (2026-04-23) — 手机玩家支持 + BO 选项扩展
- 手机玩家注册时 accountIdLo 用 battleTag 作为伪 Lo，对阵图正常显示
- check-league 匹配时跳过非数字 Lo（`lo.isdigit()`），不影响子集匹配
- 登录/注册时伪 Lo 变真 Lo 自动同步 tournament_groups + league_matches 历史记录
- BO 选项扩展到 1-7（创建赛事 + 编辑分组）
- 新增 migrate_mobile_lo.py 迁移脚本（修复已有空 Lo 数据）

### v0.4.0 (2026-04-23) — 代码重构 + Bug 修复
- app.py 拆分为 11 个模块（Blueprint 架构），最大单文件 797 行
- 修复 SSE bracket 端点返回简化快照导致对阵图闪一下就消失
- 修复洗牌重复分配：清空 ctSearchSlots 避免复用脱离 DOM 的旧实例
- 洗牌/创建赛事/选手列表三层去重保护

### v0.3.0 (2026-04-23) — 赛事报名系统
- 新增报名入口页面（`/enroll`），正选 1024 人上限 + 替补队列
- 报名/退赛/状态 API（`/api/enroll`、`/api/enroll/withdraw`、`/api/enroll/status`）
- 报名截止定时触发（环境变量 `ENROLL_DEADLINE`，ISO 时间格式）
- 正选退出后替补自动补上（按报名时间顺序）
- 截止后禁止新报名和退赛，替补仍可被补上
- 管理员查看报名列表 API（`/api/admin/enrolled`，含 accountIdLo）
- 导航栏新增"📢 报名参赛"入口
- 新增 `tournament_enrollments` 集合

### v0.2.0 (2026-04-23) — 首页整合 + 少人开打 + 管理优化
- 首页改为淘汰赛对阵图（积分赛首页备份为 index_league.html）
- 去掉首页大标题，直接展示对阵图
- 匹配逻辑改为 Lo 集合子集匹配（issubset），支持 5-8 人少人开打
- 手机玩家支持（无 Lo 不影响匹配，管理员手动补录排名）
- 管理员手动添加选手（POST /api/admin/player/add）
- 创建/管理赛事选手选择从下拉条改为搜索输入框（支持上千人）
- 导航栏添加 QQ 绑定按钮（全局可用，🔗 一键生成绑定码）
- 删除按序分配功能

### v0.1.5 (2026-04-22) — Bug 修复
- 修复 check-league 构建 players 时空 battleTag 覆盖 fallback：HearthMirror 只有本地玩家有 Name，插件发送的 players dict 中其他人 battleTag 为空串，服务端 `detail.get("battleTag", fallback)` 因 detail 存在返回空串覆盖了等待组的正确名字。改为三级 fallback（请求数据 → 等待组 name → player_records.playerId 查库，确保带 #tag 的完整 battleTag）

### v0.1.4 (2026-04-22) — Bug 修复
- 修复 tournamentGroupId (ObjectId) 导致管理面板对局列表 JSON 序列化失败
- 修复 BO 完成时 `now_str` 未定义导致 update-placement 500 错误（BO 进度卡住根因）
- 对阵图卡片增大：CARD_W 200→280, ROW_H 38→48
- 活跃组显示英雄头像（从当前 league_matches 注入）和死亡灰化
- SSE 哈希比较改为确定性序列化（stableStringify），修复等待中→进行中不推送
- 名称优先显示带 tag 的 battleTag
- check-league / update-placement 增加 BO 进度诊断日志

### v0.1.3 (2026-04-22) — Phase 3 完成
- **管理后台赛事管理 Tab**：创建赛事表单 + 赛事列表 + 管理/删除
- **创建赛事简化**：只填第一轮分组 + BO，后续轮次自动 n/2 生成
- **每轮独立 BO 设置**：创建时可为每轮指定不同 BO
- **动态轮次名**：第 N 轮 → 半决赛 → 决赛，根据总轮数计算
- **自动分组**：按组数×8 从注册选手列表顺序取人
- **确定性随机烟牌**：SHA256 seed + Fisher-Yates，人人可验证
- **窝要烟牌页面**：`/verify-shuffle` 公开验证页 + 独立 Python 脚本
- **赛事管理**：编辑分组玩家/BO、删除赛事
- 新增 API：`/api/tournaments`、`/api/admin/players-all`、`/api/tournament/manage/<name>`、`/api/tournament/group/<id>/update`、`/api/tournament/<name>` DELETE、`/api/tournament/shuffle`

### v0.1.2 (2026-04-22)
- BO 进度移到卡片头部右侧，和状态 badge 右对齐
- 淘汰时头像一起变灰（grayscale + opacity）
- done 状态淘汰者名字恢复白色，不再灰色
- waiting/done 状态不显示英雄头像（不在游戏中无头像）
- 积分在所有状态下都显示
- nextRoundGroupId 改为自动计算（ceil(groupIndex/2)），管理员无需手动指定
- 测试脚本移除报名步骤（淘汰赛不需要 queue/join）
- 修复 mock 数据 R2 淘汰者未标记 eliminated

### v0.1.1 (2026-04-22) — Phase 2 完成
- **check-league 淘汰赛匹配**：先查 tournament_groups（Lo 集合匹配 + gamesPlayed < boN），匹配不到再走 waiting_queue
- **update-placement BO 累计**：对局结束后累加积分到 tournament_groups，gamesPlayed+1
- **自动晋级**：同轮所有组 done 后自动创建下一轮分组（前 4 名晋级）
- **创建赛事 API**：`POST /api/tournament/create`，管理员指定分组 + 每轮 boN
- **对阵图改为真实数据**：`_build_bracket_data()` 从 tournament_groups 集合读取
- 对阵图折叠改为 `..` 标签（round-title 样式），必须从左到右折叠、从右到左展开
- 等待中小组不显示排名序号和积分

### v0.1.0 (2026-04-21) — 淘汰赛版首发
- 淘汰赛对阵图（`/bracket`）：数据驱动布局，SVG 连线，已完成轮次可折叠
- 多赛事支持（tournaments 数组结构）
- mock 数据对齐 tournament_groups 真实结构

### v0.5.2 (2026-04-21) — 积分赛
- 修复选手管理页日期显示为 "-"

### v0.5.1 (2026-04-21) — 积分赛
- 超级管理员系统、manage_admins.py

### v0.5.0 (2026-04-21) — 积分赛
- 管理员面板（`/admin`）：总览/对局管理/选手管理/队列管理

## 待办

- [ ] Phase 3 — 管理后台（创建赛事表单、赛事管理 Tab）— 进行中
- [ ] Phase 4 — 首页整合（对阵图接入真实数据、移除积分赛 UI）
- [ ] Phase 5 — 边界处理（弃赛/递补/历史归档）
- [ ] CSRF 防护
- [ ] HTTPS（Cloudflare Tunnel）

## API 文档

详见 [API.md](API.md)
