# LeagueWeb

酒馆战棋联赛网站 — 排行榜、对局记录、报名队列、插件 API。

配套 C# HDT 插件：[HDT_BGTracker](https://github.com/iceshoesss/HDT_BGTracker)

## 项目结构

```
LeagueWeb/
├── app.py              # Flask 后端 API + 页面路由 + 插件端点 + SSE 推送
├── templates/          # Jinja2 模板（Tailwind CSS + ECharts CDN）
├── Dockerfile
├── docker-compose.yml  # Docker 部署（Flask + MongoDB）
├── API.md              # 插件 API 文档
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

## 常用命令

```bash
docker compose logs -f web     # 看日志
docker compose down            # 停止
docker compose restart web     # 重启
```

## 版本号

当前版本：`v0.3.1`（定义在 `app.py` → `WEB_VERSION`）

修改版本号只需改 `app.py` 中的 `WEB_VERSION = "x.y.z"`，页面底部自动显示。

版本号规则：`主版本.次版本.修订号`
- **修订号 +1** — 修 bug
- **次版本 +1** — 加新功能
- **主版本 +1** — 大改/重构/正式发布

## 更新日志

### v0.3.1 (2026-04-14)
- **插件认证 + 版本强制更新**：所有 `/api/plugin/*` 端点双重校验
  - API Key：配置 `PLUGIN_API_KEY` 后，插件请求必须带 `Authorization: Bearer <key>`，否则 403
  - 版本检查：`X-HDT-Plugin` header 版本号低于 `MIN_PLUGIN_VERSION` 则 403
  - 两个 env var 配合使用，发新插件时同步更换即可让旧插件失效

### v0.3.0 (2026-04-14)
- **测试模式改为重叠人数匹配**：不再无脑判联赛，按等待组重叠人数判定（阈值 `MIN_MATCH_PLAYERS`，默认 3）
- **报名队列阈值联动**：满 N 人移入等待组，N 跟随 `MIN_MATCH_PLAYERS`（test=3, normal=8）
- `toggle-test-mode.py` 拆分为独立脚本，只管本仓库的 `app.py`

### v0.2.13 (2026-04-14)
- 修复登录后导航到其他页面丢失登录状态的问题（Session cookie SameSite 配置）
- 修复 player 页面历史对局时间显示错误（双重时区偏移）
- 时间格式统一：所有 ISO 时间字符串带 Z 后缀，前端正确解析为 UTC
- 新增 `WEB_VERSION` 常量，页面底部显示当前版本号

### v0.2.12 及更早
- 队列超时机制、SSE 推送、ECharts 图表、验证码系统等
- 详见原仓库 [HDT_BGTracker](https://github.com/iceshoesss/HDT_BGTracker) 历史

## API 文档

详见 [API.md](API.md)
