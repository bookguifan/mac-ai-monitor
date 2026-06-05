# Mac AI Monitor - API 文档

> **版本**: v2.15.2 | **更新**: 2026-06-04

## 基准 URL

```
http://127.0.0.1:8849
```

---

## 端点

### `GET /`

仪表盘页面。HTML 骨架 + 引用 `static/css/style.css` 和 `static/js/app.js`。

---

### `GET /health`

返回服务健康状态。

**响应示例：**

```json
{
  "status": "ok",
  "version": "2.15.2",
  "gateway_count": 2
}
```

**状态说明：**
- `ok` — 所有检查通过
- `warming` — 服务刚启动，缓存为空（返回 HTTP 200）
- `degraded` — 部分采集失败
- `error` — 关键故障

---

### `GET /api/data`

返回完整监控数据（60s 缓存）。前端主面板的数据源。

**响应字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `version` | string | 版本号 (如 "2.15.2") |
| `timestamp` | string | 数据时间戳 |
| `run_path` | string | 运行时入口文件名 |
| **cpu** | | |
| `cpu.user_pct` | number | 用户态 CPU 占比 (%) |
| `cpu.sys_pct` | number | 内核态 CPU 占比 (%) |
| `cpu.idle_pct` | number | 空闲 CPU 占比 (%) |
| `cpu.used_pct` | number | 总使用率 (%) |
| `cpu.cores` | number/null | CPU 核心数 (sysctl hw.ncpu) |
| `cpu.temp` | number/null | CPU 温度 (°C, 需免密 sudo powermetrics) |
| `cpu.load_1m` | number | 1分钟平均负载 |
| `cpu.load_5m` | number | 5分钟平均负载 |
| `cpu.load_15m` | number | 15分钟平均负载 |
| `cpu.history` | array | 历史趋势 (最多60条) |
| **mem** | | |
| `mem.used_pct` | number | 内存使用率 (%) |
| `mem.active_gb` | number | Active 内存 (GB) |
| `mem.wired_gb` | number | Wired 内存 (GB) |
| `mem.free_gb` | number | Free 内存 (GB) |
| `mem.inactive_gb` | number | Inactive 内存 (GB) |
| `mem.speculative_gb` | number | Speculative 内存 (GB) |
| `mem.purgeable_gb` | number | Purgeable 内存 (GB) |
| `mem.available_gb` | number | 可用内存 (GB) |
| `mem.avail_pct` | number | 可用内存占比 (%) |
| `mem.total_gb` | number | 总内存 (GB) |
| `mem.history` | array | 历史趋势 (最多60条) |
| **disk** | | |
| `disk.used` | string | 已用空间（原始格式，如 "14Gi"） |
| `disk.size` | string | 总空间（原始格式，如 "233Gi"） |
| `disk.available` | string | 可用空间（原始格式） |
| `disk.used_pct` | number | 磁盘使用率 (%) |
| `disk.size_gb` | number | 总空间 (GB) |
| `disk.used_gb` | number | 已用空间 (GB) |
| `disk.available_gb` | number | 可用空间 (GB) |
| `disk.volumes` | array | 各卷详情 |
| `disk.history` | array | 历史趋势 (最多60条) |
| **swap** | | |
| `swap.used_gb` | number/null | 已用 Swap (GB) |
| `swap.total_gb` | number/null | 总 Swap (GB) |
| `swap.used_pct` | number | Swap 使用率 (%) |
| `swap.history` | array | 历史趋势 (最多60条) |
| **net** | | |
| `net.rx_kbps` | number/null | 下载速率 (KB/s) |
| `net.tx_kbps` | number/null | 上传速率 (KB/s) |
| `net.interface` | string | 网络接口名 (如 "en0") |
| `net.connections_est` | number | ESTABLISHED 连接数 |
| `net.connections_listen` | number | LISTEN 连接数 |
| `net.connections_timewait` | number | TIME_WAIT 连接数 |
| **gateway** | | |
| `gateway.count` | number | Gateway 实例数 |
| `gateway.merged_count` | number | 按软件名合并后数量 |
| `gateway.idle_count` | number | 闲置实例数 |
| `gateway.health_score` | number | 健康评分 (0-100) |
| `gateway.models` | array | 所有模型名列表 |
| `gateway.instances` | array | 实例详情（见下表） |
| `gateway.merged` | array | 合并后实例详情（见下表） |
| `gateway.idle_instances` | array | 闲置实例列表 |
| **gateway.instances 子字段** | | |
| `instances[].pid` | string | 进程 ID |
| `instances[].name` | string | 实例名 |
| `instances[].software` | string | 软件标识 (OpenClaw/QClaw/Hermes/AutoClaw/JVS) |
| `instances[].source` | string | 来源 (OpenClaw/QClaw/Hermes/AutoClaw/JVS) |
| `instances[].status` | string | 运行状态 |
| `instances[].primary_model` | string | 主模型名 |
| `instances[].image_model` | string | 图像模型名 |
| `instances[].port` | string | 监听端口 |
| `instances[].bind` | string | 绑定地址 |
| `instances[].config_path` | string | 配置文件路径 |
| `instances[].workspace` | string | 工作区路径 |
| `instances[].channels` | array | 已配置通道 |
| `instances[].heartbeat` | string | 心跳时间 |
| `instances[].cpu_pct` | number | CPU 占用 (%) |
| `instances[].rss_mb` | number | 内存占用 (MB) |
| **gateway.merged 子字段** | | |
| `merged[].name` | string | 合并名 |
| `merged[].software` | string | 软件标识 |
| `merged[].source` | string | 来源 |
| `merged[].status` | string | 运行状态 |
| `merged[].primary_model` | string | 主模型名 |
| `merged[].image_model` | string | 图像模型名 |
| `merged[].port` | string | 主端口 |
| `merged[].bind` | string | 绑定地址 |
| `merged[].config_path` | string | 配置文件路径 |
| `merged[].workspace` | string | 工作区路径 |
| `merged[].channels` | array | 已配置通道 |
| `merged[].heartbeat` | string | 心跳时间 |
| `merged[].cpu_pct` | number | CPU 占用 (%) |
| `merged[].rss_mb` | number | 总内存占用 (MB) |
| `merged[].pid_count` | number | 合并的进程数 |
| `merged[].pids` | array | PID 列表 |
| `merged[].rpm` | number | 每分钟请求数 |
| `merged[].latency_ms` | number | 延迟 (ms) |
| `merged[].error_rate` | number | 错误率 |
| **disk_io** | | |
| `disk_io.read_mbps` | number/null | 读取速率 (MB/s) |
| `disk_io.write_mbps` | number/null | 写入速率 (MB/s) |
| `disk_io.iops` | number/null | IOPS |
| **battery** | | |
| `battery.percent` | number | 电池电量 (%) |
| `battery.charging` | bool | 是否充电中 |
| `battery.status_cn` | string | 中文状态 (充电中/放电中/已满) |
| **system** | | |
| `system.hostname` | string | 主机名 |
| `system.os_version` | string | 操作系统版本 |
| `system.uptime_sec` | number | 运行秒数 |
| `system.uptime_str` | string | 运行时长 (如 "5天 16小时 12分") |
| `system.cpu_name` | string | CPU 型号 |
| `system.cpu_p` | number | 物理核心数 |
| `system.cpu_l` | number | 逻辑核心数 |
| `system.memory_gb` | number | 总内存 GB |
| `system.gpu` | array | GPU 信息列表 |
| **sessions** | | |
| `sessions.total_tokens` | number | 估算总 Token 数 |
| `sessions.total_messages` | number | 总消息数 |
| `sessions.active_sessions` | number | 活跃会话数 |
| `sessions.today_tokens` | number | 今日 Token 数 |
| **skills** | | |
| `skills.<Software>` | object | 按软件分组的 skills（键为 QClaw/AutoClaw/JVS） |
| `skills.<Software>.内置` | array | 内置 skills 名称列表 |
| `skills.<Software>.自定义` | array | 自定义 skills 名称列表 |
| **thresholds** | | |
| `thresholds.cpu` | object | CPU 阈值 {health/alert/warn/danger} |
| `thresholds.mem` | object | 内存阈值 {health/alert/warn/danger} |
| `thresholds.disk` | object | 磁盘阈值 {health/alert/warn/danger} |
| `thresholds.swap` | object | Swap 阈值 {health/alert/warn/danger} |
| `thresholds.bat` | object | 电池阈值 {low/warn} |
| `thresholds.volume` | object | 卷阈值 {danger} |
| **processes** | | |
| `processes.top_cpu` | array | CPU 占用 Top 进程 |
| `processes.top_mem` | array | 内存占用 Top 进程 |
| **其他** | | |
| `activity` | array | 最近活动 (最多50条) |
| `cron` | array | 定时任务列表 |
| `config_changes` | array | 配置变更检测 |
| `log_stats` | array | 日志大小统计 |
| `ports` | array | 活跃端口 |
| `data_dirs` | array | 数据目录大小 |
| `all_models` | array | 所有模型名 |
| `all_providers` | array | 所有 Provider |
| `port_conflicts` | object | 端口冲突 |
| `recent_errors` | array | 最近错误 |
| `tool_stats` | array | 工具调用统计 `[[name, count], ...]` |
| `network_history` | array | 网络历史数据 [{rx, tx}, ...] |

