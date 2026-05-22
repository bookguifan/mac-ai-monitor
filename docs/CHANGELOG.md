# Mac AI Monitor — 版本历史

> 每次发布在此记录，README 不再维护版本历史

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
