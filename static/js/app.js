// Mac AI Monitor - Frontend Logic (extracted from index.html)
// Auto-generated - do not edit manually


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

function fmt_pct(n){ return (n==null?0:n).toFixed(1)+'%'; }
function fmt_gb(n){ return (n==null?0:n).toFixed(1)+'GB'; }
function fmt_mb(n){ return (n==null?0:n).toFixed(1)+'MB'; }
// Parse human size like '14Gi','233G','500M' to GB
function parse_to_gb(s){
  if(!s||typeof s!=='string') return null;
  s=s.trim();
  const m=s.match(/^([\d.]+)\s*(Gi?|Mi?|Ki?|Ti?|B)?$/i);
  if(!m) return null;
  const v=parseFloat(m[1]), u=(m[2]||'B').toLowerCase();
  if(u==='t'||u==='ti') return v*1024;
  if(u==='g'||u==='gi') return v;
  if(u==='m'||u==='mi') return v/1024;
  if(u==='k'||u==='ki') return v/1024/1024;
  return null;
}
function fmt_size(s){
  const gb=parse_to_gb(s);
  return gb!=null? gb.toFixed(1)+'GB' : (s||'—');
}
function fmt_net(kbps){
  if(kbps==null) return '0';
  if(kbps>=1024) return (kbps/1024).toFixed(1)+'M';
  return kbps.toFixed(0)+'K';
}
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

// ====== uPlot Historical Chart ======
let _histRange = '6h';
let _histChart = null;

function renderHistChart(d) {
  // 返回图表容器 HTML（异步获取数据）
  return '<div class="hist-panel"><div class="hist-card">' +
    '<div class="hist-title">📈 历史趋势</div>' +
    '<div class="hist-range">' +
      ['1h','6h','24h','7d'].map(function(r) {
        return '<button class="hist-rbtn' + (_histRange===r?' active':'') + '" data-range="'+r+'">'+r+'</button>';
      }).join('') +
    '</div>' +
    '<div id="hist-chart" style="width:100%;height:280px"></div>' +
  '</div></div>';
}

async function initHistChart() {
  if (!window.uPlot) return;
  var url = '/api/history?range=' + _histRange;
  try {
    var r = await fetch(url);
    if (!r.ok) return;
    var h = await r.json();
    if (!h.timestamps || h.timestamps.length < 2) {
      var el = document.getElementById('hist-chart');
      if (el) el.innerHTML = '<div style="color:var(--text3);font-size:12px;text-align:center;padding:40px">⏳ 收集数据中...需要 1-2 分钟建立历史</div>';
      return;
    }
    // 用时间戳的偏移(相对于第一个)作为 x 轴
    var t0 = h.timestamps[0];
    var x = h.timestamps.map(function(t) { return t - t0; });
    var opts = {
      width: document.getElementById('hist-chart')?.offsetWidth || 600, height: 280,
      cursor: { show: true, drag: { x: true, y: false } },
      legend: { show: true },
      scales: { x: { time: false }, y: { auto: true, range: function(min,max) { return [0, Math.min(100, max+10)]; } } },
      series: [
        {},
        { label: 'CPU %', stroke: 'var(--accent)', width: 2, fill: 'rgba(0,207,255,0.08)' },
        { label: '内存 %', stroke: 'var(--green)', width: 2, fill: 'rgba(61,214,140,0.08)' },
        { label: '磁盘 %', stroke: 'var(--orange)', width: 2, fill: 'rgba(255,179,71,0.08)' },
        { label: 'Swap %', stroke: '#ff6b9d', width: 2, fill: 'rgba(255,107,157,0.08)' },
        { label: '健康分', stroke: '#c084fc', width: 1.5, scale: 'y2' }
      ],
      axes: [
        { stroke: 'var(--text3)', grid: { stroke: 'var(--border)', width: 1 },
          values: function(u, vals, space) { return vals.map(function(v) { return fmtDuration(v); }); } },
        { stroke: 'var(--text3)', grid: { stroke: 'var(--border)', width: 1 },
          values: function(u, v) { return v.toFixed(0) + '%'; } },
        { stroke: '#c084fc', grid: { show: false }, side: 1, values: function(u,v) { return v.toFixed(0); } }
      ]
    };
    var data = [x, h.cpu, h.mem, h.disk, h.swap, h.health_score];
    if (_histChart) _histChart.destroy();
    _histChart = new uPlot(opts, data, document.getElementById('hist-chart'));
  } catch (e) { console.error('histChart:', e); }
}

function fmtDuration(sec) {
  if (sec < 60) return sec + 's';
  if (sec < 3600) return (sec/60).toFixed(0) + 'm';
  if (sec < 86400) return (sec/3600).toFixed(1) + 'h';
  return (sec/86400).toFixed(1) + 'd';
}

// click handlers for range buttons (delegated)
function _setupHistRangeBtns() {
  var btns = document.querySelectorAll('.hist-rbtn');
  btns.forEach(function(btn) {
    btn.onclick = function() {
      _histRange = btn.dataset.range;
      btns.forEach(function(b) { b.classList.remove('active'); });
      btn.classList.add('active');
      initHistChart();
    };
  });
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
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 15000);
    const r=await fetch('/api/data', {signal: controller.signal});
    clearTimeout(timeoutId);
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

// ---- 告警建议 ----
function getAdvice(key) {
  const map = {
    cpu_high: '检查高CPU进程，考虑关闭资源密集型应用或优化代码',
    mem_high: '关闭不需要的应用释放内存，检查是否有内存泄漏',
    disk_high: '清理磁盘空间，删除缓存/日志/不需要的大文件',
    swap_high: '增加物理内存或减少内存占用，Swap频繁会拖慢系统',
    bat_low: '请连接电源适配器充电',
    volume_high: '检查存储卷使用情况，清理不需要的文件',
    gw_offline: 'Gateway服务未运行，请检查服务状态并重启'
  };
  return map[key] || '请检查系统状态';
}

