# Mac AI Monitor — 故障排查与回滚

> 回滚指南 + 常见问题 + 流程决策树
> **版本**: v2.14.0 | **更新**: 2026-05-23

---

## 🚨 回滚指南

| 场景 | 操作 |
|------|------|
| 改坏了 `.py` 文件 | `git checkout -- mac_ai_monitor.py && ./dev.sh restart` |
| 改坏了 `static/` 文件 | `git checkout -- static/css/style.css static/js/app.js` + 硬刷新 |
| 想回到修改前（已commit） | `git log --oneline` 找到备份commit → `git revert <hash>` |
| 修改进行到一半想放弃 | `git stash`（如之前 stash 了）或 `git checkout -- .` |
| 版本号升错了 | `git checkout -- mac_ai_monitor.py` → 改回版本号 → 重新 commit |
| 改完发现更多问题 | `git stash` 暂存改动 → 另开一个 commit 修新问题 → `git stash pop` |

> 💡 大改动前务必 `./dev.sh commit "backup: ..."`，确保有回滚点。

---

## 🆘 常见问题

| 症状 | 排查 |
|------|------|
| 页面空白/卡加载 | F12→Console 看报错，`curl /api/data` 检查返回 |
| CSS/JS 修改不生效 | 检查是否用了内联 HTML_PAGE（见 QUICKSTART「修改前必读」） |
| 首次API慢 | 正常，GPU预热~15s+CPU~5s，缓存后<100ms |
| 活动显示"Session @" | 清缓存: `rm ~/.qclaw/.monitor_persistent_cache.json` 重启 |
| 数据字段为None | `curl /api/data \| python3 -m json.tool` 查原始数据 |
| 服务被signal 15杀 | 确认 `__main__` 有 `preexec_fn=os.setpgrp` |
| 改坏了想回滚 | 见上方「回滚指南」 |
| py_compile 报错 | 按报错行号修复语法错误，再重新 compile |
| git commit 后想撤销 | `git reset --soft HEAD~1` 撤销 commit 保留改动 |

---

## 📊 流程选择决策树

```
要改什么？
│
├─ 改文案/警告/小Bug / 改 static/ 文件 ───→ 🟢 极简3步
│                                        （static/ 改完无需重启）
│
├─ 改采集逻辑/新增字段/调整UI ────────────→ 🟡 标准6步
│                                        （需用户确认方案）
│
└─ 新功能/新API/重构 ─────────────────────→ 🔴 完整8步
         │                               （必须用户确认+备份+artifact）
         ├─ 是否需要回滚保障？ ─────→ 是：先 git commit 备份
         ├─ 是否影响API？ ────────→ 是：更新 docs/API.md
         ├─ 是否改了行号？ ────────→ 是：更新 docs/ARCHITECTURE.md
         └─ 是否改了流程本身？ ────→ 是：更新 docs/QUICKSTART.md
```

---

## 📖 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v2.14.0 | 2026-05-23 | 从 QUICKSTART.md 拆分为独立文档 |
