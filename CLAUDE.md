# CLAUDE.md — AI 开发指南

## 项目概述

炉石传说酒馆战棋联赛网站（Flask + MongoDB + Docker）。淘汰赛版（feat/knockout 分支）。

## 快速导航

| 模块 | 文件 | 说明 |
|------|------|------|
| 入口 | `app.py` | Flask 应用工厂 + 版本号 `WEB_VERSION` |
| 数据库 | `db.py` | `get_db()` 返回 pymongo db 对象 |
| 认证 | `auth.py` | 登录/注册/session/管理员校验 |
| 插件 API | `routes_plugin.py` | check-league / update-placement / upload-rating |
| 淘汰赛 | `routes_tournament.py` | 创建赛事/分组管理/洗牌/晋级/对阵图数据 |
| 积分赛 | `routes_league.py` | 排行榜/对局/队列/等待组 |
| 页面路由 | `routes_pages.py` | 前端页面渲染 |
| 管理后台 | `routes_admin.py` | 管理员面板 API |
| SSE 推送 | `sse.py` | 实时事件推送 |
| 后台清理 | `cleanup.py` | 超时检测/问题对局/webhook 通知 |
| 前端模板 | `templates/` | Jinja2 + Tailwind CSS + ECharts |
| 管理页面 | `templates/admin.html` | 赛事管理/创建/选手管理，**JS 全在此文件内** |

## MongoDB 集合

| 集合 | 用途 |
|------|------|
| `player_records` | 玩家记录 + 验证码（插件写入） |
| `league_matches` | 对局记录（含 tournamentGroupId） |
| `league_players` | 已注册选手 |
| `league_queue` | 报名队列（积分赛） |
| `league_waiting_queue` | 等待组（积分赛） |
| `league_admins` | 管理员 |
| `tournament_groups` | 淘汰赛分组 |
| `tournament_enrollments` | 赛事报名 |

## 淘汰赛匹配流程

```
check-league → 先查 tournament_groups（Lo 集合匹配 + gamesPlayed < boN）
  → 匹配到 → 创建 league_matches（带 tournamentGroupId）
  → 没匹配到 → 查 league_waiting_queue（积分赛）
```

- gameUuid 由服务端 `uuid4()` 生成（客户端不传）
- 积分赛仍用客户端 gameUuid

## 晋级规则

`tournament_groups.advancementRule` 字段控制同分排序方式：

| 规则 | 值 | 排序键 |
|------|------|--------|
| 黄金赛规则（默认） | `golden` | 总积分↓ → 单局最高分↓ → 最后一局分数↓ |
| 吃鸡规则 | `chicken` | 总积分↓ → 吃鸡次数↓ → 最后一局排名↑ |

- 排序逻辑在 `data.py` 的 `SORT_KEYS` 字典中
- `get_group_rankings(db, tournament_name, advancement_rule)` 读取并排序
- `try_advance_group` / `try_advance_round` 晋级时自动使用对应规则
- 修改已有赛事规则：`python scripts/set_advancement_rule.py "赛事名" golden`

## 改代码后必做

1. 更新 `DEV_NOTES.md`（如果有踩坑/新发现）
2. 更新 `README.md` 更新日志
3. `app.py` 中 `WEB_VERSION` 递增
4. commit 中文，`fix:/feat:/docs:/refactor:` 前缀

## 常见坑

- **const 变量不能重新赋值**：`mtSearchSlots` 等用 `const` 声明，清空用 `Object.keys(obj).forEach(k => delete obj[k])`
- **enrolled-players 已优化为批量查询**：不要改回逐个 `find_one`（N+1 问题）
- **时间字符串必须带 Z 后缀**：`to_iso_str()` 统一返回 UTC 带 Z，前端 `new Date()` 才能正确解析
- **ObjectId JSON 序列化**：MongoDB 的 `_id` 是 ObjectId，`jsonify` 前必须 `str(g["_id"])`
- **SSE 哈希比较**：用 `stableStringify`（确定性序列化），不能用 `JSON.stringify`（key 顺序不定）
- **自动推算排名**：7 人提交后自动补第 8 人，少人开打（5-6 人）需全部提交才触发
- **bot 空位过滤**：`isdigit()` 检查 accountIdLo，非数字的不放入 match players

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MONGO_URL` | `mongodb://mongo:27017` | MongoDB 地址 |
| `DB_NAME` | `hearthstone` | 数据库名 |
| `FLASK_SECRET_KEY` | 随机 | Session 密钥 |
| `SITE_NAME` | `酒馆战棋联赛` | 网站名称 |
| `PLUGIN_API_KEY` | _(空)_ | 插件认证 key |
| `MIN_PLUGIN_VERSION` | `0.5.5` | 最低插件版本 |
| `WEBHOOK_URL` | _(空)_ | QQ 机器人 webhook |
| `ENROLL_DEADLINE` | _(空)_ | 报名截止时间（ISO） |

## 本地开发

```bash
pip install -r requirements.txt
python app.py  # werkzeug 单线程，5000 端口
```

## Docker 部署

```bash
docker compose up -d --build
```
