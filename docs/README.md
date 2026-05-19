# Mac AI Monitor — 完整参考文档

> **入门只读 `docs/QUICKSTART.md`**
> 本文档是完整参考，包含所有技术细节
> **版本**: v2.11.0 | **更新**: 2026-05-19

---

## 1. 文件与部署

```
mac_ai_monitor/
├── mac_ai_monitor.py      # 主程序 (2545行)
├── index.html             # HTML 骨架 (46行, 引用 static/)
├── dev.sh                 # 开发工具链 (watch/restart/status/commit)
├── docs/
│   ├── README.md          # 本文件 - 完整参考
│   ├── QUICKSTART.md      # 5分钟入门
│   └── API.md             # API 接口文档
└── static/
    ├── css/style.css      # 样式 (285行, 暗色主题, 响应式4断点)
    └── js/app.js          # 前端逻辑 (986行, 渲染+刷新+交互)

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
nohup python3 mac_ai_monitor.py > /tmp/mam.log 2>&1 &

# 验证
curl -s http://127.0.0.1:8849/health
```

---

## 2. 代码架构

### mac_ai_monitor.py (2545行)

```
====== Config ====== (L12-27)
  版本号、端口、缓存TTL、告警冷却、日志配置

====== History ====== (L29-36)
  _history = {cpu, mem, disk, swap, net} 各60条 deque

====== Utilities ====== (L38-208)
  L40   _parse_cron_field() / _cron_next()     — Cron 表达式解析
  L88   run_cmd(cmd, timeout=10)                — Shell 执行, 编码自动检测
  L99   esc() / fmt_uptime() / try_json()       — 工具函数
  L118  send_alert(title, message, alert_key)   — macOS通知 + 飞书Webhook + 30min冷却
  L155  tail_errors() / get_log_sizes()         — 多日志源

====== Config Discovery ====== (L209-301)
  L210  discover_configs()                       — 4路径配置发现
  L227  get_config_hash(path)                    — MD5 变更检测
  L236  extract_instances(configs)               — 实例/模型/Provider提取

====== Main Data Collection ====== (L302-1343)
  L303  collect_all() — 单函数内完成所有数据采集

  数据块 (按 collect_all 中的注释标记):
  L330   # ---- System Info ----           sysctl 单次合并 (hostname/os/cores/temp/mem/gpu)
  L371   # ---- GPU (cached 600s) ----     system_profiler, 后台预热线程
  L389   # ---- CPU Usage ----             top -l 1 (60s缓存, 线程锁)
  L424   # ---- Memory ----                vm_stat + hw.memsize
  L455   # ---- Disk ----                  df -h (含 size_gb/used_gb/available_gb 转换)
  L479   # ---- Volumes ----               df + diskutil
  L524   # ---- Battery ----               pmset
  L537   # ---- Network ----               netstat -ib (首帧保护)
  L556   # ---- Disk IO ----               iostat
  L610   # ---- Shared Process Table ----  单次 ps aux, 多处复用
  L624   # ---- Top Processes ----         CPU/MEM 排序
  L645   # ---- Shared lsof ----           单次 lsof, Ports + Gateway 复用
  L656   # ---- Ports ----                 lsof + KNOWN_PORTS
  L676   # ---- Gateway Detection ----     4规则 + PPID链 + PID反查端口
  L796   # ---- Gateway 合并 + 性能指标 ---- 按软件名合并 + 健康评分
  L881   # ---- Trigger Alerts ----        自动告警触发
  L893   # ---- Data Directories ----      ThreadPoolExecutor 并发 du (5min缓存)
  L938   # ---- Cron ----                  4路径 jobs.json
  L977   # ---- Skills ----                3级目录扫描 (5min缓存)
  L1027  # ---- Skill Call Statistics ---- Session 文件扫描
  L1171  # ---- Session Token Statistics -- Token/消息/工具调用统计
  L1223  # ---- Recent Errors ----         日志尾扫描
  L1226  # ---- Activity ----              嵌套JSONL解析 + 系统消息过滤
  L1330  # ---- History ----               cpu/mem/disk/swap/net 趋势数据

====== HTML Template ====== (L1344-2395)
  内联 HTML 模板 (供 /api/data 响应或直接渲染)

====== HTTP Handler ====== (L2396-2503)
  L2397  class Handler(BaseHTTPRequestHandler)
    /              → 仪表盘页面
    /health        → 健康状态 (warming=200, degraded)
    /api/data      → 完整监控数据 (60s缓存)
    /api/data/lite → 轻量数据 (60s缓存)
    /api/status    → 标准化健康状态
    /api/gateway-log → Gateway 日志 (lines/grep 参数)
    /static/*      → 静态文件服务 (CSS/JS/图片等)
  L2500  class ThreadedServer — 多线程 HTTP 服务

====== Main ====== (L2504-2545)
  _shutdown() 信号处理
  GPU 预热线程 (3s 后台启动)
  preexec_fn=os.setpgrp — 独立进程组，避免 shell 退出时被 SIGTERM
```