// ---- 告警历史 ----
async function showAlerts() {
  let detEl = $('#alert-modal');
  if (!detEl) {
    detEl = document.createElement('div');
    detEl.id = 'alert-modal';
    detEl.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;z-index:9999;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center';
    document.body.appendChild(detEl);
  }
  detEl.style.display = 'flex';
  detEl.innerHTML = '<div style="background:var(--card);border-radius:12px;padding:16px;max-width:420px;width:90%;max-height:80vh;overflow-y:auto"><h3 style="margin:0 0 12px">🔔 告警历史</h3><div style="color:var(--text3)">加载中...</div></div>';
  try {
    const r = await fetch('/api/alerts');
    const j = await r.json();
    const alerts = j.alerts || {};
    const keys = Object.keys(alerts);
    const inner = detEl.querySelector('div');
    if (!keys.length) {
      inner.innerHTML = '<h3 style="margin:0 0 12px">🔔 告警历史</h3><div style="color:var(--green)">✓ 无历史告警</div>';
    } else {
      const thresholds = j.thresholds || {};
      inner.innerHTML = '<h3 style="margin:0 0 12px">🔔 告警历史</h3>' + keys.map(k => {
        const a = alerts[k];
        const t = a.last_sent ? new Date(a.last_sent*1000).toLocaleString('zh-CN') : '—';
        const age = a.last_sent ? Math.round((Date.now()/1000 - a.last_sent)/60) : null;
        const ageStr = age !== null ? (age < 60 ? age + '分钟前' : Math.round(age/60) + '小时前') : '';
        // Map alert key to threshold category
        const catMap = {cpu_high:'cpu',mem_high:'mem',disk_high:'disk',swap_high:'swap',bat_low:'bat',volume_high:'volume',gw_offline:null};
        const cat = catMap[k];
        const th = cat && thresholds[cat] ? thresholds[cat] : null;
        const thStr = th ? Object.entries(th).map(([l,v])=>l+': '+v).join(' / ') : '—';
        const level = a.message && a.message.includes('98') ? 'danger' : 'warn';
        const levelColor = level === 'danger' ? 'var(--red)' : 'var(--orange)';
        const detailId = 'alert-det-' + k;
        return '<div class="alert-row" style="padding:8px;border-bottom:1px solid var(--border);cursor:pointer;transition:background .15s" onmouseenter="this.style.background=\'var(--card-h)\'" onmouseleave="this.style.background=\'transparent\'" onclick="var d=document.getElementById(\''+detailId+'\');d.style.display=d.style.display===\'none\'?\'block\':\'none\'">' +
          '<div style="display:flex;justify-content:space-between;align-items:center"><div style="font-weight:600;color:'+levelColor+'">⚠ ' + esc(k.replace(/_/g,' ')) + '</div><div style="font-size:10px;color:var(--text3)">'+ageStr+' ▾</div></div>' +
          '<div style="font-size:11px;color:var(--text2);margin-top:2px">' + esc(a.message||'') + '</div>' +
          '<div id="'+detailId+'" style="display:none;margin-top:8px;padding:8px;background:var(--bg);border-radius:8px;font-size:11px">' +
            '<div style="margin-bottom:4px"><b>告警时间</b> '+t+'</div>' +
            (th ? '<div style="margin-bottom:4px"><b>阈值配置</b> '+esc(thStr)+'</div>' : '') +
            '<div style="margin-bottom:4px"><b>告警级别</b> <span style="color:'+levelColor+'">'+level.toUpperCase()+'</span></div>' +
            '<div><b>建议</b> '+esc(getAdvice(k))+'</div>' +
          '</div>' +
        '</div>';
      }).join('');
    }
    inner.innerHTML += '<button class="btn" style="margin-top:12px;width:100%" onclick="document.getElementById(\'alert-modal\').style.display=\'none\'">关闭</button>';
  } catch(e) {
    detEl.querySelector('div').innerHTML = '<h3>🔔 告警历史</h3><span style="color:var(--red)">加载失败</span><br><button class="btn" style="margin-top:8px" onclick="document.getElementById(\'alert-modal\').style.display=\'none\'">关闭</button>';
  }
}

// ---- 数据导出 ----
function exportData() {
  const fmt = confirm('确定导出？\n\n确定 = JSON 格式\n取消 = CSV 格式') ? 'json' : 'csv';
  window.open('/api/export/' + fmt, '_blank');
}

