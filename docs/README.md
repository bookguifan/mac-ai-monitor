# Mac AI Monitor — 完整参考文档

> **入门只读 `docs/QUICKSTART.md`**
> 本文档是完整参考，包含所有技术细节
> **版本**: v2.11.0 | **更新**: 2026-05-18

---

## 1. 文件与部署

```
mac_ai_monitor/
├── mac_ai_monitor.py      # 主程序 (2099行)
├── index.html             # HTML 骨架 (46行, 引用 static/)
├── dev.sh                 # 开发工具链 (watch/restart/status/commit)
├── docs/
│   ├── README.md          # 本文件 - 完整参考
│   ├── QUICKSTART.md      # 5分钟入门
│   └── API.md             # API 接口文档
└── static/
    ├── css/style.css      # 样式 (283行, 暗色主题, 响应式4断点)
    └── js/app.js          # 前端逻辑 (940行, 渲染+刷新+交互)

系统文件:
  结构化日志: ~/.qclaw/logs/monitor.log (JSONL, 10MB×3轮转)
  持久化缓存: ~/.qclaw/.monitor_persistent_cache.json
  告警状态: ~/.qclaw/.monitor_alerts.json
  配置hash: ~/.qclaw/.config_hashes.json
```

```bash
# 重启服务（推荐用 dev.sh）
./dev.sh restart

# 手动重启
kill $(lsof -ti :8849) 2>/dev/null
nohup python3 mac_ai_monitor.py > /tmp/mam.log 2>&1 &

# 验证
curl -s --max-time 5 http://127.0.0.1:8849/health
curl -s --max-time 5 http://127.0.0.1:8849/api/data/lite | python3 -c "
import sys,json; d=json.load(sys.stdin)
print('版本:', d.get('version'), '| Gateway:', d.get('gateway',{}).get('count'))
"
```

---

## 2. 代码架构

### mac_ai_monitor.py (2099行)

```
=== SECTION: IMPORTS & CONFIGURATION (L1-68) ===
  L20-28    配置常量 (PORT, CACHE_TTL, GPU_CACHE_TTL, CPU_TTL, VERSION)
  L34-43    结构化日志 (JSONL + RotatingFileHandler 10MB×3)
  L50-68    历史数据 + 多层缓存变量 + Network首帧保护

=== SECTION: HELPER FUNCTIONS (L70-411) ===
  L72-118   _parse_cron_field() / _cron_next()
  L125-141  run_cmd() — Shell 执行, 编码自动检测
  L143-217  Session 文件索引 (scan/load/build, _load_messages 嵌套解包)
  L219-236  esc() / fmt_uptime() / try_json()
  L238-260  send_alert() — macOS通知 + 30min冷却
  L262-314  tail_errors() / get_log_sizes() — 多日志源
  L316-411  discover_configs() / extract_instances() / get_config_hash()
             → 4路径配置发现 + hash变更 + 实例/模型提取

=== SECTION: DATA COLLECTORS (L413-1622) ===
  L415-516  Website Monitoring: _check_endpoint / _get_ssl_days_left / _check_dns / _collect_endpoints
  L518-636  _SharedProcs: 共享资源加载器 (ps/lsof/sysctl 单次合并)
             _detect_source_raw(): PPID链追溯 (4规则匹配)
  L639-674  _detect_config_changes()
  L675-738  _collect_system(): sysctl 单次合并 (含 cores/temp 采集)
  L739-760  _collect_gpu(): system_profiler (600s缓存)
  L761-900  _collect_cpu(): top -l 1 (180s缓存, 线程锁)
  L902-942  _collect_network(): netstat -ib (首帧保护)
  L944-965  _collect_disk_io(): iostat → psutil fallback
  L967-1016 _collect_processes(): Top进程 (CPU/MEM)
  L994-1016 _collect_ports(): lsof + KNOWN_PORTS
  L1018-1201 _collect_gateway(): 4规则匹配 + 合并 + 性能API + 健康评分
  L1202-1247 _collect_data_dirs(): du -sk (5min缓存, 超时降级)
  L1249-1289 _collect_cron(): 4路径 jobs.json
  L1291-1349 _collect_skills(): 3级目录扫描 (5min缓存)
  L1351-1473 _collect_session_stats() / _collect_activity()
             → 嵌套JSONL解析 (_get_user_content) / 过滤系统消息 / assistant fallback
  L1624-1671 _update_history()

=== collect_all() (L1673-1752) ===
  ThreadPoolExecutor 并行采集 → 合并 → 缓存 → 持久化

=== 持久化缓存 + 轻量端点 (L1825-1886) ===
  _load_persistent_cache() / _save_persistent_cache() / _lite_data()

=== HTTP Handler (L1888-2053) ===
  /health / /api/data / /api/data/lite / /api/status / /api/gateway-log / /static/*

=== Main (L2054-2099) ===
  _shutdown() + GPU预热线程 + 启动 (preexec_fn=os.setpgrp 独立进程组)
```

