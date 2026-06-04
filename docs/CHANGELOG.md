# Mac AI Monitor — 版本历史

> 每次发布在此记录，README 不再维护版本历史
> **格式**: 版本号(日期范围) → 分类变更列表

---

## v2.15.1 (2026-06-04)

### Bug 修复
- 修复 CLI 启动的 OpenClaw Gateway 不被检测（`node .../openclaw/dist/index.js gateway` 进程）
- 修复 IPv6 地址 `[::1]:18789` 导致端口误解析为 `1`
- 修复 IPv4/IPv6 同端口重复显示
- 来源识别增加 `openclaw+gateway` 的 fallback 判断

### 架构改进
- Gateway 端口捕获改用 `ps_procs` 预扫描 PID → 再从 lsof 反查（替代 lsof 关键词匹配）
- Gateway 检测规则从 4 条扩展为 5 条（新增 CLI 启动规则）

### 文档更新
- ARCHITECTURE.md: 行号校准 (2757→2782行)、Gateway检测章节重写、缓存策略更新
- QUICKSTART.md: 行号校准 (Activity L1318→L1343, Handler L2497→L2522)
- API.md: 版本号同步 v2.15.0、health 示例版本号修正
- TROUBLESHOOTING.md: 版本号同步
- README.md: 行数更新、持久化缓存说明更新

---

## v2.15.0 (2026-06-04)

### 重构
- 运行时数据从 `~/.qclaw/` 迁移到项目本地 `data/` 目录，项目完全自包含
- 新增 `SCRIPT_DIR` / `DATA_DIR` 常量，7 处 `HOME + .qclaw` 路径改为 `DATA_DIR`
- dev.sh 日志输出改为项目本地 `data/logs/`
- 前端提示文案更新（`~/.qclaw/.monitor_alert_config.json` → `data/alert_config.json`）
- .gitignore 适配新 `data/` 目录结构
- 清理旧 `logs/` 目录，删除 `~/.qclaw/mac_ai_monitor/` 残留

### 文档更新
- README.md: 项目路径、启动命令、项目结构同步更新
- ARCHITECTURE.md: 行号校准 (2753→2757行)、新增运行时数据目录章节、路径引用更新
- API.md: 飞书配置路径、告警文件路径更新
- QUICKSTART.md: 版本号快速定位行号校准

---

## v2.14.0 (2026-05-23)

### Bug 修复
- 修复 disk.size_gb/used_gb/available_gb 始终为 0（`_parse_to_gb()` 不支持 Gi/Mi 格式，改用正则匹配）
- 修复 GPU 采集阻塞主线程 ~10s（改为异步：缓存过期时返回旧数据 + 后台线程刷新）
- 修复 sessions.total_tokens 始终为 0（支持 `message.usage` 嵌套结构和多字段名 `totalTokens`/`total_tokens`/`total`）
- 修复前端历史趋势图不显示（后端 `net_history` → `network_history` 字段名修正）
- 修复前端内存数据显示异常（前端 `d.memory` → `d.mem` 字段名修正）
- 修复前端网络模块数据显示异常（前端 `d.network` → `d.net` 字段名修正）
- 修复 `showAlerts()` 中 NameError: `TH` 未定义 → 改为调用 `get_thresholds()`
- 修复 `get_thresholds()` 作用域问题 → 实现 deep merge 逻辑
- 修复 bare except → `except Exception:`（3处）
- 移除 `collect_all()` 中的死代码 `global _data`（导致 NameError 和服务退出）

### 新功能
- 告警阈值配置化（`~/.qclaw/.monitor_alert_config.json`，API 返回 `thresholds` 字段）
- 自定义刷新间隔（5s/10s/15s/30s/60s 选择器，localStorage 持久化）
- 告警历史面板（Header 🔔按钮，`/api/alerts` 端点，可点击展开详情）
- 数据导出（Header 📤按钮，`/api/export/json` + `/api/export/csv`）
- 进程详情查看（Top 进程行点击展开，`/api/process/<pid>`）

### 安全加固
- 路径遍历防护：`static_dir` 改为 `static/` 子目录，越界请求返回 403
- PID 校验：`/api/process/<pid>` 范围检查 1≤pid≤999999

### 文档优化
- 版本号统一：ARCHITECTURE/API/README 同步至 v2.14.0
- 行号校准：ARCHITECTURE.md 全部行号按实际代码重新标注
- API.md 新增 4 个端点文档（alerts/export/process）
- 新增 TROUBLESHOOTING.md 回滚指南
- API.md：移除不存在的 /api/data/lite 端点，补回 /api/status 和 /api/gateway-log
- 去重：README dev.sh 命令表改为链接引用 QUICKSTART
- 拆分：从 QUICKSTART 拆出 TROUBLESHOOTING.md（回滚指南+常见问题+决策树）
- 行数更新：mac_ai_monitor.py 2574行, style.css 311行, app.js 1114行

## v2.12.0 (2026-05-23)

### 文档优化
- QUICKSTART.md: 风险分层修改流程（极简3步/标准5步/完整7步）
- README.md: 更新静态文件说明，移除过时警告
- README.md: 版本号同步至 v2.12.0
- CHANGELOG.md: 格式标准化（版本号标题+分类列表）

### dev.sh 工具增强
- commit(): 修复文件列表（index.html + docs/*.md + static/）
- restart(): 新增自动验证（健康检查+静态文件模式）
- watch(): 扩展监听范围（static/css + static/js）
- 新增 verify 命令：一键验证服务状态
- 新增 rollback 命令：回滚文件并重启
- 新增 version 命令：代码/API版本号一致性检查
- health(): 修复字段名 memory→mem

---

## v2.11.0 (2026-05-17 ~ 2026-05-22)

### 2026-05-17
- 阶段1重构: CSS/JS分离、项目目录化、API文档

### 2026-05-18
- 前端优化: 单位自动切换、对比度提升、响应式4断点
- 后端: cpu.cores/temp采集、gateway.pids/ports汇总
- 健康检查: warming状态(缓存为空时返回200而非503)
- Signal 15修复: preexec_fn=os.setpgrp
- Activity: 嵌套JSONL解析、系统消息过滤、assistant fallback
- Session统计: Token估算、assistant_messages/tool_calls
- 默认手动刷新、热门工具undefined修复、页脚路径

### 2026-05-19
- 字段命名统一: memory→mem, network→net
- 磁盘: size_gb/used_gb/available_gb 字段 (df -h 转换)
- 飞书Webhook告警推送
- Gateway日志前端查看按钮 + /api/gateway-log 端点
- 数据目录采集: ThreadPoolExecutor并发du
- GPU显示: name + VRAM格式
- None安全: /health和/api/data防空指针

### 2026-05-22
- 运行时间移到Quick Bar、fmt_uptime中文格式(5天16小时12分)
- 页脚显示项目结构
- 服务优先使用index.html(引用外部CSS/JS)替代HTML_PAGE内联