function render(d){
  // Quick Bar toggle state (persist in sessionStorage)
  const qbar=document.querySelector('.qbar');
  if(qbar){
    const isCollapsed=sessionStorage.getItem('qbar-collapsed')==='1';
    qbar.classList.toggle('collapsed',isCollapsed);
    // Ensure toggle button exists
    let btn=document.getElementById('qbar-toggle');
    if(!btn){
      btn=document.createElement('button');
      btn.id='qbar-toggle';
      btn.className='qbar-toggle';
      btn.textContent='⇩';
      btn.onclick=()=>{
        qbar.classList.toggle('collapsed');
        const c= qbar.classList.contains('collapsed');
        sessionStorage.setItem('qbar-collapsed',c?'1':'0');
        btn.textContent=c?'⇪':'⇩';
      };
      qbar.parentNode.insertBefore(btn,qbar.nextSibling);
    }
    btn.textContent=isCollapsed?'⇪':'⇩';
  }

  // Historical chart placeholder
  const main=document.getElementById('app');
  if(main){
    const gauges=document.querySelector('.gauge-row');
    if(gauges&&!document.getElementById('hist-chart-insert')){
      const insert=document.createElement('div');
      insert.id='hist-chart-insert';
      insert.className='hist-panel';
      insert.innerHTML='<div class="hist-card"><div class="hist-title">📈 历史趋势</div><div id="hist-chart" style="width:100%;height:250px"></div></div>';
      main.insertBefore(insert,gauges);
    }
  }

  // Header
  document.title = 'Mac AI Monitor v'+(d.version||'?');
  $('#ts').textContent = d.timestamp||'—';
  $('#ver').textContent = 'v'+(d.version||'?');

  // P0-3: Preserve scroll position & user state across refreshes
  const scrollTop = window.scrollY;
  const savedActFilter = window._actFilter || 'all';
  const savedSkillSearch = ($('#skill-search')||{}).value || '';
  const savedSkillApp = ($('#skill-app')||{}).value || '';
  const expandedSkills = new Set();
  $$('.skill-chip.expanded').forEach(el => expandedSkills.add(el.dataset.name));
  const expandedActItems = new Set();
  $$('.act-expanded').forEach(el => expandedActItems.add(el.querySelector('.act-name')?.textContent));

  // Build app
  let html='';

  // --- Quick Bar ---
  const sys=d.system||{};
  const uptime=sys.uptime_str||'?';
  const cpu=d.cpu||{};
  const mem=d.mem||{};
  const disk=d.disk||{};
  const swp=d.swap||{};
  const gw=d.gateway||{};
  const net=d.net||{};
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
      <span class="qi-val" style="color:${pct_c(cpu_p,THc.warn||60,THc.danger||80)}">${fmt_pct(cpu_p)}</span>
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
      <span class="qi-sub">${fmt_size(disk.used)} / ${fmt_size(disk.size)}</span>
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
      <span class="qi-val">${fmt_net(net.rx_kbps)}↓ ${fmt_net(net.tx_kbps)}↑</span>
      <span class="qi-sub">RX/TX</span>
    </div>
    <div class="qi">
      <span class="qi-label">磁盘IO</span>
      <span class="qi-val">${fmt_mb(diskio.read_mbps)}↓ ${fmt_mb(diskio.write_mbps)}↑</span>
      <span class="qi-sub">${(diskio.iops||0).toFixed(0)} IOPS</span>
    </div>
    <div class="qi ${bat_p>=0&&bat_p<(THb.low||20)?'danger':bat_p<(THb.warn||50)?'warn':'ok'}">
      <span class="qi-label">电池</span>
      <span class="qi-val">${bat_p>=0?bat_p+'%':'—'}</span>
      <span class="qi-sub">${esc(bat.status_cn||'—')}</span>
    </div>
    <div class="qi">
      <span class="qi-label">会话</span>
      <span class="qi-val">${sess.active_sessions||0}</span>
      <span class="qi-sub">${sess.total_messages||0} 消息</span>
    </div>
    <div class="qi">
      <span class="qi-label">运行时间</span>
      <span class="qi-val">${uptime.split(' ')[0]||uptime}</span>
      <span class="qi-sub">${uptime}</span>
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
      <div class="g-svg">${donut(100-(mem.avail_pct||0),90,7,pct_c(mem.used_pct,THm.warn||70,THm.danger||85))}</div>
      <div class="g-label">内存使用</div>
      <div class="g-val" style="color:${pct_c(mem.used_pct,70,85)}">${fmt_pct(mem.used_pct)}</div>
      <div class="g-sub">可用 ${fmt_gb(mem.available_gb)} / ${fmt_gb(mem.total_gb)}</div>
    </div>
    <div class="gauge-card">
      ${sparkline(disk_hist, 220, 32, pct_c(disk_p,THd.warn||70,THd.danger||85))}
      <div class="g-svg">${donut(disk_p,90,7,pct_c(disk_p,THd.warn||70,THd.danger||85))}</div>
      <div class="g-label">磁盘使用</div>
      <div class="g-val" style="color:${pct_c(disk_p,THd.warn||70,THd.danger||85)}">${disk_p}%</div>
      <div class="g-sub">可用 ${fmt_size(disk.available||'—')}</div>
    </div>
  </div>`;

  // --- Health Score Breakdown (uses actual thresholds) ---
  const hs=gw.health_score||0;
  const th = d.thresholds || {};
  const th_cpu = th.cpu?.health || 80;
  const th_mem = th.mem?.health || 40;
  const th_disk = th.disk?.health || 85;
  const th_swap = th.swap?.health || 50;
  const load1=cpu.load_1m||0;
  const cores=cpu.cores||sys.cpu_p||1;
  const load_ratio=cores>0?(load1/cores).toFixed(2):'—';
  const hs_c=hs>=80?'var(--green)':hs>=50?'var(--orange)':'var(--red)';
  const diag = [];
  const pushDiag = (reason, pts) => diag.push({reason, pts});
  if(!gw.count) pushDiag('无 Gateway 实例运行', -35);
  if(gw.idle_count) pushDiag('闲置 Gateway('+gw.idle_count+'个)', -10);
  if(cpu_p > th_cpu) pushDiag('CPU '+cpu_p+'% > '+th_cpu+'%', -10);
  if(mem_p > th_mem) pushDiag('内存 '+mem_p+'% > '+th_mem+'%', -15);
  if(disk_p > th_disk) pushDiag('磁盘 '+disk_p+'% > '+th_disk+'%', -10);
  if(swp_p > th_swap) pushDiag('Swap '+swp_p+'% > '+th_swap+'%', -10);
  if(cores>0&&load1/cores>2) pushDiag('负载 '+load_ratio+' > 2.0', -10);
  const diagHtml = diag.length ? diag.map(p =>
    '<div class="diag-item"><span class="diag-reason">⚠ '+esc(p.reason)+'</span><span class="diag-pts">'+p.pts+'</span></div>'
  ).join('') : '<div class="diag-item" style="color:var(--green)">✓ 所有指标正常</div>';
  const gw_run=gw.count||0; const gw_idle=gw.idle_count||0;
  const cron_list=d.cron||[]; const cron_active=cron_list.filter(c=>c.enabled!==false).length;
  html+='<div class="hbar"><div class="card hb-card diag-trigger" onclick="document.getElementById(\'diag-panel\').classList.toggle(\'open\');this.querySelector(\'.diag-arrow\').classList.toggle(\'rotated\')">' +
      '<div class="hb-num" style="color:'+hs_c+'">'+hs+'<span class="diag-arrow">▼</span></div>' +
      '<div><div class="hb-label">健康评分</div><div class="hb-sub">'+(hs>=80?'状态良好':hs>=50?'需要关注':'异常')+'</div></div>' +
    '</div>';
  html+='<div class="card hb-card">' +
      '<div class="hb-num" style="color:'+(gw_run>0?'var(--green)':'var(--red)')+'">'+gw_run+'</div>' +
      '<div><div class="hb-label">Gateway</div><div class="hb-sub">'+gw_run+'运行'+(gw_idle?' · '+gw_idle+'闲置':'')+'</div></div>' +
    '</div>' +
    '<div class="card hb-card">' +
      '<div class="hb-num" style="color:var(--accent)">'+cron_active+'</div>' +
      '<div><div class="hb-label">定时任务</div><div class="hb-sub">'+cron_list.length+'总'+(cron_active?' · '+cron_active+'活跃':'')+'</div></div>' +
    '</div>' +
    '<div class="card hb-card">' +
      '<div class="hb-num" style="color:'+(alert_n===0?'var(--green)':'var(--orange)')+'">'+alert_n+'</div>' +
      '<div><div class="hb-label">活跃告警</div><div class="hb-sub">'+(alert_n===0?'一切正常':alert_n+'项需关注')+'</div></div>' +
    '</div>' +
  '</div>' +
  '<div id="diag-panel" class="diag-panel">' +
    '<div class="diag-title">🔍 健康诊断</div>' +
    diagHtml +
  '</div>';
  const gpu_list=sys.gpu||[];
  html+=`<div class="row row-3">
    <div class="card">
      <div class="ch">系统信息</div>
      <div class="cb">
        <table class="tbl"><tbody>
          <tr><td class="k">主机名</td><td class="v">${esc(sys.hostname||'?')}</td></tr>
          <tr><td class="k">CPU</td><td class="v">${esc(sys.cpu_name||'?')} <span style="color:var(--text3)">${cpu.cores||sys.cpu_p||1}核</span>${cpu.temp!=null?` <span style="color:${cpu.temp>80?'var(--red)':cpu.temp>60?'var(--orange)':'var(--green)'}">${cpu.temp}°C</span>`:''}</td></tr>
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
          <tr><td class="k">下载</td><td class="v">${fmt_net(net.rx_kbps)}</td></tr>
          <tr><td class="k">上传</td><td class="v">${fmt_net(net.tx_kbps)}</td></tr>
          <tr><td class="k">已连接</td><td class="v">${net.connections_est||0}</td></tr>
          <tr><td class="k">监听端口</td><td class="v">${net.connections_listen||0}</td></tr>
          <tr><td class="k">等待</td><td class="v">${net.connections_timewait||0}</td></tr>
        </tbody></table>
        <div style="margin-top:12px">
          <div style="display:flex;gap:12px;margin-bottom:4px;font-size:10px">
            <span style="color:var(--green)">● RX</span>
            <span style="color:var(--accent)">● TX</span>
          </div>
          ${sparkline((d.network_history||[]).map(n=>n.rx||0),180,28,'var(--green)')}
          ${sparkline((d.network_history||[]).map(n=>n.tx||0),180,28,'var(--accent)')}
        </div>
        <div style="display:flex;align-items:center;gap:6px;margin-top:12px;padding-top:8px;border-top:1px solid var(--border)">
          <span style="color:var(--text2);font-size:11px">磁盘IO:</span>
          <span style="color:var(--green);font-weight:600;font-size:12px">R ${fmt_mb(diskio.read_mbps)}</span>
          <span style="color:var(--accent);font-weight:600;font-size:12px">W ${fmt_mb(diskio.write_mbps)}</span>
          <span style="color:var(--text3);font-size:10px">${(diskio.iops||0).toFixed(0)} IOPS</span>
        </div>
      </div>
    </div>
    <div class="card">
      <div class="ch">存储卷 <span class="ch-r">${fmt_size(disk.used)} / ${fmt_size(disk.size)}</span></div>
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
          <span style="color:${v.pct&&parseInt(v.pct)>=90?'var(--red)':'var(--text2)'};margin-left:8px;white-space:nowrap">${esc(v.pct||'')} ${fmt_size(v.used)} / ${fmt_size(v.size)}</span>
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
    <div class="card card-collapsible" id="panel-providers" style="min-width:320px">
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
  html+=`<div class="card card-collapsible" id="panel-processes">
    <div class="ch">Top 进程 <span class=\"ch-r\">点击行查看详情</span></div>
    <div class="row row-2" style="margin:0;gap:0">
      <div class="scroll" style="max-height:240px">
        <table class="tbl"><thead><tr><th>进程</th><th class="n">PID</th><th class="n">CPU%</th><th class="n">文件</th></tr></thead><tbody>
          ${topc.map(p=>`<tr class=\"clickable-row\" data-pid=\"${p.pid}\" style=\"cursor:pointer\" title=\"点击查看PID ${p.pid}详情\"><td>${esc(p.name||'?')}</td><td class="n" style="color:var(--text3)">${esc(p.pid)}</td><td class="n" style="color:${pct_c(p.cpu,50,80)}">${p.cpu.toFixed(1)}</td><td class="n" style="color:var(--text3)">${p.fd_count||0}</td></tr>`).join('')}
        </tbody></table>
      </div>
      <div class="scroll" style="max-height:240px;border-left:1px solid var(--border)">
        <table class="tbl"><thead><tr><th>进程</th><th class="n">PID</th><th class="n">内存MB</th><th class="n">文件</th></tr></thead><tbody>
          ${topm.map(p=>`<tr class=\"clickable-row\" data-pid=\"${p.pid}\" style=\"cursor:pointer\" title=\"点击查看PID ${p.pid}详情\"><td>${esc(p.name||'?')}</td><td class="n" style="color:var(--text3)">${esc(p.pid)}</td><td class="n">${p.rss_mb||0}MB</td><td class="n" style="color:var(--text3)">${p.fd_count||0}</td></tr>`).join('')}
        </tbody></table>
      </div>
    </div>
    <div id="proc-detail" style="display:none;padding:8px;border-top:1px solid var(--border);font-size:11px"></div>
  </div>`;

  // --- Gateway Instances (按软件名合并) ---
  const inst_list=gw.merged||gw.instances||[];
  const raw_list=gw.instances||[];
  const idle_list=gw.idle_instances||[];
  html+=`<div class="card card-collapsible" id="panel-gateway">
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
          <div class="gw-item"><button onclick="window.fetchGatewayLog()" style="padding:4px 12px;background:var(--accent);color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:12px">📋 查看日志</button></div>
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
  const _actFilter = window._actFilter || 'all';
  const f_acts = _actFilter === 'all' ? acts : acts.filter(a => a.dir === _actFilter);
  const today_acts=f_acts.filter(a=>a.group==='今天');
  const yday_acts=f_acts.filter(a=>a.group==='昨天');
  const earlier_acts=f_acts.filter(a=>a.group==='更早');
  // Collect unique dirs for filter
  const act_dirs = [...new Set(acts.map(a=>a.dir))];
  html+=`<div class="row row-2">
    <div class="card card-collapsible" id="panel-activity">
      <div class="ch">最近活动 <select id="act-app" onchange="filterActivity()" style="margin-left:8px;padding:1px 6px;font-size:10px;border:1px solid var(--border);border-radius:4px;background:var(--card);color:var(--text)"><option value="all">全部</option>${act_dirs.map(d=>`<option value="${d}"${d===_actFilter?' selected':''}>${d}</option>`).join('')}</select><span class="ch-r">${f_acts.length}条</span></div>
      <div id="act-list" class="cb scroll" style="max-height:280px">
        ${today_acts.length?`<div class="act-group"><div class="act-ghdr">今天</div>
          ${today_acts.map(a=>`<div class="act-item" data-dir="${esc(a.dir||'')}" onclick="this.classList.toggle('act-expanded')"><span class="act-time">${esc(a.time||'')}</span><span class="act-icon">${_actIcon(a.dir)}</span><span class="act-name" title="${esc(a.name)}">${esc(a.name)}</span></div>${a.summary?`<div class="act-summary">${esc(a.summary)}</div>`:''}${a.detail&&a.detail.length?`<div class="act-detail">${a.detail.map(m=>`<div class="act-msg act-${esc(m.role||'')}"><b>${esc(m.role==='user'?'用户':'AI')}</b> ${esc(m.content)}</div>`).join('')}</div>`:''}`).join('')}
        </div>`:''}
        ${yday_acts.length?`<div class="act-group"><div class="act-ghdr">昨天</div>
          ${yday_acts.map(a=>`<div class="act-item" data-dir="${esc(a.dir||'')}" onclick="this.classList.toggle('act-expanded')"><span class="act-time">${esc(a.time||'')}</span><span class="act-icon">${_actIcon(a.dir)}</span><span class="act-name" title="${esc(a.name)}">${esc(a.name)}</span></div>${a.summary?`<div class="act-summary">${esc(a.summary)}</div>`:''}${a.detail&&a.detail.length?`<div class="act-detail">${a.detail.map(m=>`<div class="act-msg act-${esc(m.role||'')}"><b>${esc(m.role==='user'?'用户':'AI')}</b> ${esc(m.content)}</div>`).join('')}</div>`:''}`).join('')}
        </div>`:''}
        ${earlier_acts.length?`<div class="act-group"><div class="act-ghdr">更早</div>
          ${earlier_acts.map(a=>`<div class="act-item" data-dir="${esc(a.dir||'')}" onclick="this.classList.toggle('act-expanded')"><span class="act-time">${esc(a.time||'')}</span><span class="act-icon">${_actIcon(a.dir)}</span><span class="act-name" title="${esc(a.name)}">${esc(a.name)}</span></div>${a.summary?`<div class="act-summary">${esc(a.summary)}</div>`:''}${a.detail&&a.detail.length?`<div class="act-detail">${a.detail.map(m=>`<div class="act-msg act-${esc(m.role||'')}"><b>${esc(m.role==='user'?'用户':'AI')}</b> ${esc(m.content)}</div>`).join('')}</div>`:''}`).join('')}
        </div>`:''}
        ${f_acts.length===0?'<div style="color:var(--text3);padding:12px">暂无活动记录</div>':''}
      </div>
    </div>
    <div class="card card-collapsible" id="panel-cron">
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
  const _estTokens = sess._estimated;
  const _fmtTok = (v) => v==null ? '—' : v>=1e6 ? (v/1e6).toFixed(1)+'M' : v>=1e3 ? (v/1e3).toFixed(0)+'k' : String(v);
  html+=`<div class="card">
    <div class="ch">会话统计 <span class="ch-r">${sess.active_sessions||0}个活跃</span></div>
    <div class="cb">
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;text-align:center">
        <div>
          <div style="font-size:24px;font-weight:700;color:var(--accent)">${sess.active_sessions||0}</div>
          <div style="font-size:11px;color:var(--text3)">活跃会话</div>
        </div>
        <div>
          <div style="font-size:24px;font-weight:700;color:var(--green)">${_fmtTok(sess.total_tokens)}${_estTokens?'<span style="font-size:9px;color:var(--text3)">≈</span>':''}</div>
          <div style="font-size:11px;color:var(--text3)">总 Token${_estTokens?' (估算)':''}</div>
        </div>
        <div>
          <div style="font-size:24px;font-weight:700;color:var(--orange)">${_fmtTok(sess.today_tokens)}</div>
          <div style="font-size:11px;color:var(--text3)">今日 Token</div>
        </div>
      </div>
      <div style="margin-top:12px;padding-top:12px;border-top:1px solid var(--border);display:flex;justify-content:center;gap:20px">
        <span style="color:var(--text2);font-size:12px">消息 <span style="color:var(--accent);font-weight:600">${sess.total_messages||0}</span></span>
        <span style="color:var(--text2);font-size:12px">助手 <span style="color:var(--green);font-weight:600">${sess.assistant_messages||0}</span></span>
        <span style="color:var(--text2);font-size:12px">工具 <span style="color:var(--orange);font-weight:600">${sess.tool_calls||0}</span></span>
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
  html+=`<div class="card card-collapsible" id="panel-dirs">
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
  html+=`<div class="card card-collapsible" id="panel-skills">
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
        ${tool_stats.slice(0,10).map(t=>{
          // t may be [name, count] array or {name, count, skill} object
          const tn = Array.isArray(t) ? t[0] : (t.name||'?');
          const tc = Array.isArray(t) ? t[1] : (t.count||0);
          const ts = Array.isArray(t) ? '' : (t.skill||'');
          return `
        <span style="font-size:10px;background:var(--card-h);padding:2px 7px;border-radius:10px;color:var(--text2);cursor:pointer" title="${esc(ts)}" onclick="document.getElementById('skill-search').value='${esc(ts&&ts!=='内置工具'?ts:tn)}';filterSkills()">
          ${esc(tn)}<span style="color:var(--accent);margin-left:3px">${tc}</span>${ts&&ts!=='内置工具'?`<span style="color:var(--text3);font-size:9px;margin-left:2px">${esc(ts)}</span>`:''}
        </span>`;}).join('')}
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

  // Footer: Project Structure
  html+=`<footer class="footer">
    <h3>📁 项目结构</h3>
    <pre><code>mac_ai_monitor/
├── <span class="struct-file">mac_ai_monitor.py</span>      # 主程序 (2654行)
├── <span class="struct-file">index.html</span>             # HTML 骨架 (47行, 引用 static/)
├── <span class="struct-file">dev.sh</span>                 # 开发工具链 (watch/restart/status/commit)
├── <span class="struct-dir">docs/</span>
│   ├── <span class="struct-file">README.md</span>          # 项目首页/导航
│   ├── <span class="struct-file">QUICKSTART.md</span>      # 修改流程/检查清单
│   ├── <span class="struct-file">ARCHITECTURE.md</span>    # 架构/行号/缓存
│   ├── <span class="struct-file">API.md</span>             # API 接口文档
│   ├── <span class="struct-file">CHANGELOG.md</span>      # 版本历史
│   └── <span class="struct-file">TROUBLESHOOTING.md</span># 排障手册
└── <span class="struct-dir">static/</span>
    ├── <span class="struct-dir">css/</span>
    │   └── <span class="struct-file">style.css</span>      # 样式 (311行)
    └── <span class="struct-dir">js/</span>
        └── <span class="struct-file">app.js</span>          # 前端逻辑 (1143行)</code></pre>
    <p style="font-size:10px;color:var(--text3);margin-top:6px">配置: data/alert_config.json · 日志: data/logs/</p>
  </footer>`;

  $('#app').innerHTML = html;

  // P0-3: Restore state after render
  if (window.scrollY === 0 || Math.abs(window.scrollY - scrollTop) > 100) {
    window.scrollTo(0, scrollTop);
  }
  window._actFilter = savedActFilter;
  const actSel = $('#act-app');
  if (actSel) actSel.value = savedActFilter;
  const ssInput = $('#skill-search');
  if (ssInput && savedSkillSearch) { ssInput.value = savedSkillSearch; }
  const ssApp = $('#skill-app');
  if (ssApp && savedSkillApp) { ssApp.value = savedSkillApp; }
  if (savedSkillSearch || savedSkillApp) filterSkills();
  // Restore expanded skills
  $$('.skill-chip').forEach(el => {
    if (expandedSkills.has(el.dataset.name)) el.classList.add('expanded');
  });
  // Restore expanded activity items
  $$('.act-item').forEach(el => {
    const name = el.querySelector('.act-name')?.textContent;
    if (name && expandedActItems.has(name)) el.classList.add('act-expanded');
  });

  // P2-5: Restore collapsible panels from localStorage
  _restoreCollapsed();

  // Historical Chart (async from SQLite)
  var histEl = document.getElementById('hist-chart-insert');
  if (histEl) {
    var chartDiv = document.getElementById('hist-chart');
    if (!chartDiv) {
      chartDiv = document.createElement('div');
      chartDiv.id = 'hist-chart';
      chartDiv.style.width = '100%';
      chartDiv.style.height = '280px';
      histEl.appendChild(chartDiv);
    }
    initHistChart();
    _setupHistRangeBtns();
  }

  // Footer
  $('#ft').innerHTML=`<span>Mac AI Monitor v${esc(d.version||'?')}</span><span>${esc(d.timestamp||'')}</span><span>macOS</span><span class="ft-path" style="color:var(--text3);font-size:9px" title="代码路径">${esc(d.script_path||'')}</span><span style="color:var(--text3);font-size:9px" title="文档">mac_ai_monitor/</span>`;

  // Process row click handlers
  $$('.clickable-row').forEach(tr => {
    tr.onclick = async () => {
      const pid = tr.dataset.pid;
      const detEl = $('#proc-detail');
      if (!detEl) return;
      detEl.style.display = 'block';
      detEl.innerHTML = '<span style="color:var(--text3)">加载PID '+pid+'...</span>';
      try {
        const r = await fetch('/api/process/'+pid);
        const info = await r.json();
        if (!info.found) { detEl.innerHTML='<span style="color:var(--orange)">PID '+pid+' 未找到</span>'; return; }
        detEl.innerHTML = `
          <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px">
            <div><b>PID</b> ${info.pid}</div>
            <div><b>CPU</b> <span style="color:${pct_c(info.cpu_pct,50,80)}">${info.cpu_pct}%</span></div>
            <div><b>MEM</b> <span style="color:${pct_c(info.mem_pct,50,80)}">${info.mem_pct}%</span></div>
            <div><b>RSS</b> ${info.rss_mb}MB</div>
            <div><b>VSZ</b> ${info.vsz_mb}MB</div>
            <div><b>Nice</b> ${info.nice||'—'}</div>
            <div><b>运行时长</b> ${info.elapsed||'—'}</div>
            <div><b>打开文件</b> ${info.open_files??'—'}</div>
            <div><b>网络连接</b> ${info.connections?.length||0}个</div>
          </div>
          <div style="margin-top:4px;color:var(--text2);word-break:break-all;font-size:10px">${esc(info.command||'')}</div>
          ${info.connections?.length ? '<details style="margin-top:4px"><summary style="cursor:pointer;color:var(--accent);font-size:10px">网络连接详情</summary><pre style="font-size:9px;color:var(--text3)">'+info.connections.map(c=>esc(c)).join('\n')+'</pre></details>' : ''}
        `;
      } catch(e) { detEl.innerHTML='<span style="color:var(--red)">加载失败: '+esc(e.message)+'</span>'; }
    };
  });

  // Remove loading
  $('#loading')?.remove();
}