### index.html (46行)

HTML 骨架，引用外部静态资源：
- `<link href="static/css/style.css">` — 样式
- `<script src="static/js/app.js">` — 逻辑

### static/css/style.css (283行)

暗色主题 + 4断点响应式：
- ≥1200px: 桌面 (Quick Bar 8列, Gauges 3列)
- 768-1199px: 平板 (Quick Bar 4列)
- 480-767px: 手机 (Quick Bar 2列)
- <480px: 小屏 (Quick Bar 1列)

### static/js/app.js (940行)

```
工具函数: fmt_pct/fmt_gb/fmt_mb/fmt_net/fmt_size/esc
  fmt_net(): KB/s ↔ MB/s 自动切换
  fmt_size(): "14Gi" → "14.0GB" 格式转换

render(d): 全量 HTML 渲染
  Quick Bar (CPU/内存/磁盘/Swap/GW/网络/IO/电池/会话)
  → Gauges (3 SVG 环形图: CPU/内存/磁盘 used_pct)
  → Health Bar → System/网络/存储卷 → 内存详情/端口/模型
  → Top进程 → Gateway(运行+闲置) → Activity/Cron
  → 会话统计(Tokens/Messages/ToolCalls) → Skills/日志/数据目录

刷新控制: 默认 Manual，可切换 Auto(30s)，Tab隐藏暂停
```

---

## 3. 数据采集

### 共享执行（单次加载，多 Collector 复用）

```
ps aux (1次) → ps_procs ──┬→ Top Processes
                         ├→ Ports 进程名匹配
                         └→ Gateway 实例检测

lsof -i -P -n (1次) ────┬→ lsof_listen → Ports
                         └→ Gateway 端口

ps -eo pid,ppid,comm (1次) → ppid_map → detect_source (PPID链)

sysctl (1次合并) → boottime/memsize/ncpu/os/cpu/loadavg/swapusage
diskutil list (1次) → 所有卷名
```

### 并行采集 (ThreadPoolExecutor)

```
collect_all()
  ├── config_changes, log_stats          (直接)
  ├── _collect_system(shared)            (sysctl单次)
  ├── _collect_gpu(shared)               (600s缓存)
  ├── _collect_cpu(shared)               (180s缓存, 线程锁)
  ├── _collect_memory(shared, system)    (vm_stat + hw.memsize)
  ├── _collect_disk(shared)              (df -h)
  ├── _collect_volumes(shared)           (df + diskutil)
  ├── _collect_battery()                 (pmset)
  ├── _collect_network(shared)           (netstat -ib)
  ├── _collect_disk_io()                 (iostat)
  ├── _collect_connections()             (netstat -an)
  ├── _collect_processes(shared)         (Top CPU/MEM)
  ├── _collect_ports(shared)             (lsof + KNOWN_PORTS)
  ├── _collect_gateway(shared, ...)      (PPID链 + 4规则)
  ├── _collect_data_dirs()               (du -sk)
  ├── _collect_cron()                    (jobs.json)
  ├── _collect_skills()                  (5min缓存)
  ├── _collect_session_stats()           (采样估算20条)
  └── _collect_activity()                (50条, 时间分组)
```

---

## 4. 缓存策略

| 缓存层 | TTL | 说明 |
|--------|-----|------|
| `_data` (主缓存) | 180s | collect_all() 结果，/api/data |
| `_lite_cache` | 60s | 轻量指标，/api/data/lite |
| `_gpu_cache_store` | 600s | GPU 数据，后台预热 |
| `_cpu_cache` | 180s | CPU top 数据，线程锁 |
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

---

## 6. Activity 解析

支持两种 session 格式：

### QClaw (JSONL，嵌套结构)
```
{"type":"message","message":{"role":"user","content":[{"type":"text","text":"..."}]}}
{"type":"message","message":{"role":"assistant","content":"...","tool_calls":[...]}}
```

### Hermes (单 JSON doc)
```json
{"messages":[{"role":"user","content":"字符串"},{"role":"assistant","content":"..."}]}
```

解析流程：
1. `_session_label()`: 读取 16KB 头部
2. `_get_user_content()`: 解包嵌套 `message` 字段 + 数组/字符串 content
3. 过滤系统消息 (Conversation info/Sender/HEARTBEAT/gbrain 等)
4. 返回 user 消息前50字作为摘要
5. Fallback: user 消息全被过滤时取 assistant 消息摘要

---

## 7. 启动与信号处理

```python
# 独立进程组，避免 shell 退出时被 SIGTERM 杀掉
preexec_fn=os.setpgrp
```

信号处理：SIGTERM/SIGINT → 保存缓存 → 优雅退出

---

## 8. 版本历史

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
