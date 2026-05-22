# Mac AI Monitor — 完整参考文档

> **入门只读 `docs/QUICKSTART.md`**
> 本文档是完整参考，包含所有技术细节
> **版本**: v2.11.0 | **更新**: 2026-05-22

---

## 1. 文件与部署

```
mac_ai_monitor/
├── mac_ai_monitor.py      # 主程序 (2556行)
├── index.html             # HTML 骨架 (46行, 引用 static/)
├── dev.sh                 # 开发工具链 (watch/restart/status/commit)
├── docs/
│   ├── README.md          # 本文件 - 完整参考
│   ├── QUICKSTART.md      # 5分钟入门
│   └── API.md             # API 接口文档
└── static/
    ├── css/style.css      # 样式 (297行, 暗色主题, 响应式4断点)
    └── js/app.js          # 前端逻辑 (1012行, 渲染+刷新+交互)

系统文件:
  结构化日志: ~/.qclaw/logs/monitor.log (JSONL, 10MB×3轮转)
  持久化缓存: ~/.qclaw/.monitor_persistent_cache.json
  告警状态:   ~/.qclaw/.monitor_alerts.json
  配置hash:   ~/.qclaw/.config_hashes.json
  飞书Webhook: ~/.qclaw/.monitor_feishu_webhook
```

```bash
# 重启服务（推荐用 dev.sh）
./dev.sh restart

# 手动重启
kill $(lsof -ti :8849) 2>/dev/null
nohup python3 mac_ai_monitor.py > logs/monitor_stdout.log 2>&1 &

# 验证
curl -s http://127.0.0.1:8849/health
```

---

## 2. dev.sh 命令速查

| 命令 | 功能 |
|------|------|
| `./dev.sh watch` | 文件监听 + 自动重启（需 fswatch） |
| `./dev.sh restart` | 语法检查 + 重启 |
| `./dev.sh start` | 启动服务 |
| `./dev.sh stop` | 停止服务 |
| `./dev.sh status` | 运行状态 + 健康检查 |
| `./dev.sh health` | 详细指标 (CPU/内存/磁盘/GW) |
| `./dev.sh lint` | Python 语法检查 |
| `./dev.sh log` | 查看最近日志 |
| `./dev.sh commit "msg"` | Git 提交 |

---

## 3. 修改流程与检查清单

### ⚠️ 重要：static/ 修改可能无效

**问题**：Python 代码中的 `HTML_PAGE` 变量（L1344-2395）内联了 CSS 和 JS。
如果服务返回 `HTML_PAGE`，修改 `static/css/style.css` 和 `static/js/app.js` **不会生效**。

**排查**：检查 L2446 路由代码，确保优先读取 `index.html` 而非 `HTML_PAGE`。

**验证**：
```bash
curl -s http://127.0.0.1:8849/ | grep -E "link.*css|script.*js"
# 应输出: <link href="static/css/style.css">
#       <script src="static/js/app.js"></script>
```

### 修改流程

```bash
# 1. grep 定位
grep -n "关键词" mac_ai_monitor.py

# 2. 编辑代码...

# 3. 语法检查
python3 -m py_compile mac_ai_monitor.py

# 4. 重启服务
./dev.sh restart

# 5. 浏览器确认
open http://127.0.0.1:8849

# 6. Git 提交
./dev.sh commit "fix: 描述"
```

### 必做检查清单

- [ ] `python3 -m py_compile mac_ai_monitor.py` 语法通过
- [ ] `./dev.sh status` 服务运行正常
- [ ] `curl -s http://127.0.0.1:8849/api/data` API 正常返回
- [ ] 浏览器打开页面，滚动检查各卡片显示正常
- [ ] `git add -A && git commit` 备份

---

## 4. 代码架构

### mac_ai_monitor.py (2556行)

