# Mac AI Monitor - API 文档

> **版本**: v2.11.0 | **更新**: 2026-05-18

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
  "version": "2.11.0",
  "gateway_count": 2
}
```

**状态说明：**
- `ok` — 所有检查通过
- `warming` — 服务刚启动，缓存为空
- `degraded` — 部分采集失败
- `error` — 关键故障

---

### `GET /api/data`

返回完整监控数据（180s 缓存）。前端主面板的数据源。

**响应字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `version` | string | 版本号 (如 "2.11.0") |
| `cpu` | object | CPU 使用率 |
| `cpu.user_pct` | number | 用户态 CPU 占比 (%) |
| `cpu.sys_pct` | number | 内核态 CPU 占比 (%) |
| `cpu.idle_pct` | number | 空闲 CPU 占比 (%) |
| `cpu.used_pct` | number | 总使用率 (%) |
| `cpu.cores` | number/null | CPU 核心数 (sysctl hw.ncpu) |
| `cpu.temp` | number/null | CPU 温度 (°C, 需免密 sudo) |
| `memory` | object | 内存信息 |
| `memory.used_pct` | number | 内存使用率 (%) |
| `memory.active_gb` | number | Active 内存 (GB) |
| `memory.wired_gb` | number | Wired 内存 (GB) |
| `memory.free_gb` | number | Free 内存 (GB) |
| `memory.total_gb` | number | 总内存 (GB) |
| `memory.available_gb` | number | 可用内存 (GB) |
| `disk` | object | 磁盘信息 |
| `disk.used` | string | 已用空间（原始格式，如 "14Gi"） |
| `disk.size` | string | 总空间（原始格式，如 "233Gi"） |
| `disk.used_pct` | number | 磁盘使用率 (%) |
| `disk.volumes` | array | 各卷详情 |
| `swap` | object | Swap 信息 |
| `swap.used_gb` | number/null | 已用 Swap (GB) |
| `swap.total_gb` | number/null | 总 Swap (GB) |
| `network` | object | 网络速率 |
| `network.rx_kbps` | number/null | 下载速率 (KB/s) |
| `network.tx_kbps` | number/null | 上传速率 (KB/s) |
| `network.connections` | number/null | TCP 连接数 |
| `gateway` | object | Gateway 进程信息 |
| `gateway.count` | number | Gateway 进程数 |
| `gateway.pids` | array/null | Gateway PID 列表 |
| `gateway.ports` | array/null | 监听端口列表 |
| `gateway.procs` | array | 进程详情 (pid/name/status/model/port/age) |
| `gateway.idle` | array | 闲置 Gateway (无活跃连接) |
| `gateway.health` | object | 健康评分 (system/gateway/endpoint) |
| `disk_io` | object | 磁盘 IO |
| `disk_io.read_mbps` | number/null | 读取速率 (MB/s) |
| `disk_io.write_mbps` | number/null | 写入速率 (MB/s) |
| `battery` | object | 电池信息（仅笔记本，桌面端为 null） |
| `battery.percent` | number | 电池电量 (%) |
| `battery.status` | string | 状态（充电中/放电中/已满） |
| `system` | object | 系统信息 |
| `system.hostname` | string | 主机名 |
| `system.os` | string | 操作系统版本 |
| `system.uptime` | string | 运行时长 (如 "3d 5h 23m") |
| `system.cpu_model` | string | CPU 型号 |
| `skills` | object | Skills 统计 |
| `skills.total_skills` | number | 已安装 skills 数量 |
| `skills.tool_stats` | array | 工具调用统计 `[[name, count], ...]` |
| `sessions` | object | 会话统计 |
| `sessions.total` | number | 总会话数 |
| `sessions.total_tokens` | number | 估算总 Token 数 |
| `sessions.assistant_messages` | number | 助手消息数 |
| `sessions.tool_calls` | number | 工具调用次数 |
| `activity` | array | 最近活动 (最多50条) |
| `activity[].time` | string | 时间 (如 "07:29") |
| `activity[].dir` | string | 来源 (QClaw/Hermes/AutoClaw) |
| `activity[].name` | string | 活动摘要 |
| `activity[].age_m` | number | 距今分钟数 |
| `activity[].group` | string | 分组 (今天/昨天/更早) |
| `cron` | array | 定时任务列表 |
| `config_changes` | array | 配置变更检测 |
| `log_stats` | array | 日志大小统计 |
| `top_procs` | array | Top 进程 (CPU/MEM) |
| `ports` | array | 活跃端口 |
| `volumes` | array | 存储卷 |
| `history` | array | 历史数据点 (最近60条) |
| `data_dirs` | object | 数据目录大小 |

---

### `GET /api/data/lite`

轻量数据端点（60s 缓存），仅包含快速指标。Manual 模式下前端使用此端点。

**响应字段：** cpu, memory, disk, swap, network, battery, gateway.count, version

---

### `GET /api/status`

标准化健康状态，供 UptimeRobot 等外部监控使用。

---

### `GET /api/gateway-log`

Gateway 日志内容。

**查询参数：**
- `lines` — 返回行数（默认 50）
- `grep` — 过滤关键词

---

### `GET /static/*`

静态文件服务。返回 `static/` 目录下的文件。

- `static/css/style.css` — 样式表 (text/css)
- `static/js/app.js` — 前端脚本 (application/javascript)

---

## 错误码

| 状态码 | 说明 |
|--------|------|
| 200 | 成功 |
| 404 | 路由不存在 / 静态文件缺失 |
| 503 | 服务不可用（缓存为空 + warming 状态） |
| 500 | 内部错误 |

---

## 数据刷新

- **Manual 模式**（默认）：点击刷新按钮读取缓存数据
- **Auto 模式**：30s 自动刷新，Tab 隐藏时暂停
- `/api/data` 缓存 180s，`/api/data/lite` 缓存 60s
- 持久化缓存：服务重启后立即恢复上次数据（<30min 有效）
