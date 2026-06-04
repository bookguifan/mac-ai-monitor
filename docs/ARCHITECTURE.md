# Mac AI Monitor — 代码架构

> 行号级架构参考，改代码前先查这里
> **版本**: v2.15.0 | **更新**: 2026-06-04

---

## mac_ai_monitor.py (2757行)

```
====== Config ====== (L12-27)
  端口、缓存TTL、告警冷却、路径配置(SCRIPT_DIR/DATA_DIR)

====== History ====== (L58-65)
  _history = {cpu, mem, disk, swap, net} 各60条 deque

====== Utilities ====== (L67-236)
  L117  run_cmd(cmd, timeout=10)                — Shell 执行, 编码自动检测
  L128  esc() / fmt_uptime() / try_json()       — 工具函数
  L147  send_alert(title, message, alert_key)   — macOS通知 + 飞书Webhook + 30min冷却
  L184  tail_errors() / get_log_sizes()         — 多日志源

====== Config Discovery ====== (L238-329)
  L239  discover_configs()                       — 4路径配置发现
  L256  get_config_hash(path)                    — MD5 变更检测
  L265  extract_instances(configs)               — 实例/模型/Provider提取

====== Main Data Collection ====== (L331-1432)
  L332  collect_all() — 单函数内完成所有数据采集

  数据块 (按 collect_all 中的注释标记):
  L370   # ---- System Info ----           sysctl 单次合并 (hostname/os/cores/temp/mem/gpu)
  L411   # ---- GPU (cached 60s, with lock) ----  system_profiler
  L455   # ---- CPU Usage (sampled over 60s via top -l 1, with lock) ----
  L490   # ---- Memory (vm_stat + hw.memsize for total) ----
  L521   # ---- Disk ----                  df -h (含 size_gb/used_gb/available_gb 转换)
  L554   # ---- Volumes (df-based, single pass) ----
  L599   # ---- Battery ----               pmset
  L612   # ---- Network ----               netstat -ib (首帧保护)
  L633   # ---- Disk IO (iostat, macOS built-in) ----
  L687   # ---- Shared Process Table (single ps aux) ----
  L701   # ---- Top Processes (from shared ps_procs) ----
  L722   # ---- Shared lsof (single call, reused by Ports + Gateway) ----
  L733   # ---- Ports (from shared lsof_listen + ps_procs) ----
  L753   # ---- Gateway Detection (from shared lsof_all) ----
  L873   # ---- Gateway: 按软件名合并 + 性能指标 ----
  L959   # ---- Trigger Alerts ----
  L976   # ---- Data Directories ----
  L1021  # ---- Cron ----
  L1060  # ---- Skills ----
  L1110  # ---- Skill Call Statistics (scan session files) ----
  L1254  # ---- Session Token Statistics ----
  L1315  # ---- Recent Errors ----
  L1318  # ---- Activity ----
  L1422  # ---- History ----

====== HTML Template ====== (L1436-2496)
  内联 HTML 模板 (备用，当前优先使用 index.html)

====== HTTP Handler ====== (L2497-2709)
  L2497  class Handler(BaseHTTPRequestHandler)
    /              → 优先 index.html (引用外部CSS/JS)，备用 HTML_PAGE
    /health        → 健康状态 (warming=200, degraded)
    /api/data      → 完整监控数据 (60s缓存)
    /api/status    → 标准化健康状态
    /api/gateway-log → Gateway 日志 (lines/grep 参数)
    /api/alerts    → 告警历史
    /api/export/json|csv → 数据导出
    /api/process/<pid> → 进程详情
    /static/*      → 静态文件服务 (路径遍历防护)
  L2710  class ThreadedServer — 多线程 HTTP 服务

====== Main ====== (L2715-2757)
  L2715  _shutdown() — 信号处理优雅退出
  L2721  __main__ — 服务启动入口
```

---

## index.html (50行)

HTML 骨架，引用外部静态资源：
- `<link href="static/css/style.css">` — 样式
- `<script src="static/js/app.js">` — 逻辑

## static/css/style.css (311行)