```
====== Config ====== (L13-27)
  端口、缓存TTL、告警冷却、日志配置

====== History ====== (L29-36)
  _history = {cpu, mem, disk, swap, net} 各60条 deque

====== Utilities ====== (L89-208)
  L89   run_cmd(cmd, timeout=10)                — Shell 执行, 编码自动检测
  L100  esc() / fmt_uptime() / try_json()       — 工具函数
  L119  send_alert(title, message, alert_key)   — macOS通知 + 飞书Webhook + 30min冷却
  L156  tail_errors() / get_log_sizes()         — 多日志源

====== Config Discovery ====== (L210-301)
  L211  discover_configs()                       — 4路径配置发现
  L228  get_config_hash(path)                    — MD5 变更检测
  L237  extract_instances(configs)               — 实例/模型/Provider提取

====== Main Data Collection ====== (L304-1343)
  L304  collect_all() — 单函数内完成所有数据采集

  数据块 (按 collect_all 中的注释标记):
  L331   # ---- System Info ----           sysctl 单次合并 (hostname/os/cores/temp/mem/gpu)
  L372   # ---- GPU (cached 60s, with lock) ----  system_profiler
  L390   # ---- CPU Usage (sampled over 60s via top -l 1, with lock) ----
  L425   # ---- Memory (vm_stat + hw.memsize for total) ----
  L456   # ---- Disk ----                  df -h (含 size_gb/used_gb/available_gb 转换)
  L480   # ---- Volumes (df-based, single pass) ----
  L525   # ---- Battery ----               pmset
  L538   # ---- Network ----               netstat -ib (首帧保护)
  L557   # ---- Disk IO (iostat, macOS built-in) ----
  L611   # ---- Shared Process Table (single ps aux) ----
  L625   # ---- Top Processes (from shared ps_procs) ----
  L646   # ---- Shared lsof (single call, reused by Ports + Gateway) ----
  L657   # ---- Ports (from shared lsof_listen + ps_procs) ----
  L677   # ---- Gateway Detection (from shared lsof_all) ----
  L797   # ---- Gateway: 按软件名合并 + 性能指标 ----
  L882   # ---- Trigger Alerts ----
  L894   # ---- Data Directories ----
  L939   # ---- Cron ----
  L978   # ---- Skills ----
  L1028  # ---- Skill Call Statistics ----
  L1172  # ---- Session Token Statistics ----
  L1224  # ---- Recent Errors ----
  L1227  # ---- Activity ----
  L1331  # ---- History ----

====== HTML Template ====== (L1346-2396)
  内联 HTML 模板 (备用，当前优先使用 index.html)

====== HTTP Handler ====== (L2399-2508)
  L2399  class Handler(BaseHTTPRequestHandler)
    /              → 优先 index.html (引用外部CSS/JS)，备用 HTML_PAGE
    /health        → 健康状态 (warming=200, degraded)
    /api/data      → 完整监控数据 (60s缓存)
    /api/data/lite → 轻量数据 (60s缓存)
    /api/status    → 标准化健康状态
    /api/gateway-log → Gateway 日志 (lines/grep 参数)
    /static/*      → 静态文件服务 (CSS/JS/图片等)
  L2509  class ThreadedServer — 多线程 HTTP 服务

====== Main ====== (L2514-2556)
  L2514  _shutdown() 信号处理
  GPU 预热线程 (3s 后台启动)
  preexec_fn=os.setpgrp — 独立进程组，避免 shell 退出时被 SIGTERM
```

### index.html (46行)

HTML 骨架，引用外部静态资源：
- `<link href="static/css/style.css">` — 样式
- `<script src="static/js/app.js">` — 逻辑

### static/css/style.css (297行)

暗色主题 + 4断点响应式：
- ≥1200px: 桌面 (Quick Bar 8列, Gauges 3列)
- 768-1199px: 平板 (Quick Bar 4列)
- 480-767px: 手机 (Quick Bar 2列)
- <480px: 小屏 (Quick Bar 1列)

色彩变量：
- `--text2: #a8c4e0` (次要文本，已优化对比度)
- `--text3: #6b7f9e` (辅助文本，已优化对比度)

### static/js/app.js (1012行)

