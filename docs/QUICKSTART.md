# Mac AI Monitor — 代码修改流程

> 合并风险分层 + Mac AI Monitor 特定检查 + 任务artifact规范
> **版本**: v2.14.0 (流程更新) | **更新**: 2026-05-23

---

## ⚠️ 修改前必读（每次都要看）

### 1. 先读文档再动手

修改前先读对应文档，了解现有结构再改：
- 改数据采集/后端 → 先读 [ARCHITECTURE.md](ARCHITECTURE.md) 对应行号段落
- 改 API 端点 → 先读 [API.md](API.md) 对应字段定义
- 改前端渲染 → 先读 `static/js/app.js` 的 `render(d)` 函数
- 不确定改哪 → 先读本文档「快速定位」表格

### 2. 静态文件修改前必查

**改 `static/` 文件前**，先验证服务用的是外部文件还是内联 `HTML_PAGE`：
```bash
curl -s http://127.0.0.1:8849/ | grep -E "link.*css|script.*js"
```
- ✅ 有输出 → 用的是外部文件，可以改 `static/css/style.css` 和 `static/js/app.js`
- ❌ 无输出 → 用的是内联版本（`HTML_PAGE` 变量），改 `static/` **无效**，需先改 Python 路由或切换为外部文件模式

> 💡 **静态文件改完无需重启 Python 服务**，只需浏览器 **Cmd+Shift+R** 硬刷新即可生效。

### 3. 起飞前检查（Pre-flight）

```bash
./dev.sh status              # 服务是否在跑？
git status                   # 当前代码是否已提交？（未提交 → 先 commit 备份）
python3 -m py_compile mac_ai_monitor.py   # 当前代码是否能编译？（改 .py 前必查）
```

---

## 🎯 流程总览（按风险分层）

根据改动大小选择对应流程，兼顾效率与安全。

| 改动类型 | 流程 | 适用场景 |
|----------|------|----------|
| 🟢 简单修复 | 极简3步 | Bug修复、警告清理、小逻辑调整（80%日常） |
| 🟡 中等改动 | 标准6步 | 功能优化、超时调整、新增字段等中等风险 |
| 🔴 大改动 | 完整8步 | 新增功能、重大重构、协议变更 |

> 💡 **版本号升级时机说明**：本文档将「升级版本号」放在修改前（完整流程第2步），
> 便于 git bisect 和回滚时区分版本。原图片流程放在修改后，两种都可以，选你习惯的。

---

## 🟢 极简流程（简单修复，80%场景）

适用于：警告修复、小Bug、文案调整、样式微调

```bash
# 1. 定位
grep -n "关键词" mac_ai_monitor.py   # 或 static/js/app.js / static/css/style.css

# 2. 修改 + 语法检查
edit 修改
python3 -m py_compile mac_ai_monitor.py  # 仅改过 .py 需要

# 3. 重启（仅 .py 改动需要）+ 验证
./dev.sh restart              # 仅改 .py 需要；改 static/ 直接跳到下一步
curl -s http://127.0.0.1:8849/api/data | python3 -m json.tool  # 检查数据
# 浏览器 Cmd+Shift+R 硬刷新
```

✅ 完成后：
```bash
./dev.sh commit "fix: 描述"
# 写任务记录文件（见「任务 Artifact」章节）
```

---

## 🟡 标准流程（中等改动）

适用于：功能优化、新增API字段、调整采集逻辑

```bash
# 1. 定位 + 阅读上下文（理解现有逻辑）
grep -n "关键词" mac_ai_monitor.py
# 阅读相关函数（前后50行），先读 ARCHITECTURE.md 对应段落

# 2. 备份当前状态
./dev.sh commit "backup: 修改前备份"

# 3. 【用户确认】向用户说明修改方案，确认后再执行
# → 说明：改哪个文件的哪几行、预期行为变化、有无副作用

# 4. 执行修改 + 语法检查
edit 修改
python3 -m py_compile mac_ai_monitor.py

# 5. 重启 + 充分验证
./dev.sh restart
./dev.sh status              # 服务运行正常
curl -s http://127.0.0.1:8849/health  # 健康状态 ok
curl -s http://127.0.0.1:8849/api/data | python3 -m json.tool  # 字段完整

# 6. 提交 + 更新文档（如需要）+ 写 artifact
./dev.sh commit "feat: 描述"
# 如改了API → 同步更新 docs/API.md
# 如改了配置常量 → 同步更新 docs/ARCHITECTURE.md
# 写任务记录文件（见「任务 Artifact」章节）
```

---

## 🔴 完整流程（大改动/新功能）

适用于：新增端点、重大重构、协议/配置格式变更