暗色主题 + 4断点响应式：
- ≥1200px: 桌面 (Quick Bar 8列, Gauges 3列)
- 768-1199px: 平板 (Quick Bar 4列)
- 480-767px: 手机 (Quick Bar 2列)
- <480px: 小屏 (Quick Bar 1列)

色彩变量：`--text2: #a8c4e0` / `--text3: #6b7f9e`

## static/js/app.js (1251行)

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
  → GPU信息 (name + VRAM) → Footer (项目结构)

刷新控制: 默认 Manual，可切换 Auto(5s/10s/30s/60s)，Tab 隐藏时暂停

Gateway 日志弹窗:
  fetchGatewayLog() → /api/gateway-log → 模态框显示
```

---

## 运行时数据目录 (data/)

v2.15.0 起所有运行时数据存放于项目本地 `data/` 目录，不再依赖 `~/.qclaw/`：

```
data/
├── alerts.json            # 告警状态 (对应 ALERT_FILE)
├── alert_config.json      # 告警阈值配置 (对应 ALERT_CONFIG_FILE)
├── config_hashes.json     # 配置变更检测 (MD5 hash)
├── feishu_webhook         # 飞书 Webhook URL (可选)
└── logs/
    ├── monitor.log        # 结构化日志 (对应 LOG_FILE)
    └── monitor_stdout.log # stdout 重定向日志
```

路径定义 (mac_ai_monitor.py L21-26):
```python
SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
DATA_DIR      = os.path.join(SCRIPT_DIR, data)
LOG_FILE      = os.path.join(DATA_DIR, logs, monitor.log)
ALERT_FILE    = os.path.join(DATA_DIR, alerts.json)
ALERT_CONFIG_FILE = os.path.join(DATA_DIR, alert_config.json)
```

> 注：`~/.qclaw/.monitor_persistent_cache.json` 仍使用 HOME 路径（全局缓存，与项目位置无关）

---

## 数据采集

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
  ├── GPU (60s缓存, with lock)                          — system_profiler
  ├── CPU (60s采样, top -l 1, with lock)               — top
  ├── Memory                                            — vm_stat + hw.memsize
  ├── Disk (df -h + GB转换)                             — size_gb/used_gb/available_gb
  ├── Volumes (df + diskutil)                           — 各卷详情
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
  ├── Skills (5min缓存)                                 — 3级目录扫描
  ├── Skill Call Statistics                             — Session文件扫描
  ├── Session Token Statistics                          — Token/消息/工具调用
  ├── Recent Errors                                     — 日志尾扫描
  ├── Activity (嵌套JSONL解析, 系统消息过滤)             — 50条, 时间分组
  └── History                                           — cpu/mem/disk/swap/net 趋势
```

---

## 缓存策略

| 缓存层 | TTL | 说明 |
|--------|-----|------|
| `_data` (主缓存) | 60s | collect_all() 结果，/api/data |
| `_gpu_cache_store` | 60s | GPU 数据，后台预热 |
| `_cpu_cache` | 60s | CPU top 数据，线程锁 |
| `_data_dir_cache` | 300s | du 目录大小 |
| `_skills_cache` | 300s | Skills 扫描 |
| `_session_file_idx` | 120s | Session 文件索引 |
| 持久化缓存 | 启动时<30min | 服务重启立即恢复 |

---

## Gateway 检测

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

## Activity 解析

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

## 告警

- **macOS 系统通知**: `osascript` 弹窗
- **飞书 Webhook**: POST 到配置的 webhook URL
- **冷却机制**: 30min 内同一 alert_key 不重复推送
- **触发条件**: Gateway 全挂、CPU > 90%、内存 > 95% 等
- **配置飞书**: 将 Webhook URL 写入 `data/feishu_webhook`

---

## 启动与信号处理

```python
# 独立进程组，避免 shell 退出时被 SIGTERM 杀掉
preexec_fn=os.setpgrp
```

信号处理：SIGTERM/SIGINT → 保存持久化缓存 → 优雅退出

GPU 预热：启动3s后后台线程执行 `system_profiler SPDisplaysDataType`
