"""
gunicorn 配置 — 高流量部署
使用方式: gunicorn -c gunicorn.conf.py app:app
"""

# 绑定地址
bind = "0.0.0.0:5000"

# Worker 数量 = 2 * CPU + 1（4 核机器 → 9 workers）
workers = 4

# gevent worker — 支持 SSE 长连接 + 高并发 I/O
worker_class = "gevent"

# 每个 worker 的最大并发连接数
worker_connections = 1000

# 超时（秒）— SSE 长连接需要更长超时
timeout = 120

# 日志
accesslog = "-"
errorlog = "-"
loglevel = "info"

# 优雅重启
graceful_timeout = 30

# 每处理 2000 个请求后自动重启 worker，回收内存
max_requests = 2000
# 随机偏移，防止 4 个 worker 同时重启
max_requests_jitter = 500
