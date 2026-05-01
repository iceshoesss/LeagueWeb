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
│   ├── test_*.py           # 各类测试脚本（bot_slot/knockout/league/grid/...）
│   ├── mock_qualifier.py   # 模拟海选赛事数据
│   ├── toggle-test-mode.py # 切换测试/正常模式
│   ├── enroll_all.py       # 批量报名
│   ├── export_*.py         # 导出脚本（报名/分组/晋级者/种子选手）
│   ├── migrate_*.py        # 数据迁移脚本
│   └── set_advancement_rule.py # 修改已有赛事晋级规则
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

当前版本：`v0.17.7`（定义在 `app.py` → `WEB_VERSION`）

> 积分赛（main）和淘汰赛（feat/knockout）版本号互不关联，各自递增。

| 分支 | 系统 | 当前版本 |
|------|------|----------|
| `main` | 积分赛 | v0.5.2 |
| `feat/knockout` | 淘汰赛 | v0.17.7 |

修改版本号只需改 `app.py` 中的 `WEB_VERSION = "x.y.z"`，页面底部自动显示。

版本号规则：`主版本.次版本.修订号`
- **修订号 +1** — 修 bug
- **次版本 +1** — 加新功能
- **主版本 +1** — 大改/重构/正式发布

## 更新日志
### v0.17.10 (2026-05-01) — MongoDB 连接池安全网
- 连接池添加 `waitQueueTimeoutMS=3s`，慢查询占满连接时快速失败而非无限排队

### v0.17.9 (2026-05-01) — SSE 性能优化 + MongoDB 查询修复
- **SSE 共享缓存**: event 触发时仅 1 个 greenlet 查 MongoDB，其余读缓存，查询量降 80%
- **移除重复 SSE**: base.html 导航栏角标不再独立建 SSE 连接，复用页面已有连接
- **修复 event.clear() 导致实时更新延迟**: 改用 generation 计数器判断数据变化，对阵图秒级更新
- **tournament_groups 添加 (round, groupIndex) 索引**: 修复 build_bracket_data() 全表扫描（COLLSCAN → IXSCAN）
- **简化 endedAt 查询**: 去掉冗余的 `$or + $exists`，修复 MongoDB plan cache 选低效计划（planning 21 秒→毫秒级）

### v0.17.7 (2026-04-30) — 支持 ICP 备案号显示
- 新增 `ICP_NUMBER` 环境变量，设置后 footer 显示备案号链接
- 默认为空（兼容海外部署）

### v0.17.2 (2026-04-27) — 竞态条件修复，防 gamesPlayed 重复递增
- `update-placement` / `manual-record` 玩家排名写入改为原子条件更新
- 对局终结（endedAt）改为原子操作，防止并发请求重复触发 BO 累计
- 自动推算+终结合并为一步原子操作
- 回退 v0.16.0/v0.16.1 的后台预计算和异步晋级（引入的 bug 多于收益）

### v0.17.1 (2026-04-27) — 淘汰赛大轮子折叠修复
- **淘汰赛大轮嵌套折叠**: 第一轮 64 组展开后内部按 A1-D1、A2-D2 子折叠，第二轮起按数字编号（1~4、5~8...）
- **默认全折叠**: 所有轮次和子段默认折叠，避免大量搜索框同时渲染卡顿
- **>=16 组触发子折叠**: 任何轮次组数 ≥16 都会子折叠，展开轮次后子段保持折叠
- **子段互斥**: 同一轮内展开一个子段时自动收起其他子段，始终最多渲染 4 组
- 海选按排折叠逻辑不受影响

### v0.17.0 (2026-04-27) — 性能优化 + 管理效率提升
- **后台预计算**: 排行榜、最近对局、进行中对局改为后台线程定期刷新，页面加载直接读缓存免等 MongoDB 聚合
- **管理员补录异步**: 补录/修改排名的晋级计算和 rankings 重算改为后台执行，提交后立刻返回响应
- **批量添加分组**: 创建淘汰赛支持输入组数一键批量添加，64 组不再需要手动点 64 次
- gunicorn workers 4→5（双核 2×CPU+1）

