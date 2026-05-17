# Mac AI Monitor — 完整参考文档

> **入门只读 `mac_ai_monitor.QUICKSTART.md`**
> 本文档是完整参考，包含所有技术细节
> **代码**: `mac_ai_monitor.py` (1843行) + `templates/index.html` (1151行)
> **最后更新**: 2026-05-17 | **版本**: v2.11.0

---

## 1. 文件与部署

| 角色 | 路径 |
|------|------|
| 主程序 | `mac_ai_monitor.py` |
| HTML模板 | `templates/index.html` |
| 完整文档 | `mac_ai_monitor.README.md` |
| 快速入门 | `mac_ai_monitor.QUICKSTART.md` |
| 结构化日志 | `~/.qclaw/logs/monitor.log` (10MB轮转, 3份) |
| 持久化缓存 | `~/.qclaw/.monitor_persistent_cache.json` |
| 告警状态 | `~/.qclaw/.monitor_alerts.json` |
| 配置hash | `~/.qclaw/.config_hashes.json` |

```bash
# 重启服务（推荐用 dev.sh）
./dev.sh restart

# 手动重启
kill $(lsof -ti :8849) 2>/dev/null
nohup python3 ~/.qclaw/workspace-ua58rsb93veqtxl7/mac_ai_monitor.py > /tmp/mam.log 2>&1 &

# 验证
curl -s --max-time 5 http://127.0.0.1:8849/health
curl -s --max-time 5 http://127.0.0.1:8849/api/data/lite | python3 -c "
import sys,json; d=json.load(sys.stdin)
print('版本:', d.get('version'), '| Gateway:', d.get('gateway',{}).get('count'))
"
```

---

## 2. 代码架构（行号分区）

### mac_ai_monitor.py (1843行, 36函数)
```
L1-64     文件头 + 全局配置 + 缓存变量
  L17       PORT=8849
  L18       CACHE_TTL=180        (主缓存3分钟)
  L21       GPU_CACHE_TTL=600    (GPU缓存10分钟)
  L22       CPU_TTL=180          (CPU缓存3分钟)
  L26       __version__='2.11.0'
  L28       ALERT_COOLDOWN=1800  (告警冷却30分钟)
  L30       持久化缓存路径
  L33-43    结构化日志 (JSONL + RotatingFileHandler 10MB×3)

L46-64    历史数据 + 多层缓存变量
  L46       _history (deque maxlen=60)
  L51       _lite_cache (60s TTL, Manual模式轻量端点)
  L55       _gpu_cache_store / _cpu_cache (线程锁保护)
  L59-64    _data_dir_cache / _skills_cache / _session_file_idx

L66-213   工具函数
  L68-89    _parse_cron_field() — cron 单字段解析
  L92-113   _cron_next() — 轻量级下次运行预估 (1440分钟扫描)
  L116-142  run_cmd() — Shell 执行, utf-8/gbk/latin-1 编码自动检测
  L146-193  Session 文件索引 (scan/load/build, 120s缓存)
  L197-212  esc() / fmt_uptime() / try_json()

L216-390  告警/日志/配置发现
  L216-237  send_alert() — macOS通知 + 30min冷却 + 24h过期清理
  L240-291  tail_errors() / get_log_sizes() — 多日志源扫描
  L294-389  discover_configs() / extract_instances() / get_config_hash()
             → 4路径配置发现 + hash变更检测 + 字段级diff
             → 实例/模型/Provider/端口冲突提取

L394-493  Website Monitoring (P2)
  L396-410  SITES 配置 + DNS_WHITELIST + SSL阈值
  L412-439  _check_endpoint() — HTTP健康检查 + 时延采样
  L442-455  _get_ssl_days_left() — SSL证书剩余天数
  L458-477  _check_dns() — DNS解析一致性校验
  L480-491  _collect_endpoints() — 并发检查 + 时延直方图

L494-1521 collect_all() — 主数据采集 (1027行)
  L496-537  Config + 实例 + 变更检测 + 日志统计
  L541-582  System Info (sysctl 单次合并调用)
  L584-600  GPU (system_profiler, 600s缓存, 后台预热)
  L602-626  CPU Usage (top -l 1, 180s缓存, 线程锁)
  L628-647  Memory (vm_stat + hw.memsize)
  L649-663  Disk (df -h /)
  L665-710  Volumes (df + diskutil list, 超时降级估算)
  L712-730  Battery (pmset)
  L732-757  Network (netstat -ib 首帧保护)
  L759-830  Disk IO (iostat → psutil fallback)
  L832-843  IPv4/IPv6 连接统计分离
  L845-857  Shared Process Table (ps aux 单次)
  L859-878  Top Processes (fd_count macOS lsof fallback)
  L880-894  Shared lsof (单次 lsof -i -P -n)
  L896-912  Ports (lsof + ps_procs + KNOWN_PORTS)
  L914-1000 Gateway Detection (PPID链追溯 + 4规则匹配 + 合并 + 性能API)
  L1002-1016 Gateway 健康评分 (system/gateway/endpoint 三项拆分)
  L1018-1032 Gateway 告警触发
  L1034-1074 Data Directories (du -sk, 5min缓存, 超时降级)
  L1076-1122 Cron (4路径 jobs.json, next_run预估, 投递渠道映射)
  L1124-1176 Skills (3级目录扫描, 5min缓存, SKILL.md解析)
  L1178-1268 Skill Call Statistics + Tool Stats + Session Token (采样估算20条)
  L1270-1282 Recent Errors (tail_errors)
  L1284-1381 Activity (50条, 按时间分组, 应用过滤, 1KB头部读取)
  L1383-1027 History + Network History + Endpoints + Health Breakdown + CPU趋势 + SSL告警

L1521-1530 _load_html() — 加载模板 (缺失时HTML Fallback降级)
L1540-1570 增强功能 Stubs (dataclass/CheckEngine/DailyReport/Schema)
L1571-1589 _gray_validate() — 灰度发布验证

L1592-1650 持久化缓存 (冷启动优化)
  L1593-1609 _load_persistent_cache() — 启动恢复 (<30min)
  L1612-1621 _save_persistent_cache()
  L1624-1649 _lite_data() — Manual模式轻量采集

L1656-1795 HTTP Handler (ThreadedMixIn多线程)
  /health         — 简易健康检查
  /api/data       — 全量数据 JSON (180s缓存)
  /api/data/lite  — 轻量数据 (60s缓存, Manual模式)
  /api/status     — 标准化健康状态 (供UptimeRobot等)
  /api/gateway-log — Gateway日志内容
  /               — 前端HTML页面

L1798-1843 Main (信号处理 + GPU预热线程 + 启动)
```

