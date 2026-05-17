# ==============================================================================
# SECTION: IMPORTS & CONFIGURATION
# ==============================================================================

#!/usr/bin/env python3
"""
Mac AI Monitor v2.11.0 — OpenClaw AI 服务监控面板
增强版：双模式刷新 · Website Monitoring · SSL/DNS · Health Score 拆分 · 结构化日志
纯 JS 渲染，手动刷新，单文件部署，零外部依赖。
增强日志：~/.qclaw/logs/monitor.log（RotatingFileHandler，10MB轮转，保留3份）
新增端点：/api/status（外部监控系统可消费的标准化健康状态 JSON）
"""

import os, json, subprocess, re, time, datetime, logging, gzip, threading, logging.handlers
import urllib.request, urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from collections import deque

# ====== Config ======
PORT          = 8849
CACHE_TTL     = 180  # 主数据缓存（默认TTL，平衡新鲜度与性能）
HISTORY_SIZE  = 60
NET_IFACE     = 'en0'
GPU_CACHE_TTL = 600  # GPU信息很少变化，10分钟
CPU_TTL       = 180  # CPU采样拉长到180s，与主缓存同步
_cpu_cache    = {'data': None, 'ts': 0}
SCRIPT_FILE   = os.path.basename(__file__)
HOME          = os.path.expanduser('~')
__version__   = '2.11.0'
ALERT_FILE    = os.path.join(HOME, '.qclaw/.monitor_alerts.json')
ALERT_COOLDOWN = 1800  # 30 分钟相同告警不重复

# 持久化缓存文件（服务重启后可立即展示上次状态，避免冷启动白屏）
PERSISTENT_CACHE = os.path.join(HOME, '.qclaw/.monitor_persistent_cache.json')

# P3-30/31: 结构化日志（JSONL格式）+ RotatingFileHandler 防无限增长
_log_file = os.path.expanduser('~/.qclaw/logs/monitor.log')
os.makedirs(os.path.dirname(_log_file), exist_ok=True)
_file_handler = logging.handlers.RotatingFileHandler(
    _log_file, maxBytes=10*1024*1024, backupCount=3, encoding='utf-8'
)
_file_handler.setFormatter(logging.Formatter(
    '{"ts":"%(asctime)s","level":"%(levelname)s","module":"%(module)s","msg":%(message)s}'
))
_log_stdout = logging.StreamHandler()
_log_stdout.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
logging.basicConfig(level=logging.INFO, handlers=[_file_handler, _log_stdout])

# ====== History ======
_history = {k: deque(maxlen=HISTORY_SIZE) for k in ['cpu','memory','disk','swap','network']}
_cache   = {'data': {}, 'ts': 0}
# Manual模式轻量缓存（只含快速指标，采集开销极小）
_lite_cache = {'data': None, 'ts': 0}
LITE_CACHE_TTL = 60  # 60s内重复请求直接返回缓存
# Network首帧保护：避免首次采集差值爆表
_first_net_frame = {'done': False}
_net_s   = {'rx': 0, 'tx': 0, 'ts': 0}
_gpu_cache_store = {'gpus': [], 'ts': 0}
_cpu_lock = threading.Lock()
_data_dir_lock = threading.Lock()
_data_dir_cache = {'entries': [], 'ts': 0}
_DATA_DIR_CACHE_TTL = 300  # 5 min
_skills_cache_lock = threading.Lock()
_skills_cache = {'data': None, 'ts': 0}
_SKILLS_CACHE_TTL = 300  # 5 min
_gpu_lock = threading.Lock()
_cache_lock = threading.Lock()

# ====== Utilities ======

def _parse_cron_field(field, max_val):
    """Parse a cron field into a set of valid integers."""
    if field == '*': return set(range(max_val))
    result = set()
    for part in field.split(','):
        part = part.strip()
        if '/' in part:
            range_part, step = part.split('/', 1)
            step = int(step)
            if range_part == '*':
                start, end = 0, max_val - 1
            elif '-' in range_part:
                start, end = map(int, range_part.split('-', 1))
            else:
                start, end = int(range_part), max_val - 1
            result.update(range(start, end + 1, step))
        elif '-' in part:
            start, end = map(int, part.split('-', 1))
            result.update(range(start, end + 1))
        else:
            try: result.add(int(part))
            except ValueError: pass
    return result

def _cron_next(expr):
    """Lightweight next-run estimator for standard 5-field cron expressions."""
    try:
        parts = expr.strip().split()
        if len(parts) != 5: return None
        m_f, h_f, dom_f, mon_f, dow_f = parts
        now = datetime.datetime.now()
        base = now.replace(second=0, microsecond=0) + datetime.timedelta(minutes=1)
        m_set = _parse_cron_field(m_f, 60)
        h_set = _parse_cron_field(h_f, 24)
        dom_set = _parse_cron_field(dom_f, 32)
        mon_set = _parse_cron_field(mon_f, 13)
        dow_set = _parse_cron_field(dow_f, 7)
        for _ in range(1440):  # 最多扫描1天（1440分钟），大多数cron表达式首次命中
            if (base.minute in m_set and base.hour in h_set
                    and base.day in dom_set and base.month in mon_set
                    and (base.weekday() + 1) % 7 in dow_set):
                return base.strftime('%m-%d %H:%M')
            base += datetime.timedelta(minutes=1)
    except Exception:
        pass
    return None



# ==============================================================================
# SECTION: HELPER FUNCTIONS
# ==============================================================================

