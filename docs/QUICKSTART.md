# Mac AI Monitor — 5分钟入门

> **找不到答案** → [README.md](README.md) 项目首页 | [ARCHITECTURE.md](ARCHITECTURE.md) 代码架构 | [API.md](API.md) 接口文档
> **版本**: v2.11.0 | **更新**: 2026-05-22

---

## 🚀 访问与重启

| 操作 | 命令 |
|------|------|
| 访问面板 | `http://127.0.0.1:8849` |
| 重启 | `./dev.sh restart` |
| 检查状态 | `./dev.sh status` |
| 手动重启 | `kill $(lsof -ti :8849); nohup python3 mac_ai_monitor.py &` |

---

## 🔧 修改流程

### ⚠️ 修改前必读

**改 static/ 文件前**，先验证服务用的是外部文件还是内联 HTML_PAGE：
```bash
curl -s http://127.0.0.1:8849/ | grep -E "link.*css|script.*js"
```
- ✅ 有输出 → 用的是外部文件，可以改
- ❌ 无输出 → 用的是内联版本，改 static/ 无效，需先改 Python 路由

### 标准流程（6步）

```bash
grep -n "关键词" mac_ai_monitor.py     # 1. 定位
# 编辑代码...                           # 2. 修改
python3 -m py_compile mac_ai_monitor.py # 3. 语法检查
./dev.sh restart                       # 4. 重启
# 浏览器 Cmd+Shift+R 硬刷新确认          # 5. 验证
./dev.sh commit "feat: 描述"           # 6. 提交
```

### 按修改类型选择

| 修改内容 | 改哪个文件 | 额外步骤 |
|----------|-----------|----------|
| 样式/布局/颜色 | `static/css/style.css` | 硬刷新浏览器 |
| 前端逻辑/渲染 | `static/js/app.js` | 硬刷新浏览器 |
| 数据采集/后端 | `mac_ai_monitor.py` | py_compile + restart |
| 新增API端点 | `mac_ai_monitor.py` Handler类 | 同步更新 [API.md](API.md) |
| 端口/缓存/配置常量 | `mac_ai_monitor.py` L13-27 | 同步更新 [ARCHITECTURE.md](ARCHITECTURE.md) |

---

## ✅ 检查清单

### 每次修改后（必做）

- [ ] `python3 -m py_compile mac_ai_monitor.py` 语法通过（改过 .py 才需）
- [ ] `./dev.sh status` 服务运行正常
- [ ] 浏览器 **Cmd+Shift+R** 硬刷新，页面正常显示
- [ ] `./dev.sh commit "类型: 描述"` 提交备份

### 发布/重大修改后（选做）

- [ ] `curl -s http://127.0.0.1:8849/api/data \| python3 -m json.tool` API 字段完整
- [ ] `curl -s http://127.0.0.1:8849/health` 健康状态 ok
- [ ] 移动端/平板打开面板，响应式布局正常
- [ ] 更新 [CHANGELOG.md](CHANGELOG.md) 版本记录
- [ ] 如改了行号 → 更新 [ARCHITECTURE.md](ARCHITECTURE.md)
- [ ] 如改了API → 更新 [API.md](API.md)

---

## 📍 快速定位

| 任务 | 文件 | 关键标识 |
|------|------|----------|
| 版本号 | mac_ai_monitor.py L23 | `__version__='2.11.0'` |
| 端口 | mac_ai_monitor.py L13 | `PORT=8849` |
| 缓存TTL | mac_ai_monitor.py L14-17 | `CACHE_TTL/GPU_CACHE_TTL` |
| 告警冷却 | mac_ai_monitor.py L25 | `ALERT_COOLDOWN=1800` |
| 数据采集 | mac_ai_monitor.py L304 | `collect_all()` |
| 活动解析 | mac_ai_monitor.py L1227 | `# ---- Activity ----` |
| HTTP路由 | mac_ai_monitor.py L2399 | `class Handler` |
| 前端渲染 | static/js/app.js | `render(d)` |
| CSS样式 | static/css/style.css | 暗色主题变量 |
| GPU信息 | mac_ai_monitor.py L372 | `# ---- GPU ----` |

> 完整行号参考 → [ARCHITECTURE.md](ARCHITECTURE.md)

---

## 🆘 常见问题

| 症状 | 排查 |
|------|------|
| 页面空白/卡加载 | F12→Console 看报错，`curl /api/data` 检查返回 |
| CSS/JS 修改不生效 | 检查是否用了内联 HTML_PAGE（见上方"修改前必读"） |
| 首次API慢 | 正常，GPU预热~15s+CPU~5s，缓存后<100ms |
| 活动显示"Session @" | 清缓存: `rm ~/.qclaw/.monitor_persistent_cache.json` 重启 |
| 数据字段为None | `curl /api/data \| python3 -m json.tool` 查原始数据 |
| 服务被signal 15杀 | 确认 `__main__` 有 `preexec_fn=os.setpgrp` |
| 改坏了想回滚 | `git checkout -- mac_ai_monitor.py && ./dev.sh restart` |