### index.html (46行)

HTML 骨架，引用外部静态资源：
- `<link href="static/css/style.css">` — 样式
- `<script src="static/js/app.js">` — 逻辑

### static/css/style.css (285行)

暗色主题 + 4断点响应式：
- ≥1200px: 桌面 (Quick Bar 8列, Gauges 3列)
- 768-1199px: 平板 (Quick Bar 4列)
- 480-767px: 手机 (Quick Bar 2列)
- <480px: 小屏 (Quick Bar 1列)

色彩变量：
- `--text2: #a8c4e0` (次要文本，已优化对比度)
- `--text3: #6b7f9e` (辅助文本，已优化对比度)

### static/js/app.js (986行)

```
工具函数: fmt_pct/fmt_gb/fmt_mb/fmt_net/fmt_size/esc
  fmt_net(): KB/s ↔ MB/s 自动切换
  fmt_size(): "14Gi" → "14.0GB" 格式转换

render(d): 全量 HTML 渲染
  Quick Bar (CPU/内存/磁盘/Swap/GW/网络/IO/电池/会话)
  → Gauges (3 SVG 环形图: CPU/内存/磁盘 used_pct)
  → Health Bar → System/网络/存储卷 → 内存详情/端口/模型
  → Top进程 → Gateway(运行+闲置+查看日志按钮) → Activity/Cron
  → 会话统计(Tokens/Messages/ToolCalls) → Skills/日志/数据目录
  → GPU信息 (name + VRAM)

刷新控制:
  默认 Manual，可切换 Auto(30s)
  Tab 隐藏时暂停自动刷新
  回到页面时判断过期则立即刷新

Gateway 日志弹窗:
  fetchGatewayLog() → /api/gateway-log → 模态框显示
```

---

## 3. 数据采集

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
  ├── GPU (600s缓存, 后台预热线程)                       — system_profiler
  ├── CPU (60s缓存, 线程锁)                             — top -l 1
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

## 4. 缓存策略

| 缓存层 | TTL | 说明 |
|--------|-----|------|
| `_data` (主缓存) | 60s | collect_all() 结果，/api/data |
| `_lite_cache` | 60s | 轻量指标，/api/data/lite |
| `_gpu_cache_store` | 600s | GPU 数据，后台预热 |
| `_cpu_cache` | 60s | CPU top 数据，线程锁 |
| `_data_dir_cache` | 300s | du 目录大小 |
| `_skills_cache` | 300s | Skills 扫描 |
| `_session_file_idx` | 120s | Session 文件索引 |
| 持久化缓存 | 启动时<30min | 服务重启立即恢复 |

---

## 5. Gateway 检测

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

## 6. Activity 解析

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

## 7. 告警

- **macOS 系统通知**: `osascript` 弹窗
- **飞书 Webhook**: POST 到配置的 webhook URL
- **冷却机制**: 30min 内同一 alert_key 不重复推送
- **触发条件**: Gateway 全挂、CPU > 90%、内存 > 95% 等
- **配置飞书**: 将 Webhook URL 写入 `~/.qclaw/.monitor_feishu_webhook`

---

## 8. 启动与信号处理

```python
# 独立进程组，避免 shell 退出时被 SIGTERM 杀掉
preexec_fn=os.setpgrp
```

信号处理：SIGTERM/SIGINT → 保存持久化缓存 → 优雅退出

GPU 预热：启动3s后后台线程执行 `system_profiler SPDisplaysDataType`

---

## 9. 版本历史

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