```bash
# 1. 定位 + 完整阅读相关模块
grep -n "关键词" mac_ai_monitor.py
# 阅读 docs/ARCHITECTURE.md 相关行号段落
# 【可选】保存修改前快照：curl /api/data > /tmp/before.json

# 2. 升级版本号（修改前升级，便于区分）
# mac_ai_monitor.py L23: __version__='2.14.0'
edit 修改版本号
./dev.sh commit "chore: bump version to 2.14.0"

# 3. 备份当前状态
./dev.sh commit "backup: before refactor"
# 或: git stash

# 4. 【必须】用户确认修改方案
# → 说明：改动范围、影响模块、回滚方式、测试计划
# → 用户确认后再执行

# 5. 执行修改 + 语法检查
edit 修改
python3 -m py_compile mac_ai_monitor.py
# 如改了 static/ 文件，需额外验证（见「修改前必读」）

# 6. 重启 + 全面验证
./dev.sh restart
./dev.sh health   # 详细指标检查
# 浏览器多端验证（桌面/平板/手机）
curl -s http://127.0.0.1:8849/api/data | python3 -m json.tool
# 【可选】对比修改前后：diff <(cat /tmp/before.json) <(curl -s http://127.0.0.1:8849/api/data)

# 7. 更新文档（含本文档）
# - docs/CHANGELOG.md（记录本次变更）
# - docs/ARCHITECTURE.md（如改了行号）
# - docs/API.md（如改了API）
# - docs/QUICKSTART.md（如改了流程本身）→ 本文档！
# - 如改了行号 → 同步更新 ARCHITECTURE.md 对应行号

# 8. 最终提交 + 写任务记录
./dev.sh commit "feat: 描述（含版本号+文档更新）"
# 写任务记录文件（见「任务 Artifact」章节）
```

---

## 🛠️ dev.sh 命令参考

| 命令 | 功能 | 使用场景 |
|------|------|----------|
| `./dev.sh restart` | 停止 + 启动服务 | 改完 `.py` 文件后 |
| `./dev.sh start` | 启动服务 | 服务停止时 |
| `./dev.sh stop` | 停止服务 | 需要完全停止时 |
| `./dev.sh status` | 运行状态 + 健康检查 | 改完重启后验证 |
| `./dev.sh health` | 详细指标 (CPU/内存/磁盘/GW) | 大改动后全面验证 |
| `./dev.sh lint` | Python 语法检查 | 改完 `.py` 后快速检查 |
| `./dev.sh log` | 查看最近日志 | 排查问题时 |
| `./dev.sh commit "msg"` | Git 提交 | 每完成一步提交一次 |

> 💡 `python3 -m py_compile mac_ai_monitor.py` 等价于 `./dev.sh lint`，二选一即可。

---

## 📋 按修改类型选择文件

| 修改内容 | 改哪个文件 | 额外步骤 |
|----------|-----------|----------|
| 样式/布局/颜色 | `static/css/style.css` | ⚠️ 先查是否用内联HTML_PAGE；**无需重启服务**，硬刷新浏览器即可 |
| 前端逻辑/渲染 | `static/js/app.js` | ⚠️ 先查是否用内联HTML_PAGE；**无需重启服务**，硬刷新浏览器即可 |
| 数据采集/后端逻辑 | `mac_ai_monitor.py` | py_compile + restart |
| 新增API端点 | `mac_ai_monitor.py` Handler类 | py_compile + restart；同步更新 docs/API.md |
| 端口/缓存/配置常量 | `mac_ai_monitor.py` L13-27 | py_compile + restart；同步更新 docs/ARCHITECTURE.md |
| 告警逻辑 | `mac_ai_monitor.py` L119 `send_alert()` | py_compile + restart；测试告警触发 |

> ⚠️ **注意**：改 `static/` 文件前必查内联状态（见「修改前必读」）
> 💡 **提示**：改 `static/` 文件后无需 `./dev.sh restart`，直接浏览器硬刷新即可。

---

## ✅ 检查清单

### 🟢 简单修复（必做）

- [ ] 起飞前检查通过（服务在跑 + 代码已提交 + 能编译）
- [ ] `python3 -m py_compile mac_ai_monitor.py` 语法通过（改过 .py 才需）
- [ ] `./dev.sh status` 服务运行正常（改过 .py 才需重启）
- [ ] 浏览器 **Cmd+Shift+R** 硬刷新，页面/样式正常
- [ ] `./dev.sh commit "fix: 描述"` 提交备份
- [ ] 写任务记录文件

### 🟡 中等改动（必做 + 选做）