```
工具函数: fmt_pct/fmt_gb/fmt_mb/fmt_net/fmt_size/esc
  fmt_net(): KB/s ↔ MB/s 自动切换
  fmt_size(): "14Gi" → "14.0GB" 格式转换

render(d): 全量 HTML 渲染
  Quick Bar (告警/CPU/内存/磁盘/Swap/Gateway/网络/IO/电池/会话/运行时间)
  → Gauges (3 SVG 环形图: CPU/内存/磁盘 used_pct)
  → Health Bar → System/网络/存储卷 → 内存详情/端口/模型
  → Top进程 → Gateway(运行+闲置+查看日志按钮) → Activity/Cron
  → 会话统计(Tokens/Messages/ToolCalls) → Skills/日志/数据目录
  → GPU信息 (name + VRAM)
  → Footer (项目结构)

刷新控制:
  默认 Manual，可切换 Auto(30s)
  Tab 隐藏时暂停自动刷新
  回到页面时判断过期则立即刷新

Gateway 日志弹窗:
  fetchGatewayLog() → /api/gateway-log → 模态框显示
```

---

## 5. 数据采集

### 共享执行（单次加载，多块复用）

```
ps aux (1次) → ps_procs ──┬→ Top Processes
                         ├→ Ports 进程名匹配
                         └→ Gateway 实例检测

lsof -i -P -n (1次) ────┬→ lsof_listen → Ports
                         └→ Gateway 端口

ps -eo pid,ppid,comm (1次) → ppid_map → detect_source (PPID链)

sysctl (1次合并) → boottime/memsize/ncpu/physicalcpu/os/cpu_brand/loadavg/swapusage
diskutil list (1次) → 所有卷名
```

### collect_all() 内顺序采集

```
collect_all()
  ├── configs, instances, all_models, all_providers    (Config Discovery)
  ├── config_changes, log_stats                         (直接)
  ├── System Info (shared sysctl)                       — hostname/os/cores/temp/mem
  ├── GPU (60s缓存, with lock)                       — system_profiler
  ├── CPU (60s采样, top -l 1, with lock)                   — top
  ├── Memory                                            — vm_stat + hw.memsize
  ├── Disk (df -h + GB转换)                              — size_gb/used_gb/available_gb
  ├── Volumes (df + diskutil)                            — 各卷详情
  ├── Battery                                           — pmset
  ├── Network (首帧保护)                                 — netstat -ib
  ├── Disk IO                                           — iostat
  ├── Shared ps_procs (1次 ps aux)                      — 多处复用
  ├── Top Processes                                     — CPU/MEM排序
  ├── Shared lsof (1次)                                 — Ports + Gateway 复用
  ├── Ports                                             — lsof + KNOWN_PORTS
  ├── Gateway Detection (4规则 + PPID链)                 — 合并+性能指标+健康评分
  ├── Alerts                                            — 自动告警
  ├── Data Directories (ThreadPoolExecutor 并发, 5min缓存) — du -sk
  ├── Cron                                              — 4路径 jobs.json
  ├── Skills (5min缓存)                                  — 3级目录扫描
  ├── Skill Call Statistics                             — Session文件扫描
  ├── Session Token Statistics                          — Token/消息/工具调用
  ├── Recent Errors                                     — 日志尾扫描
  ├── Activity (嵌套JSONL解析, 系统消息过滤)              — 50条, 时间分组
  └── History                                           — cpu/mem/disk/swap/net 趋势
```

---

## 6. 缓存策略

| 缓存层 | TTL | 说明 |
|--------|-----|------|
| `_data` (主缓存) | 60s | collect_all() 结果，/api/data |
| `_lite_cache` | 60s | 轻量指标，/api/data/lite |
| `_gpu_cache_store` | 60s | GPU 数据，后台预热 |
| `_cpu_cache` | 60s | CPU top 数据，线程锁 |
| `_data_dir_cache` | 300s | du 目录大小 |
| `_skills_cache` | 300s | Skills 扫描 |
| `_session_file_idx` | 120s | Session 文件索引 |
| 持久化缓存 | 启动时<30min | 服务重启立即恢复 |

---

## 7. Gateway 检测

4规则匹配 + PPID链追溯：

| 规则 | 匹配关键词 | 优先级 |
|------|-----------|--------|
| openclaw-gateway | `openclaw-gateway`, `gateway` | P0 |
| QClaw | `qclaw` (进程名含空格如 `QClaw\x20`) | P0 |
| Claude | `claude` (Desktop App) | P1 |
| AutoClaw | `autoclaw` (排除 helper/renderer/gpu/network) | P1 |

