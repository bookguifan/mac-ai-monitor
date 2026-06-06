#!/usr/bin/env python3
"""
Mac AI Monitor v2.0 — OpenClaw AI 服务监控面板
纯 JS 渲染，手动刷新，单文件部署，零外部依赖。
"""

import os, json, subprocess, re, time, datetime, logging, threading, urllib.request, sqlite3
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import deque
try:
    import requests
except ImportError:
    requests = None  # optional dependency for Gateway perf metrics

# ====== Config ======
PORT          = 8849
CACHE_TTL     = 60
HISTORY_SIZE  = 60
NET_IFACE     = 'en0'
GPU_CACHE_TTL = 60
CPU_TTL       = 60  # top -l 1 needs 60s sampling
_cpu_cache    = {'data': None, 'ts': 0}
SCRIPT_FILE   = os.path.basename(__file__)
HOME          = os.path.expanduser('~')
SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
DATA_DIR      = os.path.join(SCRIPT_DIR, 'data')
LOG_FILE      = os.path.join(DATA_DIR, 'logs', 'monitor.log')
__version__   = '2.18.0'
ALERT_FILE    = os.path.join(DATA_DIR, 'alerts.json')
ALERT_COOLDOWN = 1800  # 30 分钟相同告警不重复
ALERT_CONFIG_FILE = os.path.join(DATA_DIR, 'alert_config.json')

# Default alert thresholds (overridable via ALERT_CONFIG_FILE)
DEFAULT_THRESHOLDS = {
    'cpu':  {'health': 85, 'alert': 90, 'warn': 60, 'danger': 80},
    'mem':  {'health': 90, 'alert': 90, 'warn': 70, 'danger': 85},
    'disk': {'health': 90, 'alert': 90, 'warn': 70, 'danger': 85},
    'swap': {'health': 80, 'alert': 80, 'warn': 50, 'danger': 75},
    'bat':  {'low': 20, 'warn': 50},
    'volume': {'danger': 90}
}

def get_thresholds():
    """Load alert thresholds from config file, fall back to defaults."""
    cfg = try_json(ALERT_CONFIG_FILE)
    if cfg and 'thresholds' in cfg:
        merged = dict(DEFAULT_THRESHOLDS)
        for k, v in cfg['thresholds'].items():
            if k in merged:
                merged[k] = {**merged[k], **v}
            else:
                merged[k] = v
        return merged
    return dict(DEFAULT_THRESHOLDS)

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)
logging.basicConfig(filename=LOG_FILE, level=logging.WARNING,
                    format='%(asctime)s %(levelname)s %(message)s')

# ====== History ======
_history = {k: deque(maxlen=HISTORY_SIZE) for k in ['cpu','mem','disk','swap','net']}
_cache   = {'data': None, 'ts': 0}
_net_s   = {'rx': 0, 'tx': 0, 'ts': 0}
_gpu_cache_store = {'gpus': [], 'ts': 0}
_boot_ts = time.time()  # 服务启动时间戳，用于冷启动保护
_cpu_lock = threading.Lock()
_gpu_lock = threading.Lock()
_cache_lock = threading.Lock()
_history_lock = threading.Lock()
_du_pool  = ThreadPoolExecutor(max_workers=3, thread_name_prefix='du')
_alert_lock = threading.Lock()

# ====== SQLite Timeseries DB ======
TSDB_PATH = os.path.join(DATA_DIR, 'timeseries.db')
TSDB_RETENTION = 7 * 86400  # 保留 7 天

def _init_tsdb():
    """初始化时序数据库和表结构"""
    os.makedirs(DATA_DIR, exist_ok=True)
    with sqlite3.connect(TSDB_PATH) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS samples (
            ts INTEGER PRIMARY KEY,
            cpu_pct REAL, mem_pct REAL, disk_pct REAL, swap_pct REAL,
            health_score INTEGER, gateway_count INTEGER,
            load_1m REAL, net_rx_kbps REAL, net_tx_kbps REAL
        )''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts)')
        conn.commit()

