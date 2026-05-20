#!/bin/bash
# Mac AI Monitor 开发工具链
# 用法: ./dev.sh [watch|restart|status|log|commit|lint|health]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PY_FILE="$SCRIPT_DIR/mac_ai_monitor.py"
PID_FILE="$SCRIPT_DIR/run/mac_ai_monitor.pid"
LOG_DIR="$SCRIPT_DIR/logs"
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
    echo "  日志: ~/.qclaw/logs/monitor.log"
    echo "  stdout: $STDOUT_LOG"
    echo "  前端: http://127.0.0.1:$PORT"
}

# 查看日志
show_log() {
    local log_file="${1:-~/.qclaw/logs/monitor.log}"
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
    git add mac_ai_monitor.py templates/index.html mac_ai_monitor.README.md dev.sh 2>/dev/null
    git diff --cached --stat
    echo ""
    git commit -m "$msg"
    ok "已提交: $msg"
}

# 文件监听 + 自动重启
watch() {
    log "文件监听模式启动..."
    log "监听: mac_ai_monitor.py, templates/index.html"
    log "修改后自动: 语法检查 → 重启服务"
    echo "─────────────────────────────────"
    echo ""
    
    # 确保服务运行
    restart
    
    # macOS fswatch 检查, 不可用则 fallback 到轮询
    if command -v fswatch >/dev/null 2>&1; then
        log "使用 fswatch 监听文件变更..."
        fswatch -o --latency 2 "$PY_FILE" "$SCRIPT_DIR/templates/index.html" | while read -r event; do
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
        local html_hash=$(md5 -q "$SCRIPT_DIR/templates/index.html" 2>/dev/null || md5sum "$SCRIPT_DIR/templates/index.html" | cut -d' ' -f1)
        while true; do
            sleep 3
            local new_py=$(md5 -q "$PY_FILE" 2>/dev/null || md5sum "$PY_FILE" | cut -d' ' -f1)
            local new_html=$(md5 -q "$SCRIPT_DIR/templates/index.html" 2>/dev/null || md5sum "$SCRIPT_DIR/templates/index.html" | cut -d' ' -f1)
            if [ "$new_py" != "$py_hash" ] || [ "$new_html" != "$html_hash" ]; then
                echo ""
                echo "─────────────────────────────────"
                [ "$new_py" != "$py_hash" ] && log "mac_ai_monitor.py 已变更"
                [ "$new_html" != "$html_hash" ] && log "index.html 已变更"
                restart
                py_hash="$new_py"
                html_hash="$new_html"
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
mem = d.get('memory', {})
disk = d.get('disk', {})
gw = d.get('gateway', {})
print(f'CPU:    {cpu.get(\"used_pct\",0):.1f}%')
print(f'内存:   {mem.get(\"used_pct\",0):.1f}% ({mem.get(\"available_gb\",0):.1f}GB可用)')
print(f'磁盘:   {disk.get(\"used_pct\",0)}%')
print(f'Gateway: {gw.get(\"count\",0)}运行 健康分{gw.get(\"health_score\",0)}')
print(f'电池:   {d.get(\"battery\",{}).get(\"percent\",\"—\")}%')
"
}

# 帮助
usage() {
    echo "Mac AI Monitor 开发工具"
    echo ""
    echo "用法: ./dev.sh <command>"
    echo ""
    echo "命令:"
    echo "  watch     文件监听 + 自动重启 (推荐开发时使用)"
    echo "  restart   语法检查 + 重启服务"
    echo "  start     启动服务"
    echo "  stop      停止服务"
    echo "  status    查看运行状态"
    echo "  log       查看最近日志"
    echo "  lint      语法检查"
    echo "  health    健康检查(详细)"
    echo "  commit    Git 提交 (git add + commit)"
    echo ""
}

# 主入口
case "${1:-}" in
    watch)   watch ;;
    restart) restart ;;
    start)   start ;;
    stop)    stop ;;
    status)  status ;;
    log)     show_log "${2:-}" ;;
    lint)    lint ;;
    health)  health ;;
    commit)  commit "${2:-}" ;;
    *)       usage ;;
esac
