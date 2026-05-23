# Mac AI Monitor — 代码修改流程

> 合并风险分层 + Mac AI Monitor 特定检查
> **版本**: v2.12.0 (流程更新) | **更新**: 2026-05-23

---

## ⚠️ 修改前必读（每次都要看）

### 静态文件修改前必查

**改 `static/` 文件前**，先验证服务用的是外部文件还是内联 `HTML_PAGE`：
```bash
curl -s http://127.0.0.1:8849/ | grep -E "link.*css|script.*js"
```
- ✅ 有输出 → 用的是外部文件，可以改 `static/css/style.css` 和 `static/js/app.js`
- ❌ 无输出 → 用的是内联版本（`HTML_PAGE` 变量），改 `static/` **无效**，需先改 Python 路由或切换为外部文件模式

---

## 🎯 流程总览（按风险分层）

根据改动大小选择对应流程，兼顾效率与安全。

| 改动类型 | 流程 | 适用场景 |
|----------|------|----------|
| 🟢 简单修复 | 极简3步 | Bug修复、警告清理、小逻辑调整（80%日常） |
| 🟡 中等改动 | 标准5步 | 功能优化、超时调整、新增字段等中等风险 |
| 🔴 大改动 | 完整7步 | 新增功能、重大重构、协议变更 |

---

## 🟢 极简流程（简单修复，80%场景）

适用于：警告修复、小Bug、文案调整、样式微调

```bash
# 1. 定位
grep -n "关键词" mac_ai_monitor.py   # 或 static/js/app.js / static/css/style.css

# 2. 修改 + 语法检查
edit 修改
python3 -m py_compile mac_ai_monitor.py  # 仅改过 .py 需要

# 3. 重启 + 验证
./dev.sh restart
curl -s http://127.0.0.1:8849/api/data | python3 -m json.tool  # 检查数据
# 浏览器 Cmd+Shift+R 硬刷新
```

✅ 完成后：
```bash
./dev.sh commit "fix: 描述"
```

---

## 🟡 标准流程（中等改动）

适用于：功能优化、新增API字段、调整采集逻辑

```bash
# 1. 定位 + 阅读上下文（理解现有逻辑）
grep -n "关键词" mac_ai_monitor.py
# 阅读相关函数（前后50行）

# 2. 【可选】备份当前状态
./dev.sh commit "backup: 修改前备份"

# 3. 修改 + 语法检查
edit 修改
python3 -m py_compile mac_ai_monitor.py

# 4. 重启 + 充分验证
./dev.sh restart
./dev.sh status              # 服务运行正常
curl -s http://127.0.0.1:8849/health  # 健康状态 ok
curl -s http://127.0.0.1:8849/api/data | python3 -m json.tool  # 字段完整

# 5. 提交 + 更新文档（如需要）
./dev.sh commit "feat: 描述"
# 如改了API → 同步更新 docs/API.md
# 如改了配置常量 → 同步更新 docs/ARCHITECTURE.md
```

---

## 🔴 完整流程（大改动/新功能）

适用于：新增端点、重大重构、协议/配置格式变更

```bash
# 1. 定位 + 完整阅读相关模块
grep -n "关键词" mac_ai_monitor.py
# 阅读 docs/ARCHITECTURE.md 相关行号段落

# 2. 升级版本号（在修改前升级，便于区分）
# mac_ai_monitor.py L23: __version__='2.12.0'
edit 修改版本号
./dev.sh commit "chore: bump version to 2.12.0"

# 3. 备份当前状态
./dev.sh commit "backup: before refactor"

# 4. 执行修改 + 语法检查
edit 修改
python3 -m py_compile mac_ai_monitor.py
# 如改了 static/ 文件，需额外验证（见"修改前必读"）

# 5. 重启 + 全面验证
./dev.sh restart
./dev.sh health   # 详细指标检查
# 浏览器多端验证（桌面/平板/手机）
curl -s http://127.0.0.1:8849/api/data | python3 -m json.tool

# 6. 更新文档
# - docs/CHANGELOG.md（记录本次变更）
# - docs/ARCHITECTURE.md（如改了行号）
# - docs/API.md（如改了API）
# - docs/QUICKSTART.md（如改了流程）

# 7. 最终提交
./dev.sh commit "feat: 描述（含文档更新）"
```