---

### `GET /static/*`

静态文件服务。返回 `static/` 目录下的文件。

- `static/css/style.css` — 样式表 (text/css)
- `static/js/app.js` — 前端脚本 (application/javascript)

### `GET /api/status`

标准化健康状态，供 UptimeRobot 等外部监控使用。

### `GET /api/gateway-log`

Gateway 日志内容。

**查询参数：**
- `lines` — 返回行数（默认 50）
- `grep` — 过滤关键词

---

## ⚠️ 已移除端点

| 端点 | 说明 | 移除原因 |
|------|------|----------|
| `GET /api/data/lite` | 轻量数据 | 代码中已不存在，功能合并入 /api/data |

---

## 错误码

| 状态码 | 说明 |
|--------|------|
| 200 | 成功（含 warming 状态） |
| 404 | 路由不存在 / 静态文件缺失 |
| 503 | 服务不可用 |
| 500 | 内部错误 |

---

## 数据刷新

- **Manual 模式**（默认）：点击刷新按钮读取缓存数据
- **Auto 模式**：30s 自动刷新，Tab 隐藏时暂停
- `/api/data` 缓存 60s
- v2.15.0 起已移除持久化缓存，服务重启后首次请求触发全量采集

---

## 告警

- macOS 系统通知 + 飞书 Webhook 推送
- 30min 冷却机制避免频繁打扰
- 飞书配置：将 Webhook URL 写入 `data/feishu_webhook`

### `GET /api/alerts`

返回告警历史记录（从 `data/alerts.json` 读取）。

**响应示例：**
```json
[
  {
    "key": "cpu_high",
    "title": "⚠️ CPU 使用率过高",
    "message": "CPU 使用率 92.6%",
    "time": "2026-05-24 06:34:36"
  }
]
```

---

### `GET /api/export/json`

返回当前监控快照的 JSON 格式（与 `/api/data` 相同结构）。

---

### `GET /api/export/csv`

返回当前监控快照的 CSV 格式。

---

### `GET /api/process/<pid>`

返回指定 PID 的进程详情。

**路径参数：**
- `pid` — 进程ID (1~999999)

**响应示例：**
```json
{
  "pid": 1234,
  "found": true,
  "cpu_pct": 12.5,
  "mem_pct": 3.2,
  "rss_mb": 256,
  "vsz_mb": 512,
  "nice": "0",
  "elapsed": "5-10:23:45",
  "command": "/usr/bin/python3 mac_ai_monitor.py"
}
```

**错误码：**
| 状态码 | 说明 |
|--------|------|
| 400 | PID 格式无效或超出范围 |
