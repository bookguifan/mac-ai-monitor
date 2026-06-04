#!/bin/bash
# Mac AI Monitor 开发工具链
# 用法: ./dev.sh [watch|restart|status|log|commit|lint|health|verify|rollback|version]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PY_FILE="$SCRIPT_DIR/mac_ai_monitor.py"
PID_FILE="$SCRIPT_DIR/run/mac_ai_monitor.pid"
LOG_DIR="$SCRIPT_DIR/data/logs"
STDOUT_LOG="$LOG_DIR/monitor_stdout.log"
PORT=8849

# 颜色
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

log() { echo -e "${CYAN}[dev]${NC} $1"; }
ok()   { echo -e "${GREEN}[ok]${NC} $1"; }
warn() { echo -e "${YELLOW}[warn]${NC} $1"; }
err()  { echo -e "${RED}[err]${NC} $1"; }

# 获取当前PID
get_pid() {
    lsof -ti :$PORT 2>/dev/null | head -1
}

# 语法检查
lint() {
    log "语法检查..."
    if python3 -m py_compile "$PY_FILE" 2>&1; then
        ok "语法检查通过"
        return 0
    else
        err "语法错误!"
        return 1
    fi
}

# 停止服务
stop() {
    local pid=$(get_pid)
    if [ -n "$pid" ]; then
        log "停止服务 PID=$pid..."
        kill "$pid" 2>/dev/null || true
        sleep 1
        # 强制杀
        pid=$(get_pid)
        [ -n "$pid" ] && kill -9 "$pid" 2>/dev/null || true
        ok "服务已停止"
    else
        log "服务未运行"
    fi
}

# 启动服务
start() {
    local pid=$(get_pid)
    if [ -n "$pid" ]; then
        warn "服务已在运行 (PID=$pid)"
        return 0
    fi
    log "启动服务..."
    cd "$SCRIPT_DIR"
    mkdir -p "$LOG_DIR"
    nohup python3 "$PY_FILE" > "$STDOUT_LOG" 2>&1 &
    local new_pid=$!
    echo "$new_pid" > "$PID_FILE"
    sleep 1
    # 验证
    if curl -s --max-time 5 "http://127.0.0.1:$PORT/health" > /dev/null 2>&1; then
        ok "服务已启动 PID=$new_pid -> http://127.0.0.1:$PORT"
    else
        warn "服务启动中(可能需要几秒预热)..."
        sleep 3
        if curl -s --max-time 5 "http://127.0.0.1:$PORT/health" > /dev/null 2>&1; then
            ok "服务已就绪"
        else
            err "启动失败! 查看 $STDOUT_LOG"
            tail -20 "$STDOUT_LOG"
            return 1
        fi
    fi
}

# 重启服务
restart() {
    lint || { err "语法错误, 不重启"; return 1; }
    stop
    start
    
    # 自动验证
    sleep 2
    echo ""
    log "验证服务状态..."
    
    # 1. 健康检查
    local health=$(curl -s --max-time 3 "http://127.0.0.1:$PORT/health" 2>/dev/null)
    if echo "$health" | grep -q '"status"'; then
        ok "健康检查: $(echo $health | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status","?"))')"
    else
        err "健康检查失败"
        return 1
    fi
    
    # 2. 检查静态文件模式
    local resp=$(curl -s "http://127.0.0.1:$PORT/" | head -20)
    if echo "$resp" | grep -q 'link.*css\|script.*js'; then
        ok "静态文件模式 (static/ 修改会生效)"
    else
        warn "内联 HTML_PAGE 模式 (static/ 修改不会生效)"
        echo "  提示: 确认 index.html 存在且有权限读取"
    fi
}

# 查看状态
status() {
    local pid=$(get_pid)
    if [ -n "$pid" ]; then
        local cpu_mem=$(ps -p "$pid" -o %cpu=,%mem= 2>/dev/null || echo "—")
        echo -e "${GREEN}● 运行中${NC} PID=$pid  CPU/MEM: $cpu_mem"
        # 健康检查
        local health=$(curl -s --max-time 3 "http://127.0.0.1:$PORT/health" 2>/dev/null || echo "无法连接")
        echo "  健康检查: $health"
        # 版本
        local ver=$(curl -s --max-time 3 "http://127.0.0.1:$PORT/api/data/lite" 2>/dev/null | python3 -c "import sys,json; print('v'+json.load(sys.stdin).get('version','?'))" 2>/dev/null || echo "—")
        echo "  版本: $ver"
    else
        echo -e "${RED}○ 未运行${NC}"
    fi
    echo ""
    echo "  端口: $PORT"
    echo "  日志: $LOG_DIR/monitor.log"
    echo "  stdout: $STDOUT_LOG"
    echo "  前端: http://127.0.0.1:$PORT"
}