### templates/index.html (1151行)
```
L1-257    <style>        CSS样式 (250行, 暗色主题, 响应式900px/600px)
L258-293  <body>         骨架屏 (shimmer loading)
L294-440  <script>       工具函数 + fetch/render
L441-1080 render(d)      仪表盘渲染 (750行全量innerHTML)
  Quick Bar (8指标) → Gauge Row (3 SVG) → Health Bar (4项)
  → System|网络|存储卷 → 内存详情|端口|模型 → Provider状态
  → Top进程 → Gateway(运行+闲置) → Activity|Cron
  → 会话统计 → 配置变更|日志 → 错误日志 → 数据目录 → Skills
L1081-1151 <script>       过滤+双模式刷新+Tab可见性控制
  Auto模式: 30s自动刷新
  Manual模式: 手动按钮读取缓存
```

---

## 3. 数据采集

### 采集流程 (共享执行)
```
ps aux (1次) → ps_procs ──┬→ Top Processes (CPU/MEM top8)
                         ├→ Ports 进程名匹配
                         └→ Gateway 实例检测

lsof -i -P -n (1次) ────┬→ lsof_listen → Ports
                         └→ lsof_all → Gateway 端口

ps -eo pid,ppid,comm (1次) → ppid_map → detect_source (PPID链)

sysctl (1次合并) → boottime/memsize/ncpu/os/cpu/loadavg/swapusage
diskutil list (1次) → 所有卷名
system_profiler (600s缓存, 后台预热) → GPU
top -l 1 (180s缓存) → CPU使用率
vm_stat → 内存明细
df -h + diskutil list → 磁盘卷
netstat -ib + -an → 网络
iostat -d disk0 → 磁盘IO (psutil fallback)
pmset -g batt → 电池
du -sk (5min缓存) → 数据目录大小
```