---

## 📋 按修改类型选择文件

| 修改内容 | 改哪个文件 | 额外步骤 |
|----------|-----------|----------|
| 样式/布局/颜色 | `static/css/style.css` | ⚠️ 先查是否用内联HTML_PAGE；硬刷新浏览器 |
| 前端逻辑/渲染 | `static/js/app.js` | ⚠️ 先查是否用内联HTML_PAGE；硬刷新浏览器 |
| 数据采集/后端逻辑 | `mac_ai_monitor.py` | py_compile + restart |
| 新增API端点 | `mac_ai_monitor.py` Handler类 | py_compile + restart；同步更新 docs/API.md |
| 端口/缓存/配置常量 | `mac_ai_monitor.py` L13-27 | py_compile + restart；同步更新 docs/ARCHITECTURE.md |
| 告警逻辑 | `mac_ai_monitor.py` L119 `send_alert()` | py_compile + restart；测试告警触发 |

> ⚠️ **注意**：改 `static/` 文件前必查内联状态（见"修改前必读"）

---

## ✅ 检查清单

### 🟢 简单修复（必做）

- [ ] `python3 -m py_compile mac_ai_monitor.py` 语法通过（改过 .py 才需）
- [ ] `./dev.sh status` 服务运行正常
- [ ] 浏览器 **Cmd+Shift+R** 硬刷新，页面正常显示
- [ ] `./dev.sh commit "fix: 描述"` 提交备份

### 🟡 中等改动（必做 + 选做）

必做：
- [ ] `python3 -m py_compile mac_ai_monitor.py` 语法通过
- [ ] `./dev.sh status` 服务运行正常
- [ ] `curl -s http://127.0.0.1:8849/health` 健康状态 ok
- [ ] `curl -s http://127.0.0.1:8849/api/data | python3 -m json.tool` API 字段完整
- [ ] `./dev.sh commit "feat: 描述"` 提交

选做：
- [ ] 如改了API → 更新 [docs/API.md](API.md)
- [ ] 如改了配置常量 → 更新 [docs/ARCHITECTURE.md](ARCHITECTURE.md)

### 🔴 大改动（完整清单）

- [ ] 升级版本号（`mac_ai_monitor.py` L23）
- [ ] `python3 -m py_compile mac_ai_monitor.py` 语法通过
- [ ] `./dev.sh status` + `./dev.sh health` 服务运行正常
- [ ] `curl -s http://127.0.0.1:8849/api/data | python3 -m json.tool` API 字段完整
- [ ] 多端验证（桌面/平板/手机）响应式布局正常
- [ ] 更新 [docs/CHANGELOG.md](CHANGELOG.md) 版本记录
- [ ] 如改了行号 → 更新 [docs/ARCHITECTURE.md](ARCHITECTURE.md)
- [ ] 如改了API → 更新 [docs/API.md](API.md)
- [ ] `./dev.sh commit "feat: 描述（含文档更新）"` 最终提交

---

## 📍 快速定位

| 任务 | 文件 | 关键标识 |
|------|------|----------|
| 版本号 | mac_ai_monitor.py L23 | `__version__='2.12.0'` |
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
| CSS/JS 修改不生效 | 检查是否用了内联 HTML_PAGE（见"修改前必读"） |
| 首次API慢 | 正常，GPU预热~15s+CPU~5s，缓存后<100ms |
| 活动显示"Session @" | 清缓存: `rm ~/.qclaw/.monitor_persistent_cache.json` 重启 |
| 数据字段为None | `curl /api/data \| python3 -m json.tool` 查原始数据 |
| 服务被signal 15杀 | 确认 `__main__` 有 `preexec_fn=os.setpgrp` |
| 改坏了想回滚 | `git checkout -- mac_ai_monitor.py && ./dev.sh restart` |

---

## 📊 流程选择决策树

```
要改什么？
│
├─ 改文案/警告/小Bug ─────────────────→ 🟢 极简3步
│
├─ 改采集逻辑/新增字段/调整UI ───────→ 🟡 标准5步
│
└─ 新功能/新API/重构 ───────────────→ 🔴 完整7步
        │
        ├─ 是否需要回滚保障？ ─────→ 是：先 commit 备份
        └─ 是否影响API？ ────────→ 是：更新 docs/API.md
```