# 查看日志
show_log() {
    local log_file="${1:-$LOG_DIR/monitor.log}"
    if [ -f "$log_file" ]; then
        tail -50 "$log_file"
    else
        warn "日志文件不存在: $log_file"
        if [ -f "$STDOUT_LOG" ]; then
            warn "尝试 stdout 日志..."
            tail -50 "$STDOUT_LOG"
        fi
    fi
}

# Git 提交
commit() {
    local msg="${1:-update}"
    cd "$SCRIPT_DIR"
    # 添加所有常用修改文件
    git add mac_ai_monitor.py index.html dev.sh \
            static/css/style.css static/js/app.js \
            docs/*.md 2>/dev/null
    git diff --cached --stat
    echo ""
    git commit -m "$msg"
    ok "已提交: $msg"
}

# 文件监听 + 自动重启
watch() {
    log "文件监听模式启动..."
    log "监听: mac_ai_monitor.py, index.html, static/css/, static/js/"
    log "修改后自动: 语法检查 → 重启服务"
    echo "─────────────────────────────────"
    echo ""
    
    # 确保服务运行
    restart
    
    # macOS fswatch 检查, 不可用则 fallback 到轮询
    if command -v fswatch >/dev/null 2>&1; then
        log "使用 fswatch 监听文件变更..."
        fswatch -o --latency 2 "$PY_FILE" "$SCRIPT_DIR/index.html" \
                 "$SCRIPT_DIR/static/css/style.css" \
                 "$SCRIPT_DIR/static/js/app.js" | while read -r event; do
            echo ""
            echo "─────────────────────────────────"
            log "检测到文件变更, 重新部署..."
            restart
            echo ""
        done
    else
        warn "fswatch 未安装, 使用轮询模式 (3s间隔)"
        warn "推荐安装: brew install fswatch"
        echo ""
        local py_hash=$(md5 -q "$PY_FILE" 2>/dev/null || md5sum "$PY_FILE" | cut -d' ' -f1)
        local html_hash=$(md5 -q "$SCRIPT_DIR/index.html" 2>/dev/null || md5sum "$SCRIPT_DIR/index.html" | cut -d' ' -f1)
        local css_hash=$(md5 -q "$SCRIPT_DIR/static/css/style.css" 2>/dev/null || md5sum "$SCRIPT_DIR/static/css/style.css" | cut -d' ' -f1)
        local js_hash=$(md5 -q "$SCRIPT_DIR/static/js/app.js" 2>/dev/null || md5sum "$SCRIPT_DIR/static/js/app.js" | cut -d' ' -f1)
        while true; do
            sleep 3
            local new_py=$(md5 -q "$PY_FILE" 2>/dev/null || md5sum "$PY_FILE" | cut -d' ' -f1)
            local new_html=$(md5 -q "$SCRIPT_DIR/index.html" 2>/dev/null || md5sum "$SCRIPT_DIR/index.html" | cut -d' ' -f1)
            local new_css=$(md5 -q "$SCRIPT_DIR/static/css/style.css" 2>/dev/null || md5sum "$SCRIPT_DIR/static/css/style.css" | cut -d' ' -f1)
            local new_js=$(md5 -q "$SCRIPT_DIR/static/js/app.js" 2>/dev/null || md5sum "$SCRIPT_DIR/static/js/app.js" | cut -d' ' -f1)
            if [ "$new_py" != "$py_hash" ] || [ "$new_html" != "$html_hash" ] || [ "$new_css" != "$css_hash" ] || [ "$new_js" != "$js_hash" ]; then
                echo ""
                echo "─────────────────────────────────"
                [ "$new_py" != "$py_hash" ] && log "mac_ai_monitor.py 已变更"
                [ "$new_html" != "$html_hash" ] && log "index.html 已变更"
                [ "$new_css" != "$css_hash" ] && log "style.css 已变更"
                [ "$new_js" != "$js_hash" ] && log "app.js 已变更"
                restart
                py_hash="$new_py"
                html_hash="$new_html"
                css_hash="$new_css"
                js_hash="$new_js"
                echo ""
            fi
        done
    fi
}

# 健康检查(详细)
health() {
    echo "=== 健康检查 ==="
    local resp=$(curl -s --max-time 5 "http://127.0.0.1:$PORT/api/data/lite" 2>/dev/null)
    if [ -z "$resp" ]; then
        err "服务未响应"
        return 1
    fi
    echo "$resp" | python3 -c "
import sys, json
d = json.load(sys.stdin)
cpu = d.get('cpu', {})
mem = d.get('mem', {})
disk = d.get('disk', {})
gw = d.get('gateway', {})
print(f'CPU:    {cpu.get(\"used_pct\",0):.1f}%')
print(f'内存:   {mem.get(\"used_pct\",0):.1f}% ({mem.get(\"available_gb\",0):.1f}GB可用)')
print(f'磁盘:   {disk.get(\"used_pct\",0)}%')
print(f'Gateway: {gw.get(\"count\",0)}运行')
print(f'电池:   {d.get(\"battery\",{}).get(\"percent\",\"—\")}%')
"
}

# 一键验证
verify() {
    echo "🔍 验证监控服务状态..."
    
    # 1. 进程
    local pid=$(get_pid)
    if [ -n "$pid" ]; then
        ok "进程运行中 PID=$pid"
    else
        err "进程未运行"
        return 1
    fi
    
    # 2. 健康检查
    local health=$(curl -s --max-time 3 "http://127.0.0.1:$PORT/health" 2>/dev/null)
    if [ -n "$health" ]; then
        ok "健康状态: $(echo $health | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("status","?")+" v"+d.get("version","?"))')"
    else
        err "健康检查失败"
        return 1
    fi
    
    # 3. 静态文件模式
    local resp=$(curl -s "http://127.0.0.1:$PORT/" | head -20)
    if echo "$resp" | grep -q 'link.*css\|script.*js'; then
        ok "静态文件模式 (static/ 修改会生效)"
    else
        warn "内联模式 (static/ 修改不会生效)"
    fi
    
    # 4. API 数据
    local lite=$(curl -s "http://127.0.0.1:$PORT/api/data/lite" 2>/dev/null)
    if [ -n "$lite" ]; then
        ok "数据: CPU $(echo $lite | python3 -c 'import json,sys; d=json.load(sys.stdin); print(str(d.get("cpu",{}).get("used_pct",0))[:5]+"%")') | Mem $(echo $lite | python3 -c 'import json,sys; d=json.load(sys.stdin); print(str(d.get("mem",{}).get("used_pct",0))[:5]+"%")')"
    else
        warn "API数据获取失败"
    fi
}

# 回滚
rollback() {
    local file="${1:-mac_ai_monitor.py}"
    log "回滚 $file..."
    git checkout -- "$file"
    ok "已回滚"
    restart
}

# 版本号检查
version() {
    # 代码版本号
    local code_ver=$(grep "__version__" "$PY_FILE" | head -1 | sed "s/.*= *'//;s/'.*//")
    # API 版本号
    local api_ver=$(curl -s --max-time 3 "http://127.0.0.1:$PORT/health" 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("version","?"))' 2>/dev/null || echo "服务未运行")
    echo "代码版本: $code_ver"
    echo "API版本:  $api_ver"
    if [ "$code_ver" != "$api_ver" ] && [ "$api_ver" != "服务未运行" ]; then
        warn "版本不一致! 可能需要重启服务"
    else
        ok "版本一致"
    fi
}