### 缓存策略 (v2.11.0)
| 数据 | TTL | 说明 |
|------|-----|------|
| 主API /api/data | 180s | 全量采集后缓存 |
| 轻量 /api/data/lite | 60s | 优先复用主缓存 |
| 持久化缓存 | 30min | 服务重启后冷启动恢复 |
| GPU | 600s | 后台线程预热,首次不阻塞 |
| CPU | 180s | top采样,线程锁保护 |
| Skills | 300s | 三级目录扫描 |
| 数据目录 | 300s | du -sk扫描 |
| Session索引 | 120s | 文件mtime+消息解析 |

### 健康评分 (满分100, 三项拆分)
| 维度 | 扣分规则 |
|------|----------|
| system_score | CPU>85% -10, 内存>90% -15, 磁盘>90% -10, Swap>80% -10 |
| gateway_score | 无Gateway -35, 闲置 -10, 负载>2倍核数 -10 |
| endpoint_score | 全OK=100, 部分OK=80, 全失败=50 |

### 告警阈值
| 告警 | 条件 | 冷却 |
|------|------|------|
| Gateway离线 | count=0 | 30min |
| CPU过高 | >90% | 30min |
| 内存过高 | >90% | 30min |
| 磁盘不足 | >90% | 30min |
| Swap过高 | >80% | 30min |
| SSL过期 | ≤3天critical/≤7天warn | 30min |
| CPU趋势 | 连续5点上升+3%/点 | — |

### Gateway detect_source (PPID链追溯)
```
进程PID → ppid_map查PPID → 递归追溯到PPID=1的根进程comm
comm匹配: autoclaw→AutoClaw, qclaw→QClaw, jvs→JVS, hermes→Hermes
fallback: args字符串匹配 → ? → CLI
```

---

## 4. 页面布局

```
┌─ Header (版本+时间+自动/手动切换) ─────────────────────────┐
├─ Quick Bar (8列: 告警|CPU|内存|磁盘|Swap|GW|网络|IO|电池|会话) ┤
├─ Gauge Row (CPU/内存/磁盘 3列SVG sparkline) ───────────────┤
├─ Health Bar (评分|Gateway数|定时任务|告警) ─────────────────┤
├─ System | 网络·流量图 | 存储卷(使用率排序) ──────────────────┤
├─ 内存详情(5层) | 监听端口 | 模型列表(15条) ─────────────────┤
├─ Provider状态(Endpoint+认证+模型数) ────────────────────────┤
├─ Top Processes (CPU/MEM双列, 含fd_count) ───────────────────┤
├─ Gateway (合并多PID, 性能RPM/延迟) + 闲置实例 ───────────────┤
├─ 最近活动(应用过滤) | 定时任务(状态+投递渠道) ───────────────┤
├─ 会话统计(Token/消息) | 配置变更 | 日志大小 ─────────────────┤
├─ 错误日志(级别标签) | 数据目录(状态标签) ───────────────────┤
├─ Skills(搜索+应用过滤+调用次数+展开详情) ───────────────────┤
└─ Footer (版本|时间|macOS|脚本路径) ─────────────────────────┘
```

### CSS 主题
```css
--bg:#0b0f1a --card:#131a2e --border:#1e2d45 --accent:#00cfff --accent2:#7b61ff
--green:#3dd68c --orange:#ffb347 --red:#ff6b6b --text:#e6edf3 --text2:#7d9ab5 --text3:#4a5a72
```

---

## 5. API 端点

| 端点 | 说明 | 缓存 |
|------|------|------|
| `/` | 仪表盘页面 | — |
| `/health` | `{"status":"ok","version":"...","gateway_count":N}` | — |
| `/api/data` | 完整监控数据 JSON (含所有模块) | 180s |
| `/api/data/lite` | 轻量数据 (CPU/内存/磁盘/Gateway/电池/网络) | 60s |
| `/api/status` | 标准化健康状态 (供外部监控UptimeRobot) | 180s |
| `/api/gateway-log` | Gateway日志内容 (最近2个日志文件) | — |