### v0.16.0 (2026-04-26) — SSE 事件驱动，告别轮询
- **性能优化**: SSE 从每 2 秒轮询 MongoDB 改为事件驱动，无数据变更时不查询数据库
  - 每个 SSE 端点绑定 `threading.Event`，写入操作完成后主动触发信号
  - 高并发场景下 MongoDB 查询频率从 125+ 次/秒降至接近 0
  - 数据变更时即时推送，比轮询响应更快
  - 兜底轮询间隔 10 秒，防止极端情况下事件丢失
  - 前端代码零改动，传输方式仍为 SSE
- 触发源覆盖：check-league / update-placement / 队列操作 / 管理员操作 / 淘汰赛操作 / 后台清理线程
- 分页省略号模式变量名冲突修复（v0.15.1）

### v0.15.0 (2026-04-26) — 对局管理界面优化 + 搜索
- 对局管理改为紧凑单行卡片布局，玩家名字带排名标签（1金4绿）
- 新增按选手名字搜索筛选功能（模糊匹配，不区分大小写）
- manual-record 创建对局使用当前时间，不再复用组的 startedAt

### v0.14.9 (2026-04-26) — 事件驱动排名缓存，对阵图零聚合
- 淘汰赛各组排名数据改为事件驱动缓存（`tournament_groups.rankings` 字段）
- 每局结束 / 管理员操作时单组重算并写入，对阵图页面直接读取不再跑聚合管道
- 解决海选赛组数多时对阵图页面卡顿问题
- 新增迁移脚本 `scripts/migrate_rankings.py`（部署后需先跑一次）

### v0.14.8 (2026-04-26) — 优先复用超时/掉线对局
- **Bug 修复**: 当对局超时时管理员界面补录排名会新建对局
  - 详见d8dfed798952d70e42c8d33e530ace5e70fbabb9
### v0.14.7 (2026-04-26) — 修复观战 bug 导致排行榜数据污染

- **Bug 修复**: 观战时插件将观战者名字写入对局记录，排行榜按 `battleTag` 聚合导致观战者名字成为独立选手条目
  - `get_players()` 改为按 `accountIdLo` 分组，身份信息统一从 `league_players` 获取
  - `get_player()` 同步修复，不再依赖 `league_matches` 中的 `battleTag`/`displayName`
- **回退**: 撤回 v0.14.6 问题对局浮动角标功能（存在 bug，后续修复后重新上线）

### v0.14.6 (2026-04-26) — 问题对局浮动角标 + 展开面板（已撤回）

- **新功能**: 所有页面右下角浮动角标，实时显示问题对局数量（SSE 推送）
- 点击角标展开面板，显示问题对局列表（超时/掉线类型、时间、涉及玩家、补录按钮）
- 鼠标移出面板区域自动收回，0 个问题对局时角标隐藏
- 新增 `GET /api/problems` 接口，返回问题对局 JSON 数据

### v0.14.5 (2026-04-26) — 性能优化：SSE 连接管理 + MongoDB 索引

- **Bug 修复**: 页面跳转时 SSE 连接未关闭，导致服务端僵尸 generator 堆积，反复切换页面后 CPU 飙升、响应变慢
  - `base.html` 添加 `window.__sse_list` 全局追踪 + `beforeunload` 统一清理
  - 所有页面的 EventSource 创建后注册到全局列表，跳转时自动关闭
- **性能优化**: 添加 MongoDB 索引，消除全集合扫描
  - `league_matches`: `tournamentGroupId+endedAt`、`endedAt+startedAt`、`gameUuid`（唯一）
  - `tournament_groups`: `tournamentName+round+groupIndex`、`status`
  - 其他集合（player_records、league_players 等）唯一索引
- **性能优化**: `build_bracket_data()` waiting 组复用 `get_group_rankings` 已有数据，消除逐组聚合
- **开发体验**: `app.py` 本地开发开启 `threaded=True`，SSE 不再阻塞其他请求

### v0.14.4 (2026-04-26) — 对阵图/海选卡片玩家名可点击