def _append_sample(data):
    """将当前数据采样写入 SQLite"""
    try:
        cpu = data.get('cpu', {})
        mem = data.get('mem', {})
        disk = data.get('disk', {})
        swap = data.get('swap', {})
        net = data.get('net', {})
        gw = data.get('gateway', {})
        sys_info = data.get('system', {})
        with sqlite3.connect(TSDB_PATH) as conn:
            conn.execute('''INSERT OR REPLACE INTO samples
                (ts, cpu_pct, mem_pct, disk_pct, swap_pct, health_score, gateway_count, load_1m, net_rx_kbps, net_tx_kbps)
                VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (int(time.time()),
                 cpu.get('used_pct', 0), mem.get('used_pct', 0),
                 disk.get('used_pct', 0), swap.get('used_pct', 0),
                 gw.get('health_score', 100), gw.get('count', 0),
                 sys_info.get('load_1m', 0),
                 net.get('rx_kbps', 0), net.get('tx_kbps', 0)))
            conn.commit()
        # 清理过期数据
        cutoff = int(time.time()) - TSDB_RETENTION
        with sqlite3.connect(TSDB_PATH) as conn:
            conn.execute('DELETE FROM samples WHERE ts < ?', (cutoff,))
            conn.commit()
    except Exception as e:
        logging.warning(f'_append_sample failed: {e}')

# Token 统计数据库
TOKEN_DB_PATH = os.path.join(DATA_DIR, 'token_stats.db')

def _init_token_db():
    """初始化 Token 统计数据库"""
    os.makedirs(DATA_DIR, exist_ok=True)
    with sqlite3.connect(TOKEN_DB_PATH) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS token_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            date TEXT NOT NULL,
            model TEXT NOT NULL DEFAULT '',
            provider TEXT NOT NULL DEFAULT '',
            session_id TEXT DEFAULT '',
            call_count INTEGER DEFAULT 1,
            tokens_in INTEGER DEFAULT 0,
            tokens_out INTEGER DEFAULT 0,
            duration_ms INTEGER DEFAULT 0
        )''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_token_ts ON token_calls(ts)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_token_date ON token_calls(date)')
        conn.commit()
    logging.info('token_stats.db initialized')

# Token 収集ロック（JSONL解析は重い処理のため排他制御）
_token_lock = threading.Lock()
_token_last_scan = 0
_token_scan_interval = 300  # 5分ごと

# モデルコスト見積り (per 1K tokens, USD)
MODEL_COSTS = {
    'deepseek-chat':      {'in': 0.00014, 'out': 0.00028},
    'deepseek-reasoner':  {'in': 0.00055, 'out': 0.00219},
    'claude-sonnet-4-20250514': {'in': 0.003,  'out': 0.015},
    'gpt-4o':             {'in': 0.0025, 'out': 0.01},
    'qwen3':              {'in': 0.0005, 'out': 0.001},
}

def _estimate_cost(model_name, tokens_in, tokens_out):
    """トークン数からコストを推定 (USD)"""
    for key, rates in MODEL_COSTS.items():
        if key in model_name.lower():
            cost_in = (tokens_in / 1000.0) * rates['in']
            cost_out = (tokens_out / 1000.0) * rates['out']
            return round(cost_in + cost_out, 6)
    # 不明なモデルはデフォルト推定
    if tokens_in > 0 or tokens_out > 0:
        return round((tokens_in + tokens_out) / 1000.0 * 0.0005, 6)
    return 0

def _collect_token_stats():
    """JSONLファイルからモデル呼び出し統計を収集してDBに保存"""
    global _token_last_scan
    now = time.time()
    if now - _token_last_scan < _token_scan_interval:
        return  # キャッシュ有効
    if not _token_lock.acquire(blocking=False):
        return  # 既存のスキャンが実行中
    try:
        jsonl_dir = os.path.join(HOME, '.qclaw')
        if not os.path.isdir(jsonl_dir):
            return
        jsonl_files = sorted(
            [f for f in os.listdir(jsonl_dir) if f.endswith('.jsonl')],
            key=lambda x: os.path.getmtime(os.path.join(jsonl_dir, x)),
            reverse=True
        )[:15]  # 最新15ファイル
        
        new_calls = 0
        for fname in jsonl_files:
            filepath = os.path.join(jsonl_dir, fname)
            session_id = 'unknown'
            try:
                with open(filepath) as fh:
                    for line in fh:
                        try:
                            d = json.loads(line)
                            if d.get('type') == 'session':
                                session_id = d.get('id', 'unknown')
                            msg = d.get('message', {})
                            if msg.get('role') != 'assistant':
                                continue
                            ts_str = d.get('timestamp', '')
                            ts = 0
                            if ts_str:
                                try:
                                    ts = int(datetime.datetime.fromisoformat(
                                        ts_str.replace('Z', '+00:00')).timestamp())
                                except Exception as _exc_e: logging.debug(f"suppressed: {_exc_e}"); pass
                            if ts == 0:
                                ts = int(os.path.getmtime(filepath))
                            date = datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d')
                            model = msg.get('model', 'unknown')
                            provider = msg.get('provider', 'unknown')
                            usage = msg.get('usage', {})
                            tokens_in = usage.get('input', 0) or 0
                            tokens_out = usage.get('output', 0) or 0
                            duration = msg.get('duration_ms', 0) or 0
                            
                            with sqlite3.connect(TOKEN_DB_PATH) as conn:
                                existing = conn.execute(
                                    'SELECT id, call_count FROM token_calls WHERE date=? AND model=? AND provider=? AND session_id=?',
                                    (date, model, provider, session_id)).fetchone()
                                if existing:
                                    conn.execute(
                                        'UPDATE token_calls SET call_count=call_count+1, tokens_in=tokens_in+?, tokens_out=tokens_out+?, duration_ms=duration_ms+? WHERE id=?',
                                        (tokens_in, tokens_out, duration, existing[0]))
                                else:
                                    conn.execute(
                                        'INSERT INTO token_calls (ts, date, model, provider, session_id, call_count, tokens_in, tokens_out, duration_ms) VALUES (?,?,?,?,?,1,?,?,?)',
                                        (ts, date, model, provider, session_id, tokens_in, tokens_out, duration))
                                conn.commit()
                            new_calls += 1
                        except (json.JSONDecodeError, KeyError):
                            continue
            except (IOError, OSError) as e:
                logging.warning(f'Token scan: cannot read {fname}: {e}')
                continue
        
        _token_last_scan = now
        if new_calls > 0:
            logging.info(f'Token scan: {new_calls} new calls from {len(jsonl_files)} files')
        
        # クリーンアップ: 30日以上前のデータを削除
        cutoff_date = (datetime.datetime.now() - datetime.timedelta(days=30)).strftime('%Y-%m-%d')
        with sqlite3.connect(TOKEN_DB_PATH) as conn:
            conn.execute('DELETE FROM token_calls WHERE date < ?', (cutoff_date,))
            conn.commit()
    finally:
        _token_lock.release()

# 初始化数据库
_init_tsdb()
_init_token_db()

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
            except ValueError as _exc_e: logging.debug(f"value err: {_exc_e}"); pass
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
        for _ in range(2880):
            if (base.minute in m_set and base.hour in h_set
                    and base.day in dom_set and base.month in mon_set
                    and (base.weekday() + 1) % 7 in dow_set):
                return base.strftime('%m-%d %H:%M')
            base += datetime.timedelta(minutes=1)
    except Exception as _exc_e:

        logging.warning(f"suppressed exception: {_exc_e}")

        pass
    return None


def run_cmd(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding='utf-8', errors='replace')
        if r.stderr and r.returncode != 0:
            logging.warning(f'cmd stderr: {cmd[:60]}: {r.stderr[:200]}')
        return r.stdout.strip()
    except Exception as e:
        logging.warning(f'cmd failed: {cmd[:60]}: {e}')
        return ''

def esc(s):
    if s is None: return ''
    return str(s).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('"','&quot;')

def fmt_uptime(sec):
    if sec < 0: return '?'
    d = int(sec//86400); r = int(sec%86400)
    h = r//3600; m = (r%3600)//60
    parts = []
    if d: parts.append(f'{d}天')
    if h: parts.append(f'{h}小时')
    if m: parts.append(f'{m}分')
    return ' '.join(parts) or '0分'

def try_json(path):
    try:
        with open(path, encoding='utf-8') as f: return json.load(f)
    except Exception: return {}

def send_alert(title, message, alert_key=''):
    """macOS 系统通知 + 飞书 webhook 推送，带冷却机制避免频繁打扰"""
    try:
        now = time.time()
        with _alert_lock:
            alerts = try_json(ALERT_FILE)
            last_sent = alerts.get(alert_key, {}).get('last_sent', 0)
            if alert_key and (now - last_sent) < ALERT_COOLDOWN:
                return  # 冷却期内不重复通知
            # 提前记录发送时间(防止并发重复发送)
            if alert_key:
                alerts[alert_key] = {'last_sent': now, 'message': message}
                with open(ALERT_FILE, 'w', encoding='utf-8') as f: json.dump(alerts, f)
        # 发送系统通知
        cmd = f'''osascript -e 'display notification "{message}" with title "{title}"' '''
        subprocess.run(cmd, shell=True, capture_output=True, timeout=5)
        
        # 飞书 webhook 推送（需在 data/feishu_webhook 中配置 webhook URL）
        _feishu_webhook = ''
        _wh_file = os.path.join(DATA_DIR, 'feishu_webhook')
        try:
            if os.path.exists(_wh_file):
                _feishu_webhook = open(_wh_file, encoding='utf-8').read().strip()
        except Exception as _exc_e: logging.debug(f"suppressed: {_exc_e}"); pass
        if _feishu_webhook and _feishu_webhook.startswith('http'):
            try:
                _payload = json.dumps({'title': title, 'text': message}).encode('utf-8')
                _req = urllib.request.Request(_feishu_webhook, data=_payload, headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(_req, timeout=5) as _resp: pass
                logging.info(f'Feishu alert sent: {title}')
            except Exception as _e:
                logging.warning(f'Feishu alert failed: {_e}')
        
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
        ('监控面板', LOG_FILE),
        ('监控面板 stdout', os.path.join(DATA_DIR, 'logs', 'monitor_stdout.log')),
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
_discover_cache = {'data': None, 'ts': 0, 'ttl': 300}

def discover_configs():
    """Scan openclaw.json from known instance dirs (5min cache)"""
    now = time.time()
    if _discover_cache['data'] is not None and (now - _discover_cache['ts']) < _discover_cache['ttl']:
        return _discover_cache['data']
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
            except Exception as _exc_e: logging.debug(f"suppressed: {_exc_e}"); pass
    _discover_cache['data'] = out
    _discover_cache['ts'] = time.time()
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
def collect_all():
    now = time.time(); data = {}

    # 部分成功策略：用上次缓存作为默认值，采集失败时用缓存兜底
    if _cache.get("data"):
        for key in ['cpu','mem','disk','swap','net','gateway','system',
                     'disk_io','battery','session_stats','skills','activity',
                     'cron','config_changes','log_stats','top_procs',
                     'ports','volumes','history','data_dirs',
                     'all_models','all_providers','port_conflicts']:
            if key in _cache["data"]:
                data[key] = _cache["data"][key]

    configs = discover_configs()
    instances, all_models, all_providers, port_conflicts = extract_instances(configs)
    data['all_models'] = all_models
    data['all_providers'] = all_providers
    data['port_conflicts'] = port_conflicts
    
    # Config change detection
    config_changes = []
    prev_hashes = try_json(os.path.join(DATA_DIR, 'config_hashes.json')) or {}
    for inst in instances:
        path = inst.get('config_path', '')
        curr_hash = inst.get('config_hash', '')
        prev_hash = prev_hashes.get(path, '')
        if prev_hash and curr_hash and prev_hash != curr_hash:
            config_changes.append({'path': path.replace(HOME, '~'), 'old': prev_hash, 'new': curr_hash})
        prev_hashes[path] = curr_hash
    # Save hashes for next comparison
    try:
        os.makedirs(os.path.dirname(ALERT_FILE), exist_ok=True)
        with open(os.path.join(DATA_DIR, 'config_hashes.json'), 'w', encoding='utf-8') as f:
            json.dump(prev_hashes, f)
    except Exception as _exc_e: logging.debug(f"suppressed: {_exc_e}"); pass
    data['config_changes'] = config_changes
    data['log_stats'] = get_log_sizes()

    # ---- System Info ----
    try:
        out = run_cmd(['sysctl','kern.boottime','hw.memsize','hw.ncpu','hw.physicalcpu',
                       'kern.osproductversion','machdep.cpu.brand_string',
                       'vm.loadavg','vm.swapusage'], 5)
        bt = int(re.search(r'sec\s*=\s*(\d+)',out).group(1)) if re.search(r'sec\s*=\s*(\d+)',out) else 0
        up  = now - bt
        nm  = re.search(r'hw\.ncpu:\s*(\d+)',out)
        pm  = re.search(r'hw\.physicalcpu:\s*(\d+)',out)
        vm  = re.search(r'kern\.osproductversion:\s*(.+)',out)
        cb  = re.search(r'machdep\.cpu\.brand_string:\s*(.+)',out)
        lm  = re.search(r'vm\.loadavg:\s+\{\s+(\S+)\s+(\S+)\s+(\S+)',out)
        sm  = re.search(r'vm\.swapusage:\s*total\s*=\s*([\d.]+)([MG])\s*used\s*=\s*([\d.]+)([MG])',out)
        mem_gb = round(int(re.search(r'hw\.memsize:\s*(\d+)',out).group(1))/1024**3,1) if re.search(r'hw\.memsize:\s*(\d+)',out) else 8
        data['system'] = {
            'hostname': os.uname().nodename,
            'cpu_name': cb.group(1).strip() if cb else 'Apple Silicon',
            'os_version': f"macOS {vm.group(1).strip()}" if vm else 'macOS',
            'uptime_sec': up,'uptime_str': fmt_uptime(up),
            'cpu_p': int(pm.group(1)) if pm else 1,
            'cpu_l': int(nm.group(1)) if nm else 1,
            'memory_gb': mem_gb, 'gpu': [],
        }
        data['cpu_load'] = {
            'load_1m': float(lm.group(1)) if lm else 0,
            'load_5m': float(lm.group(2)) if lm else 0,
            'load_15m': float(lm.group(3)) if lm else 0,
        }
        if sm:
            st = float(sm.group(1)); su = float(sm.group(3))
            if sm.group(2)=='M': st/=1024
            if sm.group(4)=='M': su/=1024
            data['swap'] = {'total_gb':round(st,1),'used_gb':round(su,1),'used_pct':round(su/st*100,1) if st>0 else 0}
        else: data['swap'] = {'total_gb':0,'used_gb':0,'used_pct':0}
    except Exception as e:
        logging.warning(f'system: {e}')
        data['system'] = {'hostname':'?','cpu_name':'?','os_version':'macOS','uptime_sec':0,
                         'uptime_str':'?','cpu_p':1,'cpu_l':1,'memory_gb':0,'gpu':[]}
        data['cpu_load'] = {'load_1m':0,'load_5m':0,'load_15m':0}
        data['swap'] = {'total_gb':0,'used_gb':0,'used_pct':0}

    # ---- GPU (cached 60s, with lock) ----
    _now = time.time()
    with _gpu_lock:
        _cache_available = _gpu_cache_store and _gpu_cache_store.get('gpus')
        _cache_fresh = _cache_available and (_now - _gpu_cache_store['ts']) < GPU_CACHE_TTL

        if _cache_fresh:
            data['system']['gpu'] = list(_gpu_cache_store['gpus'])

        elif _cache_available:
            # 缓存过期但有旧数据：先返回旧数据，后台异步刷新
            data['system']['gpu'] = list(_gpu_cache_store['gpus'])
            def _gpu_refresh():
                gpus = []
                try:
                    out = run_cmd(['system_profiler','SPDisplaysDataType'],10)
                    for line in out.split('\n'):
                        for kw in ['Chipset Model','芯片组型号','Chip','GPU','Graphics']:
                            if kw in line:
                                n = line.split(':')[-1].strip()
                                if n: gpus.append(n)
                                break
                except Exception as _exc_e: logging.debug(f"suppressed: {_exc_e}"); pass
                with _gpu_lock:
                    if gpus:
                        _gpu_cache_store['gpus'] = gpus
                    _gpu_cache_store['ts'] = time.time()
            threading.Thread(target=_gpu_refresh, daemon=True).start()

        else:
            # 无缓存：首次阻塞采集
            gpus = []
            try:
                out = run_cmd(['system_profiler','SPDisplaysDataType'],10)
                for line in out.split('\n'):
                    if 'Chipset Model' in line or '芯片组型号' in line:
                        n = line.split(':')[-1].strip()
                        if n: gpus.append(n)
            except Exception as _exc_e: logging.debug(f"suppressed: {_exc_e}"); pass
            data['system']['gpu'] = gpus
            with _gpu_lock:
                _gpu_cache_store['gpus'] = gpus
                _gpu_cache_store['ts'] = _now

    # ---- CPU Usage (sampled over 60s via top -l 1, with lock) ----
    try:
        _now = time.time()
        with _cpu_lock:
            if _cpu_cache['data'] and _now - _cpu_cache['ts'] < CPU_TTL:
                data['cpu'] = _cpu_cache['data'].copy()
            else:
                out = run_cmd(['top','-l','1','-n','0'], 8)
                for line in out.split('\n'):
                    if 'CPU usage' in line:
                        u = re.search(r'(\d+\.?\d*)%\s*user',line)
                        s = re.search(r'(\d+\.?\d*)%\s*sys',line)
                        i = re.search(r'(\d+\.?\d*)%\s*idle',line)
                        cpu_data = {
                            'user_pct': round(float(u.group(1)),1) if u else 0,
                            'sys_pct': round(float(s.group(1)),1) if s else 0,
                            'idle_pct': round(float(i.group(1)),1) if i else 0,
                            'used_pct': round(((float(u.group(1)) if u else 0)+(float(s.group(1)) if s else 0)),1),
                            'cores': data.get('system', {}).get('cpu_p', 1),
                            'temp': None,
                        }
                        cpu_data.update(data.pop('cpu_load'))
                        _cpu_cache['data'] = cpu_data
                        _cpu_cache['ts'] = _now
                        data['cpu'] = cpu_data.copy()
                        break
                if 'cpu' not in data:
                    data['cpu'] = {'user_pct':0,'sys_pct':0,'idle_pct':100,'used_pct':0,
                                  'load_1m':0,'load_5m':0,'load_15m':0}
    except Exception as e:
        logging.warning(f'cpu: {e}')
        with _cpu_lock:
            data['cpu'] = _cpu_cache['data'].copy() if _cpu_cache['data'] else {'user_pct':0,'sys_pct':0,'idle_pct':100,'used_pct':0,
                          'load_1m':0,'load_5m':0,'load_15m':0}

    # ---- Memory (vm_stat + hw.memsize for total) ----
    try:
        out = run_cmd(['vm_stat'],5); page=4096
        hw_total = data.get('system',{}).get('memory_gb',0)
        if not hw_total:
            _sysctl = run_cmd(['sysctl','hw.memsize'],3)
            _m = re.search(r'hw\.memsize:\s*(\d+)',_sysctl)
            hw_total = round(int(_m.group(1))/1024**3,1) if _m else 8
        def _v(k):
            m = re.search(rf'{k}:\s+(\d+)\.',out)
            return int(m.group(1))*page/1024**3 if m else 0
        act=_v('Pages active'); wir=_v('Pages wired down')
        fre=_v('Pages free'); ina=_v('Pages inactive')
        spe=_v('Pages speculative'); pur=_v('Pages purgeable')
        total = hw_total  # use hw.memsize for accurate physical total
        avail = fre+ina+pur  # available = free+inactive+purgeable (exclude speculative)
        data['mem'] = {
            'active_gb':round(act,1),'wired_gb':round(wir,1),
            'free_gb':round(fre,1),'inactive_gb':round(ina,1),
            'speculative_gb':round(spe,1),'purgeable_gb':round(pur,1),
            'available_gb':round(avail,1),
            'total_gb':round(total,1),
            'used_pct':round((act+wir)/total*100,1) if total>0 else 0,
            'avail_pct':round(avail/total*100,1) if total>0 else 0,
        }
    except Exception as e:
        logging.warning(f'memory: {e}')
        data['mem'] = {'used_pct':0,'active_gb':0,'wired_gb':0,'free_gb':0,
                         'inactive_gb':0,'speculative_gb':0,'purgeable_gb':0,
                         'available_gb':0,'total_gb':0,'avail_pct':0}

    # ---- Disk ----
    try:
        out = run_cmd(['df','-h','/'],5)
        p = out.split('\n')[1].split()
        # 转换磁盘大小为 GB（P3-12: 支持 Gi/Mi/Ki 格式）
        def _parse_to_gb(s):
            s = s.strip()
            # macOS df -h 使用 Gi/Mi/Ki/Ti 格式 (e.g. 233Gi, 14Gi)
            upper = s.upper()
            try:
                if upper.endswith('TI'): return float(s[:-2]) * 1024
                if upper.endswith('GI'): return float(s[:-2])
                if upper.endswith('MI'): return float(s[:-2]) / 1024
                if upper.endswith('KI'): return float(s[:-2]) / 1048576
                # 无 i 后缀 (e.g. 1.5T, 500G, 200M)
                if upper.endswith('T'): return float(s[:-1]) * 1024
                if upper.endswith('G'): return float(s[:-1])
                if upper.endswith('M'): return float(s[:-1]) / 1024
                if upper.endswith('K'): return float(s[:-1]) / 1048576
            except (ValueError, IndexError) as _exc_e: logging.debug(f"suppressed: {_exc_e}"); pass
            return 0
        disk_size_gb = _parse_to_gb(p[1])
        disk_used_gb = _parse_to_gb(p[2])
        disk_avail_gb = _parse_to_gb(p[3])
        data['disk'] = {
            'size':p[1],'used':p[2],'available':p[3],
            'size_gb':round(disk_size_gb,1),'used_gb':round(disk_used_gb,1),'available_gb':round(disk_avail_gb,1),
            'used_pct':int(p[4].replace('%','')),'volumes':[],
        }
    except Exception as e:
        logging.warning(f'disk: {e}')
        data['disk'] = {'size':'?','used':'?','available':'?','size_gb':0,'used_gb':0,'available_gb':0,'used_pct':0,'volumes':[]}

    # ---- Volumes (df-based, single pass) ----
    try:
        # Use df -h for actual per-volume usage, diskutil list for names
        # 1) Parse df -h to get dev -> {size, used, avail, pct}
        df_map = {}
        df_out = run_cmd(['df','-h'],5)
        for line in df_out.split('\n'):
            parts = line.split()
            if len(parts) < 9 or not parts[0].startswith('/dev/disk'):
                continue
            dev = parts[0]
            # Skip snapshot devices (e.g. disk1s5s1)
            if len(dev.replace('/dev/','').split('s')) > 3:
                continue
            df_map[dev] = {
                'size': parts[1], 'used': parts[2],
                'avail': parts[3], 'pct': parts[4],
                'mount': parts[8],
            }

        # 2) Parse diskutil list -h to get all volume names in one call
        vol_names = {}
        try:
            du_out = run_cmd(['diskutil','list'],5)
            cur_dev = None
            for line in du_out.split('\n'):
                vm_disk = re.match(r'\s+(/dev/disk\d+s\d+)\s+(.+)', line)
                if vm_disk:
                    dn = vm_disk.group(1)
                    vname = vm_disk.group(2).strip()
                    if len(dn.replace('/dev/','').split('s')) <= 3 and 'Not applicable' not in vname:
                        vol_names[dn] = vname
        except Exception as _exc_e: logging.debug(f"suppressed: {_exc_e}"); pass
        # 3) Merge: df_map provides size, vol_names provides display name
        for dev, info in sorted(df_map.items()):
            name = vol_names.get(dev, dev.split('/')[-1])
            data['disk']['volumes'].append({
                'dev': dev, 'name': name,
                'size': info['size'], 'used': info['used'],
                'avail': info['avail'], 'pct': info['pct'],
            })
    except Exception as e:
        logging.warning(f'volumes: {e}')

    # ---- Battery ----
    data['battery'] = {'percent':-1,'charging':True,'status_cn':'—'}
    try:
        out = run_cmd(['pmset','-g','batt'],3)
        m = re.search(r'(\d+)%;\s*(\w+)',out)
        if m:
            data['battery']['percent'] = int(m.group(1))
            st = m.group(2)
            data['battery']['charging'] = st in ('charged','charging','AC')
            data['battery']['status_cn'] = {'charging':'充电中','charged':'已充满',
                'discharging':'放电','finishing':'即将充满','AC':'电源'}.get(st,st)
    except Exception as _exc_e: logging.debug(f"suppressed: {_exc_e}"); pass
    # ---- Network ----
    data['net'] = {'interface':NET_IFACE,'rx_kbps':0,'tx_kbps':0,
                       'connections_est':0,'connections_listen':0,'connections_timewait':0}
    try:
        out = run_cmd(['netstat','-ib'],5)
        for line in out.split('\n'):
            if NET_IFACE in line and 'Link#' not in line:
                p=line.split()
                if len(p)>=10:
                    try: rx=int(p[6]); tx=int(p[9]); break
                    except Exception as _exc_e: logging.debug(f"suppressed: {_exc_e}"); pass
        else: rx=tx=0
        dt = now-_net_s['ts']
        if dt>0.5:
            if _net_s['rx']>0:
                data['net']['rx_kbps'] = round((rx-_net_s['rx'])/dt/1024,1)
                data['net']['tx_kbps'] = round((tx-_net_s['tx'])/dt/1024,1)
            # Store current for next delta even on first call
            _net_s.update({'rx':rx,'tx':tx,'ts':now})
    except Exception as e: logging.warning(f'network: {e}')

    # ---- Disk IO (iostat, macOS built-in) ----
    # macOS iostat -d disk0 outputs 3 lines:
    #   disk0
    #       KB/t  tps  MB/s
    #      18.88  112  2.07
    # We parse the data row (3 numeric columns after header)
    data['disk_io'] = {'read_mbps': 0, 'write_mbps': 0, 'iops': 0}
    try:
        out = run_cmd(['iostat', '-d', 'disk0'], 5)
        lines = out.strip().split('\n')
        data_row = None
        for line in lines:
            parts = line.split()
            # header row: KB/t tps MB/s
            if len(parts) >= 3 and parts[0] in ('KB/t', 'KB/'):
                continue
            # data row after disk0 line: 3 numeric values
            if len(parts) >= 3:
                try:
                    kbt = float(parts[0])
                    tps = float(parts[1])
                    mbs = float(parts[2])
                    data['disk_io']['iops'] = tps
                    data['disk_io']['read_mbps'] = mbs
                    data['disk_io']['write_mbps'] = 0  # iostat -d gives combined MB/s
                    break
                except (ValueError, IndexError):
                    continue
    except Exception as e:
        logging.warning(f'disk_io(iostat): {e}')
        # fallback: try psutil if available
        try:
            import psutil
            io1 = psutil.disk_io_counters()
            time.sleep(0.5)
            io2 = psutil.disk_io_counters()
            if io1 and io2:
                dt = 0.5
                read_bytes = (io2.read_bytes - io1.read_bytes) / dt
                write_bytes = (io2.write_bytes - io1.write_bytes) / dt
                data['disk_io']['read_mbps'] = round(read_bytes / 1024 / 1024, 1)
                data['disk_io']['write_mbps'] = round(write_bytes / 1024 / 1024, 1)
                data['disk_io']['iops'] = 0
        except Exception as _exc_e:

            logging.warning(f"suppressed exception: {_exc_e}")

            pass
    try:
        out = run_cmd(['netstat','-an'],5)
        for line in out.split('\n'):
            if 'ESTABLISHED' in line: data['net']['connections_est']+=1
            elif 'LISTEN' in line: data['net']['connections_listen']+=1
            elif 'TIME_WAIT' in line: data['net']['connections_timewait']+=1
    except Exception as _exc_e: logging.debug(f"suppressed: {_exc_e}"); pass
    # ---- Shared Process Table (single ps aux) ----
    ps_procs = {}
    try:
        out = run_cmd(['ps','aux'],5)
        for line in out.strip().split('\n')[1:]:
            p = line.split(None,10)
            if len(p)<11: continue
            try:
                ps_procs[p[1]] = {'rss':int(p[5]),'cpu':float(p[2]),'mem':float(p[3]),
                                   'comm':p[10].split()[0] if p[10] else '',
                                   'args':p[10] if len(p)>10 else ''}
            except (ValueError, IndexError) as _exc_e: logging.debug(f"suppressed: {_exc_e}"); pass
    except Exception as e: logging.warning(f'ps table: {e}')

    # ---- Top Processes (from shared ps_procs) ----
    data['processes'] = {'top_cpu':[],'top_mem':[]}
    try:
        procs = []
        for pid,info in ps_procs.items():
            if SCRIPT_FILE in info.get('args',''): continue
            comm = os.path.basename(info['comm']) if info['comm'] else '?'
            # Get open file count
            fd_count = 0
            try:
                fd_count = len(os.listdir(f'/proc/{pid}/fd')) if os.path.exists(f'/proc/{pid}/fd') else 0
            except Exception:
                # macOS fallback: lsof -p {pid} | wc -l (slow, so skip for now)
                pass
            procs.append({'name':comm[:50],'pid':pid,
                         'cpu':info['cpu'],'mem':info['mem'],'rss_mb':info['rss']//1024,
                         'fd_count': fd_count})
        data['processes']['top_cpu'] = sorted(procs,key=lambda x:x['cpu'],reverse=True)[:8]
        data['processes']['top_mem'] = sorted(procs,key=lambda x:x['rss_mb'],reverse=True)[:8]
    except Exception as e: logging.warning(f'top procs: {e}')

    # ---- Shared lsof (single call, reused by Ports + Gateway) ----
    lsof_listen = []
    lsof_all = []
    try:
        out = run_cmd(['lsof','-i','-P','-n'],5)
        for line in out.split('\n')[1:]:
            lsof_all.append(line)
            if 'LISTEN' in line:
                lsof_listen.append(line)
    except Exception as _exc_e: logging.debug(f"suppressed: {_exc_e}"); pass
    # ---- Ports (from shared lsof_listen + ps_procs) ----
    data['ports'] = []
    try:
        pid_ports = {}
        for line in lsof_listen:
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

    # ---- Gateway Detection (from shared lsof_all) ----
    gateways = []; gw_ports = {}
    # Pre-scan: find gateway PIDs from ps_procs (handles both binary and CLI launches)
    _gw_pid_set = set()
    _gw_cli_rules = [
        {'kw': 'openclaw-gateway'},
        {'kw': 'openclaw', 'extra': lambda a: 'gateway' in a.lower() and 'index.js' in a.lower()},
        {'kw': 'hermes', 'extra': lambda a: 'python' in a.lower()},
        {'kw': 'autoclaw'},
        {'kw': 'jvs', 'extra': lambda a: 'python' not in a.lower() and 'relay' not in a,
         'kw_upper': 'JVS'},
    ]
    for _pid, _info in ps_procs.items():
        _args = _info.get('args', '').lower()
        if SCRIPT_FILE in _args: continue
        for _rule in _gw_cli_rules:
            _kw = _rule.get('kw', '')
            _kw_u = _rule.get('kw_upper', '')
            _cond = (_kw in _args) or (_kw_u and _kw_u in _info.get('args', ''))
            if not _cond: continue
            _extra = _rule.get('extra')
            if _extra and not _extra(_info.get('args', '')): continue
            _gw_pid_set.add(_pid); break
    # Capture ports for gateway PIDs from lsof (dedup per PID)
    try:
        for line in lsof_all:
            if 'LISTEN' not in line: continue
            p = line.split()
            pid = p[1] if len(p) >= 2 else ''
            if pid not in _gw_pid_set: continue
            # Use last :port before (LISTEN) to avoid IPv6 ::1 false match
            m = re.search(r':(\d+)\s+\(LISTEN', line)
            if not m: m = re.search(r':(\d+)', line)
            port = m.group(1) if m else ''
            if port and 1 <= int(port) <= 65535 and pid:
                if port not in gw_ports.get(pid, []):
                    gw_ports.setdefault(pid, []).append(port)
    except Exception as _exc_e: logging.debug(f"suppressed: {_exc_e}"); pass
    ppid_map = {}
    try:
        for line in run_cmd(['ps','-eo','pid,ppid,comm'],5).split('\n')[1:]:
            p=line.strip().split(None,2)
            if len(p)>=2: ppid_map[p[0]]={'ppid':p[1],'comm':p[2] if len(p)>2 else ''}
    except Exception as _exc_e: logging.debug(f"suppressed: {_exc_e}"); pass
    def _detect_source_raw(pid):
        # Trace full PPID chain to root app (PPID=1 = directly launched app)
        def trace_to_app(pid):
            visited = set()
            while pid and pid not in visited:
                visited.add(pid)
                info = ppid_map.get(pid, {})
                ppid = info.get('ppid', '')
                comm = info.get('comm', '')
                if ppid == '1' or not ppid:
                    return comm
                pid = ppid
            return ''

        app_comm = trace_to_app(pid)
        app_lower = app_comm.lower()
        if 'autoclaw' in app_lower: return 'AutoClaw'
        if 'qclaw' in app_lower and 'jvs' not in app_lower: return 'QClaw'
        if 'jvs' in app_lower: return 'JVS'
        if 'hermes' in app_lower: return 'Hermes'
        # fallback: check args from ps_procs
        ps = ps_procs.get(pid, {})
        args = ps.get('args', '').lower()
        if 'autoclaw' in args: return 'AutoClaw'
        if 'jvs' in args or '.jvs' in args: return 'JVS'
        if 'openclaw' in args and 'gateway' in args: return 'OpenClaw'
        if not app_comm: return 'CLI'
        return '?'

    # Override: for openclaw-gateway spawned by JVS Claw, show 'JVS Claw' not '?'
    def detect_source(pid):
        src = _detect_source_raw(pid)
        return src

    _GW_RULES = [
        {'kw': 'openclaw-gateway', 'name_fmt': '{src} Gateway', 'default_name': 'OpenClaw',
         'software': 'OpenClaw Gateway', 'match_inst': True},
        {'kw': 'openclaw', 'name_fmt': '{src} Gateway', 'default_name': 'OpenClaw',
         'software': 'OpenClaw Gateway', 'match_inst': True,
         'extra': lambda a: 'gateway' in a.lower() and 'index.js' in a.lower()},
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
            # Override: openclaw-gateway launched by JVS Claw → source = JVS
            if src == '?' and 'openclaw-gateway' in args_lower:
                ppid = ppid_map.get(pid, {}).get('ppid', '')
                if ppid and ppid in ppid_map:
                    parent_comm = ppid_map[ppid].get('comm', '').lower()
                    if 'jvs' in parent_comm:
                        src = 'JVS'
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

    # ---- Gateway: 按软件名合并 + 性能指标 ----
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
        
        # Try to fetch performance metrics from Gateway API
        port = g.get('port', '—')
        if port and port.isdigit() and 'perf' not in d['perf'] and requests:
            try:
                resp = requests.get(f'http://127.0.0.1:{port}/v1/status', timeout=1)
                if resp.status_code == 200:
                    status_data = resp.json()
                    d['perf'] = {
                        'requests_per_min': status_data.get('rpm', 0) or status_data.get('requests_per_min', 0),
                        'avg_response_ms': status_data.get('latency_ms', 0) or status_data.get('avg_latency', 0),
                        'error_rate': status_data.get('error_rate', 0),
                    }
            except Exception as _exc_e:

                logging.warning(f"suppressed exception: {_exc_e}")

                pass
    merged = []
    for n, d in _by_name.items():
        f = d['first']
        valid_ports = [p for p in d['ports'] if p.isdigit()]
        ports = ','.join(sorted(valid_ports, key=lambda x: (len(x), x))) if valid_ports else f.get('port', '—')
        inst = f
        perf = d.get('perf', {})
        merged.append({
            'name': n,
            'software': f.get('software', '?'),
            'pids': d['pids'],
            'pid_count': len(d['pids']),
            'port': ports,
            'status': '运行中',
            'rss_mb': d['total_rss'],
            'cpu_pct': round(d['total_cpu'], 1),
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
            for i in instances if i['name'] not in active_srcs
            and i.get('mode') != 'local']
    gw_models = list(dict.fromkeys(g.get('primary_model', '') for g in gateways
                                   if g.get('primary_model') and g['primary_model'] != '—'))
    # Health score
    hs = 100
    if not gateways: hs -= 35
    if idle: hs -= 10
    th = get_thresholds()
    data_cpu = data.get('cpu', {})
    data_mem = data.get('mem', {})
    data_dsk = data.get('disk', {})
    data_swp = data.get('swap', {})
    if data_cpu.get('used_pct', 0) > th['cpu']['health']: hs -= 10
    if data_mem.get('used_pct', 0) > th['mem']['health']: hs -= 15
    if data_dsk.get('used_pct', 0) > th['disk']['health']: hs -= 10
    if data_swp.get('used_pct', 0) > th['swap']['health']: hs -= 10
    l1 = data_cpu.get('load_1m', 0)
    cores = data.get('system', {}).get('cpu_p', 1)
    if cores > 0 and l1 / cores > 2: hs -= 10
    data['gateway'] = {'count': len(gateways), 'merged_count': len(merged),
        'instances': gateways, 'merged': merged,
        'idle_count': len(idle), 'idle_instances': idle, 'models': gw_models,
        'health_score': max(hs, 0)}
    
    # ---- Trigger Alerts ----
    _cold_boot = (time.time() - _boot_ts) < 30  # 启动 30 秒内不触发离线告警
    if not gateways and not _cold_boot:
        send_alert('🚨 Gateway 离线', '所有 Gateway 实例已停止运行', 'gw_offline')
    if data_cpu.get('used_pct', 0) > th['cpu']['alert']:
        send_alert('⚠️ CPU 使用率过高', f'CPU 使用率 {data_cpu["used_pct"]}%', 'cpu_high')
    if data_mem.get('used_pct', 0) > th['mem']['alert']:
        send_alert('⚠️ 内存使用率过高', f'内存 使用率 {data_mem["used_pct"]}%', 'mem_high')
    if data_dsk.get('used_pct', 0) > th['disk']['alert']:
        send_alert('⚠️ 磁盘空间不足', f'磁盘使用率 {data_dsk["used_pct"]}%', 'disk_high')
    if data_swp.get('used_pct', 0) > th['swap']['alert']:
        send_alert('⚠️ Swap 使用过高', f'Swap 使用率 {data_swp["used_pct"]}%', 'swap_high')

    # ---- Inject thresholds + normalize None fields ----
    data['thresholds'] = th
    data.setdefault('skills', {})
    data.setdefault('processes', {'top_cpu': [], 'top_mem': []})

    # ---- Data Directories ----
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
    data['data_dirs'] = []
    _dd_results = {}
    _dd_paths = [(label,path,status) for label,path,status in dirs if os.path.isdir(path)]
    _dd_notdir = [(label,path,status) for label,path,status in dirs if not os.path.isdir(path)]
    # 并发 du（全局线程池复用）
    def _du_one(label, path, status):
        try:
            r = subprocess.run(['du','-sk',path], capture_output=True, text=True, timeout=8)
            kb = int(r.stdout.split()[0]) if r.returncode==0 else 0
            size_s = f'{kb/1048576:.1f} GB' if kb>=1048576 else (f'{kb/1024:.0f} MB' if kb>0 else '—')
        except subprocess.TimeoutExpired:
            size_s = '—'
            logging.warning(f'du timeout: {path}')
        except Exception as e:
            size_s = '—'
            logging.warning(f'du error {path}: {e}')
        return (label, path, status, size_s)
    _futures = [_du_pool.submit(_du_one, label, path, status) for label,path,status in _dd_paths]
    for _f in as_completed(_futures):
        try:
            label, path, status, size_s = _f.result()
            _dd_results[label] = {'label':label,'path':path.replace(HOME,'~'),'size':size_s,'status':status}
        except Exception as _e:
            logging.warning(f'du future error: {_e}')
    for label,path,status in _dd_paths:
        if label in _dd_results:
            data['data_dirs'].append(_dd_results[label])
        else:
            data['data_dirs'].append({'label':label,'path':path.replace(HOME,'~'),'size':'—','status':status})
    for label,path,status in _dd_notdir:
        data['data_dirs'].append({'label':label,'path':path.replace(HOME,'~'),'size':'—','status':status})

    # ---- Cron ----
    data['cron'] = []
    # load state file for richer cron observability
    cron_state = {}
    state_path = os.path.join(HOME,'.qclaw/cron/jobs-state.json')
    if os.path.exists(state_path):
        try:
            state_data = try_json(state_path)
            cron_state = state_data.get('jobs',{})
        except Exception as _exc_st: logging.debug(f"suppressed: {_exc_st}")
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
                jid = job.get('id','')
                sched=job.get('schedule',{})
                expr=sched.get('expr','') or sched.get('display',job.get('schedule_display','?'))
                lr=job.get('last_run_at','')
                if lr:
                    try:
                        dt=datetime.datetime.fromisoformat(lr)
                        lr=dt.strftime('%m-%d %H:%M')
                    except Exception as _exc_e: logging.debug(f"suppressed: {_exc_e}"); pass
                delivery=job.get('delivery',{})
                dc=delivery.get('channel','—')
                ch_cn={'openclaw-weixin':'微信','feishu':'飞书','telegram':'Telegram'}.get(dc,dc)
                payload=job.get('payload',{})
                # enrich with state data
                st = cron_state.get(jid,{}).get('state',{})
                last_err = st.get('lastError','')
                last_dur_ms = st.get('lastDurationMs',0)
                consec_err = st.get('consecutiveErrors',0)
                last_status = st.get('lastRunStatus') or st.get('lastStatus') or job.get('last_status','')
                data['cron'].append({
                    'id':jid[:12],
                    'name':job.get('name',f"Cron#{jid[:8]}"),
                    'schedule':expr,
                    'model':payload.get('model',job.get('model','—')),
                    'last_run':lr or '—',
                    'last_run_at_ms':st.get('lastRunAtMs',0),
                    'next_run':_cron_next(expr) or '—',
                    'next_run_at_ms':st.get('nextRunAtMs',0),
                    'enabled':job.get('enabled',True),
                    'delivery_mode':delivery.get('mode','—'),
                    'delivery_channel':ch_cn,
                    'delivery_to':delivery.get('to','')[:40],
                    'source':src,
                    'status':'✅' if last_status=='ok' else ('❌' if last_status=='error' else ('⚠️' if last_status else '—')),
                    'last_error':last_err[:120] if last_err else '',
                    'last_duration_ms':last_dur_ms,
                    'consecutive_errors':consec_err,
                })
        except Exception as e: logging.warning(f'cron {cp}: {e}')

    # ---- Skills ----
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
    data['skills'] = {}
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
                    except Exception as _exc_e: logging.debug(f"suppressed: {_exc_e}"); pass
                    skills.append({'name':item,'desc':desc,'version':ver,'updated':mdate})
            skills.sort(key=lambda x:x['name'])
            data['skills'][app][cat]=skills

    # ---- Skill Call Statistics (scan session files) ----
    _skill_stats = {}
    # Build tool prefix → skill name mapping
    _tool_skill_map = {}
    for app, cats in data['skills'].items():
        for cat, slist in cats.items():
            for s in slist:
                sn = s['name']
                # Convert skill-name → likely tool prefix (e.g., feishu-bitable → feishu_bitable)
                prefix = sn.replace('-', '_')
                _tool_skill_map[prefix] = sn
                _skill_stats[sn] = {'calls': 0, 'last_call': '', 'last_mtime': 0}

    # Scan session directories for tool calls
    _session_dirs = [
        os.path.join(HOME,'.qclaw/agents/main/sessions'),
        os.path.join(HOME,'.qclaw-hermes/sessions'),
        os.path.join(HOME,'.qclaw/workspace/sessions'),
        os.path.join(HOME,'.openclaw-autoclaw/agents/main/sessions'),
    ]
    _scanned = 0
    for sd in _session_dirs:
        if not os.path.isdir(sd): continue
        try:
            for fn in os.listdir(sd):
                fp = os.path.join(sd, fn)
                if fn.startswith('.') or '.checkpoint.' in fn: continue
                if not (fn.endswith('.json') or fn.endswith('.jsonl') or fn.endswith('.jsonl.gz')):
                    if os.path.isdir(fp):
                        sf = os.path.join(fp, 'store.json')
                        if os.path.exists(sf): fp = sf
                        else: continue
                    else: continue
                _scanned += 1
                if _scanned > 200: break  # limit scan
                mtime = os.path.getmtime(fp)
                try:
                    opener = gzip.open if fp.endswith('.gz') else open
                    with opener(fp, 'rt', errors='replace') as sf:
                        raw = sf.read()
                    # Try single JSON first (Hermes format: {messages:[...]})
                    try:
                        doc = json.loads(raw)
                        msgs = doc.get('messages', [])
                    except Exception:
                        # JSONL: one JSON object per line
                        msgs = []
                        for line in raw.strip().split('\n'):
                            if not line.strip(): continue
                            try: msgs.append(json.loads(line))
                            except Exception: continue
                    for obj in msgs:
                        if not isinstance(obj, dict): continue
                        if obj.get('type') == 'message' or obj.get('role') == 'assistant':
                            tcs = obj.get('tool_calls') or obj.get('message', {}).get('tool_calls') or []
                            for tc in (tcs if isinstance(tcs, list) else []):
                                if not isinstance(tc, dict): continue
                                fn = tc.get('function', {}) if isinstance(tc, dict) else {}
                                tname = fn.get('name', '') if isinstance(fn, dict) else str(fn)
                                if not tname: continue
                                for prefix, skill_name in _tool_skill_map.items():
                                    if tname.startswith(prefix):
                                        st = _skill_stats.get(skill_name)
                                        if st:
                                            st['calls'] += 1
                                            if mtime > st['last_mtime']:
                                                st['last_mtime'] = mtime
                                                st['last_call'] = datetime.datetime.fromtimestamp(mtime).strftime('%m-%d %H:%M')
                except Exception:
                    continue
            if _scanned > 200: break
        except Exception as e:
            logging.warning(f'skill stats scan {sd}: {e}')

    # Also collect per-tool stats for display
    _tool_counts = {}
    for sd in _session_dirs:
        if not os.path.isdir(sd): continue
        try:
            for fn in os.listdir(sd):
                fp = os.path.join(sd, fn)
                if fn.startswith('.') or '.checkpoint.' in fn: continue
                if not (fn.endswith('.json') or fn.endswith('.jsonl') or fn.endswith('.jsonl.gz')):
                    if os.path.isdir(fp):
                        sf = os.path.join(fp, 'store.json')
                        if os.path.exists(sf): fp = sf
                        else: continue
                    else: continue
                try:
                    opener = gzip.open if fp.endswith('.gz') else open
                    with opener(fp, 'rt', errors='replace') as sf:
                        raw = sf.read()
                    try:
                        doc = json.loads(raw)
                        msgs = doc.get('messages', [])
                    except Exception:
                        msgs = []
                        for line in raw.strip().split('\n'):
                            if not line.strip(): continue
                            try: msgs.append(json.loads(line))
                            except Exception: continue
                    for obj in msgs:
                        if not isinstance(obj, dict): continue
                        if obj.get('type') == 'message' or obj.get('role') == 'assistant':
                            tcs = obj.get('tool_calls') or obj.get('message', {}).get('tool_calls') or []
                            for tc in (tcs if isinstance(tcs, list) else []):
                                if not isinstance(tc, dict): continue
                                fn = tc.get('function', {}) if isinstance(tc, dict) else {}
                                tname = fn.get('name', '') if isinstance(fn, dict) else str(fn)
                                if tname: _tool_counts[tname] = _tool_counts.get(tname, 0) + 1
                except Exception:
                    continue
        except Exception:
            continue
    data['tool_stats'] = sorted(_tool_counts.items(), key=lambda x: -x[1])[:15]

    # Merge call stats into skills (use improved mapping)
    # Direct tool→skill mapping for known tools
    _direct_map = {
        'mem': 'memory', 'memory-1.0.2': 'memory',
        'session_search': 'session-search',
        'cronjob': 'cron-job',
        'terminal': 'terminal',
        'execute_code': 'executing-plans',
        'read_file': 'file-reader',
        'write_file': 'file-writer',
        'search_files': 'file-search',
        'patch': 'code-patcher',
        'process': 'process-manager',
        'skills_list': 'skill-creator', 'skill_view': 'skill-creator', 'skill_manage': 'skill-creator',
        'todo': 'todo',
    }
    for tname, skill_name in _direct_map.items():
        if tname in _tool_counts and skill_name in _skill_stats:
            st = _skill_stats[skill_name]
            st['calls'] += _tool_counts[tname]

    for app, cats in data['skills'].items():
        for cat, slist in cats.items():
            for s in slist:
                st = _skill_stats.get(s['name'], {})
                s['calls'] = st.get('calls', 0)
                s['last_call'] = st.get('last_call', '—')

    # ---- Session Token Statistics ----
    session_stats = {'total_tokens': 0, 'total_messages': 0, 'active_sessions': 0, 'today_tokens': 0}
    today = datetime.date.today()
    for ad in [
        os.path.join(HOME,'.qclaw-hermes/sessions'),
        os.path.join(HOME,'.qclaw/agents/main/sessions'),
        os.path.join(HOME,'.qclaw/workspace/sessions'),
        os.path.join(HOME,'.openclaw-autoclaw/agents/main/sessions'),
    ]:
        if not os.path.isdir(ad): continue
        try:
            for fn in os.listdir(ad):
                if fn.startswith('.') or '.checkpoint.' in fn: continue
                fp = os.path.join(ad, fn)
                if os.path.isdir(fp):
                    fp = os.path.join(fp, 'store.json')
                    if not os.path.exists(fp): continue
                if not (fn.endswith('.json') or fn.endswith('.jsonl') or fn.endswith('.jsonl.gz')): continue
                session_stats['active_sessions'] += 1
                try:
                    opener = gzip.open if str(fp).endswith('.gz') else open
                    with opener(fp, 'rt', errors='replace') as sf:
                        raw = sf.read()
                    try:
                        doc = json.loads(raw)
                        msgs = doc.get('messages', [])
                    except Exception:
                        msgs = []
                        for line in raw.strip().split('\n'):
                            if not line.strip(): continue
                            try: msgs.append(json.loads(line))
                            except Exception: continue
                    session_stats['total_messages'] += len(msgs)
                    for m in msgs:
                        if not isinstance(m, dict): continue
                        # QClaw: usage 在 message.usage 内；Hermes: usage 在消息顶层
                        if 'message' in m and isinstance(m.get('message'), dict):
                            usage = m['message'].get('usage', {})
                        else:
                            usage = m.get('usage', {})
                        if isinstance(usage, dict):
                            tokens = 0
                            for key in ['totalTokens','total_tokens','total']:
                                v = usage.get(key)
                                if v is not None:
                                    tokens = v
                                    break
                            if tokens:
                                session_stats['total_tokens'] += tokens
                                # Check if today
                                ts = m.get('timestamp', '')
                                if ts:
                                    try:
                                        mt = datetime.datetime.fromisoformat(ts.replace('Z', '+00:00')).date()
                                        if mt == today:
                                            session_stats['today_tokens'] += tokens
                                    except Exception as _exc_e: logging.debug(f"suppressed: {_exc_e}"); pass
                except Exception: continue
        except Exception: continue
    data['sessions'] = session_stats
    
    # ---- Recent Errors ----
    data['recent_errors'] = tail_errors(LOG_FILE, 20)

    # ---- Activity ----
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
                    # 目录型 session (workspace/sessions/UUID/store.json)
                    sf = os.path.join(fp, 'store.json')
                    if os.path.exists(sf) and fn not in seen_names:
                        seen_names.add(fn)
                        all_files.append((ad, fn, True))  # True = is dir
                elif fn.endswith('.json') or fn.endswith('.jsonl') or fn.endswith('.jsonl.gz'):
                    if fn not in seen_names:
                        seen_names.add(fn)
                        all_files.append((ad, fn, False))
        except Exception as _exc_e: logging.debug(f"suppressed: {_exc_e}"); pass
    # Sort by mtime descending, limit to 50
    all_files.sort(key=lambda x: os.path.getmtime(os.path.join(x[0], x[1])), reverse=True)
    all_files = all_files[:50]

    # Helper: read label/user-msg from session file
    def _session_label(fp, fn):
        try:
            if fn == 'sessions.json':
                # QClaw/JVS index: top-level entries have displayName/label/sessionFile
                with open(fp, 'r', errors='replace') as sf:
                    data = json.load(sf)
                for entry in data.values():
                    if not isinstance(entry, dict): continue
                    if ':heartbeat' in str(entry): continue
                    lbl = entry.get('displayName', '') or entry.get('label', '')
                    if lbl: return lbl
                    sf_path = entry.get('sessionFile', '')
                    if sf_path and os.path.isfile(sf_path):
                        return _session_label(sf_path, os.path.basename(sf_path))
            else:
                with _read_jsonl(fp) as sf:
                    raw = sf.read().strip()
                if raw.startswith('{') and not raw.startswith('{\n  "agent:'):
                    # Hermes JSON: single object with messages[]
                    rec = json.loads(raw)
                    for m in rec.get('messages', []):
                        if m.get('role') == 'user':
                            c = m.get('content', '')
                            if isinstance(c, str) and c.strip() and 'HEARTBEAT' not in c:
                                return c.replace('\\n', ' ').replace('\n', ' ').strip()[:50]
                else:
                    for line in raw.split('\n'):
                        if not line.strip(): continue
                        try: msg = json.loads(line)
                        except Exception: continue
                        if msg.get('type') == 'session':
                            lbl = msg.get('label', '')
                            if lbl: return lbl
                        if msg.get('role') == 'user':
                            c = msg.get('content', '')
                            if isinstance(c, str) and c.strip() and 'HEARTBEAT' not in c:
                                return c.replace('\\n', ' ').replace('\n', ' ').strip()[:50]
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
            except Exception as _exc_e: logging.debug(f"suppressed: {_exc_e}"); pass
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
    data['activity'] = activity
    # ---- History ----
    with _history_lock:
        for k in ('cpu','mem','disk','swap'):
            v = data.get(k,{}).get('used_pct',0)
            _history[k].append(v)
            data[k]['history'] = list(_history[k])
        # Network history
        net_rx = data.get('net',{}).get('rx_kbps',0)
        _history['net'].append({'rx': net_rx, 'tx': data['net'].get('tx_kbps',0)})
        data['network_history'] = list(_history['net'])

    data['timestamp'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # 时序采样写入 SQLite（异步不阻塞）
    _append_sample(data)
    
    # Token 使用统计収集（JSONL スキャン、5分キャッシュ）
    _collect_token_stats()
    
    return data


# ====== HTML Template ======
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Mac AI Monitor v2.17.0</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0b0f1a;--card:#131a2e;--card-h:#19213a;
  --border:#1e2d45;--accent:#00cfff;--accent2:#7b61ff;
  --green:#3dd68c;--orange:#ffb347;--red:#ff6b6b;
  --text:#e6edf3;--text2:#7d9ab5;--text3:#4a5a72;
  --r:6px;--glow:0 0 20px rgba(0,207,255,.15);
}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'SF Pro',sans-serif;font-size:13px;line-height:1.5;min-height:100vh}
svg{overflow:visible}
a{color:var(--accent);text-decoration:none}
button{cursor:pointer;border:none;outline:none;font:inherit}

/* Header */
.hdr{display:flex;align-items:center;justify-content:space-between;padding:14px 20px;
  background:var(--card);border-bottom:1px solid var(--border);gap:16px}
.hdr-l{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.logo{width:28px;height:28px;background:linear-gradient(135deg,var(--accent),var(--accent2));
  border-radius:8px;display:flex;align-items:center;justify-content:center;font-weight:900;font-size:15px;color:#000}
.title{font-size:15px;font-weight:700;letter-spacing:.3px}
.title span{color:var(--accent);font-weight:400;font-size:11px;margin-left:6px}
.hdr-r{display:flex;align-items:center;gap:8px}
.btn{padding:5px 14px;border-radius:var(--r);font-size:12px;font-weight:500;transition:.2s;background:var(--card-h);color:var(--text);border:1px solid var(--border)}
.btn:hover{background:var(--accent);color:#000;border-color:var(--accent)}
.btn-on{background:var(--accent);color:#000;border-color:var(--accent)}
.btn-on:hover{background:#ff6b6b;border-color:#ff6b6b;color:#fff}
.btn-auto{background:var(--accent);color:#000;border-color:var(--accent);animation:pulse-dot 2s ease-in-out infinite}
.btn-auto:hover{background:var(--card-h);color:var(--text);border-color:var(--border);animation:none}
@keyframes pulse-dot{0%,100%{opacity:1}50%{opacity:.7}}
.time{color:var(--text3);font-size:11px;font-variant-numeric:tabular-nums}
.auto-indicator{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--green);margin-right:4px;animation:pulse-dot 1.5s ease-in-out infinite}

/* Main */
.main{max-width:1400px;margin:0 auto;padding:16px 16px 32px}

/* Quick Bar */
.qbar{display:grid;grid-template-columns:repeat(8,1fr);gap:8px;margin-bottom:16px}
.qi{background:var(--card);border:1px solid var(--border);border-radius:var(--r);
  padding:10px 12px;display:flex;flex-direction:column;gap:3px;transition:.2s}
.qi:hover{border-color:var(--accent);box-shadow:var(--glow)}
.qi-label{font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.5px}
.qi-val{font-size:18px;font-weight:700;font-variant-numeric:tabular-nums}
.qi-sub{font-size:10px;color:var(--text2)}
.danger .qi-val{color:var(--red)}
.warn .qi-val{color:var(--orange)}
.ok .qi-val{color:var(--green)}

/* Row layouts */
.row{display:grid;gap:12px;margin-bottom:12px}
.row-2{grid-template-columns:1fr 1fr}
.row-3{grid-template-columns:1fr 1fr 1fr}
.row-flex{display:flex;gap:12px;flex-wrap:wrap}
.row-flex>*{flex:1;min-width:280px}
.row-full{width:100%}

/* Cards */
.card{background:var(--card);border:1px solid var(--border);border-radius:var(--r)}
.ch{padding:10px 14px;border-bottom:1px solid var(--border);display:flex;align-items:center;
  justify-content:space-between;font-size:11px;font-weight:600;color:var(--text2);
  text-transform:uppercase;letter-spacing:.5px}
.ch-r{color:var(--text3);font-weight:400;text-transform:none}
.cb{padding:12px 14px}

/* Gauges */
.gauge-row{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:12px}
.gauge-card{background:var(--card);border:1px solid var(--border);border-radius:var(--r);
  padding:16px;display:flex;flex-direction:column;align-items:center;gap:10px}
.gauge-card:hover{border-color:var(--accent)}
.g-svg{position:relative}
.g-label{font-size:11px;color:var(--text2);text-transform:uppercase;letter-spacing:.5px}
.g-val{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums}
.g-sub{font-size:10px;color:var(--text3)}

/* Sparkline */
.spark{width:100%;height:32px;display:block}

/* System Info */
.info-table{width:100%;border-collapse:collapse}
.info-table td{padding:5px 8px;font-size:12px;border-bottom:1px solid var(--border)}
.info-table tr:last-child td{border-bottom:none}
.info-table .k{color:var(--text2);white-space:nowrap;width:1%}
.info-table .v{font-variant-numeric:tabular-nums}

/* Memory Detail */
.mem-bars{display:flex;flex-direction:column;gap:8px;margin-bottom:12px}
.mbar{display:flex;align-items:center;gap:8px;font-size:11px}
.mbar-label{min-width:80px;color:var(--text2)}
.mbar-track{flex:1;height:6px;background:var(--border);border-radius:3px;overflow:hidden}
.mbar-fill{height:100%;border-radius:3px;transition:width .5s}
.mbar-val{min-width:40px;text-align:right;font-variant-numeric:tabular-nums}

/* Tables */
.tbl{width:100%;border-collapse:collapse;font-size:12px}
.tbl th{padding:6px 8px;text-align:left;font-size:10px;text-transform:uppercase;
  letter-spacing:.5px;color:var(--text3);border-bottom:1px solid var(--border);
  position:sticky;top:0;background:var(--card)}
.tbl td{padding:6px 8px;border-bottom:1px solid var(--border);vertical-align:top}
.tbl tr:last-child td{border-bottom:none}
.tbl tr:hover td{background:rgba(0,207,255,.04)}
.tbl .n{font-variant-numeric:tabular-nums;text-align:right}
.tbl .warn-bg{background:rgba(255,107,107,.08)}
.tbl .ok-bg{background:rgba(61,214,140,.08)}

/* Ports */
.port-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:6px}
.port-chip{display:flex;align-items:center;gap:6px;padding:5px 10px;
  background:var(--card-h);border-radius:4px;font-size:11px;border:1px solid var(--border)}
.port-chip .port-num{font-weight:700;color:var(--accent);font-variant-numeric:tabular-nums}
.port-chip .port-pid{color:var(--text3)}

/* Gateway */
.gw-grid{display:flex;flex-direction:column;gap:8px}
.gw-card{background:var(--card-h);border:1px solid var(--border);border-radius:var(--r);
  padding:12px 14px;display:flex;flex-wrap:wrap;gap:16px;align-items:flex-start}
.gw-card.run{border-left:3px solid var(--green)}
.gw-card.idle{border-left:3px solid var(--orange);border-style:dashed;opacity:.75}
.gw-card.warn{border-left:3px solid var(--red)}
.gw-item{display:flex;flex-direction:column;gap:2px}
.gw-k{font-size:9px;color:var(--text3);text-transform:uppercase;letter-spacing:.5px}
.gw-v{font-size:12px;font-weight:500}
.gw-badge{display:inline-flex;align-items:center;padding:1px 7px;border-radius:10px;font-size:10px;font-weight:600}
.gw-badge.green{background:rgba(61,214,140,.15);color:var(--green)}
.gw-badge.orange{background:rgba(255,179,71,.15);color:var(--orange)}
.gw-badge.red{background:rgba(255,107,107,.15);color:var(--red)}
.gw-badge.blue{background:rgba(0,207,255,.15);color:var(--accent)}

/* Data Dirs */
.dir-list{display:flex;flex-direction:column;gap:4px}
.dir-row{display:flex;align-items:center;gap:8px;padding:5px 8px;border-radius:4px;font-size:12px}
.dir-row:hover{background:rgba(0,207,255,.04)}
.dir-label{min-width:90px;font-weight:500}
.dir-path{color:var(--text3);flex:1;font-size:11px}
.dir-size{font-variant-numeric:tabular-nums;color:var(--text2)}
.dir-st{font-size:10px;padding:1px 6px;border-radius:8px}
.st-active{background:rgba(61,214,140,.12);color:var(--green)}
.st-cleanup{background:rgba(255,179,71,.12);color:var(--orange)}
.st-backup{background:rgba(123,97,255,.12);color:var(--accent2)}

/* Activity */
.act-group{margin-bottom:8px}
.act-ghdr{font-size:10px;text-transform:uppercase;letter-spacing:.5px;
  color:var(--text3);padding:4px 0;border-bottom:1px solid var(--border);margin-bottom:4px}
.act-item{display:flex;align-items:center;gap:8px;padding:4px 6px;border-radius:4px;font-size:12px}
.act-item:hover{background:rgba(0,207,255,.04)}
.act-time{min-width:40px;color:var(--text3);font-size:11px;font-variant-numeric:tabular-nums}
.act-icon{font-size:13px}
.act-name{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

/* Skills */
.skill-bar{display:flex;align-items:center;gap:8px;margin-bottom:10px}
.skill-bar input{flex:1;padding:6px 10px;background:var(--card-h);border:1px solid var(--border);
  border-radius:var(--r);color:var(--text);font-size:12px;outline:none}
.skill-bar input:focus{border-color:var(--accent)}
.skill-bar select{padding:6px 10px;background:var(--card-h);border:1px solid var(--border);
  border-radius:var(--r);color:var(--text);font-size:12px;outline:none}
.skill-cat{margin-bottom:10px}
.skill-cat-h{font-size:11px;color:var(--text2);padding:4px 0 6px;
  border-bottom:1px solid var(--border);margin-bottom:6px}
.skill-list{display:flex;flex-wrap:wrap;gap:6px}
.skill-chip{display:inline-flex;align-items:center;gap:4px;padding:3px 10px;
  background:var(--card-h);border:1px solid var(--border);border-radius:20px;font-size:11px;
  cursor:pointer;transition:.2s}
.skill-chip:hover{border-color:var(--accent);color:var(--accent)}
.sk-name{font-weight:500}
.sk-ver{color:var(--text3);font-size:10px}
.sk-desc{font-size:10px;color:var(--text3);margin-top:8px;padding-left:4px}
.skill-chip .sk-detail{display:none}
.skill-chip.expanded,.skill-chip.expanded:hover{border-color:var(--accent);background:var(--accent);color:#fff}
.skill-chip.expanded .sk-detail{display:block;margin-left:8px}
.skill-chip.expanded .sk-name{color:#fff}

/* Cron */
.cron-badge{display:inline-flex;align-items:center;gap:3px;padding:1px 6px;
  border-radius:10px;font-size:9px;font-weight:600}
.cb-ok{background:rgba(61,214,140,.15);color:var(--green)}
.cb-err{background:rgba(255,107,107,.15);color:var(--red)}
.cb-warn{background:rgba(255,179,71,.15);color:var(--orange)}
.cb-off{background:rgba(74,90,114,.3);color:var(--text3);text-decoration:line-through}
.cb-dis{background:rgba(74,90,114,.3);color:var(--text3)}

/* Footer */
.ft{text-align:center;padding:16px;color:var(--text3);font-size:11px;
  border-top:1px solid var(--border);margin-top:24px}
.ft span{margin:0 8px}

/* Health bar */
.hbar{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:12px}
.hb-card{padding:10px 14px;display:flex;align-items:center;gap:10px}
.hb-num{font-size:28px;font-weight:800}
.hb-label{font-size:11px;color:var(--text3)}
.hb-sub{font-size:13px;font-weight:600}

/* Responsive */
@media(max-width:900px){
  .row-3{grid-template-columns:1fr 1fr}
  .row-2{grid-template-columns:1fr}
  .qbar{grid-template-columns:repeat(4,1fr)}
  .hbar{grid-template-columns:repeat(2,1fr)}
  .gauge-row{grid-template-columns:1fr 1fr}
  .qi-label{font-size:10px}
  .qi-val{font-size:15px}
}
@media(max-width:600px){
  .qbar{grid-template-columns:repeat(2,1fr)}
  .hbar{grid-template-columns:1fr}
  .gauge-row{grid-template-columns:1fr}
  .row-3{grid-template-columns:1fr}
}

/* Scrollable */
.scroll{overflow-x:auto;max-height:300px;overflow-y:auto}
.scroll::-webkit-scrollbar{width:4px;height:4px}
.scroll::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px}

/* Loading */
.loading{text-align:center;padding:60px;color:var(--text3);animation:pulse 1.5s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:.4}50%{opacity:1}}
/* Skeleton shimmer */
.skeleton{background:linear-gradient(90deg,var(--border) 25%,var(--card) 50%,var(--border) 75%);background-size:200% 100%;animation:shimmer 1.5s ease-in-out infinite}
@keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}
.skeleton-box{display:inline-block;background:var(--border);border-radius:4px}
.skeleton-text{height:12px;margin:6px 0;border-radius:2px}
.skeleton-title{height:20px;width:60%;margin-bottom:12px}
.skeleton-card{height:80px;margin:8px 0;border-radius:8px}
.error{color:var(--red);padding:20px;text-align:center;font-size:13px}

/* Toggle panels */
.toggle-btn{background:none;border:none;color:var(--text2);font-size:11px;
  cursor:pointer;display:flex;align-items:center;gap:4px;padding:0}
.toggle-btn:hover{color:var(--accent)}
.toggle-arrow{transition:transform .2s}
.collapsed .toggle-arrow{transform:rotate(-90deg)}
.collapsed .toggle-content{display:none}

/* Health score */
.health-ring{position:relative;width:60px;height:60px}
.health-ring svg{transform:rotate(-90deg)}
.health-ring text{transform:rotate(90deg);transform-origin:center}

/* Skills show-more */
.sk-hidden{display:none}
.skill-subcat{margin-bottom:10px}
.skill-subcat-h{font-size:11px;color:var(--text2);padding:4px 0 6px;
  border-bottom:1px solid var(--border);margin-bottom:6px}
.sk-showmore{display:block;width:100%;text-align:center;padding:4px 0;
  color:var(--accent);font-size:11px;cursor:pointer;background:none;
  border:none;margin-top:4px;transition:.2s}
.sk-showmore:hover{color:#fff}

/* Hidden */
.dn{display:none}
</style>
</head>
<body>

<!-- Header -->
<div class="hdr">
  <div class="hdr-l">
    <div class="logo">M</div>
    <div class="title">Mac AI Monitor<span id="ver"></span></div>
  </div>
  <div class="hdr-r">
    <span class="time" id="ts">—</span>
    <select id="refresh-sel" class="btn" style="padding:2px 6px;font-size:12px;background:var(--card);color:var(--text);border:1px solid var(--border);border-radius:6px;cursor:pointer" title="刷新间隔"></select>
    <button class="btn btn-auto" id="btn-auto" title="自动刷新: 开" onclick="toggleAutoRefresh()"><span class="auto-indicator"></span>⟳ 自动刷新</button>
    <button class="btn" id="btn-alerts" title="告警历史" onclick="showAlerts()">🔔</button>
    <button class="btn" id="btn-export" title="导出数据" onclick="exportData()">📤</button>
    <button class="btn" id="btn-refresh" title="手动刷新">⟳ 刷新</button>
  </div>
</div>

<!-- App -->
<div class="main" id="app">
  <div id="loading" style="padding:20px">
    <div class="skeleton skeleton-title"></div>
    <div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:16px">
      <div class="skeleton skeleton-card" style="width:120px;height:60px"></div>
      <div class="skeleton skeleton-card" style="width:120px;height:60px"></div>
      <div class="skeleton skeleton-card" style="width:120px;height:60px"></div>
      <div class="skeleton skeleton-card" style="width:120px;height:60px"></div>
    </div>
    <div class="skeleton skeleton-text" style="width:80%;margin-top:24px"></div>
    <div class="skeleton skeleton-text" style="width:60%"></div>
    <div class="skeleton skeleton-text" style="width:70%"></div>
    <div style="color:var(--text3);margin-top:16px;font-size:12px">正在加载数据...</div>
  </div>
</div>

<!-- Footer -->
<div class="ft" id="ft"></div>

<script>
// ====== State ======
let _data = null;
let _interval = null;

// ====== Utilities ======
const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);

function pct_c(p, w=70, c=85){
  if(p>=c) return 'var(--red)';
  if(p>=w) return 'var(--orange)';
  return 'var(--green)';
}

function fmt_pct(n){ return (n||0).toFixed(1)+'%'; }
function fmt_gb(n){ return ((n||0)).toFixed(1)+'GB'; }
function fmt_mb(n){ return Math.round(n||0)+'MB'; }
function fmt_age(m){
  if(m<1) return '刚刚';
  if(m<60) return m+'分钟前';
  return Math.floor(m/60)+'小时前';
}
function esc(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

// ====== Sparkline SVG ======
function sparkline(vals, w=200, h=30, color='#00cfff'){
  if(!vals||vals.length<2) return '';
  const min=Math.min(...vals), max=Math.max(...vals);
  const range=max-min||1;
  const pts=vals.map((v,i)=>{
    const x=(i/(vals.length-1))*w;
    const y=h-((v-min)/range)*(h-4)-2;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <defs><linearGradient id="sg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="${color}" stop-opacity=".3"/>
      <stop offset="100%" stop-color="${color}" stop-opacity="0"/>
    </linearGradient></defs>
    <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.5"/>
    <polygon points="0,${h} ${pts} ${w},${h}" fill="url(#sg)"/>
  </svg>`;
}

// ====== Donut Gauge SVG ======
function donut(pct, size=100, stroke=8, color='#3dd68c'){
  const r=(size-stroke)/2; const c=2*Math.PI*r;
  const dash=c*pct/100; const gap=c-dash;
  return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
    <circle cx="${size/2}" cy="${size/2}" r="${r}" fill="none" stroke="var(--border)" stroke-width="${stroke}"/>
    <circle cx="${size/2}" cy="${size/2}" r="${r}" fill="none" stroke="${color}" stroke-width="${stroke}"
      stroke-dasharray="${dash.toFixed(1)} ${gap.toFixed(1)}" stroke-linecap="round"/>
    <text x="${size/2}" y="${size/2}" text-anchor="middle" dominant-baseline="middle"
      font-size="${size*.18}" font-weight="700" fill="var(--text)" dy=".1em">${pct.toFixed(0)}%</text>
  </svg>`;
}

// ====== Progress Bar ======
function pbar(pct, color){
  return `<div style="width:${Math.min(pct,100).toFixed(1)}%;background:${color};height:100%;border-radius:3px;transition:width .5s"></div>`;
}

function _actIcon(dir){
  if(!dir) return '💬';
  if(dir.includes('JVS')) return '🦅';
  if(dir.includes('hermes')) return '🦊';
  if(dir.includes('AutoClaw')) return '⚙️';
  if(dir.includes('QClaw')) return '🤖';
  return '💬';
}

// ====== Fetch ======
async function load(){
  try{
    const r=await fetch('/api/data');
    if(!r.ok) throw new Error('HTTP '+r.status);
    _data=await r.json();
    try{ render(_data); }catch(e2){
      console.error('render error:',e2);
      $('#app').innerHTML='<div class="error">渲染失败: '+esc(e2.message)+'<br><button class="btn" onclick="load()" style="margin-top:12px">重试</button></div>';
    }
  }catch(e){
    console.error('load error:',e);
    $('#app').innerHTML='<div class="error">加载失败: '+esc(e.message)+'<br><button class="btn" onclick="load()" style="margin-top:12px">重试</button></div>';
  }
}

function render(d){
  // Header
  document.title = 'Mac AI Monitor v'+(d.version||'?');
  $('#ts').textContent = d.timestamp||'—';
  $('#ver').textContent = 'v'+(d.version||'?');

  // Build app
  let html='';

  // --- Quick Bar ---
  const sys=d.system||{};
  const cpu=d.cpu||{};
  const mem=d.memory||{};
  const disk=d.disk||{};
  const swp=d.swap||{};
  const gw=d.gateway||{};
  const net=d.network||{};
  const bat=d.battery||{};
  const diskio=d.disk_io||{};
  const sess=d.session_stats||{};
  const logs=d.log_stats||[];
  const errs=d.recent_errors||[];
  const cfgChg=d.config_changes||[];

  const cpu_p=cpu.used_pct||0;
  const mem_p=mem.used_pct||0;
  const disk_p=disk.used_pct||0;
  const swp_p=swp.used_pct||0;
  const bat_p=bat.percent||0;

  // Thresholds from backend config
  const TH=d.thresholds||{};
  const THc=TH.cpu||{},THm=TH.mem||{},THd=TH.disk||{},THs=TH.swap||{},THb=TH.bat||{},THv=TH.volume||{};

  // Alert counter
  const alerts=[];
  if(cpu_p>(THc.danger||80)) alerts.push('CPU '+cpu_p+'%');
  if(mem_p>(THm.danger||85)) alerts.push('内存 '+mem_p+'%');
  if(disk_p>(THd.danger||85)) alerts.push('磁盘 '+disk_p+'%');
  if(swp_p>(THs.danger||75)) alerts.push('Swap '+swp_p+'%');
  if(bat_p>=0&&bat_p<(THb.low||20)&&!bat.charging) alerts.push('电池 '+bat_p+'%');
  if(!gw.count) alerts.push('无Gateway');
  if(gw.idle_count) alerts.push(gw.idle_count+'个闲置');
  // Check high-usage volumes
  (disk.volumes||[]).forEach(v=>{const p=parseInt(v.pct);if(p>=(THv.danger||90)) alerts.push(v.name+' '+p+'%');});
  const alert_n=alerts.length;

  html+=`<div class="qbar">
    <div class="qi ${alert_n>2?'danger':alert_n>0?'warn':'ok'}" title="${alerts.length?alerts.join(', '):'一切正常'}">
      <span class="qi-label">⚠️ 告警</span>
      <span class="qi-val" style="color:${alert_n>2?'var(--red)':alert_n>0?'var(--orange)':'var(--green)'}">${alert_n||'✓'}</span>
      <span class="qi-sub">${alert_n?alert_n+'项异常':'一切正常'}</span>
    </div>
    <div class="qi ${pct_c(cpu_p,THc.warn||60,THc.danger||80)}">
      <span class="qi-label">CPU</span>
      <span class="qi-val" style="color:${pct_c(cpu_p)}">${fmt_pct(cpu_p)}</span>
      <span class="qi-sub">用户${fmt_pct(cpu.user_pct)} 系统${fmt_pct(cpu.sys_pct)}</span>
    </div>
    <div class="qi ${mem_p>(THm.danger||85)?'danger':mem_p>(THm.warn||70)?'warn':'ok'}">
      <span class="qi-label">内存</span>
      <span class="qi-val" style="color:${pct_c(mem_p,THm.warn||70,THm.danger||85)}">${fmt_pct(mem_p)}</span>
      <span class="qi-sub">可用 ${fmt_gb(mem.available_gb||0)} / ${fmt_gb(mem.total_gb)}</span>
    </div>
    <div class="qi ${disk_p>(THd.danger||85)?'danger':disk_p>(THd.warn||70)?'warn':'ok'}">
      <span class="qi-label">磁盘</span>
      <span class="qi-val" style="color:${pct_c(disk_p,THd.warn||70,THd.danger||85)}">${fmt_pct(disk_p)}</span>
      <span class="qi-sub">${esc(disk.used)} / ${esc(disk.size)}</span>
    </div>
    <div class="qi ${swp_p>(THs.danger||75)?'danger':swp_p>(THs.warn||50)?'warn':'ok'}">
      <span class="qi-label">Swap</span>
      <span class="qi-val" style="color:${pct_c(swp_p,THs.warn||50,THs.danger||75)}">${fmt_pct(swp_p)}</span>
      <span class="qi-sub">${fmt_gb(swp.used_gb)} / ${fmt_gb(swp.total_gb)}</span>
    </div>
    <div class="qi ${gw.count>0?'ok':'danger'}">
      <span class="qi-label">Gateway</span>
      <span class="qi-val">${gw.count||0}</span>
      <span class="qi-sub">${gw.idle_count?'闲置'+gw.idle_count:''} · 健康${gw.health_score||0}</span>
    </div>
    <div class="qi">
      <span class="qi-label">网络 ↓↑</span>
      <span class="qi-val">${net.rx_kbps||0}↓</span>
      <span class="qi-sub">↑${esc(net.tx_kbps||'0')} KB/s</span>
    </div>
    <div class="qi">
      <span class="qi-label">磁盘IO</span>
      <span class="qi-val">${(diskio.read_mbps||0).toFixed(1)}</span>
      <span class="qi-sub">${(diskio.iops||0).toFixed(0)} IOPS · MB/s</span>
    </div>
    <div class="qi ${bat_p>=0&&bat_p<(THb.low||20)?'danger':bat_p<(THb.warn||50)?'warn':'ok'}">
      <span class="qi-label">电池</span>
      <span class="qi-val">${bat_p>=0?bat_p+'%':'—'}</span>
      <span class="qi-sub">${esc(bat.status_cn||'—')}</span>
    </div>
    <div class="qi">
      <span class="qi-label">会话</span>
      <span class="qi-val">${sess.active_sessions||0}</span>
      <span class="qi-sub">${((sess.total_tokens||0)/1000).toFixed(0)}k tokens</span>
    </div>
  </div>`;

  // --- Gauges Row ---
  const cpu_hist=cpu.history||[];
  const mem_hist=mem.history||[];
  const disk_hist=disk.history||[];
  html+=`<div class="gauge-row">
    <div class="gauge-card">
      ${sparkline(cpu_hist, 220, 32, pct_c(cpu_p,THc.warn||60,THc.danger||80))}
      <div class="g-svg">${donut(cpu_p,90,7,pct_c(cpu_p,THc.warn||60,THc.danger||80))}</div>
      <div class="g-label">CPU 使用率</div>
      <div class="g-val" style="color:${pct_c(cpu_p,THc.warn||60,THc.danger||80)}">${fmt_pct(cpu_p)}</div>
      <div class="g-sub">用户${fmt_pct(cpu.user_pct)} · 系统${fmt_pct(cpu.sys_pct)}</div>
    </div>
    <div class="gauge-card">
      ${sparkline(mem_hist, 220, 32, pct_c(mem_p,THm.warn||70,THm.danger||85))}
      <div class="g-svg">${donut(mem.avail_pct||0,90,7,pct_c(100-mem.avail_pct||0,THm.warn||70,THm.danger||85))}</div>
      <div class="g-label">内存可用</div>
      <div class="g-val" style="color:${pct_c(100-mem.avail_pct||0,THm.warn||70,THm.danger||85)}">${fmt_pct(mem.avail_pct)}</div>
      <div class="g-sub">可用 ${fmt_gb(mem.available_gb)} / ${fmt_gb(mem.total_gb)}</div>
    </div>
    <div class="gauge-card">
      ${sparkline(disk_hist, 220, 32, pct_c(disk_p,THd.warn||70,THd.danger||85))}
      <div class="g-svg">${donut(disk_p,90,7,pct_c(disk_p,THd.warn||70,THd.danger||85))}</div>
      <div class="g-label">磁盘使用</div>
      <div class="g-val" style="color:${pct_c(disk_p,THd.warn||70,THd.danger||85)}">${disk_p}%</div>
      <div class="g-sub">可用 ${esc(disk.available||'—')}</div>
    </div>
  </div>`;

  // --- Health Summary Bar ---
  const hs=gw.health_score||0;
  const sys_cpu_p=sys.cpu_p||1;
  const sys_mem_p=sys.mem_p||1;
  const sys_disk_p=sys.disk_p||1;
  const sys_swp_p=sys.swp_p||1;
  const load1=cpu.load_1m||0;
  const cores=sys.cpu_p||1;
  const load_ratio=(load1/cores).toFixed(2);
  const hs_c=hs>=80?'var(--green)':hs>=50?'var(--orange)':'var(--red)';
  const hs_parts=[]; // health score breakdown for tooltip
  if(!gw.count) hs_parts.push('无Gateway -35');
  if(gw.idle_count) hs_parts.push('闲置Gateway -10');
  if(cpu_p>85) hs_parts.push('CPU '+cpu_p+'% -10');
  if(mem_p>90) hs_parts.push('内存 '+mem_p+'% -15');
  if(disk_p>90) hs_parts.push('磁盘 '+disk_p+'% -10');
  if(swp_p>80) hs_parts.push('Swap '+swp_p+'% -10');
  if(cores>0&&load1/cores>2) hs_parts.push('负载 '+load_ratio+' -10');
  const hs_tip=hs_parts.length?'影响因子:\n'+hs_parts.join('\n'):'所有指标正常';
  const gw_run=gw.count||0; const gw_idle=gw.idle_count||0;
  const cron_list=d.cron||[]; const cron_active=cron_list.filter(c=>c.enabled!==false).length;
  html+=`<div class="hbar"><div class="card hb-card">
      <div class="hb-num" style="color:${hs_c};cursor:help" title="${hs_tip}">${hs}</div>
      <div><div class="hb-label">健康评分</div><div class="hb-sub">${hs>=80?'状态良好':hs>=50?'需要关注':'异常'}</div></div>
    </div>
    <div class="card hb-card">
      <div class="hb-num" style="color:${gw_run>0?'var(--green)':'var(--red)'}">${gw_run}</div>
      <div><div class="hb-label">Gateway</div><div class="hb-sub">${gw_run}运行${gw_idle?' · '+gw_idle+'闲置':''}</div></div>
    </div>
    <div class="card hb-card">
      <div class="hb-num" style="color:var(--accent)">${cron_active}</div>
      <div><div class="hb-label">定时任务</div><div class="hb-sub">${cron_list.length}总${cron_active?' · '+cron_active+'活跃':''}</div></div>
    </div>
    <div class="card hb-card">
      <div class="hb-num" style="color:${alert_n===0?'var(--green)':'var(--orange)'}">${alert_n}</div>
      <div><div class="hb-label">活跃告警</div><div class="hb-sub">${alert_n===0?'一切正常':alert_n+'项需关注'}</div></div>
    </div>
  </div>`;
  const gpu_list=sys.gpu||[];
  html+=`<div class="row row-3">
    <div class="card">
      <div class="ch">系统信息</div>
      <div class="cb">
        <table class="tbl"><tbody>
          <tr><td class="k">主机名</td><td class="v">${esc(sys.hostname||'?')}</td></tr>
          <tr><td class="k">CPU</td><td class="v">${esc(sys.cpu_name||'?')} <span style="color:var(--text3)">${sys.cpu_p||1}P/${sys.cpu_l||1}L核</span></td></tr>
          ${gpu_list.map(g=>`<tr><td class="k">GPU</td><td class="v">${esc(g)}</td></tr>`).join('')}
          <tr><td class="k">内存</td><td class="v">${sys.memory_gb||0} GB</td></tr>
          <tr><td class="k">系统</td><td class="v">${esc(sys.os_version||'macOS')}</td></tr>
          <tr><td class="k">运行时间</td><td class="v">${esc(sys.uptime_str||'?')} <span style="color:var(--text3)">负载${load1} (${load_ratio})</span></td></tr>
        </tbody></table>
      </div>
    </div>
    <div class="card">
      <div class="ch">网络 · ${esc(net.interface||'en0')}</div>
      <div class="cb">
        <table class="tbl"><tbody>
          <tr><td class="k">下载</td><td class="v">${(net.rx_kbps||0).toFixed(1)} KB/s</td></tr>
          <tr><td class="k">上传</td><td class="v">${(net.tx_kbps||0).toFixed(1)} KB/s</td></tr>
          <tr><td class="k">已连接</td><td class="v">${net.connections_est||0}</td></tr>
          <tr><td class="k">监听端口</td><td class="v">${net.connections_listen||0}</td></tr>
          <tr><td class="k">等待</td><td class="v">${net.connections_timewait||0}</td></tr>
        </tbody></table>
        <div style="margin-top:12px">
          ${sparkline((d.network_history||[]).map(n=>n.rx||0),180,28,'var(--green)')}
          ${sparkline((d.network_history||[]).map(n=>n.tx||0),180,28,'var(--accent)')}
        </div>
        <div style="display:flex;align-items:center;gap:6px;margin-top:12px;padding-top:8px;border-top:1px solid var(--border)">
          <span style="font-size:11px;color:var(--text2)">磁盘IO:</span>
          <span style="color:var(--accent);font-weight:600;font-size:12px">${(diskio.read_mbps||0).toFixed(1)} MB/s</span>
          <span style="color:var(--text3);font-size:10px">${(diskio.iops||0).toFixed(0)} IOPS</span>
        </div>
      </div>
    </div>
    <div class="card">
      <div class="ch">存储卷 <span class="ch-r">${esc(disk.used)} / ${esc(disk.size)}</span></div>
      <div class="cb">
        <div style="margin-bottom:12px">
          <div style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:4px">
            <span style="color:var(--text2)">根卷</span>
            <span style="color:${pct_c(disk_p,THd.warn||70,THd.danger||85)}">${disk_p}%</span>
          </div>
          <div style="height:6px;background:var(--border);border-radius:3px;overflow:hidden">
            ${pbar(disk_p, pct_c(disk_p,THd.warn||70,THd.danger||85))}
          </div>
        </div>
        ${(disk.volumes||[]).length===0?'<div style="color:var(--text3);padding:12px">暂无可用磁盘</div>':(disk.volumes||[]).slice(0,6).map(v=>`
        <div style="display:flex;justify-content:space-between;align-items:center;padding:4px 0;border-bottom:1px solid var(--border);font-size:11px">
          <span style="color:var(--text2);flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(v.dev)}">${esc(v.name||v.dev)}</span>
          <span style="color:${v.pct&&parseInt(v.pct)>=90?'var(--red)':'var(--text2)'};margin-left:8px;white-space:nowrap">${esc(v.pct||'')} ${v.used||''} / ${esc(v.size||'')}</span>
        </div>`).join('')}
      </div>
    </div>
  </div>`;

  // --- Row 3: Memory Detail + Ports + Models ---
  html+=`<div class="row row-flex">
    <div class="card" style="min-width:240px">
      <div class="ch">内存详情</div>
      <div class="cb">
        <div class="mem-bars">
          <div class="mbar"><span class="mbar-label">活跃</span>
            <div class="mbar-track"><div class="mbar-fill" style="width:${((mem.active_gb/mem.total_gb)*100||0).toFixed(1)}%;background:var(--accent)"></div></div>
            <span class="mbar-val">${fmt_gb(mem.active_gb)}</span></div>
          <div class="mbar"><span class="mbar-label">有线</span>
            <div class="mbar-track"><div class="mbar-fill" style="width:${((mem.wired_gb/mem.total_gb)*100||0).toFixed(1)}%;background:var(--red)"></div></div>
            <span class="mbar-val">${fmt_gb(mem.wired_gb)}</span></div>
          <div class="mbar"><span class="mbar-label">非活跃</span>
            <div class="mbar-track"><div class="mbar-fill" style="width:${((mem.inactive_gb/mem.total_gb)*100||0).toFixed(1)}%;background:var(--text3)"></div></div>
            <span class="mbar-val">${fmt_gb(mem.inactive_gb)}</span></div>
          <div class="mbar"><span class="mbar-label">空闲</span>
            <div class="mbar-track"><div class="mbar-fill" style="width:${((mem.free_gb/mem.total_gb)*100||0).toFixed(1)}%;background:var(--green)"></div></div>
            <span class="mbar-val">${fmt_gb(mem.free_gb)}</span></div>
          <div class="mbar"><span class="mbar-label">可清除</span>
            <div class="mbar-track"><div class="mbar-fill" style="width:${((mem.purgeable_gb/mem.total_gb)*100||0).toFixed(1)}%;background:var(--orange)"></div></div>
            <span class="mbar-val">${fmt_gb(mem.purgeable_gb)}</span></div>
        </div>
        <div style="margin-top:10px">
          <div style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:4px">
            <span style="color:var(--text2)">Swap ${fmt_gb(swp.used_gb)} / ${fmt_gb(swp.total_gb)}</span>
            <span style="color:${pct_c(swp_p,THs.warn||50,THs.danger||75)}">${fmt_pct(swp_p)}</span>
          </div>
          <div style="height:6px;background:var(--border);border-radius:3px;overflow:hidden">
            ${pbar(swp_p, pct_c(swp_p,THs.warn||50,THs.danger||75))}
          </div>
        </div>
        ${sparkline(d.swap&&d.swap.history?d.swap.history:[],180,24,'var(--orange)')}
      </div>
    </div>

    <div class="card" style="min-width:240px">
      <div class="ch">监听端口 <span class="ch-r">${(d.ports||[]).length}个</span></div>
      <div class="cb">
        ${(d.ports||[]).length===0?'<div style="color:var(--text3);padding:12px">暂无监听端口</div>':`<div class="port-grid">
          ${(d.ports||[]).slice(0,20).map(p=>`
          <div class="port-chip">
            <span class="port-num">:${esc(p.port||'')}</span>
            <span style="color:var(--text3);font-size:10px">${esc(p.process||'?')}</span>
            ${p.desc?`<span style="color:var(--text3);font-size:9px">${esc(p.desc)}</span>`:''}
          </div>`).join('')}
        </div>`}
      </div>
    </div>

    <div class="card" style="min-width:280px">
      <div class="ch">模型列表 <span class="ch-r">${(d.all_models||[]).length}个</span></div>
      <div class="cb scroll">
        ${(d.all_models||[]).length===0?'<div style="color:var(--text3);padding:12px">暂无模型列表</div>':`<table class="tbl" style="min-width:260px"><thead>
          <tr><th>来源</th><th>Provider</th><th>模型</th><th class="n">上下文</th></tr>
        </thead><tbody>
          ${(d.all_models||[]).slice(0,15).map(m=>`
          <tr class="${m.is_primary?'ok-bg':''} ${m.is_image?'warn-bg':''}">
            <td><span class="gw-badge blue" style="font-size:9px">${esc(m.app||'?')}</span></td>
            <td><span style="color:var(--accent2);font-weight:500">${esc(m.provider||'?')}</span></td>
            <td>${esc(m.id||'?')} ${m.is_primary?'<span style="color:var(--green);font-size:10px">主</span>':''} ${m.is_image?'<span style="color:var(--orange);font-size:10px">图</span>':''}</td>
            <td class="n" style="color:var(--text2)">${m.context?(m.context/1000).toFixed(0)+'k':'—'}</td>
          </tr>`).join('')}
        </tbody></table>`}
      </div>
    </div>
  </div>`;


  // --- Row 4: Model Provider Status ---
  html+=`<div class="row row-flex">
    <div class="card" style="min-width:320px">
      <div class="ch">模型提供商状态 <span class="ch-r">${(d.all_providers||[]).length}个</span></div>
      <div class="cb scroll">
        ${(d.all_providers||[]).length===0?'<div style="color:var(--text3);padding:12px">暂无提供商配置</div>':`<table class="tbl" style="min-width:300px"><thead>
          <tr><th>来源</th><th>提供商</th><th>Endpoint</th><th>认证</th><th>API</th><th class="n">模型数</th><th>密钥</th></tr>
        </thead><tbody>
          ${(d.all_providers||[]).map(p=>`
          <tr>
            <td><span class="gw-badge blue" style="font-size:9px">${esc(p.app||'?')}</span></td>
            <td><span style="color:var(--accent2);font-weight:500">${esc(p.name||'?')}</span></td>
            <td style="color:var(--text2);font-size:10px;max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(p.base_url||'—')}">${esc(p.base_url||'—')}</td>
            <td><span style="color:var(--text2);font-size:11px">${esc(p.auth||'—')}</span></td>
            <td><span style="color:var(--text2);font-size:10px">${esc(p.api||'—')}</span></td>
            <td class="n" style="color:var(--green);font-weight:500">${p.model_count||0}</td>
            <td>${p.has_key?'<span style="color:var(--green);font-size:13px">✅</span>':'<span style="color:var(--text3);font-size:13px">—</span>'}</td>
          </tr>`).join('')}
        </tbody></table>`}
      </div>
    </div>
  </div>`;

  // --- Top Processes ---
  const topc=(d.processes||{}).top_cpu||[];
  const topm=(d.processes||{}).top_mem||[];
  html+=`<div class="card">
    <div class="ch">Top 进程</div>
    <div class="row row-2" style="margin:0;gap:0">
      <div class="scroll" style="max-height:240px">
        <table class="tbl"><thead><tr><th>进程</th><th class="n">PID</th><th class="n">CPU%</th><th class="n">文件</th></tr></thead><tbody>
          ${topc.map(p=>`<tr><td>${esc(p.name||'?')}</td><td class="n" style="color:var(--text3)">${esc(p.pid)}</td><td class="n" style="color:${pct_c(p.cpu,50,80)}">${p.cpu.toFixed(1)}</td><td class="n" style="color:var(--text3)">${p.fd_count||0}</td></tr>`).join('')}
        </tbody></table>
      </div>
      <div class="scroll" style="max-height:240px;border-left:1px solid var(--border)">
        <table class="tbl"><thead><tr><th>进程</th><th class="n">PID</th><th class="n">内存MB</th></tr></thead><tbody>
          ${topm.map(p=>`<tr><td>${esc(p.name||'?')}</td><td class="n" style="color:var(--text3)">${esc(p.pid)}</td><td class="n">${p.rss_mb||0}</td><td class="n" style="color:var(--text3)">${p.fd_count||0}</td></tr>`).join('')}
        </tbody></table>
      </div>
    </div>
  </div>`;

  // --- Gateway Instances (按软件名合并) ---
  const inst_list=gw.merged||gw.instances||[];
  const raw_list=gw.instances||[];
  const idle_list=gw.idle_instances||[];
  html+=`<div class="card">
    <div class="ch">
      Gateway 实例 <span class="ch-r">${inst_list.length}运行 · ${raw_list.length}进程 · ${idle_list.length}闲置 · 健康评分
        <span style="color:${hs_c};font-weight:700">${hs}</span></span>
    </div>
    <div class="cb">
      <div class="gw-grid">
        ${inst_list.map(g=>`
        <div class="gw-card run">
          <div class="gw-item"><span class="gw-k">软件</span><span class="gw-v">${esc(g.software||'?')}</span></div>
          <div class="gw-item"><span class="gw-k">进程数</span><span class="gw-v">${g.pid_count||1}个</span></div>
          ${g.pid_count>1?`<div class="gw-item"><span class="gw-k">PID列表</span><span class="gw-v" style="color:var(--text2);font-size:11px">${(g.pids||[]).join(', ')}</span></div>`:''}
          ${g.pid_count===1?`<div class="gw-item"><span class="gw-k">PID</span><span class="gw-v" style="color:var(--text2)">${esc(g.pids?.[0]||g.pid||'')}</span></div>`:''}
          <div class="gw-item"><span class="gw-k">端口</span>
            <span class="gw-v" style="color:var(--accent)">${esc(g.port||'—')}</span>
          </div>
          <div class="gw-item"><span class="gw-k">来源</span><span class="gw-v">${esc(g.source||'?')}</span></div>
          ${g.primary_model&&g.primary_model!='—'?`<div class="gw-item"><span class="gw-k">主模型</span><span class="gw-v" style="color:var(--green)">${esc(g.primary_model)}</span></div>`:''}
          ${g.image_model&&g.image_model!='—'?`<div class="gw-item"><span class="gw-k">图像</span><span class="gw-v" style="color:var(--orange)">${esc(g.image_model)}</span></div>`:''}
          ${g.channels&&g.channels.length?`<div class="gw-item"><span class="gw-k">渠道</span><span class="gw-v">${g.channels.map(c=>`<span class="gw-badge blue">${esc(c)}</span>`).join(' ')}</span></div>`:''}
          <div class="gw-item"><span class="gw-k">CPU</span>
            <span class="gw-v" style="color:${(g.cpu_pct||0)>50?'var(--red)':(g.cpu_pct||0)>20?'var(--orange)':'var(--text)'}">${(g.cpu_pct||0).toFixed(1)}%</span>
          </div>
          <div class="gw-item"><span class="gw-k">内存</span><span class="gw-v">${esc(String(g.rss_mb||0))}MB</span></div>
          ${g.rpm||g.latency_ms?`
          <div class="gw-item"><span class="gw-k">性能</span>
            <span class="gw-v" style="color:var(--text2);font-size:10px">
              ${g.rpm?`${g.rpm} req/m`:''}${g.rpm&&g.latency_ms?' · ':''}${g.latency_ms?`${g.latency_ms}ms`:''}
            </span>
          </div>`:''}
          ${g.workspace&&g.workspace!='—'?`<div class="gw-item" style="flex:1"><span class="gw-k">工作区</span><span class="gw-v" style="color:var(--text3);font-size:10px">${esc(g.workspace)}</span></div>`:''}
        </div>`).join('')}
        ${idle_list.map(i=>`
        <div class="gw-card idle">
          <div class="gw-item"><span class="gw-k">闲置</span><span class="gw-v">${esc(i.name||'?')}</span></div>
          <div class="gw-item"><span class="gw-k">配置端口</span><span class="gw-v">${esc(String(i.port))}</span></div>
          <div class="gw-item" style="flex:1"><span class="gw-k">配置</span><span class="gw-v" style="color:var(--text3);font-size:10px">${esc(i.config_path||'').replace('/Users/','~/')}</span></div>
        </div>`).join('')}
        ${inst_list.length===0&&idle_list.length===0?'<div style="color:var(--text3);padding:12px;text-align:center">未检测到 Gateway 实例</div>':''}
      </div>
    </div>
  </div>`;

  // --- Activity + Cron ---
  const acts=(d.activity||[]);
  const today_acts=acts.filter(a=>a.group==='今天');
  const yday_acts=acts.filter(a=>a.group==='昨天');
  const earlier_acts=acts.filter(a=>a.group==='更早');
  html+=`<div class="row row-2">
    <div class="card">
      <div class="ch">最近活动 <span class="ch-r">${acts.length}条</span></div>
      <div class="cb scroll" style="max-height:280px">
        ${today_acts.length?`<div class="act-group"><div class="act-ghdr">今天</div>
          ${today_acts.map(a=>`<div class="act-item"><span class="act-time">${esc(a.time||'')}</span><span class="act-icon">${_actIcon(a.dir)}</span><span class="act-name" title="${esc(a.name)}">${esc(a.name)}</span><span style="color:var(--text3);font-size:10px">${esc(a.dir||'')}</span></div>`).join('')}
        </div>`:''}
        ${yday_acts.length?`<div class="act-group"><div class="act-ghdr">昨天</div>
          ${yday_acts.map(a=>`<div class="act-item"><span class="act-time">${esc(a.time||'')}</span><span class="act-icon">${_actIcon(a.dir)}</span><span class="act-name" title="${esc(a.name)}">${esc(a.name)}</span></div>`).join('')}
        </div>`:''}
        ${earlier_acts.length?`<div class="act-group"><div class="act-ghdr">更早</div>
          ${earlier_acts.map(a=>`<div class="act-item"><span class="act-time">${esc(a.time||'')}</span><span class="act-icon">${_actIcon(a.dir)}</span><span class="act-name" title="${esc(a.name)}">${esc(a.name)}</span></div>`).join('')}
        </div>`:''}
        ${acts.length===0?'<div style="color:var(--text3);padding:12px">暂无活动记录</div>':''}
      </div>
    </div>
    <div class="card">
      <div class="ch">定时任务 <span class="ch-r">${(d.cron||[]).length}个</span></div>
      <div class="cb scroll" style="max-height:280px">
        ${(d.cron||[]).map(j=>`
        <div style="padding:6px 0;border-bottom:1px solid var(--border);display:flex;align-items:flex-start;gap:6px;flex-wrap:wrap">
          <span style="font-size:12px;min-width:100px;font-weight:500">${esc(j.name||'?')}</span>
          ${j.source?`<span class="gw-badge" style="background:var(--blue);color:#fff;font-size:9px">${esc(j.source)}</span>`:''}
          ${j.software&&j.software!==j.source?`<span style="color:var(--text2);font-size:10px">${esc(j.software)}</span>`:''}
          <span class="cron-badge ${j.enabled===false?'cb-dis':j.status==='✅'?'cb-ok':j.status==='⚠️'?'cb-warn':'cb-off'}">${esc(j.status||'—')}</span>
          <span style="color:var(--text2);font-size:11px">${esc(j.schedule||'?')}</span>
          ${j.next_run&&j.next_run!='—'?`<span style="color:var(--green);font-size:10px">下次 ${esc(j.next_run)}</span>`:''}
          ${j.delivery_channel&&j.delivery_channel!='—'?`<span style="color:var(--accent);font-size:10px">${esc(j.delivery_channel)}</span>`:''}
          ${j.last_run&&j.last_run!='—'?`<span style="color:var(--text3);font-size:10px;margin-left:auto">${esc(j.last_run)}</span>`:''}
        </div>`).join('')}
        ${(d.cron||[]).length===0?'<div style="color:var(--text3);padding:12px">暂无定时任务</div>':''}
      </div>
    </div>
  </div>`;

  // --- Session Stats ---
  html+=`<div class="card">
    <div class="ch">会话统计 <span class="ch-r">${sess.active_sessions||0}个活跃</span></div>
    <div class="cb">
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;text-align:center">
        <div>
          <div style="font-size:24px;font-weight:700;color:var(--accent)">${sess.active_sessions||0}</div>
          <div style="font-size:11px;color:var(--text3)">活跃会话</div>
        </div>
        <div>
          <div style="font-size:24px;font-weight:700;color:var(--green)">${((sess.total_tokens||0)/1000).toFixed(0)}k</div>
          <div style="font-size:11px;color:var(--text3)">总 Token</div>
        </div>
        <div>
          <div style="font-size:24px;font-weight:700;color:var(--orange)">${((sess.today_tokens||0)/1000).toFixed(0)}k</div>
          <div style="font-size:11px;color:var(--text3)">今日 Token</div>
        </div>
      </div>
      <div style="margin-top:12px;padding-top:12px;border-top:1px solid var(--border);text-align:center">
        <span style="color:var(--text2);font-size:12px">总消息数：</span>
        <span style="color:var(--accent);font-weight:600">${sess.total_messages||0}</span>
      </div>
    </div>
  </div>`;

  // --- Config Changes & Log Stats ---
  html+=`<div class="row row-2">
    <div class="card">
      <div class="ch">配置变更 <span class="ch-r">${cfgChg.length}个变化</span></div>
      <div class="cb">
        ${cfgChg.length===0?'<div style="color:var(--green);padding:12px;text-align:center">✅ 配置无变化</div>':cfgChg.map(c=>`
        <div style="padding:6px 0;border-bottom:1px solid var(--border)">
          <div style="font-size:11px;color:var(--text2)">${esc(c.path)}</div>
          <div style="font-size:10px;color:var(--text3)">hash: ${esc(c.old)} → <span style="color:var(--accent)">${esc(c.new)}</span></div>
        </div>`).join('')}
      </div>
    </div>
    <div class="card">
      <div class="ch">日志大小 <span class="ch-r">${logs.length}个文件</span></div>
      <div class="cb scroll" style="max-height:200px">
        ${logs.length===0?'<div style="color:var(--text3);padding:12px">暂无日志文件</div>':logs.map(l=>`
        <div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid var(--border);font-size:11px">
          <span style="color:var(--text2)">${esc(l.label)}</span>
          <span style="color:var(--text3)">${l.size_mb>=1?l.size_mb.toFixed(1)+'MB':l.size_kb+'KB'}</span>
        </div>`).join('')}
      </div>
    </div>
  </div>`;

  // --- Recent Errors ---
  html+=`<div class="card">
    <div class="ch">最近错误日志 <span class="ch-r">${errs.length}条</span></div>
    <div class="cb">
      ${errs.length===0?'<div style="color:var(--green);padding:12px;text-align:center">✅ 无错误日志</div>':`
      <div class="scroll" style="max-height:240px">
        <table class="tbl">
          <thead><tr><th style="width:140px">时间</th><th style="width:80px">级别</th><th>消息</th></tr></thead>
          <tbody>
            ${errs.map(e=>`
            <tr class="${e.level==='ERROR'?'warn-bg':''}">
              <td style="color:var(--text3);font-size:11px">${esc(e.time)}</td>
              <td><span style="font-size:9px;padding:1px 5px;border-radius:3px;background:${e.level==='ERROR'?'var(--red)':'var(--orange)'};color:#fff">${esc(e.level)}</span></td>
              <td style="color:var(--text2);font-size:11px">${esc(e.msg)}</td>
            </tr>`).join('')}
          </tbody>
        </table>
      </div>`}
    </div>
  </div>`;

  // --- Data Dirs ---
  html+=`<div class="card">
    <div class="ch">数据目录 <span class="ch-r">${((d.data_dirs||[]).filter(d=>d.size&&d.size!='—').length)}个活跃</span></div>
    <div class="cb">
      <div class="dir-list">
        ${(d.data_dirs||[]).map(dd=>`
        <div class="dir-row">
          <span class="dir-label">${esc(dd.label||'?')}</span>
          <span class="dir-path">${esc(dd.path||'')}</span>
          <span class="dir-size">${esc(dd.size||'—')}</span>
          <span class="dir-st st-${esc(dd.status||'active')}">${esc(dd.status||'active')}</span>
        </div>`).join('')}
      </div>
    </div>
  </div>`;

  // --- Skills (全部展开) ---
  const skills_data=d.skills||{};
  const tool_stats=d.tool_stats||[];
  const skill_apps=Object.keys(skills_data);
  const skill_flat=[];
  skill_apps.forEach(app=>{
    Object.entries(skills_data[app]||{}).forEach(([cat,list])=>list.forEach(s=>skill_flat.push({...s,app,cat})));
  });
  const total_skills=skill_flat.length;
  html+=`<div class="card">
    <div class="ch">已安装技能 <span class="ch-r">${total_skills}个 · ${(d.tool_stats||[]).length}种工具调用</span></div>
    <div class="cb">
      <div class="skill-bar">
        <input type="text" id="skill-search" placeholder="搜索技能..." oninput="filterSkills()">
        <select id="skill-app" onchange="filterSkills()">
          <option value="">全部应用</option>
          ${skill_apps.map(a=>`<option value="${esc(a)}">${esc(a)}</option>`).join('')}
        </select>
      </div>
      ${tool_stats.length>0?`
      <div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:10px;padding:6px 0;border-bottom:1px solid var(--border)">
        <span style="font-size:10px;color:var(--text3);margin-right:4px;line-height:2">热门工具:</span>
        ${tool_stats.slice(0,10).map(([name,cnt])=>`
        <span style="font-size:10px;background:var(--card-h);padding:2px 7px;border-radius:10px;color:var(--text2)">
          ${esc(name)}<span style="color:var(--accent);margin-left:3px">${cnt}</span>
        </span>`).join('')}
      </div>`:''}
      <div id="skill-content">
        ${skill_apps.map(app=>`
        <div class="skill-cat" data-app="${esc(app)}">
          <div class="skill-cat-h">${esc(app)}</div>
          ${Object.entries(skills_data[app]||{}).map(([cat,list])=>{
            if(list.length===0) return '';
            return `<div class="skill-subcat" data-subcat="${esc(cat)}">
            <div class="skill-subcat-h">${esc(cat)} <span style="color:var(--text3);font-weight:400;font-size:10px">${list.length}个</span></div>
            <div class="skill-list">
              ${list.map(s=>`
              <div class="skill-chip" data-name="${esc(s.name||'')}" data-desc="${esc(s.desc||'')}" data-app="${esc(app)}" data-cat="${esc(cat)}" data-updated="${esc(s.updated||'')}" onclick="toggleSkill(this)" title="点击展开查看详情">
                <span class="sk-name">${esc(s.name||'?')}</span>
                ${s.version&&s.version!='1.0'?`<span class="sk-ver">v${esc(s.version)}</span>`:''}
                ${s.calls>0?`<span class="sk-calls" style="font-size:9px;color:var(--accent);margin-left:2px">${s.calls}次</span>`:''}
                <span class="sk-detail"><span class="sk-desc">${esc(s.desc||'无描述')}</span>${s.updated?`<span class="sk-ver"> 更新:${esc(s.updated)}</span>`:''}${s.last_call&&s.last_call!=='—'?`<span class="sk-ver" style="color:var(--accent)"> 最后:${esc(s.last_call)}</span>`:''}</span>
              </div>`).join('')}
            </div>
          </div>`;
          }).join('')}
        </div>`).join('')}
        ${total_skills===0?'<div style="color:var(--text3);padding:12px">暂无已安装技能</div>':''}
      </div>
    </div>
  </div>`;

  $('#app').innerHTML = html;

  // Footer
  $('#ft').innerHTML=`<span>Mac AI Monitor v${esc(d.version||'?')}</span><span>${esc(d.timestamp||'')}</span><span>macOS</span>`;

  // Remove loading
  $('#loading')?.remove();
}

function toggleSkill(el){
  el.classList.toggle('expanded');
}

function filterSkills(){
  const q=$('#skill-search').value.toLowerCase();
  const app=$('#skill-app').value;
  $$('.skill-chip').forEach(chip=>{
    const n=chip.dataset.name||'';
    const d=chip.dataset.desc||'';
    const a=chip.dataset.app||'';
    const show=(!q||n.includes(q)||d.includes(q))&&(!app||a===app);
    chip.style.display=show?'':'none';
  });
  $$('.skill-cat').forEach(cat=>{
    const visible=Array.from(cat.querySelectorAll('.skill-chip')).some(c=>c.style.display!=='none');
    cat.style.display=visible?'':'none';
  });
  $$('.skill-subcat').forEach(sc=>{
    const chips=sc.querySelectorAll('.skill-chip');
    const visible=Array.from(chips).some(c=>c.style.display!=='none');
    sc.style.display=visible?'':'none';
    const btn=sc.querySelector('.sk-showmore');
    if(!btn) return;
    // 关键修复：搜索时隐藏按钮；清空时重置所有 .sk-hidden 为隐藏状态
    if(q||app){
      btn.style.display='none';
      sc.querySelectorAll('.sk-hidden').forEach(c=>c.style.display='none');
    } else {
      btn.style.display='';
      sc.querySelectorAll('.sk-hidden').forEach(c=>c.style.display='none');
      btn.textContent='显示全部 '+chips.length+' 个';
    }
  });
}

function toggleSkillCat(btn){
  const subcat=btn.dataset.subcat;
  const list=btn.parentElement.querySelector('.skill-list');
  if(!list) return;
  const allChips=list.querySelectorAll('.skill-chip');
  const hidden=list.querySelectorAll('.sk-hidden');
  const showing=hidden.length>0&&hidden[0].style.display!=='none';
  if(showing){
    hidden.forEach(c=>c.style.display='none');
    btn.textContent='显示全部 '+allChips.length+' 个';
  }else{
    hidden.forEach(c=>c.style.display='');
    btn.textContent='收起';
  }
}

// Auto-refresh state
let _autoRefresh = true;
let _autoInterval = null;
const AUTO_INTERVAL_MS = 5000; // 5秒自动刷新

function toggleAutoRefresh(){
  _autoRefresh = !_autoRefresh;
  const btn = $('#btn-auto');
  if(_autoRefresh){
    btn.className = 'btn btn-auto';
    btn.title = '自动刷新: 开';
    btn.innerHTML = '<span class="auto-indicator"></span>⟳ 自动刷新';
    startAutoRefresh();
  }else{
    btn.className = 'btn';
    btn.title = '自动刷新: 关';
    btn.innerHTML = '⟳ 手动刷新';
    stopAutoRefresh();
  }
}

function startAutoRefresh(){
  stopAutoRefresh();
  _autoInterval = setInterval(() => silentLoad(), AUTO_INTERVAL_MS);
}

function stopAutoRefresh(){
  if(_autoInterval){ clearInterval(_autoInterval); _autoInterval = null; }
}

async function silentLoad(){
  try{
    const r = await fetch('/api/data');
    if(!r.ok) return;
    _data = await r.json();
    try{ render(_data); }catch(e){ console.error('silent render:', e); }
  }catch(e){ console.error('silent load:', e); }
}

function fullReload(){
  stopAutoRefresh();
  // 完整刷新后恢复自动刷新
  load().then(() => { if(_autoRefresh) startAutoRefresh(); });
}

// Refresh
$('#btn-refresh').onclick=()=>{ $('#app').innerHTML='<div class="loading">正在刷新</div>'; load().then(()=>{ if(_autoRefresh) startAutoRefresh(); }); };

// Load
load().then(() => { startAutoRefresh(); });
</script>

</body>
</html>"""

# ====== HTTP Handler ======
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path == '/health':
            # Health check endpoint (UptimeRobot-compatible)
            try:
                data = _cache.get('data') or {}
                gw_count = data.get('gateway', {}).get('count', 0) if isinstance(data, dict) else 0
                warming = (time.time() - _boot_ts) < 30 and not _cache.get('data')
                if warming:
                    status = 'warming'
                    code = 200  # 冷启动期间不返回非 200，避免 UptimeRobot 误报
                elif gw_count > 0:
                    status = 'ok'
                    code = 200
                else:
                    status = 'degraded'
                    code = 503
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
        elif self.path == '/api/data':
            now = time.time()
            with _cache_lock:
                cache_hit = (_cache.get('data') and now - _cache.get('ts', 0) < CACHE_TTL)
                if cache_hit:
                    data = _cache['data']
            if not cache_hit:
                try:
                    data = collect_all()
                    data['version'] = __version__
                    data['run_path'] = SCRIPT_FILE
                    data['script_path'] = SCRIPT_FILE  # 兼容旧前端引用
                    with _cache_lock:
                        _cache['data'] = data
                        _cache['ts'] = now
                except Exception as e:
                    logging.error(f'collect_all: {e}')
                    # 部分成功策略：返回上次缓存，附上本次异常信息
                    with _cache_lock:
                        if _cache.get('data'):
                            data = dict(_cache['data'])
                            data['error'] = str(e)
                            data['timestamp'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        else:
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
        elif self.path == '/api/alerts':
            # Return alert history from alerts file
            alerts = try_json(ALERT_FILE) or {}
            body = json.dumps({'alerts': alerts, 'thresholds': get_thresholds()}, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            try: self.wfile.write(body)
            except BrokenPipeError: pass
        elif self.path == '/api/gateway-log':
            # Return Gateway log (latest N lines)
            import urllib.parse
            qs = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(qs)
            lines = int(params.get('lines', [50])[0])
            grep = params.get('grep', [None])[0]
            log_content = ''
            gateways = (_cache.get('data') or {}).get('gateway', {})
            for inst in (gateways.get('instances') or []) + (gateways.get('merged') or []):
                try:
                    cpath = inst.get('config_path', '')
                    if cpath:
                        log_dir = os.path.join(os.path.dirname(cpath), '..', 'logs')
                        if os.path.isdir(log_dir):
                            for fname in sorted(os.listdir(log_dir), reverse=True):
                                if fname.endswith('.log'):
                                    with open(os.path.join(log_dir, fname), 'r', errors='replace') as lf:
                                        log_content += f'\n=== {inst.get("name","?")} / {fname} ===\n'
                                        log_content += ''.join(lf.readlines()[-lines:])
                except Exception as _exc_e: logging.debug(f"suppressed: {_exc_e}"); pass
            if not log_content:
                log_content = '暂无 Gateway 日志\n（Gateway 配置路径不可用或日志目录不存在）'
            body = json.dumps({'log': log_content, 'lines': lines, 'grep': grep}, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            try: self.wfile.write(body)
            except BrokenPipeError: pass
        elif self.path == '/api/status':
            # Standardized health endpoint (UptimeRobot-compatible)
            gw_count = 0
            hs = 100
            with _cache_lock:
                if _cache.get('data'):
                    gw = _cache['data'].get('gateway', {})
                    gw_count = gw.get('count', 0)
                    hs = gw.get('health_score', 100)
            status = 'ok' if gw_count > 0 else 'warming'
            body = json.dumps({
                'status': status,
                'version': __version__,
                'gateway_count': gw_count,
                'health_score': hs,
                'uptime_seconds': int(time.time() - (_cache.get('data') or {}).get('_boot_ts', time.time())),
            }, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            try: self.wfile.write(body)
            except BrokenPipeError: pass
        elif self.path.startswith('/api/history'):
            # 时序历史数据查询
            import urllib.parse
            qs = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(qs)
            range_h = params.get('range', ['6h'])[0]
            # 解析时间范围
            range_map = {'1h': 3600, '6h': 21600, '24h': 86400, '7d': 604800}
            seconds = range_map.get(range_h, 21600)
            cutoff = int(time.time()) - seconds
            try:
                with sqlite3.connect(TSDB_PATH) as conn:
                    rows = conn.execute(
                        'SELECT ts, cpu_pct, mem_pct, disk_pct, swap_pct, health_score, gateway_count, load_1m, net_rx_kbps, net_tx_kbps FROM samples WHERE ts >= ? ORDER BY ts ASC',
                        (cutoff,)).fetchall()
                history = {
                    'timestamps': [r[0] for r in rows],
                    'cpu': [r[1] for r in rows],
                    'mem': [r[2] for r in rows],
                    'disk': [r[3] for r in rows],
                    'swap': [r[4] for r in rows],
                    'health_score': [r[5] for r in rows],
                    'gateway_count': [r[6] for r in rows],
                    'load_1m': [r[7] for r in rows],
                    'net_rx': [r[8] for r in rows],
                    'net_tx': [r[9] for r in rows],
                }
                body = json.dumps(history, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Cache-Control', 'no-cache')
                self.end_headers()
                try: self.wfile.write(body)
                except BrokenPipeError: pass
            except Exception as e:
                logging.warning(f'/api/history error: {e}')
                body = json.dumps({'error': str(e)}).encode()
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(body)
        elif self.path.startswith('/api/tokens'):
            # Token 使用量统计
            import urllib.parse as uparse
            qs = uparse.urlparse(self.path).query
            params = uparse.parse_qs(qs)
            range_h = params.get('range', ['7d'])[0]
            range_map = {'1d': 86400, '7d': 604800, '30d': 2592000}
            seconds = range_map.get(range_h, 604800)
            now = int(time.time())
            cutoff_date = datetime.datetime.fromtimestamp(now - seconds).strftime('%Y-%m-%d')
            try:
                with sqlite3.connect(TOKEN_DB_PATH) as conn:
                    # モデル別集計
                    model_rows = conn.execute(
                        'SELECT model, provider, SUM(call_count) as calls, SUM(tokens_in) as t_in, SUM(tokens_out) as t_out, SUM(duration_ms) as dur FROM token_calls WHERE date >= ? GROUP BY model, provider ORDER BY calls DESC',
                        (cutoff_date,)).fetchall()
                    # 日別集計
                    daily_rows = conn.execute(
                        'SELECT date, SUM(call_count) as calls, SUM(tokens_in) as t_in, SUM(tokens_out) as t_out FROM token_calls WHERE date >= ? GROUP BY date ORDER BY date ASC',
                        (cutoff_date,)).fetchall()
                models = []
                total_calls = 0
                total_tokens = 0
                for r in model_rows:
                    cost = _estimate_cost(r[1] or (r[0] or ''), r[3] or 0, r[4] or 0)
                    models.append({
                        'model': r[0] or 'unknown',
                        'provider': r[1] or 'unknown',
                        'calls': r[2] or 0,
                        'tokens_in': r[3] or 0,
                        'tokens_out': r[4] or 0,
                        'duration_ms': r[5] or 0,
                        'cost_est': cost
                    })
                    total_calls += (r[2] or 0)
                    total_tokens += (r[3] or 0) + (r[4] or 0)
                daily = [{'date': r[0], 'calls': r[1], 'tokens_in': r[2], 'tokens_out': r[3]} for r in daily_rows]
                total_cost = sum(m['cost_est'] for m in models)
                result = {
                    'range': range_h,
                    'total_calls': total_calls,
                    'total_tokens': total_tokens,
                    'total_cost_est': round(total_cost, 4),
                    'models': models,
                    'daily': daily
                }
                body = json.dumps(result, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Cache-Control', 'no-cache')
                self.end_headers()
                try: self.wfile.write(body)
                except BrokenPipeError: pass
            except Exception as e:
                logging.warning(f'/api/tokens error: {e}')
                body = json.dumps({'error': str(e)}).encode()
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(body)
        elif self.path.startswith('/api/cron-runs'):
            # Cron 运行历史
            import urllib.parse as uparse
            qs = uparse.urlparse(self.path).query
            params = uparse.parse_qs(qs)
            job_id = params.get('jobId', [None])[0]
            limit = min(int(params.get('limit', ['20'])[0]), 100)
            runs_dir = os.path.join(HOME,'.qclaw/cron/runs')
            runs = []
            if job_id and os.path.isdir(runs_dir):
                run_file = os.path.join(runs_dir, f"{job_id}.jsonl")
                if os.path.isfile(run_file):
                    try:
                        with open(run_file) as f:
                            lines = [ln.strip() for ln in f if ln.strip()]
                        # parse last N lines (newest = last line)
                        parse_limit = min(limit, len(lines))
                        for ln in lines[-parse_limit:]:
                            try:
                                rec = json.loads(ln)
                                dur = rec.get('durationMs', 0)
                                dur_s = round(dur / 1000, 1) if dur else 0
                                runs.append({
                                    'ts': rec.get('ts'),
                                    'status': rec.get('status', 'unknown'),
                                    'duration_s': dur_s,
                                    'summary': (rec.get('summary') or '')[:200],
                                    'error': (rec.get('error') or '')[:200],
                                    'model': rec.get('model', ''),
                                })
                            except Exception:
                                pass
                    except Exception as _exc_cr: logging.debug(f"cron-runs: {_exc_cr}")
            else:
                # no jobId: return summary of all jobs
                pass
            body = json.dumps({'runs': runs, 'total': len(runs)}, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            try: self.wfile.write(body)
            except BrokenPipeError: pass
        elif self.path.startswith('/api/export'):
            # Export current snapshot as JSON or CSV
            fmt = 'json'
            if 'csv' in self.path.lower(): fmt = 'csv'
            data = _cache.get('data') or collect_all()
            if fmt == 'csv':
                import io, csv as csv_mod
                buf = io.StringIO()
                w = csv_mod.writer(buf)
                # Flatten key metrics
                w.writerow(['metric','value','unit'])
                def row(k,v,u=''): w.writerow([k,v,u])
                cpu = data.get('cpu',{})
                mem = data.get('mem',{})
                dsk = data.get('disk',{})
                swp = data.get('swap',{})
                bat = data.get('battery',{})
                row('CPU Used', cpu.get('used_pct'), '%')
                row('Memory Used', mem.get('used_pct'), '%')
                row('Memory Used GB', mem.get('used_gb'), 'GB')
                row('Disk Used', dsk.get('used_pct'), '%')
                row('Disk Size GB', dsk.get('size_gb'), 'GB')
                row('Swap Used', swp.get('used_pct'), '%')
                row('Battery', bat.get('percent', -1), '%')
                row('Battery Charging', bat.get('charging', False), '')
                gw = data.get('gateway',{})
                row('Gateway Count', gw.get('count', 0), '')
                row('Health Score', gw.get('health_score', 0), '')
                # Top processes
                for i,p in enumerate((data.get('processes') or {}).get('top_cpu',[])[:5]):
                    row(f'TopCPU_{i+1}', f"{p.get('name','?')} ({p.get('cpu',0):.1f}%)", '')
                for i,p in enumerate((data.get('processes') or {}).get('top_mem',[])[:5]):
                    row(f'TopMem_{i+1}', f"{p.get('name','?')} ({p.get('rss_mb',0)}MB)", '')
                body = buf.getvalue().encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'text/csv; charset=utf-8')
                self.send_header('Content-Disposition', 'attachment; filename=monitor_export.csv')
            else:
                body = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Disposition', 'attachment; filename=monitor_export.json')
            self.send_header('Content-Length', len(body))
            self.end_headers()
            try: self.wfile.write(body)
            except BrokenPipeError: pass
        elif self.path.startswith('/api/process/'):
            # Detail for a specific PID
            try:
                pid = int(self.path.split('/')[-1])
                if pid < 1 or pid > 999999:
                    self.send_response(400); self.end_headers(); return
            except ValueError:
                self.send_response(400); self.end_headers(); return
            detail = {'pid': pid, 'found': False}
            try:
                import subprocess as sp
                out = sp.check_output(['ps','-p',str(pid),'-o','pid,pcpu,pmem,rss,vsz,nice,etime,command'], timeout=3).decode()
                lines = out.strip().split('\n')
                if len(lines) >= 2:
                    parts = lines[1].split(None, 7)
                    if len(parts) >= 8:
                        detail = {'pid':pid,'found':True,
                            'cpu_pct':float(parts[1]),'mem_pct':float(parts[2]),
                            'rss_mb':int(parts[3])//1024,'vsz_mb':int(parts[4])//1024,
                            'nice':parts[5],'elapsed':parts[6],'command':parts[7]}
                # Open files count (macOS: lsof)
                try:
                    fd_out = sp.check_output(['lsof','-p',str(pid)], timeout=3).decode()
                    detail['open_files'] = max(0, len(fd_out.strip().split('\n')) - 1)
                except Exception: detail['open_files'] = None
                # Network connections
                try:
                    net_out = sp.check_output(['lsof','-i','-P','-n','-p',str(pid)], timeout=3).decode()
                    conns = [l.strip() for l in net_out.strip().split('\n')[1:] if l.strip()]
                    detail['connections'] = conns[:20]
                except Exception: detail['connections'] = []
            except Exception as e:
                detail['error'] = str(e)
            body = json.dumps(detail, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            try: self.wfile.write(body)
            except BrokenPipeError: pass
        elif self.path == '/' or self.path == '/index.html':
            # Serve index.html (references external CSS/JS)
            static_dir = os.path.dirname(os.path.abspath(__file__))
            index_path = os.path.join(static_dir, 'index.html')
            if os.path.isfile(index_path):
                with open(index_path, 'r', encoding='utf-8') as f:
                    body = f.read().encode('utf-8')
            else:
                body = HTML_PAGE.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', len(body))
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                pass
        elif self.path.startswith('/static/'):
            # Serve static files (path traversal protection)
            static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
            file_path = os.path.normpath(os.path.join(static_dir, self.path[len('/static/'):]))
            if not file_path.startswith(static_dir + os.sep):
                self.send_response(403); self.end_headers(); return
            if os.path.isfile(file_path):
                # Determine Content-Type
                ext = os.path.splitext(file_path)[1].lower()
                content_type = 'application/octet-stream'
                if ext == '.css':
                    content_type = 'text/css; charset=utf-8'
                elif ext == '.js':
                    content_type = 'application/javascript; charset=utf-8'
                elif ext == '.html':
                    content_type = 'text/html; charset=utf-8'
                elif ext == '.json':
                    content_type = 'application/json'
                elif ext in ['.png', '.jpg', '.jpeg', '.gif', '.ico']:
                    content_type = f'image/{ext[1:]}'
                try:
                    with open(file_path, 'rb') as f:
                        body = f.read()
                    self.send_response(200)
                    self.send_header('Content-Type', content_type)
                    self.send_header('Content-Length', len(body))
                    self.end_headers()
                    self.wfile.write(body)
                except Exception as e:
                    self.send_response(500)
                    self.end_headers()
            else:
                self.send_response(404)
                self.end_headers()
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
        global _gpu_cache_store
        _now = time.time()
        try:
            import subprocess
            r = subprocess.run(['system_profiler','SPDisplaysDataType'],capture_output=True,text=True,timeout=20)
            if r.returncode==0:
                gpus = []
                # 简单解析 GPU 信息
                for line in r.stdout.split('\n'):
                    if 'Chip' in line or 'VRAM' in line:
                        if line.strip():
                            gpus.append({'name':line.strip()[:40],'type':''})
                if gpus:
                    with _gpu_lock:
                        _gpu_cache_store['gpus'] = gpus
                        _gpu_cache_store['ts'] = _now
        except Exception as e:
            logging.warning(f'GPU prewarm: {e}')
    threading.Thread(target=_prewarm, daemon=True).start()
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    print(f'http://127.0.0.1:{PORT}/')
    print(f'Log: {LOG_FILE}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()
    print('Stopped.')