### /api/status 响应格式
```json
{
  "status": "ok|degraded|critical",
  "version": "2.11.0",
  "timestamp": "2026-05-17 07:00:00",
  "endpoints": [{"name":"HttpBin","url":"...","ok":true,"latency_ms":45.2}],
  "ssl_warnings": [{"name":"HttpBin","days_left":5}]
}
```

---

## 6. 性能

| 指标 | 耗时 | 说明 |
|------|------|------|
| GPU | ~15s (首次,后台预热) | system_profiler, 600s缓存 |
| CPU | ~5s (首次) | top -l 1采样, 180s缓存 |
| 其他 | ~1s | sysctl/vm_stat/ps/lsof/df |
| **总计(首次)** | **~8s** (GPU后台预热不阻塞) | 持久化缓存可跳过 |
| **总计(缓存后)** | **<100ms** | 直接返回JSON |

---

## 7. 调试

```bash
# 实时日志
tail -f ~/.qclaw/logs/monitor.log

# 语法检查
python3 -m py_compile mac_ai_monitor.py

# API原始数据
curl -s http://127.0.0.1:8849/api/data | python3 -m json.tool | head -80

# 轻量端点(快速)
curl -s http://127.0.0.1:8849/api/data/lite | python3 -m json.tool

# 健康检查
curl -s http://127.0.0.1:8849/health

# 标准状态
curl -s http://127.0.0.1:8849/api/status

# 服务管理(推荐)
./dev.sh status|restart|log|watch
```

---

## 8. 开发工作流

详见 `dev.sh` — 文件监听自动重启 + 语法检查 + git管理

```bash
# 启动文件监听(自动语法检查+重启服务)
./dev.sh watch

# 手动重启
./dev.sh restart

# 查看状态
./dev.sh status

# 提交代码
./dev.sh commit "描述"
```

---

## 9. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v2.11.0 | 2026-05-17 | 网站监控(端点/SSL/DNS) + 时延直方图 + Health三项拆分 + CPU趋势告警 + 持久化缓存 + 轻量端点 + 双模式刷新 + 结构化日志 + 灰度验证 + dev.sh开发工具链 |
| v2.10.1 | 2026-05-16 | 性能优化包：run_cmd编码检测/GPU缓存300s→600s+预热/Session统一索引/Skills+目录缓存5min/HTML模板分离/Gateway日志API/Session 1KB读取/前端应用过滤 |
| v2.10.0 | 2026-05-15 | P0+P1功能包：告警通知/健康检查API/Token统计/错误日志/配置变更检测/日志大小/Gateway性能(RPM)/进程fd数 |
| v2.9.3 | 2026-05-15 | 自动刷新/模型提供商状态/Skill调用统计/磁盘IO修复/Gateway CPU |
| v2.9.0 | 2026-05-15 | 网络自动刷新 + 磁盘IO (iostat) |
| v2.8.0 | 2026-05-14 | JVS/AutoClaw全面补全：活动来源标识/KNOWN_PORTS/detect_source/模型来源列 |
| v2.6.0 | 2026-05-13 | GPU缓存/health总览栏/cron next_run |
| v2.3.0 | 2026-05-13 | 共享ps/lsof, detect_source优化 |
| v2.2.0 | 2026-05-12 | 状态栏告警, 闲置排除, 内存可用/总计 |

---

## 10. 已知限制

- 首次采集 GPU~15s (后台预热), CPU~5s, 后续缓存命中 <100ms
- collect_all() 1027行, 单一巨型函数 (P0重构计划中)
- 前端全量innerHTML重建, 刷新时滚动位置重置 (P0优化计划中)
- 前端仅暗色主题, 无浅色模式
- 移动端适配仅2级断点 (900px/600px)
- 告警仅macOS系统通知, 无Webhook/邮件渠道

## 11. 修改流程

### 流程总览
```
改动分类 → 定位代码 → 修改 → 验证 → 提交
         ↓
    简单: grep → edit → py_compile → restart → commit
    大改: backup → 确认 → 分支 → 分步开发 → 验证 → 合并
```

---

### 简单改动（≤30min）

```
grep定位 → edit修改 → py_compile验证 → 重启测试 → git提交
```

