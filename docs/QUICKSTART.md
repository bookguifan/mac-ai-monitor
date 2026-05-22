# Mac AI Monitor — 5分钟入门

> 新会话首次阅读，5分钟快速上手
> 找不到答案 → `docs/README.md` 完整参考
> **版本**: v2.11.0 | **更新**: 2026-05-19

---

## 🚀 快速开始

**访问**: `http://127.0.0.1:8849` (本机) 或 `http://<局域网IP>:8849` (局域网)

**重启**:
```bash
./dev.sh restart    # 推荐：语法检查 + 重启
# 或手动：
kill $(lsof -ti :8849) 2>/dev/null
nohup python3 mac_ai_monitor.py > logs/monitor_stdout.log 2>&1 &
```

**验证**:
```bash
./dev.sh status     # 运行状态 + 版本 + 健康检查
./dev.sh health     # 详细指标 (CPU/内存/磁盘/Gateway)
curl -s http://127.0.0.1:8849/health
```

---

## 📁 文件结构

```
mac_ai_monitor/
├── mac_ai_monitor.py      # 主程序 (2556行)
├── index.html             # HTML 骨架 (46行)
├── dev.sh                 # 开发工具链 (watch/restart/status/commit)
├── docs/
│   ├── README.md          # 完整参考文档（本文档的上级）
│   ├── QUICKSTART.md      # 本文件
│   └── API.md             # API 接口文档
└── static/
    ├── css/style.css      # 样式 (297行, 暗色主题, 响应式)
    └── js/app.js          # 前端逻辑 (1012行, 渲染+刷新+交互)

日志: ~/.qclaw/logs/monitor.log (JSONL, 10MB×3轮转)
缓存: ~/.qclaw/.monitor_persistent_cache.json
告警: ~/.qclaw/.monitor_alerts.json
飞书: ~/.qclaw/.monitor_feishu_webhook (可选, webhook URL)
```

---

## 🔧 修改流程

### 简单改动（≤30min）— 6步
```
grep定位 → edit修改 → py_compile验证 → restart → 浏览器确认 → commit
```
```bash
grep -n "关键词" mac_ai_monitor.py     # 1. 定位
# 编辑代码...                           # 2. 修改
python3 -m py_compile mac_ai_monitor.py # 3. 语法检查
./dev.sh restart                       # 4. 重启
# 浏览器打开 http://127.0.0.1:8849      # 5. 确认
./dev.sh commit "fix: 描述"            # 6. 提交
```

### 大改动（≥1h）— 4阶段
```
准备: backup → 用户确认 → 创建分支
开发: 分步修改 → py_compile+restart → wip存档
验证: status → health → 全页面检查 → API数据检查
收尾: 合并main → 清理分支 → 更新docs/
```

### 文件监听模式（可选）
```bash
./dev.sh watch    # 保存文件 → 自动语法检查 → 自动重启
# 需要 fswatch: brew install fswatch（无则降级为3s轮询）
```

### 必做检查清单
- [ ] `python3 -m py_compile mac_ai_monitor.py` 语法通过
- [ ] `./dev.sh status` 服务运行正常
- [ ] `curl -s http://127.0.0.1:8849/api/data` API 正常返回
- [ ] 浏览器打开页面，滚动检查各卡片显示正常

---

## 📍 快速定位

| 任务 | 文件 | 关键标识 |
|------|------|----------|
| 版本号 | mac_ai_monitor.py L12 | `__version__='2.11.0'` |
| 端口 | mac_ai_monitor.py L12 | `PORT=8849` |
| 主缓存TTL | mac_ai_monitor.py L12 | `CACHE_TTL=60` |
| GPU缓存TTL | mac_ai_monitor.py L12 | `GPU_CACHE_TTL=60` |
| 告警冷却 | mac_ai_monitor.py L12 | `ALERT_COOLDOWN=1800` |
| 数据采集 | mac_ai_monitor.py L303 | `collect_all()` |
| 活动解析 | mac_ai_monitor.py L1226 | `# ---- Activity ----` |
| HTTP处理 | mac_ai_monitor.py L2397 | `class Handler` |
| 静态文件服务 | mac_ai_monitor.py Handler | `/static/` 路由 |
| CSS样式 | static/css/style.css | 暗色主题 + 4断点响应式 |
| 前端渲染 | static/js/app.js | `render(d)` |
| 工具函数 | static/js/app.js | `fmt_pct/fmt_gb/fmt_net/fmt_size` |

---

## 🆘 常见问题

| 症状 | 排查 |
|------|------|
| 页面空白/卡加载 | F12→Console 看红色报错，检查 `/api/data` 是否返回 |
| CSS/JS 404 | 检查 `static/` 目录是否存在，Handler 中 `/static/` 路由 |
| 首次API慢 | 正常，GPU预热~15s+CPU~5s，缓存后<100ms |
| 活动显示"Session @" | 清缓存: `rm ~/.qclaw/.monitor_persistent_cache.json` 重启 |
| 数据字段为None | `curl /api/data \| python3 -m json.tool` 查原始数据 |
| 服务挂了 | `./dev.sh status` 检查，`./dev.sh restart` 重启 |
| 服务被signal 15杀 | 主进程组被shell退出杀掉，确认 `__main__` 有 `preexec_fn=os.setpgrp` |
| 样式/布局问题 | 改 `static/css/style.css` |
| 前端逻辑问题 | 改 `static/js/app.js` |
| 采集逻辑问题 | 改 `mac_ai_monitor.py` 的 `collect_all()` |
| 改坏了想回滚 | `git checkout -- mac_ai_monitor.py && ./dev.sh restart` |

---

## 🔗 API 端点

| 端点 | 缓存 | 说明 |
|------|------|------|
| `/` | — | 仪表盘页面 |
| `/health` | — | `{"status":"ok","version":"2.11.0","gateway_count":N}` |
| `/api/data` | 60s | 完整监控数据 JSON |
| `/api/data/lite` | 60s | 轻量数据 (CPU/内存/磁盘/GW/电池/网络) |
| `/api/status` | 180s | 标准化健康状态 (供UptimeRobot) |
| `/api/gateway-log` | — | Gateway 日志 (参数: lines, grep) |
| `/static/*` | — | 静态文件 (CSS/JS) |

---

## 🛠 dev.sh 命令速查

| 命令 | 功能 |
|------|------|
| `./dev.sh watch` | 文件监听 + 自动重启 |
| `./dev.sh restart` | 语法检查 + 重启 |
| `./dev.sh start` | 启动服务 |
| `./dev.sh stop` | 停止服务 |
| `./dev.sh status` | 运行状态 + 健康检查 |
| `./dev.sh health` | 详细指标 (CPU/内存/磁盘/GW) |
| `./dev.sh lint` | Python 语法检查 |
| `./dev.sh log` | 查看最近日志 |
| `./dev.sh commit "msg"` | Git 提交 |

---

## 📖 找不到答案？

→ `docs/README.md` 完整参考（含代码架构、数据采集、缓存策略、版本历史）
→ `docs/API.md` API 接口详细文档