- 对阵图/海选分组卡片玩家名可点击跳转选手详情页
- 撤回首页最近对局卡片的玩家名链接（嵌套 `<a>` 导致性能问题）

### v0.14.3 (2026-04-26) — 修复对阵图排序不遵循晋级规则

- **Bug 修复**: `build_bracket_data()` 中 done 组和 waiting 组的排序硬编码为吃鸡规则，无视组的 `advancementRule` 字段
- 现在对阵图/海选视图会根据每组的 `advancementRule`（`chicken` 或 `golden`）使用正确的排序逻辑
- waiting 组聚合管道扩展：新增 `maxGamePoints`、`lastGamePoints`、`chickens` 字段

### v0.14.2 (2026-04-25) — 支持多套晋级规则
- 创建赛事时可选择晋级规则（吃鸡规则 / 黄金赛规则），默认黄金赛规则
- 吃鸡规则：总积分 → 吃鸡次数 → 最后一局排名
- 黄金赛规则：总积分 → 单局最高分 → 最后一局分数
- 管理弹窗标题和分组卡片显示当前规则
- 添加 `scripts/set_advancement_rule.py` 脚本修改已有赛事规则
- 新增 `advancementRule` 字段存储于 `tournament_groups`

### v0.14.0 (2026-04-25) — 创建赛事选手加载修复 + enrolled-players 性能优化
- **enrolled-players N+1 查询修复**：报名选手列表从逐个查 league_players 改为批量 `$in` 查询，1000 人从 1001 次查询降为 2 次
- **创建赛事搜索框防并发**：快速切换布局或多次打开弹窗时旧请求自动作废，防止数据错乱
- **搜索框焦点重试**：数据为空时点击搜索框自动重新加载选手列表
- **radio 事件去重**：布局切换事件只绑定一次，避免每次打开弹窗叠加触发
- **加载失败自动重试**：最多重试 3 次，间隔 2 秒

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

### v0.1.0 ~ v0.9.4 (2026-04-21 ~ 2026-04-24) — 早期迭代

<details>
<summary>展开完整记录</summary>

#### 淘汰赛 (feat/knockout)

- **v0.9.4** — 报名进度环修复（正选+替补）
- **v0.9.3** — 进度环分母修复
- **v0.9.2** — BO1 后续玩家匹配修复
- **v0.9.1** — 正选名额 + 取前N人修复
- **v0.9.0** — 多赛事按创建时间排序
- **v0.8.0** — 512强自动隐藏海选
- **v0.7.0** — 种子选手 + 海选晋级洗牌 + 平铺网格取前N人
- **v0.5.0** — 手机玩家支持 + BO 选项扩展
- **v0.4.0** — 代码重构（app.py 拆分 11 模块）+ Bug 修复
- **v0.3.0** — 赛事报名系统（正选 1024 人 + 替补队列 + 截止锁定）
- **v0.2.0** — 首页整合对阵图 + 少人开打（5-8 人 Lo 子集匹配）+ 管理优化
- **v0.1.5** — check-league 空 battleTag 覆盖 fallback 修复
- **v0.1.4** — tournamentGroupId 序列化 + BO 完成 500 修复
- **v0.1.3** — Phase 3：管理后台 + 创建赛事 + 确定性洗牌
- **v0.1.2** — 对阵图卡片 UI 细节
- **v0.1.1** — Phase 2：check-league 淘汰赛匹配 + 自动晋级
- **v0.1.0** — 淘汰赛版首发：对阵图 + SVG 连线

#### 积分赛 (main)

- **v0.5.2** — 修复选手管理页日期显示
- **v0.5.1** — 超级管理员系统
- **v0.5.0** — 管理员面板（总览/对局/选手/队列）

</details>

## 待办

- [ ] 边界处理（弃赛/递补/历史归档）
- [ ] CSRF 防护
- [ ] HTTPS（Cloudflare Tunnel）
- [ ] 问题对局浮动角标重新上线（v0.14.6 撤回，待修复后重新上线）
- [ ] 观战者身份修正（服务端 check-league 不信任插件传的 battleTag，从 player_records 按 accountIdLo 查库）

## API 文档

详见 [API.md](API.md)