| 步骤 | 操作 | 命令 |
|------|------|------|
| 1. 定位 | 搜索目标代码 | `grep -n "关键词" mac_ai_monitor.py` |
| 2. 修改 | 编辑代码 | 单文件、单函数级别 |
| 3. 验证 | 语法检查 | `python3 -m py_compile mac_ai_monitor.py` |
| 4. 重启 | 重启服务 | `./dev.sh restart` |
| 5. 确认 | 浏览器检查 | `http://127.0.0.1:8849` |
| 6. 提交 | Git记录 | `./dev.sh commit "fix: 描述"` |

**适用**：阈值调整、CSS微调、文案修改、Bug修复、单函数修改
**回滚**：`git checkout -- mac_ai_monitor.py && ./dev.sh restart`

---

### 大改动（≥1h）

```
git commit备份 → 用户确认 → 创建分支 → 分步开发(py_compile+restart) → 完整验证 → 版本commit → 合并main
```

| 阶段 | 步骤 | 操作 | 命令 |
|------|------|------|------|
| **准备** | 1. 备份 | 提交当前代码确保可回滚 | `./dev.sh commit "backup: 改动前快照"` |
| | 2. 确认 | 向用户说明改动范围、影响面、预期效果 | — |
| | 3. 分支 | 创建功能分支 | `git checkout -b feature/xxx` |
| **开发** | 4. 修改 | 分步修改代码 | edit |
| | 5. 验证 | 每步语法检查+重启 | `py_compile` → `./dev.sh restart` |
| | 6. 存档 | 每完成一个子模块提交 | `./dev.sh commit "wip: 子模块完成"` |
| **验证** | 7. 状态 | 确认服务运行正常 | `./dev.sh status` |
| | 8. 健康 | 检查关键指标 | `./dev.sh health` |
| | 9. 页面 | 浏览器全页面检查（滚动/刷新/搜索/响应式） | — |
| | 10. API | 检查数据结构完整性 | `curl /api/data \| python3 -m json.tool \| head -80` |
| **收尾** | 11. 合并 | 合入主分支 | `git checkout main && git merge feature/xxx` |
| | 12. 清理 | 删除功能分支 | `git branch -d feature/xxx` |
| | 13. 更新 | 同步README版本号+行号分区 | — |

**适用**：架构重构、新功能模块、多文件联动、collect_all拆分、前端增量更新改造

---

### 回滚策略

| 场景 | 命令 | 说明 |
|------|------|------|
| 撤销单文件 | `git checkout -- <file>` | 未提交的单文件回退 |
| 撤销全部修改 | `git checkout .` | 回到上次commit状态 |
| 回退上次提交 | `git reset --hard HEAD` | 丢弃最近一次commit |
| 回退指定版本 | `git reset --hard <hash>` | 恢复到任意历史commit |
| 保留修改回退 | `git reset --soft HEAD~1` | 撤销commit但保留文件改动 |
| 查看历史 | `git log --oneline -10` | 找到目标commit hash |

### 分支命名规范

| 前缀 | 用途 | 示例 |
|------|------|------|
| `feature/` | 新功能 | `feature/night-mode` |
| `refactor/` | 重构 | `refactor/p0-async-collect` |
| `fix/` | Bug修复 | `fix/diskio-write-zero` |
| `docs/` | 文档 | `docs/update-readme` |
| `wip/` | 实验性 | `wip/psutil-fallback` |

### Commit 规范

```
<type>: <简短描述>

type: feat | fix | refactor | docs | perf | style | chore
```

| 类型 | 说明 | 示例 |
|------|------|------|
| feat | 新功能 | `feat: 新增夜间模式` |
| fix | 修复 | `fix: Swap单位换算错误` |
| refactor | 重构 | `refactor: collect_all拆分` |
| docs | 文档 | `docs: 更新README至v2.11` |
| perf | 性能 | `perf: subprocess合并减少50%` |
| style | 样式 | `style: 移动端断点适配` |
| chore | 杂项 | `chore: 新增dev.sh` |

### 文件监听模式（可选）

开发过程中可开启自动监听，保存即重启：
```bash
./dev.sh watch
# 监听 mac_ai_monitor.py + templates/index.html
# 每次保存 → 自动 py_compile → 自动 restart
# 需要 fswatch: brew install fswatch（无则降级为3s轮询）
```
