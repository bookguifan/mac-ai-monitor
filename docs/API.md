# Mac AI Monitor - API 文档

## 基准 URL

```
http://127.0.0.1:8849
```

## 端点

### `GET /api/health`

返回服务健康状态。

**响应示例：**

```json
{
  "status": "ok",
  "version": "2.11.0",
  "gw_count": 3,
  "uptime": 3600
}
```

**状态说明：**
- `ok` — 所有检查通过
- `warming` — 服务刚启动，缓存尚未完整
- `degraded` — 部分采集失败
- `error` — 关键故障

---

### `GET /api/data`

返回完整监控数据。这是前端主面板的数据源。

**响应字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `cpu` | object | CPU 使用率 |
| `cpu.user_pct` | number | 用户态 CPU 占比 (%) |
| `cpu.sys_pct` | number | 内核态 CPU 占比 (%) |
| `cpu.idle_pct` | number | 空闲 CPU 占比 (%) |
| `cpu.used_pct` | number | 总使用率 (%) |
| `cpu.cores` | number/null | CPU 核心数 |
| `cpu.temp` | number/null | CPU 温度 (°C) |
| `memory` | object | 内存信息 |
| `memory.used_pct` | number | 内存使用率 (%) |
| `memory.active_gb` | number | Active 内存 (GB) |
| `memory.wired_gb` | number | Wired 内存 (GB) |
| `memory.free_gb` | number | Free 内存 (GB) |
| `memory.total_gb` | number | 总内存 (GB) |
| `memory.available_gb` | number | 可用内存 (GB) |
| `disk` | object | 磁盘信息 |
| `disk.used` | string | 已用空间（人类可读，如 "14Gi"） |
| `disk.size` | string | 总空间（人类可读，如 "233Gi"） |
| `disk.used_pct` | number | 磁盘使用率 (%) |
| `disk.volumes` | array | 各卷详情 |
| `network` | object | 网络速率 |
| `network.rx_kbps` | number | 下载速率 (KB/s) |
| `network.tx_kbps` | number | 上传速率 (KB/s) |
| `gateway` | object | Gateway 进程信息 |
| `gateway.pids` | array | Gateway PID 列表 |
| `gateway.ports` | array | 监听端口列表 |
| `gateway.procs` | array | 进程详情 |
| `skills` | object | Skills 统计 |
| `skills.total_skills` | number | 已安装 skills 数量 |
| `skills.tool_stats` | array | 工具调用统计 |
| `sessions` | object | 会话统计 |
| `sessions.total` | number | 总会话数 |
| `battery` | object | 电池信息（仅笔记本） |
| `battery.percent` | number | 电池电量 (%) |
| `battery.status` | string | 状态（充电中/放电中/已满） |

---

### `GET /api/log`

返回最近日志条目。

**查询参数：**
- `n` — 返回条数（默认 50）

**响应示例：**

```json
{
  "log": [
    "2026-05-17 23:04:00,717 INFO persistent cache loaded (176s old)",
    "2026-05-17 23:04:24,247 INFO shutdown signal=15"
  ]
}
```

---

## 错误码

| 状态码 | 说明 |
|--------|------|
| 200 | 成功 |
| 503 | 服务不可用（系统未准备好） |
| 500 | 内部错误 |

---

## 数据刷新

- 前端每 5 秒自动刷新（Auto 模式）
- 可切换为 Manual 模式，手动点击刷新
- Tab 隐藏时自动暂停刷新（节省资源）
