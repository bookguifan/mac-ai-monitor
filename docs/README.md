# Mac AI Monitor

> macOS AI 网关监控系统 — CPU/内存/磁盘/网络/Gateway/会话/技能 一览
> **版本**: v2.12.0 | **更新**: 2026-05-23

---

## 📖 文档导航

| 文档 | 说明 |
|------|------|
| [QUICKSTART.md](QUICKSTART.md) | 5分钟入门：修改流程、检查清单、FAQ、快速定位 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 代码架构：行号级参考、数据采集、缓存策略、Gateway检测 |
| [API.md](API.md) | API 接口：端点定义、响应字段、错误码 |
| [CHANGELOG.md](CHANGELOG.md) | 版本历史：每次发布的变更记录 |

---

## 🚀 快速开始

```bash
cd ~/.qclaw/mac_ai_monitor
./dev.sh restart              # 启动/重启
open http://127.0.0.1:8849    # 打开面板
```

手动启动：
```bash
kill $(lsof -ti :8849) 2>/dev/null
nohup python3 mac_ai_monitor.py > logs/monitor_stdout.log 2>&1 &
```

---

## 📁 项目结构

```
mac_ai_monitor/
├── mac_ai_monitor.py      # 主程序 (2556行)
├── index.html             # HTML 骨架 (46行)
├── dev.sh                 # 开发工具链 (watch/restart/status/commit)
├── docs/
│   ├── README.md          # 本文件 - 项目首页
│   ├── QUICKSTART.md      # 5分钟入门
│   ├── ARCHITECTURE.md    # 代码架构参考
│   ├── API.md             # API 接口文档
│   └── CHANGELOG.md       # 版本历史
└── static/
    ├── css/style.css      # 样式 (297行, 暗色主题, 响应式4断点)
    └── js/app.js          # 前端逻辑 (1012行, 渲染+刷新+交互)

系统文件:
  结构化日志:   ~/.qclaw/logs/monitor.log (JSONL, 10MB×3轮转)
  持久化缓存:   ~/.qclaw/.monitor_persistent_cache.json
  告警状态:     ~/.qclaw/.monitor_alerts.json
  配置hash:     ~/.qclaw/.config_hashes.json
  飞书Webhook:  ~/.qclaw/.monitor_feishu_webhook
```

---

## ℹ️ 静态文件修改

服务优先使用 `index.html`（引用 `static/css/style.css` 和 `static/js/app.js`）。
修改静态文件后，浏览器硬刷新（Cmd+Shift+R）即可生效。

**⚠️ 修改前验证**（详见 [QUICKSTART.md](QUICKSTART.md#修改前必读））：
```bash
curl -s http://127.0.0.1:8849/ | grep -E "link.*css|script.*js"
```
- ✅ 有输出 → 静态文件模式，修改 `static/` 会生效
- ❌ 无输出 → 内联 `HTML_PAGE` 模式，改 `static/` 无效

**备用机制**：若 `index.html` 不存在，服务会 fallback 到内联 `HTML_PAGE`。

---

## 🔧 dev.sh 命令

| 命令 | 功能 |
|------|------|
| `./dev.sh restart` | 语法检查 + 重启 |
| `./dev.sh start` | 启动服务 |
| `./dev.sh stop` | 停止服务 |
| `./dev.sh watch` | 文件监听 + 自动重启（需 fswatch） |
| `./dev.sh status` | 运行状态 + 健康检查 |
| `./dev.sh health` | 详细指标 (CPU/内存/磁盘/GW) |
| `./dev.sh lint` | Python 语法检查 |
| `./dev.sh log` | 查看最近日志 |
| `./dev.sh commit "msg"` | Git 提交 |

---

## 📡 API 端点

| 端点 | 缓存 | 说明 |
|------|------|------|
| `/` | — | 仪表盘页面 |
| `/health` | — | 健康状态 |
| `/api/data` | 60s | 完整监控数据 |
| `/api/data/lite` | 60s | 轻量数据 (CPU/内存/磁盘/GW/电池/网络) |
| `/api/status` | 180s | 标准化健康状态 (供UptimeRobot) |
| `/api/gateway-log` | — | Gateway 日志 (参数: lines, grep) |
| `/static/*` | — | 静态文件 |

完整字段定义 → [API.md](API.md)
