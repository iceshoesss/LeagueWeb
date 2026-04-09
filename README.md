# 联赛网站 (league/)

酒馆战棋联赛排行榜 + 报名系统 + 对局管理。

## 技术栈

- **后端**: Flask + PyMongo
- **前端**: Jinja2 模板 + Tailwind CSS CDN + 原生 JS
- **数据库**: MongoDB（与插件共用 `hearthstone` 库）
- **部署**: gunicorn + gevent（Linux）/ Flask dev server（Windows 开发）

## 快速开始

### 本地开发（Windows）

```bash
cd league
pip install -r requirements.txt
python app.py
# 访问 http://localhost:5000
```

### 生产部署（Linux）

```bash
cd league
pip install -r requirements.txt
gunicorn -c gunicorn.conf.py app:app
```

### 配置

`app.py` 中修改 MongoDB 连接地址：
```python
MONGO_URL = "mongodb://YOUR_MONGO_HOST:27017"
```

## 目录结构

```
league/
├── app.py                  # Flask 主应用（路由 + API + SSE）
├── gunicorn.conf.py        # gunicorn 配置
├── requirements.txt        # Python 依赖
├── mock-data/              # 早期 mock 数据（已废弃，数据从 MongoDB 读取）
└── templates/
    ├── base.html           # 基础模板（导航栏 + 样式）
    ├── index.html          # 首页（排行榜 + 对局 + 队列 + SSE）
    ├── player.html         # 选手详情页
    ├── match.html          # 对局详情页
    ├── match_edit.html     # 对局补录页（问题对局手动填排名）
    ├── register.html       # 注册/登录页
    └── problems.html       # 问题对局管理页
```

## 页面说明

| 路由 | 功能 |
|------|------|
| `/` | 排行榜 + 最近对局 + 进行中对局 + 报名/等待队列 |
| `/player/<battleTag>` | 选手详情：积分/胜率/历史对局/对手统计 |
| `/match/<gameUuid>` | 对局详情：8 人排名/英雄/积分 |
| `/match/<gameUuid>/edit` | 问题对局补录：手动填写缺失排名 |
| `/register` | 注册/登录（BattleTag + 验证码） |
| `/problems` | 问题对局列表（超时/掉线/数据缺失） |

## API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/players` | GET | 排行榜数据 |
| `/api/active-games` | GET | 进行中的对局 |
| `/api/matches` | GET | 最近对局 |
| `/api/queue` | GET | 报名队列 |
| `/api/waiting-queue` | GET | 等待队列 |
| `/api/queue/join` | POST | 报名（需登录） |
| `/api/queue/leave` | POST | 退出（需登录） |
| `/api/register` | POST | 注册验证 |
| `/api/login` | POST | 登录 |
| `/api/events/active-games` | SSE | 进行中对局推送 |
| `/api/events/queue` | SSE | 报名队列推送 |
| `/api/events/waiting-queue` | SSE | 等待队列推送 |

## 数据流

```
C# 插件 → MongoDB:
  bg_ratings (玩家分数 + 验证码)
  league_matches (联赛对局 + players 数组)
  league_waiting_queue (等待组)

Flask ← MongoDB:
  排行榜 = league_matches 聚合
  对局详情 = league_matches 直查
  队列 = league_queue + league_waiting_queue
```

## 已知问题

- SSE 在低性能 NAS 上可能偶发延迟（连接管理已优化，120 秒自动重连）
- Flask dev server 单线程不适合生产环境，Linux 部署务必用 gunicorn