# 帮助
usage() {
    echo "Mac AI Monitor 开发工具"
    echo ""
    echo "用法: ./dev.sh <command>"
    echo ""
    echo "命令:"
    echo "  watch     文件监听 + 自动重启 (推荐开发时使用)"
    echo "  restart   语法检查 + 重启服务 + 自动验证"
    echo "  start     启动服务"
    echo "  stop      停止服务"
    echo "  status    查看运行状态"
    echo "  log       查看最近日志"
    echo "  lint      语法检查"
    echo "  health    健康检查(详细)"
    echo "  verify    一键验证 (进程+健康+静态文件+API)"
    echo "  rollback  回滚文件并重启 (默认 mac_ai_monitor.py)"
    echo "  version   版本号检查 (代码 vs API)"
    echo "  commit    Git 提交 (自动添加常用文件)"
    echo ""
}

# 主入口
case "${1:-}" in
    watch)    watch ;;
    restart)  restart ;;
    start)    start ;;
    stop)     stop ;;
    status)   status ;;
    log)      show_log "${2:-}" ;;
    lint)     lint ;;
    health)   health ;;
    verify)   verify ;;
    rollback) rollback "${2:-}" ;;
    version)  version ;;
    commit)   commit "${2:-}" ;;
    *)        usage ;;
esac