def run_cmd(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        out = r.stdout
        if isinstance(out, str):  # already text mode
            return out.strip()
        # 自动检测编码（修复 Windows GBK 问题）
        for enc in ('utf-8', 'gbk', 'latin-1'):
            try:
                return out.decode(enc).strip()
            except (UnicodeDecodeError, LookupError):
                continue
        return out.decode('utf-8', errors='replace').strip()
    except Exception as e:
        logging.warning(f'cmd failed: {cmd[:60]}: {e}')
        return ''


# Session file index cache — avoid re-scanning same files 3x per collect_all()
_SESSION_IDX_TTL = 120  # 2 min
_session_file_idx = {'files': [], 'ts': 0}
_session_idx_lock = threading.Lock()

_SESSION_DIRS = [
    os.path.join(HOME, '.qclaw/agents/main/sessions'),
    os.path.join(HOME, '.qclaw-hermes/sessions'),
    os.path.join(HOME, '.qclaw/workspace/sessions'),
    os.path.join(HOME, '.openclaw-autoclaw/agents/main/sessions'),
]

def _scan_session_dirs():
    """Return list of (filepath, mtime) for session files, deduplicated by filename."""
    files = []; seen = set()
    for sd in _SESSION_DIRS:
        if not os.path.isdir(sd): continue
        try:
            for fn in os.listdir(sd):
                if fn.startswith('.') or '.checkpoint.' in fn: continue
                fp = os.path.join(sd, fn)
                if os.path.isdir(fp):
                    fp2 = os.path.join(fp, 'store.json')
                    if os.path.exists(fp2):
                        if fn not in seen: seen.add(fn); files.append((fp2, os.path.getmtime(fp2)))
                elif fn.endswith(('.json', '.jsonl', '.jsonl.gz')):
                    if fn not in seen: seen.add(fn); files.append((fp, os.path.getmtime(fp)))
        except Exception: pass
    files.sort(key=lambda x: -x[1])  # newest first
    return files

def _load_messages(fp):
    """Parse a session file into a list of message dicts, normalizing nested formats."""
    opener = gzip.open if str(fp).endswith('.gz') else open
    with opener(fp, 'rt', errors='replace') as sf:
        raw = sf.read()
    try:
        doc = json.loads(raw)
        raw_msgs = doc.get('messages', [])
    except Exception:
        raw_msgs = []
        for line in raw.strip().split('\n'):
            if line.strip():
                try: raw_msgs.append(json.loads(line))
                except Exception: continue
    # Normalize: unwrap {"type":"message","message":{...}} -> inner message
    msgs = []
    for m in raw_msgs:
        if not isinstance(m, dict): continue
        inner = m.get('message')
        if isinstance(inner, dict) and inner.get('role'):
            # Merge outer metadata into inner
            merged = dict(inner)
            if m.get('timestamp'): merged['timestamp'] = m['timestamp']
            if not merged.get('timestamp'): merged['timestamp'] = inner.get('timestamp', '')
            msgs.append(merged)
        elif m.get('role'):
            msgs.append(m)
    return msgs

def _build_session_index():
    """Build cached index of session files with parsed messages. Returns list of (fp, mtime, messages)."""
    _now = time.time()
    with _session_idx_lock:
        if (_now - _session_file_idx['ts']) < _SESSION_IDX_TTL:
            return _session_file_idx['files']
        files = []
        for fp, mtime in _scan_session_dirs()[:200]:
            try:
                msgs = _load_messages(fp)
                files.append((fp, mtime, msgs))
            except Exception: continue
        _session_file_idx['files'] = files
        _session_file_idx['ts'] = _now
        return files

def esc(s):
    if s is None: return ''
    return str(s).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('"','&quot;')

def fmt_uptime(sec):
    if sec < 0: return '?'
    d = int(sec//86400); r = int(sec%86400)
    h = r//3600; m = (r%3600)//60
    parts = []
    if d: parts.append(f'{d}d')
    if h: parts.append(f'{h}h')
    if m: parts.append(f'{m}m')
    return ' '.join(parts) or '0m'

def try_json(path):
    try:
        with open(path) as f: return json.load(f)
    except Exception: return {}

def send_alert(title, message, alert_key=''):
    """macOS 系统通知，带冷却机制避免频繁打扰，附带告警文件过期清理"""
    try:
        now = time.time()
        alerts = try_json(ALERT_FILE)
        # 清理超过24小时的过期告警记录，防止文件无限膨胀
        alerts = {k: v for k, v in alerts.items() if now - v.get('last_sent', 0) < 86400}
        last_sent = alerts.get(alert_key, {}).get('last_sent', 0)
        if alert_key and (now - last_sent) < ALERT_COOLDOWN:
            return  # 冷却期内不重复通知
        # 发送系统通知（带重试）
        cmd = f'''osascript -e 'display notification "{message}" with title "{title}"' '''
        for attempt in range(2):
            r = subprocess.run(cmd, shell=True, capture_output=True, timeout=5)
            if r.returncode == 0:
                break
        # 记录发送时间
        if alert_key:
            alerts[alert_key] = {'last_sent': now, 'message': message}
            with open(ALERT_FILE, 'w') as f: json.dump(alerts, f)
        logging.info(f'alert sent: {title} - {message}')
    except Exception as e:
        logging.warning(f'send_alert failed: {e}')

def tail_errors(log_path, lines=20):
    """读取日志文件最近 N 条 ERROR/WARNING"""
    result = []
    try:
        if not os.path.exists(log_path):
            return []
        with open(log_path, 'r', errors='replace') as f:
            all_lines = f.readlines()
            errors = []
            for l in all_lines:
                if 'ERROR' in l or 'WARNING' in l or 'CRITICAL' in l:
                    errors.append(l.strip())
            for l in errors[-lines:]:
                # 解析日志行：2026-05-15 23:00:00,123 WARNING message
                m = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ (\w+) (.+)', l)
                if m:
                    result.append({'time': m.group(1), 'level': m.group(2), 'msg': m.group(3)})
                else:
                    result.append({'time': '', 'level': 'INFO', 'msg': l[:100]})
    except Exception as e:
        logging.warning(f'tail_errors {log_path}: {e}')
    return result

def get_log_sizes():
    """统计关键日志文件大小"""
    log_files = [
        ('监控面板', '/tmp/mac_ai_monitor.log'),
        ('监控面板 stdout', '/tmp/mac_ai_monitor_stdout.log'),
    ]
    # 添加 OpenClaw 日志
    for base in ['.qclaw', '.qclaw-hermes', '.openclaw-autoclaw', '.jvs/.openclaw']:
        log_files.append((base.split('/')[-1].replace('.','')+' 日志', os.path.join(HOME, base, 'logs/gateway.log')))
    result = []
    for label, path in log_files:
        if os.path.exists(path):
            size = os.path.getsize(path)
            result.append({
                'label': label,
                'path': path.replace(HOME, '~'),
                'size_mb': round(size / 1024 / 1024, 2),
                'size_kb': size // 1024
            })
    return result

KNOWN_PORTS = {
    '18789':'OpenClaw GW','8848':'JVS Monitor','8849':'AI Monitor',
    '8642':'Hermes GW','18801':'JVS Claw GW','18432':'AutoClaw GW',
    '19654':'AutoClaw','19723':'AutoClaw','19000':'QClaw GW',
    '19001':'OpenClaw GW','6379':'Redis','3306':'MySQL',
    '5432':'PostgreSQL','27017':'MongoDB','8080':'HTTP Dev',
    '3000':'Dev Server','5173':'Vite Dev','22':'SSH',
    '80':'HTTP','443':'HTTPS','5000':'Flask',
}

# ====== Config Discovery ======
def discover_configs():
    """
    P3-48: 核心函数文档 - 发现所有 OpenClaw 配置文件
    扫描多个已知路径，返回包含config_path/app_name/config_data的列表
    使用config_path作为唯一实例标识，避免同名冲突（P3-35）
    """
    out = []
    for p in [
        os.path.join(HOME,'.jvs/.openclaw','openclaw.json'),
        os.path.join(HOME,'.openclaw-autoclaw','openclaw.json'),
        os.path.join(HOME,'.qclaw','openclaw.json'),
        os.path.join(HOME,'.openclaw','openclaw.json'),
    ]:
        if os.path.exists(p):
            try:
                with open(p) as f: cfg = json.load(f)
                cfg['_path'] = p; cfg['_dir'] = os.path.dirname(p)
                out.append(cfg)
            except Exception: pass
    return out

def get_config_hash(path):
    """计算配置文件 hash，用于变更检测"""
    try:
        import hashlib
        with open(path, 'rb') as f:
            return hashlib.md5(f.read()).hexdigest()[:8]
    except Exception:
        return ''

def extract_instances(configs):
    instances = []; all_models = []; seen_k = set()
    all_providers = []; seen_prov = set()
    for cfg in configs:
        path = cfg.get('_path',''); base = cfg.get('_dir','')
        if '.openclaw-autoclaw' in path: name = 'AutoClaw'
        elif '.jvs' in path: name = 'JVS Claw'
        elif '.qclaw' in path: name = 'QClaw'
        elif '.openclaw' in path: name = 'OpenClaw'
        else: name = os.path.basename(base)
        gw = cfg.get('gateway',{})
        a = cfg.get('agents',{}).get('defaults',{})
        ch = cfg.get('channels',{})
        provs = cfg.get('models',{}).get('providers',{})
        primary = a.get('model',{}).get('primary','—')
        image = a.get('imageModel',{}).get('primary','—')
        active_ch = [c for c,v in ch.items() if isinstance(v,dict) and v.get('enabled')]
        models = []
        for pn,pv in provs.items():
            short = pn.split('__')[0]
            for m in pv.get('models',[]):
                mid = m.get('id','?')
                entry = {'provider':short,'id':mid,'name':m.get('name',mid),
                         'context':m.get('contextWindow',0),'app':name,
                         'is_primary':f'{pn}/{mid}'==primary,
                         'is_image':f'{pn}/{mid}'==image}
                models.append(entry)
                k = f'{short}/{mid}'
                if k not in seen_k:
                    seen_k.add(k); all_models.append(entry)
            # Collect provider-level info (dedup by short name across all instances)
            if short not in seen_prov:
                seen_prov.add(short)
                bu = pv.get('baseUrl', '')
                # Mask URL to show only domain:port
                try:
                    from urllib.parse import urlparse
                    pu = urlparse(bu)
                    bu_display = pu.netloc or bu[:40]
                except Exception:
                    bu_display = bu[:40] if bu else '—'
                all_providers.append({
                    'name': short,
                    'base_url': bu_display,
                    'has_key': bool(pv.get('apiKey')),
                    'auth': pv.get('auth', 'api-key'),
                    'api': pv.get('api', '—'),
                    'model_count': len(pv.get('models', [])),
                    'app': name,
                })
        instances.append({
            'name':name,'port':gw.get('port',0),'bind':gw.get('bind','local'),
            'mode':gw.get('mode','local'),
            'config_path':path,'base_dir':base,
            'config_hash': get_config_hash(path),
            'primary_model':primary,'image_model':image,
            'channels':active_ch,'heartbeat':a.get('heartbeat',{}).get('every','未配置'),
            'workspace':a.get('workspace','—'),'models':models,
        })
    pm = {}
    for i in instances:
        p = str(i.get('port',''))
        if p and p != '0': pm.setdefault(p,[]).append(i['name'])
    conflicts = {p:n for p,n in pm.items() if len(n)>1}
    return instances, all_models, all_providers, conflicts

# ====== Main Data Collection ======
# ============================================================
# Website Monitoring (P2: HTTP Endpoint / SSL / DNS)
# ============================================================
# P2-15/16/17: HTTP Endpoint Health Check + SSL + DNS
SITES = [
    {'url': 'https://httpbin.org/get', 'name': 'HttpBin Test', 'kind': 'http'},
    # {'url': 'https://api.openai.com/v1/models', 'name': 'OpenAI API', 'kind': 'http'},
    # {'url': 'https://your-gateway.example.com/health', 'name': 'Gateway', 'kind': 'http'},
]
# DNS 白名单：域名 -> 预期 IP 前缀列表（支持通配符）
DNS_WHITELIST = {
    # 'localhost': ['127.0.0.1', '::1'],
    # 'api.openai.com': ['23.'],  # 只要以 23. 开头即通过
}
SSL_WARNING_DAYS = 7   # SSL 证书提前 7 天告警
SSL_CRITICAL_DAYS = 3  # SSL 证书提前 3 天升 P0

# P2-19: 响应时延直方图 buckets (<100ms / 100-500ms / >500ms)
_endpoint_latency_hist = []  # 滚动最近 100 条记录

def _check_endpoint(site, timeout=5):
    """检查单个 HTTP 端点，返回 {ok, status_code, latency_ms, error, ssl_days_left}"""
    result = {'ok': False, 'status_code': 0, 'latency_ms': 0, 'error': '', 'ssl_days_left': None}
    url = site.get('url', '')
    try:
        start = time.time()
        r = urllib.request.urlopen(url, timeout=timeout)
        latency = (time.time() - start) * 1000
        result['ok'] = True
        result['status_code'] = r.getcode() or 200
        result['latency_ms'] = round(latency, 1)
        # 更新时延直方图
        _endpoint_latency_hist.append(result['latency_ms'])
        if len(_endpoint_latency_hist) > 100:
            _endpoint_latency_hist.pop(0)
        # SSL 证书检查
        if url.startswith('https://'):
            cert = _get_ssl_days_left(url)
            if cert is not None:
                result['ssl_days_left'] = cert
        return result
    except urllib.error.HTTPError as e:
        result['error'] = f'HTTP {e.code}'
        result['status_code'] = e.code
        _endpoint_latency_hist.append(timeout * 1000)
    except Exception as e:
        result['error'] = str(e)[:60]
        _endpoint_latency_hist.append(timeout * 1000)
    return result

def _get_ssl_days_left(url):
    """获取 SSL 证书剩余有效期（天数），失败返回 None"""
    try:
        import ssl
        u = __import__('urllib.parse', fromlist=['urlparse']).urlparse(url)
        host = u.netloc.split(':')[0] if u.netloc else u.path.split('/')[0]
        ctx = ssl.create_default_context()
        with __import__('urllib.request', fromlist=['urlopen']).urlopen(f'https://{host}', context=ctx, timeout=5) as r:
            cert = r.getpeercert()
            not_after = cert.get('notAfter', '')
            expire_date = datetime.datetime.strptime(not_after, '%b %d %H:%M:%S %Y %Z')
            days = (expire_date - datetime.datetime.utcnow()).days
            return days
    except Exception:
        return None

def _check_dns(domain, expected_ips):
    """DNS 解析一致性检测，返回 {ok, resolved, expected, mismatch}"""
    result = {'ok': True, 'resolved': [], 'expected': expected_ips, 'mismatch': False}
    try:
        import socket
        resolved = list(set(socket.getaddrinfo(domain, None)))
        result['resolved'] = list(set(ip for r in resolved for ip in [r[4][0]]))
        for ip in result['resolved']:
            matched = any(ip.startswith(exp) for exp in expected_ips)
            if not matched:
                result['mismatch'] = True
                result['ok'] = False
                break
    except urllib.error.URLError as e:
        result['ok'] = False
        result['error'] = str(e)[:60]
        return result
    except Exception as e:
        result['ok'] = False
        result['error'] = str(e)[:60]
    return result

def _collect_endpoints():
    """并发检查所有端点，返回端点统计和时延直方图"""
    results = []
    for site in SITES:
        r = _check_endpoint(site)
        results.append({'name': site['name'], 'url': site['url'], **r})
    # 计算时延直方图
    hist = {'<100ms': 0, '100-500ms': 0, '>500ms': 0}
    for lat in _endpoint_latency_hist:
        if lat < 100: hist['<100ms'] += 1
        elif lat <= 500: hist['100-500ms'] += 1
        else: hist['>500ms'] += 1
    return results, hist


# ====== Shared Resource Loader ======
class _SharedProcs:
    """Pre-load shared subprocess data once, pass to all collectors.
    Merges duplicate calls: sysctl (was called 2x), ps aux, lsof, ppid map."""
    def __init__(self):
        self.ps_procs = {}
        self.lsof_listen = []
        self.lsof_all = []
        self.ppid_map = {}
        self.sysctl_out = ''
        self.instances = []
        self.all_models = []
        self.all_providers = []
        self.port_conflicts = {}

    def load(self):
        """Load all shared resources sequentially (~3s total)."""
        self._load_sysctl()
        self._load_ps()
        self._load_lsof()
        self._load_ppid()
        self._load_configs()

    def _load_sysctl(self):
        try:
            self.sysctl_out = run_cmd([
                'sysctl', 'kern.boottime', 'hw.memsize', 'hw.ncpu', 'hw.physicalcpu',
                'kern.osproductversion', 'machdep.cpu.brand_string',
                'vm.loadavg', 'vm.swapusage'
            ], 5)
        except Exception as e:
            logging.warning(f'shared sysctl: {e}')
            self.sysctl_out = ''

    def _load_ps(self):
        try:
            out = run_cmd(['ps', 'aux'], 5)
            for line in out.strip().split('\n')[1:]:
                p = line.split(None, 10)
                if len(p) < 11: continue
                try:
                    self.ps_procs[p[1]] = {
                        'rss': int(p[5]), 'cpu': float(p[2]), 'mem': float(p[3]),
                        'comm': p[10].split()[0] if p[10] else '',
                        'args': p[10] if len(p) > 10 else ''
                    }
                except (ValueError, IndexError): pass
        except Exception as e:
            logging.warning(f'shared ps: {e}')

    def _load_lsof(self):
        try:
            out = run_cmd(['lsof', '-i', '-P', '-n'], 5)
            for line in out.split('\n')[1:]:
                self.lsof_all.append(line)
                if 'LISTEN' in line:
                    self.lsof_listen.append(line)
        except Exception: pass

    def _load_ppid(self):
        try:
            for line in run_cmd(['ps', '-eo', 'pid,ppid,comm'], 5).split('\n')[1:]:
                p = line.strip().split(None, 2)
                if len(p) >= 2:
                    self.ppid_map[p[0]] = {'ppid': p[1], 'comm': p[2] if len(p) > 2 else ''}
        except Exception: pass

    def _load_configs(self):
        configs = discover_configs()
        self.instances, self.all_models, self.all_providers, self.port_conflicts = extract_instances(configs)


# ====== PPID Chain Cache (P3-5) ======
_ppid_chain_cache = {'results': {}, 'ts': 0}
_PPID_CHAIN_TTL = 60  # 60s

def _detect_source_raw(ppid_map, ps_procs, pid):
    """Trace PPID chain to root app. Cached 60s."""
    _now = time.time()
    cache_key = pid
    if _now - _ppid_chain_cache['ts'] < _PPID_CHAIN_TTL:
        cached = _ppid_chain_cache['results'].get(cache_key)
        if cached is not None:
            return cached

    def trace_to_app(pid):
        visited = set()
        depth = 0
        while pid and pid not in visited and depth < 30:
            visited.add(pid)
            depth += 1
            info = ppid_map.get(pid, {})
            ppid = info.get('ppid', '')
            comm = info.get('comm', '')
            if ppid == '1' or not ppid:
                return comm
            pid = ppid
        return ''

    app_comm = trace_to_app(pid)
    app_lower = app_comm.lower()
    if 'autoclaw' in app_lower: result = 'AutoClaw'
    elif 'qclaw' in app_lower and 'jvs' not in app_lower: result = 'QClaw'
    elif 'jvs' in app_lower: result = 'JVS'
    elif 'hermes' in app_lower: result = 'Hermes'
    else:
        ps = ps_procs.get(pid, {})
        args = ps.get('args', '').lower()
        if 'autoclaw' in args: result = 'AutoClaw'
        elif 'jvs' in args or '.jvs' in args: result = 'JVS'
        elif not app_comm: result = 'CLI'
        else: result = '?'

    _ppid_chain_cache['results'][cache_key] = result
    if _now - _ppid_chain_cache['ts'] > _PPID_CHAIN_TTL:
        _ppid_chain_cache['results'] = {cache_key: result}
        _ppid_chain_cache['ts'] = _now
    return result


# ====== Individual Collectors ======

def _detect_config_changes(instances):
    """P3-45: Config change detection with field-level diff."""
    config_changes = []
    prev_hashes = try_json(os.path.join(HOME, '.qclaw/.config_hashes.json')) or {}
    prev_full = try_json(os.path.join(HOME, '.qclaw/.config_full.json')) or {}
    for inst in instances:
        path = inst.get('config_path', '')
        curr_hash = inst.get('config_hash', '')
        prev_hash = prev_hashes.get(path, '')
        prev_data = prev_full.get(path, {})
        if prev_hash and curr_hash and prev_hash != curr_hash:
            curr_data = inst.get('config_data', {})
            diff_fields = []
            all_keys = set(list(prev_data.keys()) + list(curr_data.keys()))
            for k in all_keys:
                old_val = prev_data.get(k)
                new_val = curr_data.get(k)
                if old_val != new_val:
                    diff_fields.append({'field': k, 'old': str(old_val)[:60], 'new': str(new_val)[:60]})
            config_changes.append({
                'path': path.replace(HOME, '~'),
                'old': prev_hash[:8], 'new': curr_hash[:8],
                'diff': diff_fields[:5]
            })
            prev_full[path] = curr_data
        prev_hashes[path] = curr_hash
    try:
        os.makedirs(os.path.dirname(ALERT_FILE), exist_ok=True)
        with open(os.path.join(HOME, '.qclaw/.config_hashes.json'), 'w') as f:
            json.dump(prev_hashes, f)
        with open(os.path.join(HOME, '.qclaw/.config_full.json'), 'w') as f:
            json.dump(prev_full, f)
    except Exception: pass
    return config_changes


def _collect_system(shared, now):
    """System info, CPU load, swap, GPU. Uses shared sysctl (P0-2: merged from 2 calls to 1)."""
    result = {}
    try:
        out = shared.sysctl_out
        bt = int(re.search(r'sec\s*=\s*(\d+)',out).group(1)) if re.search(r'sec\s*=\s*(\d+)',out) else 0
        up  = now - bt
        nm  = re.search(r'hw\.ncpu:\s*(\d+)',out)
        pm  = re.search(r'hw\.physicalcpu:\s*(\d+)',out)
        vm  = re.search(r'kern\.osproductversion:\s*(.+)',out)
        cb  = re.search(r'machdep\.cpu\.brand_string:\s*(.+)',out)
        lm  = re.search(r'vm\.loadavg:\s+\{\s+(\S+)\s+(\S+)\s+(\S+)',out)
        sm  = re.search(r'vm\.swapusage:\s*total\s*=\s*([\d.]+)([MG])\s*used\s*=\s*([\d.]+)([MG])',out)
        mem_gb = round(int(re.search(r'hw\.memsize:\s*(\d+)',out).group(1))/1024**3,1) if re.search(r'hw\.memsize:\s*(\d+)',out) else 8
        result['system'] = {
            'hostname': os.uname().nodename,
            'cpu_name': cb.group(1).strip() if cb else 'Apple Silicon',
            'os_version': f"macOS {vm.group(1).strip()}" if vm else 'macOS',
            'uptime_sec': up,'uptime_str': fmt_uptime(up),
            'cpu_p': int(pm.group(1)) if pm else 1,
            'cpu_l': int(nm.group(1)) if nm else 1,
            'memory_gb': mem_gb, 'gpu': [],
        }
        result['cpu_load'] = {
            'load_1m': float(lm.group(1)) if lm else 0,
            'load_5m': float(lm.group(2)) if lm else 0,
            'load_15m': float(lm.group(3)) if lm else 0,
        }
        if sm:
            mult_total = (1024**3) if sm.group(2)=='G' else (1024**2)
            mult_used  = (1024**3) if sm.group(4)=='G' else (1024**2)
            st = float(sm.group(1)) * mult_total / 1024**3
            su = float(sm.group(3)) * mult_used  / 1024**3
            result['swap'] = {'total_gb':round(st,1),'used_gb':round(su,1),'used_pct':round(su/st*100,1) if st>0 else 0}
        else:
            result['swap'] = {'total_gb':0,'used_gb':0,'used_pct':0}
    except Exception as e:
        logging.warning(f'system: {e}')
        result['system'] = {'hostname':'?','cpu_name':'?','os_version':'macOS','uptime_sec':0,
                         'uptime_str':'?','cpu_p':1,'cpu_l':1,'memory_gb':0,'gpu':[]}
        result['cpu_load'] = {'load_1m':0,'load_5m':0,'load_15m':0}
        result['swap'] = {'total_gb':0,'used_gb':0,'used_pct':0}

    # GPU (cached)
    _now = time.time()
    with _gpu_lock:
        if _gpu_cache_store and (_now - _gpu_cache_store['ts']) < GPU_CACHE_TTL:
            result['system']['gpu'] = list(_gpu_cache_store['gpus'])
        else:
            gpus = []
            try:
                out = run_cmd(['system_profiler','SPDisplaysDataType'],10)
                for line in out.split('\n'):
                    if 'Chipset Model' in line or '芯片组型号' in line:
                        n = line.split(':')[-1].strip()
                        if n: gpus.append(n)
            except Exception: pass
            result['system']['gpu'] = gpus
            _gpu_cache_store['gpus'] = gpus
            _gpu_cache_store['ts'] = _now
    return result



# ==============================================================================
# SECTION: DATA COLLECTORS
# ==============================================================================

def _collect_cpu(now):
    """CPU usage (sampled via top, cached 180s with lock)."""
    try:
        _now = time.time()
        with _cpu_lock:
            if _cpu_cache['data'] and _now - _cpu_cache['ts'] < CPU_TTL:
                return {'cpu': _cpu_cache['data'].copy()}
            out = run_cmd(['top','-l','1','-n','0'], 8)
            for line in out.split('\n'):
                if 'CPU usage' in line:
                    u = re.search(r'(\d+\.?\d*)%\s*user',line)
                    s = re.search(r'(\d+\.?\d*)%\s*sys',line)
                    i = re.search(r'(\d+\.?\d*)%\s*idle',line)
                    # cores via sysctl (cached with CPU)
                    cores = None
                    try:
                        cores_out = run_cmd(['sysctl','-n','hw.ncpu'], 3)
                        cores = int(cores_out.strip()) if cores_out.strip().isdigit() else None
                    except Exception:
                        pass
                    # CPU die temp (requires sudo powermetrics on macOS)
                    temp = None
                    try:
                        t_out = run_cmd(['sudo','powermetrics','--samplers','smc','-i','1','-n','1'], 5)
                        t_m = re.search(r'(?:CPU|Die) die temperature[^:]*:\s*(\d+)', t_out)
                        if t_m: temp = int(t_m.group(1))
                    except Exception:
                        pass
                    cpu_data = {
                        'user_pct': round(float(u.group(1)),1) if u else 0,
                        'sys_pct': round(float(s.group(1)),1) if s else 0,
                        'idle_pct': round(float(i.group(1)),1) if i else 0,
                        'used_pct': round(((float(u.group(1)) if u else 0)+(float(s.group(1)) if s else 0)),1),
                        'cores': cores,
                        'temp': temp,
                    }
                    _cpu_cache['data'] = cpu_data
                    _cpu_cache['ts'] = _now
                    return {'cpu': cpu_data.copy()}
            return {'cpu': {'user_pct':0,'sys_pct':0,'idle_pct':100,'used_pct':0}}
    except Exception as e:
        logging.warning(f'cpu: {e}')
        with _cpu_lock:
            if _cpu_cache['data']:
                return {'cpu': _cpu_cache['data'].copy()}
        return {'cpu': {'user_pct':0,'sys_pct':0,'idle_pct':100,'used_pct':0}}


def _collect_memory(shared):
    """Memory details via vm_stat. Uses shared.sysctl_out for hw.memsize (P0-2: no extra sysctl call)."""
    try:
        out = run_cmd(['vm_stat'],5); page=4096
        # P0-2: Use shared sysctl output instead of calling sysctl hw.memsize again
        hw_total = 0
        m = re.search(r'hw\.memsize:\s*(\d+)', shared.sysctl_out)
        if m:
            hw_total = round(int(m.group(1))/1024**3, 1)
        if not hw_total:
            hw_total = 8
        def _v(k):
            m2 = re.search(rf'{k}:\s+(\d+)\.',out)
            return int(m2.group(1))*page/1024**3 if m2 else 0
        act=_v('Pages active'); wir=_v('Pages wired down')
        fre=_v('Pages free'); ina=_v('Pages inactive')
        spe=_v('Pages speculative'); pur=_v('Pages purgeable')
        total = hw_total
        avail = fre+ina+pur
        return {'memory': {
            'active_gb':round(act,1),'wired_gb':round(wir,1),
            'free_gb':round(fre,1),'inactive_gb':round(ina,1),
            'speculative_gb':round(spe,1),'purgeable_gb':round(pur,1),
            'available_gb':round(avail,1),
            'total_gb':round(total,1),
            'used_pct':round((act+wir)/total*100,1) if total>0 else 0,
            'avail_pct':round(avail/total*100,1) if total>0 else 0,
        }}
    except Exception as e:
        logging.warning(f'memory: {e}')
        return {'memory': {'used_pct':0,'active_gb':0,'wired_gb':0,'free_gb':0,
                         'inactive_gb':0,'speculative_gb':0,'purgeable_gb':0,
                         'available_gb':0,'total_gb':0,'avail_pct':0}}


def _collect_disk():
    """Disk usage + volumes. P3-3: Sort volumes by used_pct DESC, >=90% first."""
    result = {'disk': {'size':'?','used':'?','available':'?','used_pct':0,'volumes':[]}}
    try:
        out = run_cmd(['df','-h','/'],5)
        p = out.split('\n')[1].split()
        result['disk'] = {
            'size':p[1],'used':p[2],'available':p[3],
            'used_pct':int(p[4].replace('%','')),'volumes':[],
        }
    except Exception as e:
        logging.warning(f'disk: {e}')
    try:
        df_map = {}
        df_out = run_cmd(['df','-h'],5)
        for line in df_out.split('\n'):
            parts = line.split()
            if len(parts) < 9 or not parts[0].startswith('/dev/disk'):
                continue
            dev = parts[0]
            if len(dev.replace('/dev/','').split('s')) > 3:
                continue
            df_map[dev] = {
                'size': parts[1], 'used': parts[2],
                'avail': parts[3], 'pct': parts[4],
                'mount': parts[8],
            }
        vol_names = {}
        try:
            du_out = run_cmd(['diskutil','list'],5)
            for line in du_out.split('\n'):
                vm_disk = re.match(r'\s+(/dev/disk\d+s\d+)\s+(.+)', line)
                if vm_disk:
                    dn = vm_disk.group(1)
                    vname = vm_disk.group(2).strip()
                    if len(dn.replace('/dev/','').split('s')) <= 3 and 'Not applicable' not in vname:
                        vol_names[dn] = vname
        except Exception: pass

        volumes = []
        for dev, info in df_map.items():
            name = vol_names.get(dev, dev.split('/')[-1])
            pct_val = int(info['pct'].replace('%',''))
            volumes.append({
                'dev': dev, 'name': name,
                'size': info['size'], 'used': info['used'],
                'avail': info['avail'], 'pct': info['pct'],
                '_pct_val': pct_val,
            })
        # P3-3: Sort by used_pct DESC, >=90% first
        volumes.sort(key=lambda v: (-1 if v['_pct_val'] >= 90 else 0, -v['_pct_val']))
        # Clean internal key before returning
        for v in volumes:
            v.pop('_pct_val', None)
        result['disk']['volumes'] = volumes
    except Exception as e:
        logging.warning(f'volumes: {e}')
    return result


def _collect_battery():
    """Battery status."""
    data = {'battery': {'percent':-1,'charging':True,'status_cn':'—'}}
    try:
        out = run_cmd(['pmset','-g','batt'],3)
        m = re.search(r'(\d+)%;\s*(\w+)',out)
        if m:
            data['battery']['percent'] = int(m.group(1))
            st = m.group(2)
            data['battery']['charging'] = st in ('charged','charging','AC')
            data['battery']['status_cn'] = {'charging':'充电中','charged':'已充满',
                'discharging':'放电','finishing':'即将充满','AC':'电源'}.get(st,st)
    except Exception: pass
    return data


def _collect_network(shared, now):
    """Network traffic, connections (IPv4/IPv6), disk IO."""
    data = {'network': {'interface':NET_IFACE,'rx_kbps':0,'tx_kbps':0,
                       'connections_est':0,'connections_listen':0,'connections_timewait':0}}
    try:
        out = run_cmd(['netstat','-ib'],5)
        rx = tx = 0
        for line in out.split('\n'):
            if NET_IFACE in line and 'Link#' not in line:
                p=line.split()
                if len(p)>=10:
                    try: rx=int(p[6]); tx=int(p[9]); break
                    except Exception: pass
        dt = now - _net_s['ts']
        if _first_net_frame['done'] and dt > 0.5 and _net_s['rx'] > 0:
            data['network']['rx_kbps'] = round((rx - _net_s['rx']) / dt / 1024, 1)
            data['network']['tx_kbps'] = round((tx - _net_s['tx']) / dt / 1024, 1)
        else:
            _first_net_frame['done'] = True
        _net_s.update({'rx': rx, 'tx': tx, 'ts': now})
    except Exception as e: logging.warning(f'network: {e}')

    # P3-39: IPv4/IPv6 connection split
    try:
        out = run_cmd(['netstat','-an'],5)
        conns_v4 = {'est': 0, 'listen': 0, 'timewait': 0}
        conns_v6 = {'est': 0, 'listen': 0, 'timewait': 0}
        for line in out.split('\n'):
            is_v6 = '::ffff:' in line or '.ipv6.' in line.lower() or (' IPv6' in line)
            bucket = conns_v6 if is_v6 else conns_v4
            if 'ESTABLISHED' in line: bucket['est'] += 1
            elif 'LISTEN' in line: bucket['listen'] += 1
            elif 'TIME_WAIT' in line: bucket['timewait'] += 1
        data['network']['connections_est'] = conns_v4['est']
        data['network']['connections_listen'] = conns_v4['listen']
        data['network']['connections_timewait'] = conns_v4['timewait']
        data['network']['ipv4'] = conns_v4
        data['network']['ipv6'] = conns_v6
    except Exception: pass
    return data


def _collect_disk_io():
    """Disk IO stats via iostat."""
    data = {'disk_io': {'read_mbps': 0, 'write_mbps': 0, 'iops': 0}}
    try:
        out = run_cmd(['iostat', '-d', 'disk0'], 5)
        lines = out.strip().split('\n')
        for line in lines:
            parts = line.split()
            if len(parts) >= 3 and parts[0] not in ('KB/t', 'KB/'):
                try:
                    tps = float(parts[1])
                    mbs = float(parts[2])
                    data['disk_io']['iops'] = tps
                    data['disk_io']['read_mbps'] = mbs
                    data['disk_io']['write_mbps'] = None
                    break
                except (ValueError, IndexError):
                    continue
    except Exception as e:
        logging.warning(f'disk_io(iostat): {e}')
    return data


def _collect_processes(shared):
    """Top processes by CPU and memory (uses shared ps_procs)."""
    ps_procs = shared.ps_procs
    data = {'processes': {'top_cpu':[],'top_mem':[]}}
    try:
        procs = []
        for pid,info in ps_procs.items():
            if SCRIPT_FILE in info.get('args',''): continue
            comm = os.path.basename(info['comm']) if info['comm'] else '?'
            fd_count = 0
            try:
                fd_count = len(os.listdir(f'/proc/{pid}/fd')) if os.path.exists(f'/proc/{pid}/fd') else 0
            except Exception:
                try:
                    lsof_out = run_cmd(['lsof', '-p', str(pid)], 3)
                    fd_count = len([l for l in lsof_out.split('\n') if l.strip()])
                except Exception:
                    fd_count = -1
            procs.append({'name':comm[:50],'pid':pid,
                         'cpu':info['cpu'],'mem':info['mem'],'rss_mb':info['rss']//1024,
                         'fd_count': fd_count})
        data['processes']['top_cpu'] = sorted(procs,key=lambda x:x['cpu'],reverse=True)[:8]
        data['processes']['top_mem'] = sorted(procs,key=lambda x:x['rss_mb'],reverse=True)[:8]
    except Exception as e: logging.warning(f'top procs: {e}')
    return data


def _collect_ports(shared):
    """Listening ports (uses shared lsof + ps_procs)."""
    ps_procs = shared.ps_procs
    data = {'ports': []}
    try:
        pid_ports = {}
        for line in shared.lsof_listen:
            p = line.split()
            if len(p)<9: continue
            m = re.search(r':(\d+)',p[8])
            if m: pid_ports.setdefault(p[1],[]).append(m.group(1))
        for pid,ports in pid_ports.items():
            ps = ','.join(ports[:4])+('...' if len(ports)>4 else '')
            info = ps_procs.get(pid,{})
            proc = os.path.basename(info.get('comm','?')) if info else '?'
            desc = ''
            for pt in ports:
                if pt in KNOWN_PORTS: desc=KNOWN_PORTS[pt]; break
            data['ports'].append({'port':ps,'pid':pid,'process':proc,'desc':desc})
        data['ports'].sort(key=lambda x:int(x['port'].split(',')[0]))
    except Exception as e: logging.warning(f'ports: {e}')
    return data


def _collect_gateway(shared, now, system_data, cpu_data, mem_data, disk_data, swap_data):
    """Gateway detection, merge, health score, alerts. Uses shared ps_procs + lsof + ppid_map."""
    ps_procs = shared.ps_procs
    instances = shared.instances
    data = {}

    gateways = []; gw_ports = {}
    _gw_kw = {'openclaw-gateway','hermes','autoclaw','jvs'}
    try:
        for line in shared.lsof_all:
            if 'LISTEN' not in line: continue
            lower = line.lower()
            if not any(kw in lower for kw in _gw_kw): continue
            if 'script.py' in lower: continue
            p = line.split()
            pid = p[1] if len(p) >= 2 else ''
            m = re.search(r':(\d+)', line)
            port = m.group(1) if m else ''
            if port and 1 <= int(port) <= 65535 and pid:
                gw_ports.setdefault(pid, []).append(port)
    except Exception: pass

    def detect_source(pid):
        src = _detect_source_raw(shared.ppid_map, ps_procs, pid)
        # Override: openclaw-gateway launched by JVS Claw -> source = JVS
        if src == '?' and pid in ps_procs:
            args = ps_procs[pid].get('args', '').lower()
            if 'openclaw-gateway' in args:
                ppid = shared.ppid_map.get(pid, {}).get('ppid', '')
                if ppid and ppid in shared.ppid_map:
                    parent_comm = shared.ppid_map[ppid].get('comm', '').lower()
                    if 'jvs' in parent_comm:
                        src = 'JVS'
        return src

    _GW_RULES = [
        {'kw': 'openclaw-gateway', 'name_fmt': '{src} Gateway', 'default_name': 'OpenClaw',
         'software': 'OpenClaw Gateway', 'match_inst': True},
        {'kw': 'hermes', 'name': 'Hermes', 'software': 'Hermes Gateway',
         'extra': lambda a: 'python' in a.lower(), 'default_port': '8642'},
        {'kw': 'autoclaw', 'name': 'AutoClaw', 'software': 'AutoClaw Gateway',
         'default_port': '18432'},
        {'kw': 'jvs', 'name': 'JVS Claw', 'software': 'JVS Claw',
         'extra': lambda a: 'python' not in a.lower() and 'relay' not in a,
         'default_port': '18801', 'kw_upper': 'JVS'},
    ]
    try:
        seen_pids = set()
        for pid, info in ps_procs.items():
            args = info.get('args', '')
            if SCRIPT_FILE in args or pid in seen_pids: continue
            matched_rule = None
            args_lower = args.lower()
            for rule in _GW_RULES:
                kw = rule.get('kw', '')
                kw_upper = rule.get('kw_upper', '')
                cond = (kw in args_lower) or (kw_upper and kw_upper in args)
                if not cond: continue
                extra = rule.get('extra')
                if extra and not extra(args): continue
                matched_rule = rule; break
            if not matched_rule: continue
            seen_pids.add(pid)
            rss = info['rss'] // 1024
            src = detect_source(pid)
            dp = matched_rule.get('default_port', '—')
            ports = ','.join(gw_ports.get(pid, [dp]))
            if matched_rule.get('match_inst'):
                inst = next((i for i in instances if i['name'] == src), None)
                if not inst and src == 'CLI':
                    inst = next((i for i in instances if i['name'] == 'OpenClaw'), None)
                name = matched_rule['name_fmt'].replace('{src}', src) if src != '?' else matched_rule.get('default_name', 'OpenClaw')
            else:
                inst = None
                name = matched_rule.get('name', '?')
            gw_cpu = info.get('cpu', 0)
            gateways.append({
                'name': name, 'software': matched_rule.get('software', '?'),
                'pid': pid, 'port': ports, 'status': '运行中',
                'rss_mb': rss, 'cpu_pct': gw_cpu, 'source': src,
                'primary_model': inst.get('primary_model', '—') if inst else '—',
                'image_model': inst.get('image_model', '—') if inst else '—',
                'channels': inst.get('channels', []) if inst else [],
                'heartbeat': inst.get('heartbeat', '未配置') if inst else '未配置',
                'workspace': inst.get('workspace', '—') if inst else '—',
                'config_path': inst.get('config_path', '—') if inst else '—',
                'bind': inst.get('bind', 'local') if inst else 'local',
            })
    except Exception as e: logging.warning(f'gateway detect: {e}')

    # Merge by name
    _by_name = {}
    for g in gateways:
        n = g.get('name', '?')
        if n not in _by_name:
            _by_name[n] = {'first': g, 'pids': [], 'ports': set(), 'total_rss': 0, 'total_cpu': 0.0, 'sources': set(), 'perf': {}}
        d = _by_name[n]
        d['pids'].append(g.get('pid', ''))
        for p in str(g.get('port', '')).split(','):
            if p and p != '—': d['ports'].add(p)
        d['total_rss'] += g.get('rss_mb', 0)
        d['total_cpu'] += g.get('cpu_pct', 0)
        d['sources'].add(g.get('source', '?'))
        port = g.get('port', '—')
        if port and port.isdigit() and 'perf' not in d['perf']:
            try:
                import requests
                resp = requests.get(f'http://127.0.0.1:{port}/v1/status', timeout=1)
                if resp.status_code == 200:
                    status_data = resp.json()
                    d['perf'] = {
                        'requests_per_min': status_data.get('rpm', 0) or status_data.get('requests_per_min', 0),
                        'avg_response_ms': status_data.get('latency_ms', 0) or status_data.get('avg_latency', 0),
                        'error_rate': status_data.get('error_rate', 0),
                    }
            except Exception:
                pass

    merged = []
    for n, d in _by_name.items():
        f = d['first']
        valid_ports = [p for p in d['ports'] if p.isdigit()]
        ports = ','.join(sorted(valid_ports, key=lambda x: (len(x), x))) if valid_ports else f.get('port', '—')
        inst = f
        perf = d.get('perf', {})
        merged.append({
            'name': n, 'software': f.get('software', '?'),
            'pids': d['pids'], 'pid_count': len(d['pids']),
            'port': ports, 'status': '运行中',
            'rss_mb': d['total_rss'], 'cpu_pct': round(d['total_cpu'], 1),
            'source': '/'.join(sorted(d['sources'])) if len(d['sources']) > 1 else (list(d['sources'])[0] if d['sources'] else '?'),
            'primary_model': inst.get('primary_model', '—'),
            'image_model': inst.get('image_model', '—'),
            'channels': inst.get('channels', []),
            'heartbeat': inst.get('heartbeat', '未配置'),
            'workspace': inst.get('workspace', '—'),
            'config_path': inst.get('config_path', '—'),
            'bind': inst.get('bind', 'local'),
            'rpm': perf.get('requests_per_min', 0),
            'latency_ms': perf.get('avg_response_ms', 0),
            'error_rate': perf.get('error_rate', 0),
        })

    active_srcs = set(g.get('source', '') for g in gateways)
    idle = [{'name': i['name'], 'port': i.get('port', '—'), 'config_path': i.get('config_path', '—')}
            for i in instances if i['name'] not in active_srcs and i.get('mode') != 'local']
    gw_models = list(dict.fromkeys(g.get('primary_model', '') for g in gateways
                                   if g.get('primary_model') and g['primary_model'] != '—'))

    # Health score
    hs = 100
    if not gateways: hs -= 35
    if idle: hs -= 10
    if cpu_data.get('used_pct', 0) > 85: hs -= 10
    if mem_data.get('used_pct', 0) > 90: hs -= 15
    if disk_data.get('used_pct', 0) > 90: hs -= 10
    if swap_data.get('used_pct', 0) > 80: hs -= 10
    l1 = cpu_data.get('load_1m', 0)
    cores = system_data.get('cpu_p', 1)
    if cores > 0 and l1 / cores > 2: hs -= 10

    # Extract pids and ports from instances for convenience
    gw_pids = [g.get('pid') for g in gateways if g.get('pid')]
    gw_ports = [g.get('port') for g in gateways if g.get('port') and g.get('port') != '—']
    data['gateway'] = {'count': len(gateways), 'merged_count': len(merged),
        'instances': gateways, 'merged': merged,
        'idle_count': len(idle), 'idle_instances': idle, 'models': gw_models,
        'pids': gw_pids, 'ports': gw_ports,
        'health_score': max(hs, 0)}

    # Trigger alerts
    if not gateways:
        send_alert('🚨 Gateway 离线', '所有 Gateway 实例已停止运行', 'gw_offline')
    if cpu_data.get('used_pct', 0) > 90:
        send_alert('⚠️ CPU 使用率过高', f'CPU 使用率 {cpu_data["used_pct"]}%', 'cpu_high')
    if mem_data.get('used_pct', 0) > 90:
        send_alert('⚠️ 内存使用率过高', f'内存使用率 {mem_data["used_pct"]}%', 'mem_high')
    if disk_data.get('used_pct', 0) > 90:
        send_alert('⚠️ 磁盘空间不足', f'磁盘使用率 {disk_data["used_pct"]}%', 'disk_high')
    if swap_data.get('used_pct', 0) > 80:
        send_alert('⚠️ Swap 使用过高', f'Swap 使用率 {swap_data["used_pct"]}%', 'swap_high')
    return data


def _collect_data_dirs():
    """Data directory sizes (cached 5 min). P1-4: Already has timeout protection."""
    dirs = [
        ('JVS Claw', os.path.join(HOME,'.jvs'),'active'),
        ('AutoClaw 工作区', os.path.join(HOME,'.openclaw-autoclaw'),'active'),
        ('QClaw 主数据', os.path.join(HOME,'.qclaw'),'active'),
        ('QClaw 备份', os.path.join(HOME,'.qclaw-backups'),'cleanup'),
        ('QClaw Hermes', os.path.join(HOME,'.qclaw-hermes'),'active'),
        ('AutoClaw 应用', os.path.join(HOME,'Library/Application Support/autoclaw'),'active'),
        ('QClaw 应用', os.path.join(HOME,'Library/Application Support/QClaw'),'active'),
        ('应用备份', os.path.join(HOME,'Documents/app-backups'),'backup'),
    ]
    _now = time.time()
    with _data_dir_lock:
        if (_now - _data_dir_cache['ts']) > _DATA_DIR_CACHE_TTL:
            _data_dir_cache['entries'] = []
            for label,path,status in dirs:
                if not os.path.isdir(path):
                    _data_dir_cache['entries'].append({'label':label,'path':path.replace(HOME,'~'),'size':'—','status':status})
                    continue
                try:
                    r = subprocess.run(['du','-sk',path],capture_output=True,text=True,timeout=5)
                    kb = int(r.stdout.split()[0]) if r.returncode==0 else 0
                    size_s = f'{kb/1048576:.1f} GB' if kb>=1048576 else (f'{kb/1024:.0f} MB' if kb>0 else '—')
                except subprocess.TimeoutExpired:
                    try:
                        kb = 0
                        for entry in os.listdir(path):
                            sub = os.path.join(path, entry)
                            if os.path.isfile(sub):
                                kb += max(0, os.path.getsize(sub) // 1024)
                            elif os.path.isdir(sub):
                                r2 = subprocess.run(['du','-sk',sub],capture_output=True,text=True,timeout=3)
                                if r2.returncode == 0:
                                    kb += int(r2.stdout.split()[0])
                    except Exception:
                        kb = 0
                    size_s = f'{kb/1048576:.1f} GB' if kb>=1048576 else (f'{kb/1024:.0f} MB' if kb>0 else '—（超时）')
                    logging.warning(f'du timeout: {path}, fallback estimate: {size_s}')
                except Exception as e:
                    size_s = '—'
                    logging.warning(f'du error {path}: {e}')
                _data_dir_cache['entries'].append({'label':label,'path':path.replace(HOME,'~'),'size':size_s,'status':status})
            _data_dir_cache['ts'] = _now
        return {'data_dirs': list(_data_dir_cache['entries'])}


def _collect_cron():
    """Cron jobs from all instances."""
    data = {'cron': []}
    for cp,src in [
        (os.path.join(HOME,'.qclaw/cron/jobs.json'),'QClaw'),
        (os.path.join(HOME,'.qclaw-hermes/cron/jobs.json'),'QClaw'),
        (os.path.join(HOME,'.openclaw-autoclaw/cron/jobs.json'),'AutoClaw'),
        (os.path.join(HOME,'.jvs/.openclaw/cron/jobs.json'),'JVS'),
    ]:
        if not os.path.exists(cp): continue
        try:
            cd=try_json(cp)
            for job in cd.get('jobs',[]):
                sched=job.get('schedule',{})
                expr=sched.get('expr','') or sched.get('display',job.get('schedule_display','?'))
                lr=job.get('last_run_at','')
                if lr:
                    try:
                        dt=datetime.datetime.fromisoformat(lr)
                        lr=dt.strftime('%m-%d %H:%M')
                    except Exception: pass
                delivery=job.get('delivery',{})
                dc=delivery.get('channel','—')
                ch_cn={'openclaw-weixin':'微信','feishu':'飞书','telegram':'Telegram'}.get(dc,dc)
                payload=job.get('payload',{})
                data['cron'].append({
                    'name':job.get('name',f"Cron#{job.get('id','?')[:8]}"),
                    'schedule':expr,
                    'model':payload.get('model',job.get('model','—')),
                    'last_run':lr or '—',
                    'next_run':_cron_next(expr) or '—',
                    'enabled':job.get('enabled',True),
                    'delivery_mode':delivery.get('mode','—'),
                    'delivery_channel':ch_cn,
                    'delivery_to':delivery.get('to','')[:40],
                    'source':src,
                    'status':'✅' if job.get('last_status')=='ok' else ('⚠️' if job.get('last_status') else '—'),
                })
        except Exception as e: logging.warning(f'cron {cp}: {e}')
    return data


def _collect_skills():
    """Installed skills (cached 5 min)."""
    _now = time.time()
    with _skills_cache_lock:
        if (_now - _skills_cache['ts']) < _SKILLS_CACHE_TTL and _skills_cache['data']:
            return {'skills': json.loads(json.dumps(_skills_cache['data']))}
    skill_dirs = {'QClaw':{}, 'JVS':{}, 'AutoClaw':{}}
    skill_dirs['QClaw']['内置'] = [
        os.path.join(HOME,'Library/Application Support/QClaw/openclaw/config/skills'),
        os.path.join(HOME,'Library/Application Support/QClaw/openclaw/node_modules/openclaw/skills'),
    ]
    skill_dirs['QClaw']['自定义'] = [
        os.path.join(HOME,'.qclaw/skills'), os.path.join(HOME,'.qclaw-hermes/skills'),
    ]
    skill_dirs['JVS']['自定义'] = [
        os.path.join(HOME,'.jvs/.openclaw/skills'),
    ]
    skill_dirs['AutoClaw']['自定义'] = [
        os.path.join(HOME,'.openclaw-autoclaw/skills'),
    ]
    data = {'skills': {}}
    for app, cats in skill_dirs.items():
        data['skills'][app] = {}
        for cat, dirlist in cats.items():
            skills=[]; seen=set()
            for d in dirlist:
                if not os.path.isdir(d): continue
                for item in os.listdir(d):
                    if item in seen or item.startswith('.'): continue
                    ip=os.path.join(d,item)
                    if not os.path.isdir(ip): continue
                    smd=os.path.join(ip,'SKILL.md')
                    if not os.path.exists(smd): continue
                    seen.add(item)
                    desc,ver,mdate='','1.0','—'
                    try:
                        with open(smd) as sf: content=sf.read()
                        fm=re.match(r'---\s*\n(.*?)\n---',content,re.DOTALL)
                        if fm:
                            yt=fm.group(1)
                            dm=re.search(r'description:\s*"?(.+?)"?\s*$',yt,re.MULTILINE)
                            vm=re.search(r'version:\s*"?([^"\n]+)"?',yt)
                            if dm: desc=dm.group(1).strip(' "\'')[:80]
                            if vm: ver=vm.group(1).strip(' "\'')
                        if not desc:
                            for line in content.split('\n'):
                                line=line.strip()
                                if line and not line.startswith('#') and not line.startswith('---') and len(line)>10:
                                    desc=line[:80]; break
                        mdate=datetime.datetime.fromtimestamp(os.path.getmtime(smd)).strftime('%Y-%m-%d')
                    except Exception: pass
                    skills.append({'name':item,'desc':desc,'version':ver,'updated':mdate})
            skills.sort(key=lambda x:x['name'])
            data['skills'][app][cat]=skills
    with _skills_cache_lock:
        _skills_cache['data'] = json.loads(json.dumps(data['skills']))
        _skills_cache['ts'] = _now
    return data


def _collect_session_stats(skills_data):
    """Session stats, tool stats, skill call stats. Uses cached session index."""
    _skill_stats = {}
    _TOOL_SKILL_EXACT = {
        'memory_search': 'memory', 'memory_get': 'memory',
        'lcm_grep': 'context-management', 'lcm_expand': 'context-management',
        'lcm_describe': 'context-management',
        'feishu_doc': 'feishu', 'feishu_bitable': 'feishu', 'feishu_chat': 'feishu',
        'feishu_wiki': 'feishu', 'feishu_drive': 'feishu',
        'browser': 'xbrowser', 'canvas': 'xbrowser',
        'message': 'messaging', 'sessions_send': 'sessions',
        'qclaw_tdoc_mcp_call': 'tencent-docs', 'qclaw_tdoc_mcp_list': 'tencent-docs',
        'ima': 'ima', 'youdaonote': 'youdaonote',
        'skillhub_install': 'skillhub',
        'session_status': 'session',
        'web_fetch': 'web', 'canvas_snapshot': 'canvas',
    }
    _tool_skill_map = {}
    for app, cats in skills_data.items():
        for cat, slist in cats.items():
            for s in slist:
                sn = s['name']
                prefix = sn.replace('-', '_')
                _tool_skill_map[prefix] = sn
                _skill_stats[sn] = {'calls': 0, 'last_call': '', 'last_mtime': 0}

    _tool_counts = {}
    _assistant_count = 0
    _tool_call_total = 0
    _total_content_len = 0
    session_stats = {'total_tokens': 0, 'total_messages': 0, 'active_sessions': 0, 'today_tokens': 0,
                      'assistant_messages': 0, 'tool_calls': 0}
    today = datetime.date.today()

    _session_files = _build_session_index()
    session_stats['active_sessions'] = len(_session_files)
    _MAX_TOKEN_SAMPLE = 20
    for fp, mtime, msgs in _session_files:
        session_stats['total_messages'] += len(msgs)
        sample_msgs = msgs[:_MAX_TOKEN_SAMPLE]
        for obj in sample_msgs:
            if not isinstance(obj, dict): continue
            # Try usage field (some session formats include it)
            usage = obj.get('usage', {})
            if isinstance(usage, dict):
                tokens = usage.get('total_tokens', 0) or usage.get('total', 0)
                if tokens:
                    session_stats['total_tokens'] += tokens
                    ts = obj.get('timestamp', '')
                    if ts:
                        try:
                            mt = datetime.datetime.fromisoformat(ts.replace('Z', '+00:00')).date()
                            if mt == today:
                                session_stats['today_tokens'] += tokens
                        except Exception: pass
            # Count assistant messages and tool calls for estimation
            if obj.get('role') == 'assistant':
                _assistant_count += 1
                content = obj.get('content', '')
                if isinstance(content, str):
                    _total_content_len += len(content)
                tcs = obj.get('tool_calls', [])
                if isinstance(tcs, list):
                    _tool_call_total += len(tcs)
                    for tc in tcs:
                        if not isinstance(tc, dict): continue
                        fn = tc.get('function', {})
                        tname = fn.get('name', '') if isinstance(fn, dict) else str(fn)
                        if not tname: continue
                        _tool_counts[tname] = _tool_counts.get(tname, 0) + 1
                        if tname in _TOOL_SKILL_EXACT:
                            skill_name = _TOOL_SKILL_EXACT[tname]
                            st = _skill_stats.get(skill_name)
                            if st:
                                st['calls'] += 1
                                if mtime > st['last_mtime']:
                                    st['last_mtime'] = mtime
                                    st['last_call'] = datetime.datetime.fromtimestamp(mtime).strftime('%m-%d %H:%M')
                        else:
                            matched = False
                            for prefix, skill_name in sorted(_tool_skill_map.items(), key=lambda x: -len(x[0])):
                                if tname.startswith(prefix):
                                    st = _skill_stats.get(skill_name)
                                    if st:
                                        st['calls'] += 1
                                        if mtime > st['last_mtime']:
                                            st['last_mtime'] = mtime
                                            st['last_call'] = datetime.datetime.fromtimestamp(mtime).strftime('%m-%d %H:%M')
                                    matched = True
                                    break
    # Estimate tokens from assistant content + tool calls when usage not available
    if session_stats['total_tokens'] == 0 and _assistant_count > 0:
        # Rough estimate: ~3 chars per token for mixed content, ~150 tokens per tool call
        estimated = int(_total_content_len / 3) + (_tool_call_total * 150)
        session_stats['total_tokens'] = estimated
        session_stats['today_tokens'] = None  # can't estimate per-day from sample
        session_stats['_estimated'] = True
    session_stats['assistant_messages'] = _assistant_count
    session_stats['tool_calls'] = _tool_call_total

    tool_stats = sorted(_tool_counts.items(), key=lambda x: -x[1])[:15]
    _direct_map = {
        'memory': 'memory', 'memory-1.0.2': 'memory',
        'session_search': 'session-search', 'cronjob': 'cron-job',
        'terminal': 'terminal', 'execute_code': 'executing-plans',
        'read_file': 'file-reader', 'write_file': 'file-writer',
        'search_files': 'file-search', 'patch': 'code-patcher',
        'process': 'process-manager',
        'skills_list': 'skill-creator', 'skill_view': 'skill-creator', 'skill_manage': 'skill-creator',
        'todo': 'todo',
    }
    for tname, skill_name in _direct_map.items():
        if tname in _tool_counts and skill_name in _skill_stats:
            st = _skill_stats[skill_name]
            st['calls'] += _tool_counts[tname]
    for app, cats in skills_data.items():
        for cat, slist in cats.items():
            for s in slist:
                st = _skill_stats.get(s['name'], {})
                s['calls'] = st.get('calls', 0)
                s['last_call'] = st.get('last_call', '—')
    return {'tool_stats': tool_stats, 'session_stats': session_stats, 'skills': skills_data}


def _collect_activity(now):
    """Recent activity across all instances."""
    adirs = [
        os.path.join(HOME,'.qclaw-hermes/sessions'),
        os.path.join(HOME,'.qclaw/agents/main/sessions'),
        os.path.join(HOME,'.qclaw/workspace/sessions'),
        os.path.join(HOME,'.openclaw-autoclaw/agents/main/sessions'),
        os.path.join(HOME,'.jvs/.openclaw/agents/main/sessions'),
    ]
    def _read_jsonl(fp):
        if fp.endswith('.gz'):
            return gzip.open(fp,'rt',errors='replace')
        return open(fp,'r',errors='replace')
    activity = []; seen_names=set(); today=datetime.date.today()
    all_files = []
    for ad in adirs:
        if not os.path.isdir(ad): continue
        try:
            for fn in os.listdir(ad):
                if fn.startswith('.') or '.reset.' in fn: continue
                fp = os.path.join(ad, fn)
                if os.path.isdir(fp):
                    sf = os.path.join(fp, 'store.json')
                    if os.path.exists(sf) and fn not in seen_names:
                        seen_names.add(fn)
                        all_files.append((ad, fn, True))
                elif fn.endswith('.json') or fn.endswith('.jsonl') or fn.endswith('.jsonl.gz'):
                    if fn not in seen_names:
                        seen_names.add(fn)
                        all_files.append((ad, fn, False))
        except Exception: pass
    all_files.sort(key=lambda x: os.path.getmtime(os.path.join(x[0], x[1])), reverse=True)
    all_files = all_files[:50]

    def _session_label(fp, fn):
        def _extract_text(content):
            """Extract text from content which may be str or [{type:text, text:...}]."""
            if isinstance(content, str): return content
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get('type') == 'text':
                        return item.get('text', '')
            return ''
        def _get_user_content(msg):
            """Get (role, text) handling both flat and nested message formats."""
            role = msg.get('role', '')
            inner = msg.get('message', {})
            if isinstance(inner, dict) and inner.get('role'):
                role = inner['role']
                return role, _extract_text(inner.get('content', msg.get('content', '')))
            return role, _extract_text(msg.get('content', ''))
        try:
            with open(fp, 'r', errors='replace') as sf:
                raw = sf.read(16384).strip()
            if fn == 'sessions.json':
                data = json.loads(raw)
                for entry in data.values():
                    if not isinstance(entry, dict): continue
                    if ':heartbeat' in str(entry): continue
                    lbl = entry.get('displayName', '') or entry.get('label', '')
                    if lbl: return lbl
            elif raw.startswith('{'):
                try:
                    rec = json.loads(raw)
                    _assistant_snippets = []
                    for m in rec.get('messages', []):
                        role, c = _get_user_content(m)
                        if role == 'assistant' and c.strip():
                            _assistant_snippets.append(c.strip()[:50])
                        elif role == 'user' and c.strip() and 'HEARTBEAT' not in c:
                            t = c.replace('\n', ' ').replace('\n', ' ').strip()
                            if t.startswith(('Conversation info', 'Sender', '## Runtime', 'Inbound', '[gbrain')):
                                continue
                            return t[:50]
                    if _assistant_snippets:
                        return _assistant_snippets[-1]
                except (json.JSONDecodeError, ValueError):
                    # JSONL format - parse line by line
                    _assistant_snippets = []
                    for line in raw.split('\n'):
                        if not line.strip(): continue
                        try: msg = json.loads(line)
                        except: continue
                        if msg.get('type') == 'session':
                            lbl = msg.get('label', '')
                            if lbl: return lbl
                        role, c = _get_user_content(msg)
                        if role == 'assistant' and c.strip():
                            _assistant_snippets.append(c.strip()[:50])
                        elif role == 'user' and c.strip() and 'HEARTBEAT' not in c:
                            t = c.replace('\n', ' ').replace('\n', ' ').strip()
                            if t.startswith(('Conversation info', 'Sender', '## Runtime', 'Inbound', '[gbrain')):
                                continue
                            return t[:50]
                    if _assistant_snippets:
                        return _assistant_snippets[-1]
            else:
                _assistant_snippets = []
                for line in raw.split('\n'):
                    if not line.strip(): continue
                    try: msg = json.loads(line)
                    except: continue
                    if msg.get('type') == 'session':
                        lbl = msg.get('label', '')
                        if lbl: return lbl
                    role, c = _get_user_content(msg)
                    if role == 'assistant' and c.strip():
                        _assistant_snippets.append(c.strip()[:50])
                    elif role == 'user' and c.strip() and 'HEARTBEAT' not in c:
                        t = c.replace('\n', ' ').replace('\n', ' ').strip()
                        if t.startswith(('Conversation info', 'Sender', '## Runtime', 'Inbound', '[gbrain')):
                            continue
                        return t[:50]
                # Fallback: use last assistant message snippet
                if _assistant_snippets:
                    return _assistant_snippets[-1]
        except Exception as e:
            if 'Extra data' not in str(e):
                logging.warning(f'activity read {fp}: {e}')
        return None

    for item in all_files:
        ad, fn, is_dir = item
        if is_dir:
            fp = os.path.join(ad, fn, 'store.json')
            mtime = os.path.getmtime(fp)
            name = 'Session @ ' + datetime.datetime.fromtimestamp(mtime).strftime('%H:%M')
            try:
                with open(fp, 'r', errors='replace') as sf:
                    rec = json.load(sf)
                    label = rec.get('label', '')
                    if label: name = label
            except Exception: pass
        else:
            fp = os.path.join(ad, fn)
            mtime = os.path.getmtime(fp)
            name = 'Session @ ' + datetime.datetime.fromtimestamp(mtime).strftime('%H:%M')
            label = _session_label(fp, fn)
            if label: name = label
        name = name.replace('\\n', ' ').replace('\n', ' ')[:50]
        md = datetime.datetime.fromtimestamp(mtime).date()
        grp = '今天' if md == today else ('昨天' if md == today - datetime.timedelta(days=1) else '更早')
        activity.append({'name': name, 'age_m': int((now - mtime) // 60),
            'time': datetime.datetime.fromtimestamp(mtime).strftime('%H:%M'),
            'group': grp, 'dir': 'JVS' if '.jvs' in ad else 'AutoClaw' if '.openclaw-autoclaw' in ad else 'Hermes' if '.qclaw-hermes' in ad else 'QClaw'})
    activity.sort(key=lambda x:(0 if x['group']=='今天' else (1 if x['group']=='昨天' else 2),-x['age_m']))
    return {'activity': activity}


def _update_history(data, now):
    """Update rolling history + endpoints + health breakdown."""
    for k in ('cpu','memory','disk','swap'):
        v = data.get(k,{}).get('used_pct',0)
        _history[k].append(v)
        data[k]['history'] = list(_history[k])
    net_rx = data.get('network',{}).get('rx_kbps',0)
    _history['network'].append({'rx': net_rx, 'tx': data['network'].get('tx_kbps',0)})
    data['network_history'] = list(_history['network'])

    # Endpoints
    data['endpoints'], data['latency_hist'] = _collect_endpoints()

    # Health breakdown
    system_score = 100
    cpu_data = data.get('cpu', {})
    mem_data = data.get('memory', {})
    disk_data = data.get('disk', {})
    swap_data = data.get('swap', {})
    if cpu_data.get('used_pct', 0) > 85: system_score -= 10
    if mem_data.get('used_pct', 0) > 90: system_score -= 15
    if disk_data.get('used_pct', 0) > 90: system_score -= 10
    if swap_data.get('used_pct', 0) > 80: system_score -= 10
    endpoints = data.get('endpoints', [])
    ep_score = 100 if all(e.get('ok') for e in endpoints) else (80 if any(e.get('ok') for e in endpoints) else 50)
    data['health_breakdown'] = {
        'system_score': max(system_score, 0),
        'gateway_score': data.get('gateway', {}).get('health_score', 100),
        'endpoint_score': ep_score,
    }

    # CPU trend alert
    cpu_hist = list(_history['cpu'])
    if len(cpu_hist) >= 5:
        trend = [cpu_hist[i+1] - cpu_hist[i] for i in range(len(cpu_hist)-1)]
        if all(t > 3 for t in trend):
            data['cpu_trend'] = {'rising': True, 'last5': cpu_hist[-5:], 'alert': 'CPU 持续上升中，建议关注'}
            logging.warning(f'CPU TREND ALERT: rising={trend}')
    # SSL alerts
    for ep in endpoints:
        days = ep.get('ssl_days_left')
        if days is not None:
            if days <= SSL_CRITICAL_DAYS:
                send_alert('🚨 SSL 证书即将过期', f'{ep["name"]} 证书仅剩 {days} 天', f'ssl_critical_{ep["name"]}')
            elif days <= SSL_WARNING_DAYS:
                send_alert('⚠️ SSL 证书告警', f'{ep["name"]} 证书 {days} 天后到期', f'ssl_warn_{ep["name"]}')


# ====== Orchestrator (P0-1 + P1-1) ======
def collect_all():
    """Collect all system metrics with parallel execution (P1-1: ThreadPoolExecutor).
    Architecture: P0-1 split into independent collectors with shared pre-loaded data."""
    from concurrent.futures import ThreadPoolExecutor
    now = time.time()
    data = {}
    data['script_path'] = os.path.abspath(__file__)

    # Phase 1: Load shared resources (~3s, sequential)
    shared = _SharedProcs()
    shared.load()

    # Phase 2: Config + metadata
    data['config_changes'] = _detect_config_changes(shared.instances)
    data['log_stats'] = get_log_sizes()
    data['all_models'] = shared.all_models
    data['all_providers'] = shared.all_providers
    data['port_conflicts'] = shared.port_conflicts

    # Phase 3: Fast collectors (parallel, ~8s -> ~3s with 4 workers)
    def _safe(key, fn):
        try: return fn()
        except Exception as e: logging.warning(f'{key}: {e}'); return {}

    with ThreadPoolExecutor(max_workers=4) as pool:
        sys_f = pool.submit(_safe, 'system', lambda: _collect_system(shared, now))
        cpu_f = pool.submit(_safe, 'cpu', lambda: _collect_cpu(now))
        mem_f = pool.submit(_safe, 'memory', lambda: _collect_memory(shared))
        disk_f = pool.submit(_safe, 'disk', _collect_disk)
        bat_f = pool.submit(_safe, 'battery', _collect_battery)
        net_f = pool.submit(_safe, 'network', lambda: _collect_network(shared, now))
        dio_f = pool.submit(_safe, 'disk_io', _collect_disk_io)

        data.update(sys_f.result())
        data.update(cpu_f.result())
        data.update(mem_f.result())
        data.update(disk_f.result())
        data.update(bat_f.result())
        data.update(net_f.result())
        data.update(dio_f.result())

    # Merge cpu_load into cpu dict
    if 'cpu_load' in data and 'cpu' in data:
        data['cpu'].update(data.pop('cpu_load'))

    # Phase 4: Gateway (depends on shared procs + system/cpu/mem/disk/swap results)
    gw = _collect_gateway(shared, now,
        data.get('system', {}), data.get('cpu', {}),
        data.get('memory', {}), data.get('disk', {}), data.get('swap', {}))
    data.update(gw)

    # Phase 5: Processes + Ports (need shared ps_procs/lsof)
    data.update(_collect_processes(shared))
    data.update(_collect_ports(shared))

    # Phase 6: Heavy I/O collectors (parallel, ~10s -> ~5s with 3 workers)
    with ThreadPoolExecutor(max_workers=3) as pool:
        dd_f = pool.submit(_safe, 'data_dirs', _collect_data_dirs)
        sk_f = pool.submit(_safe, 'skills', _collect_skills)
        cr_f = pool.submit(_safe, 'cron', _collect_cron)
        act_f = pool.submit(_safe, 'activity', lambda: _collect_activity(now))

        data.update(dd_f.result())
        data.update(sk_f.result())
        data.update(cr_f.result())
        data.update(act_f.result())

    # Phase 7: Session stats (needs skills data)
    ss = _safe('session_stats', lambda: _collect_session_stats(data.get('skills', {})))
    data['tool_stats'] = ss.get('tool_stats', [])
    data['session_stats'] = ss.get('session_stats', {})
    if ss.get('skills'):
        data['skills'] = ss['skills']

    # Phase 8: Post-processing
    data['recent_errors'] = tail_errors('/tmp/mac_ai_monitor.log', 20)
    _update_history(data, now)

    data['timestamp'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    return data

def _load_html():
    try:
        _tpl = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index.html')
        with open(_tpl, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception:
        return "<h1>index.html not found</h1>"

HTML_PAGE = _load_html()
# P3-37: HTML Fallback（模板缺失时硬编码降级）
_HTML_FALLBACK = '<!DOCTYPE html><html><head><meta charset="utf-8"><title>AI Monitor</title></head>'
_HTML_FALLBACK += '<body style="font-family:sans-serif;padding:3rem;text-align:center;background:#f9fafb">'
_HTML_FALLBACK += '<h1 style="color:#1f2937">AI Monitor</h1><p style="color:#6b7280">Loading...</p>'
_HTML_FALLBACK += '<script>setTimeout(()=>location.reload(),3000)</script></body></html>'
if not HTML_PAGE or 'not found' in HTML_PAGE.lower():
    logging.warning('index.html not found, using fallback HTML')
    HTML_PAGE = _HTML_FALLBACK

# ============================================================
# 增强功能 Stubs（架构改进，待实施）
# ============================================================
# P3-26: dataclass 结构化改造
# TODO: 将 data dict 替换为 @dataclass 定义的强类型对象
# class SystemMetrics:
#     cpu: float; mem: float; disk: float; swap: float
#     def health_score(self) -> int: ...

# P3-27: Check Engine 插件化（HealthCheck Protocol）
# TODO: 定义 HealthCheck Protocol，支持自定义检查插件注册
# class HealthCheck(Protocol):
#     name: str
#     def check(self, data: dict) -> tuple[bool, str]: ...
# _health_checks: list[HealthCheck] = []
# def register_health_check(check: HealthCheck): ...

# P3-41: Daily Report 生成器
# TODO: 每日汇总报告（CPU峰值/Token消耗/Gateway可用性）写入 ~/.qclaw/logs/daily_{date}.json
# def _generate_daily_report(): ...

# P3-49: 模板字段 Schema（前端解耦，字段变更时自动检测）
TEMPLATE_SCHEMA = {
    'version': '2',
    'required_fields': ['system','cpu','memory','disk','swap','network','processes','gateway','skills'],
    'optional_fields': ['endpoints','latency_hist','health_breakdown','cpu_trend','config_changes'],
    'data_directory_fields': ['session_stats','tool_stats','skill_stats','cron'],
    'description': 'API响应JSON的字段契约，前端据此渲染UI，字段变更需同步schema'
}

# P3-38: Gray Release 验证脚本（不写缓存，只读校验）
def _gray_validate():
    """灰度发布验证：检查当前 collect_all() 返回数据字段是否完整"""
    import sys
    try:
        data = collect_all()
        required = TEMPLATE_SCHEMA['required_fields']
        missing = [f for f in required if f not in data]
        if missing:
            logging.error(f'GRAY VALIDATE FAILED: missing fields: {missing}')
            return False
        # 检查数据类型
        for f in required:
            if data[f] is None:
                logging.error(f'GRAY VALIDATE FAILED: {f} is None')
                return False
        logging.info('GRAY VALIDATE PASSED: all required fields present')
        return True
    except Exception as e:
        logging.error(f'GRAY VALIDATE ERROR: {e}')
        return False

# ====== 持久化缓存（冷启动优化） ======
def _load_persistent_cache():
    """启动时加载上次缓存，避免服务重启后白屏等待采集"""
    try:
        if os.path.exists(PERSISTENT_CACHE):
            mtime = os.path.getmtime(PERSISTENT_CACHE)
            age = time.time() - mtime
            # 缓存文件不超过30分钟仍可复用
            if age < 1800:
                with open(PERSISTENT_CACHE) as f:
                    data = json.load(f)
                    # 写入内存缓存（TTL内不会重采）
                    _cache['data'] = data
                    _cache['ts'] = time.time() - (CACHE_TTL - min(age, CACHE_TTL))
                    logging.info(f'persistent cache loaded ({age:.0f}s old)')
                    return data
    except Exception as e:
        logging.warning(f'persistent cache load failed: {e}')
    return None

def _save_persistent_cache(data):
    """采集完成后写入持久化缓存"""
    try:
        os.makedirs(os.path.dirname(PERSISTENT_CACHE), exist_ok=True)
        with open(PERSISTENT_CACHE, 'w') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        logging.warning(f'persistent cache save failed: {e}')

# 启动时尝试恢复缓存
_startup_cache = _load_persistent_cache()

def _lite_data():
    """轻量数据采集：只获取快速指标，不触发重型系统调用
    用于Manual模式下用户点击刷新时快速返回缓存数据"""
    now = time.time()
    # 60s内返回缓存
    if _lite_cache['data'] and now - _lite_cache['ts'] < LITE_CACHE_TTL:
        return _lite_cache['data']
    # 优先复用主缓存（已包含全量数据）
    if _cache['data'] and now - _cache['ts'] < CACHE_TTL:
        d = _cache['data']
    else:
        d = collect_all()
        _cache['data'] = d
        _cache['ts'] = now
    # 只提取轻量字段
    lite = {
        'timestamp': d.get('timestamp', ''),
        'version': d.get('version', __version__),
        'cpu': d.get('cpu', {}),
        'memory': d.get('memory', {}),
        'disk': d.get('disk', {}),
        'swap': d.get('swap', {}),
        'gateway': d.get('gateway', {}),
        'battery': d.get('battery', {}),
        'network': d.get('network', {}),
        'health_score': d.get('gateway', {}).get('health_score', 100),
    }
    _lite_cache['data'] = lite
    _lite_cache['ts'] = now
    return lite

# ====== HTTP Handler ======
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path == '/health':
            # Health check endpoint
            try:
                body_data = _cache.get('data') or {}
                gw_count = body_data.get('gateway', {}).get('count', 0)
                status = 'ok' if gw_count > 0 else 'degraded'
                code = 200 if status == 'ok' else 503
                body = json.dumps({'status': status, 'version': __version__, 'gateway_count': gw_count}).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', len(body))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                body = json.dumps({'status': 'error', 'error': str(e)}).encode()
                self.send_header('Content-Length', len(body))
                self.end_headers()
                self.wfile.write(body)
        elif self.path == '/api/gateway-log':
            import glob as _glob
            log_files = sorted(_glob.glob(os.path.join(HOME, '.qclaw/logs/gateway*.log')), reverse=True)
            log_content = ''
            for lf in log_files[:2]:
                try:
                    with open(lf, 'r', errors='replace') as f:
                        log_content += f.read()[-8000:] + '\n---\n'
                except Exception: pass
            body = json.dumps({'log': log_content[-8000:], 'files': [os.path.basename(f) for f in log_files[:5]]}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(body)
        elif self.path == '/api/status':
            # P2-23: 内置 Status Page JSON（供外部监控系统 UptimeRobot 等消费）
            try:
                body_data = _cache.get('data', {})
                endpoints = body_data.get('endpoints', [])
                health = 'ok' if all(e.get('ok') for e in endpoints) else 'degraded'
                ssl_warnings = [e for e in endpoints if e.get('ssl_days_left') is not None and e['ssl_days_left'] <= SSL_WARNING_DAYS]
                ssl_ok = [e for e in endpoints if e.get('ssl_days_left') is not None and e['ssl_days_left'] > SSL_CRITICAL_DAYS]
                overall = 'ok' if health == 'ok' and not ssl_warnings else ('degraded' if ssl_ok else 'critical')
                status_body = {
                    'status': overall,
                    'version': __version__,
                    'timestamp': body_data.get('timestamp', ''),
                    'endpoints': [{'name': e['name'], 'url': e['url'], 'ok': e['ok'],
                                    'latency_ms': e.get('latency_ms', 0)} for e in endpoints],
                    'ssl_warnings': [{'name': e['name'], 'days_left': e['ssl_days_left']} for e in ssl_warnings],
                }
                body = json.dumps(status_body, ensure_ascii=False).encode('utf-8')
                self.send_response(200 if overall == 'ok' else 503)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', len(body))
                self.end_headers()
                try: self.wfile.write(body)
                except BrokenPipeError: pass
            except Exception as e:
                body = json.dumps({'status': 'error', 'error': str(e)}).encode()
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', len(body))
                self.end_headers()
                try: self.wfile.write(body)
                except BrokenPipeError: pass
        elif self.path == '/api/data/lite':
            # 轻量端点：Manual模式用户点击刷新时使用，不触发全量采集
            try:
                data = _lite_data()
                data['version'] = __version__
                body = json.dumps(data, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', len(body))
                self.send_header('Cache-Control', 'private, max-age=60')
                self.end_headers()
                try: self.wfile.write(body)
                except BrokenPipeError: pass
            except Exception as e:
                logging.error(f'lite_data: {e}')
                body = json.dumps({'error': str(e), 'version': __version__}).encode()
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', len(body))
                self.end_headers()
                try: self.wfile.write(body)
                except BrokenPipeError: pass
        elif self.path == '/api/data':
            now = time.time()
            _data = _cache.get('data') or {}
            if _data and now - _cache['ts'] < CACHE_TTL:
                data = _data
            else:
                try:
                    data = collect_all()
                    data['version'] = __version__
                    data['run_path'] = SCRIPT_FILE
                    _cache['data'] = data
                    _cache['ts'] = now
                    # 采集成功后写入持久化缓存（服务重启时可快速恢复）
                    _save_persistent_cache(data)
                except Exception as e:
                    logging.error(f'collect_all: {e}')
                    data = {'error': str(e), 'version': __version__}
            body = json.dumps(data, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', len(body))
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                pass
        elif self.path == '/' or self.path == '/index.html':
            body = HTML_PAGE.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', len(body))
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                pass
        else:
            # Serve static files (CSS/JS/images)
            static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
            rel_path = self.path.lstrip('/')
            # Strip 'static/' prefix since static_dir already includes it
            if rel_path.startswith('static/'):
                rel_path = rel_path[len('static/'):]
            file_path = os.path.normpath(os.path.join(static_dir, rel_path))
            if file_path.startswith(static_dir) and os.path.isfile(file_path):
                ext = os.path.splitext(file_path)[1].lower()
                ct = {'.css': 'text/css; charset=utf-8', '.js': 'application/javascript; charset=utf-8',
                      '.png': 'image/png', '.jpg': 'image/jpeg', '.svg': 'image/svg+xml',
                      '.ico': 'image/x-icon', '.woff2': 'font/woff2'}.get(ext, 'application/octet-stream')
                try:
                    with open(file_path, 'rb') as f:
                        body = f.read()
                    self.send_response(200)
                    self.send_header('Content-Type', ct)
                    self.send_header('Content-Length', len(body))
                    self.send_header('Cache-Control', 'max-age=300')
                    self.end_headers()
                    self.wfile.write(body)
                except (IOError, BrokenPipeError):
                    pass
            else:
                self.send_response(404)
                self.end_headers()
                try:
                    self.wfile.write(b'Not Found')
                except BrokenPipeError:
                    pass

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

class ThreadedServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

# ====== Main ======
def _shutdown(signum, frame):
    print(f'\nReceived signal {signum}, shutting down...')
    logging.info(f'shutdown signal={signum}')
    import threading
    threading.Thread(target=server.shutdown, daemon=True).start()

if __name__ == '__main__':
    import signal
    print(f'Mac AI Monitor v{__version__} starting on port {PORT}...')
    server = ThreadedServer(('0.0.0.0', PORT), Handler)
    # 后台预热 GPU 缓存
    def _prewarm():
        import time
        time.sleep(3)
        global _gpu_cache_store, _now
        _now = time.time()
        try:
            import subprocess
            r = subprocess.run(['system_profiler','SPDisplaysDataType'],capture_output=True,text=True,timeout=20)
            if r.returncode==0:
                _gpu_cache = {'gpus': [], 'ts': 0}
                # 简单解析 GPU 信息
                for line in r.stdout.split('\n'):
                    if 'Chip' in line or 'VRAM' in line:
                        if line.strip():
                            _gpu_cache['gpus'].append({'name':line.strip()[:40],'type':''})
                if _gpu_cache['gpus']:
                    _gpu_cache['ts'] = _now
                    _gpu_cache_store.update(_gpu_cache)
        except: pass
    threading.Thread(target=_prewarm, daemon=True).start()
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    print(f'http://127.0.0.1:{PORT}/')
    print(f'Log: /tmp/mac_ai_monitor.log')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()
    print('Stopped.')