PPID链追溯：`ps -eo pid,ppid,comm` → 父进程匹配 → 发现隐藏Gateway进程。

PID反查：已知Gateway PID → lsof反查端口（解决进程名显示为 `node` 的情况）。

健康评分：system(负载/CPU/内存) + gateway(进程数/闲置) + endpoint(可选)。

---

## 8. Activity 解析

支持两种 session 格式：

### QClaw (JSONL，嵌套结构)
```json
{"type":"message","message":{"role":"user","content":[{"type":"text","text":"..."}]}}
{"type":"message","message":{"role":"assistant","content":"...","tool_calls":[...]}}
```

### Hermes (单 JSON doc)
```json
{"messages":[{"role":"user","content":"字符串"},{"role":"assistant","content":"..."}]}
```

解析流程：
1. Session 文件索引 (120s 缓存)
2. `_get_user_content()`: 解包嵌套 `message` 字段 + 数组/字符串 content
3. 过滤系统消息 (Conversation info/Sender/HEARTBEAT/gbrain 等)
4. 返回 user 消息前50字作为摘要
5. Fallback: user 消息全被过滤时取 assistant 消息摘要

---

## 9. 告警

- **macOS 系统通知**: `osascript` 弹窗
- **飞书 Webhook**: POST 到配置的 webhook URL
- **冷却机制**: 30min 内同一 alert_key 不重复推送
- **触发条件**: Gateway 全挂、CPU > 90%、内存 > 95% 等
- **配置飞书**: 将 Webhook URL 写入 `~/.qclaw/.monitor_feishu_webhook`

---

## 10. 启动与信号处理

```python
# 独立进程组，避免 shell 退出时被 SIGTERM 杀掉
preexec_fn=os.setpgrp
```

信号处理：SIGTERM/SIGINT → 保存持久化缓存 → 优雅退出

GPU 预热：启动3s后后台线程执行 `system_profiler SPDisplaysDataType`

---

## 11. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v2.11.0 | 2026-05-17 | 阶段1重构: CSS/JS分离、项目目录化、API文档 |
| v2.11.0 | 2026-05-18 | 前端优化: 单位自动切换、对比度提升、响应式4断点 |
| v2.11.0 | 2026-05-18 | 后端: cpu.cores/temp采集、gateway.pids/ports汇总 |
| v2.11.0 | 2026-05-18 | 健康检查: warming状态(缓存为空时返回200而非503) |
| v2.11.0 | 2026-05-18 | Signal 15修复: preexec_fn=os.setpgrp |
| v2.11.0 | 2026-05-18 | Activity: 嵌套JSONL解析、系统消息过滤、assistant fallback |
| v2.11.0 | 2026-05-18 | Session统计: Token估算、assistant_messages/tool_calls |
| v2.11.0 | 2026-05-18 | 默认手动刷新、热门工具undefined修复、页脚路径 |
| v2.11.0 | 2026-05-19 | 字段命名统一: memory→mem, network→net |
| v2.11.0 | 2026-05-19 | 磁盘: size_gb/used_gb/available_gb 字段 (df -h 转换) |
| v2.11.0 | 2026-05-19 | 飞书Webhook告警推送 |
| v2.11.0 | 2026-05-19 | Gateway日志前端查看按钮 + /api/gateway-log 端点 |
| v2.11.0 | 2026-05-19 | 数据目录采集: ThreadPoolExecutor并发du |
| v2.11.0 | 2026-05-19 | GPU显示: name + VRAM格式 |
| v2.11.0 | 2026-05-19 | None安全: /health和/api/data防空指针 |
| v2.11.0 | 2026-05-22 | 运行时间移到Quick Bar、fmt_uptime中文格式(5天16小时12分) |
| v2.11.0 | 2026-05-22 | 页脚显示项目结构 |
| v2.11.0 | 2026-05-22 | 服务优先使用index.html(引用外部CSS/JS)替代HTML_PAGE内联 |