function toggleSkill(el){
  el.classList.toggle('expanded');
}

function filterActivity(){
  const sel = document.getElementById('act-app');
  if (sel) window._actFilter = sel.value;
  const filter = window._actFilter || 'all';
  $$('.act-item').forEach(el => {
    const dir = el.dataset.dir || '';
    const show = filter === 'all' || dir === filter;
    el.style.display = show ? '' : 'none';
    // Toggle adjacent siblings (summary + detail)
    let sib = el.nextElementSibling;
    while(sib && (sib.classList.contains('act-summary') || sib.classList.contains('act-detail'))){
      sib.style.display = show ? '' : 'none';
      sib = sib.nextElementSibling;
    }
  });
  // Toggle group headers
  $$('.act-group').forEach(grp => {
    const visible = Array.from(grp.querySelectorAll('.act-item')).some(el => el.style.display !== 'none');
    grp.style.display = visible ? '' : 'none';
  });
  // Update count
  const cnt = $$('.act-item').filter(el => el.style.display !== 'none').length;
  const cntSpan = document.querySelector('#act-list')?.closest('.card')?.querySelector('.ch-r');
  if (cntSpan) cntSpan.textContent = cnt + '条';
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

// P2-5: Collapsible panels
function _restoreCollapsed(){
  try{
    const saved=JSON.parse(localStorage.getItem('mam_collapsed')||'{}');
    Object.entries(saved).forEach(([id,v])=>{
      if(v) document.getElementById(id)?.classList.add('collapsed');
    });
  }catch(e){}
}
function _initCollapsible(){
  document.addEventListener('click',e=>{
    const hdr=e.target.closest('.card-collapsible .ch');
    if(!hdr) return;
    const card=hdr.closest('.card-collapsible');
    if(!card) return;
    card.classList.toggle('collapsed');
    const collapsed={};
    $$('.card-collapsible').forEach(c=>{
      if(c.id&&c.classList.contains('collapsed')) collapsed[c.id]=true;
    });
    try{localStorage.setItem('mam_collapsed',JSON.stringify(collapsed));}catch(ex){}
  });
  _restoreCollapsed();
}

// =============================================
// 双模式刷新控制
// Auto模式: 每30s拉取全量数据，适合持续观察
// Manual模式: 不自动拉取，用户点击刷新按钮读取缓存，适合低频查看
// =============================================
let _autoRefresh = false;
let _autoInterval = null;
let _countdown = null;
let _lastRefreshMs = 0;

const REFRESH_OPTIONS = [{label:'智能',ms:0},{label:'5s',ms:5000},{label:'10s',ms:10000},{label:'15s',ms:15000},{label:'30s',ms:30000},{label:'60s',ms:60000}];
let _refreshMs = parseInt(localStorage.getItem('monitor_refresh_ms')) || 0;
const _smartBase = () => {
  // 智能刷新: 基于 CPU 负载动态调整
  if (!_data) return 30000;
  const cpu = _data.cpu?.used_pct || 0;
  if (cpu > 90) return 10000;
  if (cpu > 70) return 15000;
  return 30000;
};
const AUTO_INTERVAL_MS = () => _refreshMs === 0 ? _smartBase() : _refreshMs;
// 默认智能模式下 base interval
let _curSmartInterval = 30000;
const MANUAL_FETCH_LITE = true;     // Manual模式优先读取轻量端点

// ---- Header 状态刷新 ----
function updateRefreshStatus() {
  const el = $('#refresh-status');
  if (!el) return;
  if (_autoRefresh) {
    const curMs = _refreshMs === 0 ? _curSmartInterval : _refreshMs;
    const left = Math.max(0, curMs - (Date.now() - _lastRefreshMs));
    const sec = Math.ceil(left / 1000);
    const prefix = _refreshMs === 0 ? '⚡' : '';
    el.textContent = prefix + sec + 's';
    el.style.color = left < 10000 ? 'var(--orange)' : 'var(--text3)';
  } else {
    const ago = Math.round((Date.now() - _lastRefreshMs) / 1000);
    if (ago < 60) el.textContent = ago + 's前';
    else if (ago < 3600) el.textContent = Math.floor(ago / 60) + 'm前';
    else el.textContent = Math.floor(ago / 3600) + 'h前';
    el.style.color = 'var(--text3)';
  }
}

// ---- 模式切换 ----
function toggleAutoRefresh() {
  _autoRefresh = !_autoRefresh;
  const btn = $('#btn-auto');
  if (_autoRefresh) {
    btn.className = 'btn btn-auto';
    btn.title = '自动刷新: 开 (每' + (_refreshMs / 1000) + 's)';
    btn.innerHTML = '<span class="auto-indicator"></span>⟳ 自动刷新';
    startAutoRefresh();
    // 立即刷新一次
    silentLoad();
  } else {
    btn.className = 'btn';
    btn.title = '手动刷新: 关 (点刷新按钮读取缓存)';
    btn.innerHTML = '⟳ 手动刷新';
    stopAutoRefresh();
    updateRefreshStatus();
  }
}

function startAutoRefresh() {
  stopAutoRefresh();
  _lastRefreshMs = Date.now();
  const ms = AUTO_INTERVAL_MS();
  _curSmartInterval = ms;
  _autoInterval = setInterval(() => silentLoad(), ms);
  _countdown = setInterval(() => {
    // 智能模式动态检查并调整间隔
    const newMs = AUTO_INTERVAL_MS();
    if (_autoRefresh && newMs !== _curSmartInterval) {
      _curSmartInterval = newMs;
      clearInterval(_autoInterval);
      _autoInterval = setInterval(() => silentLoad(), newMs);
    }
    updateRefreshStatus();
  }, 1000);
}

function stopAutoRefresh() {
  if (_autoInterval) { clearInterval(_autoInterval); _autoInterval = null; }
  if (_countdown) { clearInterval(_countdown); _countdown = null; }
}

// ---- 静默刷新（不重绘页面骨架） ----
async function silentLoad() {
  try {
    _lastRefreshMs = Date.now();
    updateRefreshStatus();
    const r = await fetch('/api/data');
    if (!r.ok) return;
    _data = await r.json();
    try { render(_data); } catch (e) { console.error('silent render:', e); }
  } catch (e) { console.error('silent load:', e); }
}

// ---- 全量加载（手动刷新触发） ----
async function manualRefresh() {
  $('#app').innerHTML = '<div class="loading">读取中…</div>';
  _lastRefreshMs = Date.now();
  updateRefreshStatus();
  await load();
  // Manual模式下不自动重启轮询
}

// ---- 刷新间隔选择器 ----
(function initRefreshSelector() {
  const sel = $('#refresh-sel');
  if (!sel) return;
  REFRESH_OPTIONS.forEach(o => {
    const opt = document.createElement('option');
    opt.value = o.ms; opt.textContent = o.label;
    if (o.ms === _refreshMs) opt.selected = true;
    sel.appendChild(opt);
  });
  // If saved value not in options, add it
  if (!REFRESH_OPTIONS.find(o => o.ms === _refreshMs)) {
    const opt = document.createElement('option');
    opt.value = _refreshMs; opt.textContent = (_refreshMs/1000)+'s'; opt.selected = true;
    sel.insertBefore(opt, sel.firstChild);
  }
  sel.onchange = () => {
    _refreshMs = parseInt(sel.value);
    localStorage.setItem('monitor_refresh_ms', _refreshMs);
    if (_autoRefresh) startAutoRefresh(); // restart with new interval
  };
})();

// ---- 刷新按钮 ----
$('#btn-refresh').onclick = () => {
  if (!_autoRefresh) {
    manualRefresh();
  } else {
    // Auto模式下点击刷新: 立即触发一次并重置计时
    silentLoad();
    startAutoRefresh();
  }
};


// ---- Gateway 日志弹窗 ----
window.fetchGatewayLog = function() {
  let modal = document.getElementById('gw-log-modal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'gw-log-modal';
    modal.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.7);z-index:9999;display:flex;align-items:center;justify-content:center;';
    const box = document.createElement('div');
    box.style.cssText = 'background:#1a1a2e;color:#e0e0e0;width:90%;max-width:800px;height:80%;border-radius:8px;display:flex;flex-direction:column;overflow:hidden;';
    const hdr = document.createElement('div');
    hdr.style.cssText = 'padding:12px 16px;background:#16213e;border-bottom:1px solid #333;display:flex;justify-content:space-between;align-items:center;';
    hdr.innerHTML = '<span style="font-weight:bold;">📋 Gateway 日志</span>';
    const btn = document.createElement('button');
    btn.textContent = '×';
    btn.style.cssText = 'background:none;border:none;color:#e0e0e0;font-size:20px;cursor:pointer;';
    btn.onclick = () => { modal.style.display = 'none'; };
    hdr.appendChild(btn);
    const pre = document.createElement('pre');
    pre.id = 'gw-log-content';
    pre.style.cssText = 'flex:1;padding:16px;overflow:auto;font-size:12px;line-height:1.6;white-space:pre-wrap;word-break:break-all;';
    box.appendChild(hdr);
    box.appendChild(pre);
    modal.appendChild(box);
    document.body.appendChild(modal);
  }
  modal.style.display = 'flex';
  const contentEl = document.getElementById('gw-log-content');
  contentEl.textContent = '加载中...';
  fetch('/api/gateway-log')
    .then(r => r.json())
    .then(data => {
      contentEl.textContent = data.log || '暂无日志';
    })
    .catch(err => {
      contentEl.textContent = '加载失败: ' + err.message;
    });
};

// ---- 页面可见性控制（Tab切换停止Auto刷新） ----
document.addEventListener('visibilitychange', () => {
  if (document.hidden && _autoRefresh) {
    stopAutoRefresh();
  } else if (!document.hidden && _autoRefresh) {
    // 回到页面时判断是否过期需要刷新
    const elapsed = Date.now() - _lastRefreshMs;
    if (elapsed > _refreshMs) silentLoad();
    startAutoRefresh();
  }
});

// ---- 初始化 ----
_initCollapsible();
load().then(() => {
  updateRefreshStatus();
  if (_autoRefresh) startAutoRefresh();
});