必做：
- [ ] 起飞前检查通过
- [ ] 备份当前状态（`./dev.sh commit "backup: ..."`）
- [ ] **用户确认修改方案** 后再执行
- [ ] `python3 -m py_compile mac_ai_monitor.py` 语法通过
- [ ] `./dev.sh status` 服务运行正常
- [ ] `curl -s http://127.0.0.1:8849/health` 健康状态 ok
- [ ] `curl -s http://127.0.0.1:8849/api/data | python3 -m json.tool` API 字段完整
- [ ] `./dev.sh commit "feat: 描述"` 提交
- [ ] 写任务记录文件

选做：
- [ ] 如改了API → 更新 [docs/API.md](API.md)
- [ ] 如改了配置常量 → 更新 [docs/ARCHITECTURE.md](ARCHITECTURE.md)

### 🔴 大改动（完整清单）

- [ ] 起飞前检查通过
- [ ] **用户确认修改方案** 后再执行
- [ ] 升级版本号（`mac_ai_monitor.py` L23）
- [ ] 备份当前状态（`git commit` 或 `git stash`）
- [ ] `python3 -m py_compile mac_ai_monitor.py` 语法通过
- [ ] `./dev.sh status` + `./dev.sh health` 服务运行正常
- [ ] `curl -s http://127.0.0.1:8849/api/data | python3 -m json.tool` API 字段完整
- [ ] 多端验证（桌面/平板/手机）响应式布局正常
- [ ] 更新 [docs/CHANGELOG.md](CHANGELOG.md) 版本记录
- [ ] 如改了行号 → 更新 [docs/ARCHITECTURE.md](ARCHITECTURE.md)
- [ ] 如改了API → 更新 [docs/API.md](API.md)
- [ ] 如改了流程本身 → 更新本文档（docs/QUICKSTART.md）← 别忘了！
- [ ] `./dev.sh commit "feat: 描述（含版本号+文档更新）"` 最终提交
- [ ] 写任务记录文件

---

## 📍 快速定位

| 任务 | 文件 | 关键标识 |
|------|------|----------|
| 版本号 | mac_ai_monitor.py L23 | `__version__='2.14.0'` |
| 端口 | mac_ai_monitor.py L13 | `PORT=8849` |
| 缓存TTL | mac_ai_monitor.py L14-17 | `CACHE_TTL/GPU_CACHE_TTL` |
| 告警冷却 | mac_ai_monitor.py L25 | `ALERT_COOLDOWN=1800` |
| 数据采集 | mac_ai_monitor.py L328 | `collect_all()` |
| 活动解析 | mac_ai_monitor.py L1314 | `# ---- Activity ----` |
| HTTP路由 | mac_ai_monitor.py L2493 | `class Handler` |
| 前端渲染 | static/js/app.js | `render(d)` |
| CSS样式 | static/css/style.css | 暗色主题变量 |
| GPU信息 | mac_ai_monitor.py L407 | `# ---- GPU ----` |

> 完整行号参考 → [ARCHITECTURE.md](ARCHITECTURE.md)

---

回滚指南、常见问题、决策树 → [TROUBLESHOOTING.md](TROUBLESHOOTING.md)

---

## 📝 任务 Artifact（每次修改后必写）

> 遵循 OpenClaw **[MANDATORY] Task Artifacts** 规范：每次完成实质性工作后，必须写 artifact 文件。

**文件位置**：`~/.qclaw/workspace/task-summary_YYYYMMDD-HHMM-描述.md`

**内容模板**：
```markdown
# 任务：<简短描述>

## 目标
<修改了什么，为什么>

## 关键推理
<方案选择理由、踩过的坑、为什么选A不选B>

## 结论
<最终改了哪些文件/行号、验证结果>

## 后续
<有没有遗留问题、建议下一步>
```

**何时写**：
- ✅ 修 Bug、加功能、改配置 → **必须写**
- ✅ 大改动分多步完成 → **每完成一步写一个**
- ❌ 仅查文档、回答问题 → **不用写**

**写在哪里**：`~/.qclaw/workspace/`（OpenClaw 工作区根目录）

---

回滚指南和更多排查 → [TROUBLESHOOTING.md](TROUBLESHOOTING.md)

---

## 📖 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v2.14.0 | 2026-05-23 | 新增：起飞前检查、dev.sh命令参考、版本号时机说明、更新本文档提醒、回滚指南增强 |
| v2.13.0 | 2026-05-23 | 新增：任务 Artifact 规范、回滚指南、决策树可视化 |
| v2.12.0 | 2026-05-23 | 合并风险分层流程（简单/中等/大改动） |
| v2.11.0 | 2026-05-22 | 原始版本 |
