/**
 * Dashboard Logic Engine
 * V2.2.3 [Modularized]
 */

// ── State Management & Config ──
const MAX_PTS = 60;
let tsArr = [], curArr = [], probArr = [];
let trendTick = 0;
let agentSocket = null;
let isCopilotRunning = false;

// ── 1. ECharts Initialization ──
function initDashboardCharts() {
    console.log('[Dashboard] Init Charts...');
    
    // Hourly Production
    const hourlyEl = $('chart-hourly');
    if (hourlyEl) {
        console.log('[Dashboard] Found chart-hourly');
        window.cHourly = echarts.init(hourlyEl);
        cHourly.setOption({
            grid: { left: 4, right: 4, top: 12, bottom: 20, containLabel: false },
            xAxis: {
                type: 'category', data: [],
                axisLine: { show: false }, axisTick: { show: false },
                axisLabel: { fontSize: 10, color: '#909399' }
            },
            yAxis: { type: 'value', show: false },
            series: [{
                type: 'bar',
                data: [],
                itemStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: '#409EFF' }, { offset: 1, color: '#a8c8ff' }] }, borderRadius: [2, 2, 0, 0] },
                barMaxWidth: 18
            }]
        });
    }

    // 24h Alert Trend
    const trendEl = $('chart-trend');
    if (trendEl) {
        console.log('[Dashboard] Found chart-trend');
        window.cTrend = echarts.init(trendEl);
        cTrend.setOption({
            tooltip: { trigger: 'axis', formatter: params => params[0].name + '<br/>频次：' + params[0].value },
            grid: { left: '3%', right: '12%', top: '20%', bottom: '15%', containLabel: true },
            xAxis: {
                type: 'category', boundaryGap: false, data: [],
                axisLabel: { fontSize: 10, color: '#6b7280', interval: 5, fontFamily: 'Roboto Mono,monospace' },
                axisLine: { show: false }, axisTick: { show: false }
            },
            yAxis: {
                type: 'value', min: 0,
                axisLabel: { fontSize: 10, color: '#6b7280' },
                splitLine: { lineStyle: { color: '#f0f2f5' } },
                axisLine: { show: false }, axisTick: { show: false }
            },
            series: [{
                type: 'line', smooth: true, symbol: 'none',
                lineStyle: { color: '#409EFF', width: 2 },
                areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(64,158,255,.35)' }, { offset: 1, color: 'rgba(64,158,255,0)' }] } },
                markPoint: {
                    symbol: 'circle', symbolSize: 8,
                    itemStyle: { color: '#F5222D' },
                    data: [{ type: 'max', name: '峰值', label: { formatter: p => '峰值 ' + p.value, color: '#F5222D', fontSize: 10, offset: [0, -12] } }]
                },
                data: []
            }]
        });
    }

    // Stream Dual Axis Line
    const lineEl = $('chart-line');
    if (lineEl) {
        window.cLine = echarts.init(lineEl);
        cLine.setOption({
            tooltip: { trigger: 'axis', axisPointer: { type: 'cross' }, backgroundColor: '#fff', borderColor: '#e4e7ed', textStyle: { color: '#303133', fontSize: 12 } },
            legend: {
                bottom: 0, left: 0, icon: 'roundRect', textStyle: { color: '#4a5565', fontSize: 11 }, itemWidth: 24, itemHeight: 2,
                data: [{ name: '电流', itemStyle: { color: '#2b7fff' } }, { name: 'AI预警', itemStyle: { color: '#ff6467' } }]
            },
            grid: { left: '6%', right: '8%', bottom: '18%', containLabel: true },
            xAxis: {
                type: 'category', boundaryGap: false, data: [], axisLine: { show: false }, axisTick: { show: false },
                axisLabel: { color: '#6b7280', fontSize: 10, fontFamily: 'Roboto Mono,monospace' }, splitLine: { show: false }
            },
            yAxis: [
                {
                    type: 'value', name: '电流(A)', position: 'left', axisLine: { show: false }, axisTick: { show: false },
                    splitLine: { lineStyle: { color: '#f0f2f5' } }, axisLabel: { color: '#4a5565', fontSize: 10 }, nameTextStyle: { color: '#4a5565', fontSize: 10 }
                },
                {
                    type: 'value', name: 'AI预警(%)', position: 'right', nameGap: 15, offset: 10, min: 0, max: 100, interval: 20,
                    axisLine: { show: false }, axisTick: { show: false }, splitLine: { show: false },
                    axisLabel: { color: '#ff6467', fontSize: 10 }, nameTextStyle: { color: '#ff6467', fontSize: 10 }
                }
            ],
            series: [
                {
                    name: '电流', type: 'line', smooth: true, symbol: 'none', yAxisIndex: 0,
                    lineStyle: { color: '#2b7fff', width: 1.5 }, data: []
                },
                {
                    name: 'AI预警', type: 'line', smooth: true, symbol: 'none', yAxisIndex: 1,
                    lineStyle: { color: '#ff6467', width: 1.5 }, itemStyle: { color: '#ff6467' },
                    markLine: { symbol: ['none', 'none'], data: [{ yAxis: 75, lineStyle: { color: '#f56c6c', type: 'dashed', width: 1, opacity: .35 }, label: { show: false } }] },
                    data: []
                }
            ]
        });
    }
}

// ── 2. Data Polling Logic ──
async function fetchStats() {
    const el = document.getElementById('oee-pct');
    if (!el) {
        if (DashboardApp.state.pollInterval) clearInterval(DashboardApp.state.pollInterval);
        return;
    }
    try {
        const d = await fetch('/api/stats/').then(r => r.json());
        if (d.status !== 'ok') return;

        const oee = d.avg_oee != null ? +(d.avg_oee * 100).toFixed(1) : 0;
        $('oee-pct').textContent = oee.toFixed(1) + '%';
        $('oee-bar').style.width = Math.min(oee, 100) + '%';
        
        const q = d.quality != null ? +(d.quality * 100).toFixed(1) : 0;
        const p = d.performance != null ? +(d.performance * 100).toFixed(1) : 0;
        const a = d.availability != null ? +(d.availability * 100).toFixed(1) : 0;

        const setBar = (vId, bId, val) => {
            const elV = $(vId), elB = $(bId);
            if (elV) elV.textContent = val.toFixed(1) + '%';
            if (elB) elB.style.height = Math.min(val, 100) + '%';
        };
        setBar('oee-q-val', 'oee-q-bar', q);
        setBar('oee-p-val', 'oee-p-bar', p);
        setBar('oee-a-val', 'oee-a-bar', a);

        const actual = d.total_output || 0;
        const TARGET = d.daily_target || 4000;
        const rate = Math.min(100, TARGET > 0 ? (actual / TARGET * 100) : 0);

        $('prod-output').textContent = Number(actual).toLocaleString();
        $('prod-target').textContent = Number(TARGET).toLocaleString();
        $('prod-bar').style.width = rate.toFixed(1) + '%';
        $('prod-badge').textContent = rate.toFixed(1) + '%';
        $('prod-quality').textContent = Number(actual).toLocaleString() + ' 件';
        $('prod-defects').textContent = Number(Math.max(0, (d.total_input || 0) - actual)).toLocaleString() + ' 件';

        if (window.cHourly) {
            const labels = d.hourly_labels || [];
            const barData = d.hourly_output || [];
            window.cHourly.setOption({ xAxis: { data: labels }, series: [{ data: barData }] });
        }
    } catch (e) { console.warn('[stats]', e); }
}

async function fetchMatrix() {
    try {
        const d = await fetch('/api/device-matrix/').then(r => r.json());
        if (d.status !== 'ok') return;
        const sorted = [...d.data].sort((a, b) => (a.device_id || 0) - (b.device_id || 0));
        if (window.renderHoneycomb) window.renderHoneycomb(sorted);
    } catch (e) { console.warn('[matrix]', e); }
}

async function fetchTrend() {
    try {
        const d = await fetch('/api/alert-trend/').then(r => r.json());
        if (d.status !== 'ok') return;
        if (window.cTrend) window.cTrend.setOption({ xAxis: { data: d.labels }, series: [{ data: d.values }] });
    } catch (e) { console.warn('[trend]', e); }
}

async function fetchAlerts() {
    try {
        const d = await fetch('/api/alerts/?limit=7').then(r => r.json());
        if (d.status !== 'ok' || !d.data) return;
        const el = $('alert-list');
        if (!el) return;
        if (!d.data.length) { el.innerHTML = '<div style="color:#c0c4cc;font-size:13px;text-align:center;padding:16px 0;">暂无异常上报</div>'; return; }
        
        const typeMap = { 'HIGH_CURRENT': '主轴过电流报警', 'HIGH_POWER': '主轴功率异常', 'LOW_VELOCITY': '主轴运转报警', 'UNPLANNED_DOWNTIME': '设备停机报警' };
        el.innerHTML = d.data.map(a => {
            const score = a.anomaly_score ?? 0;
            const isHigh = score > 0.75;
            const isWarn = score > 0.40;
            const dot = isHigh ? '#F5222D' : isWarn ? '#FADB14' : '#52C41A';
            const sc = isHigh ? '#F5222D' : isWarn ? '#E8B800' : '#52C41A';
            const t = new Date(a.alert_time);
            const diffMin = Math.round((Date.now() - t.getTime()) / 60000);
            const relTime = diffMin < 60 ? `${diffMin}分钟前` : `${Math.floor(diffMin / 60)}小时前`;
            return `<div class="arow" onclick="window.location.href='/devices/?device=${a.device_id}'">
                <div><div class="arow-time">${String(t.getHours()).padStart(2,'0')}:${String(t.getMinutes()).padStart(2,'0')}</div><div style="font-size:10px;color:#c0c4cc;">${relTime}</div></div>
                <div class="arow-desc"><div class="dev" style="color:${sc};"><span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:${dot};margin-right:4px;vertical-align:middle;"></span>${a.device_name || '--'}</div><div style="color:#606266;font-size:11px;margin-top:1px;">${typeMap[a.alert_type] || '传感器异常报警'}</div></div>
                <div class="arow-score" style="color:${sc};">${(score * 100).toFixed(0)}%</div>
                <div class="arow-arrow">›</div>
            </div>`;
        }).join('');

        const footerText = $('alert-footer-text');
        if (footerText && d.total !== undefined) footerText.textContent = `最新 ${d.data.length} 条异常 · 共 ${d.total} 条`;
    } catch (e) { console.warn('[alerts]', e); }
}

async function fetchStream() {
    try {
        const d = await fetch('/api/stream/').then(r => r.json());
        if (d.status !== 'ok' || !d.data.length) return;
        const latest = d.data[0];
        const t = new Date(latest.timestamp);
        const lbl = `${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}:${String(t.getSeconds()).padStart(2, '0')}`;
        if (tsArr.length && tsArr[tsArr.length - 1] === lbl) return;
        tsArr.push(lbl); curArr.push(latest.spindle_current);
        probArr.push(latest.anomaly_probability != null ? +(latest.anomaly_probability * 100).toFixed(1) : 0);
        if (tsArr.length > MAX_PTS) { tsArr.shift(); curArr.shift(); probArr.shift(); }
        if (window.cLine) window.cLine.setOption({ xAxis: { data: tsArr }, series: [{ data: curArr }, { data: probArr }] });
    } catch (e) { console.warn('[stream]', e); }
}

window.pollAll = async function() {
    // 强制生命周期管理：如果仪表盘核心元素不存在，说明已切换页面，直接杀掉整个轮询
    const checkEl = document.getElementById('oee-pct');
    if (!checkEl) {
        if (DashboardApp.state.pollInterval) clearInterval(DashboardApp.state.pollInterval);
        return;
    }

    try {
        const st = await fetch('/api/stream-status/').then(r => r.json());
        const badge = $('stream-status-badge');
        if (badge) {
            badge.innerHTML = st.is_realtime_active 
                ? `<span style="width:7px;height:7px;background:#52c41a;border-radius:50%;box-shadow:0 0 5px #52c41a;animation:pulse 1.5s infinite;"></span><span style="color:#52c41a;">LIVE</span>`
                : `<span style="width:7px;height:7px;background:#909399;border-radius:50%;"></span><span style="color:#909399;">PAUSED</span>`;
        }
        fetchStats(); fetchMatrix(); fetchAlerts(); fetchStream();
        if (++trendTick === 1 || trendTick % 20 === 0) fetchTrend();
    } catch (e) { console.warn('[poll]', e); }
}

// ── 3. Topology (Honeycomb) Engine ──
function hexPoints(cx, cy, r) {
    const pts = [];
    for (let i = 0; i < 6; i++) {
        const a = (Math.PI / 180) * (60 * i - 30);
        pts.push(`${(cx + r * Math.cos(a)).toFixed(1)},${(cy + r * Math.sin(a)).toFixed(1)}`);
    }
    return pts.join(' ');
}

window.renderHoneycomb = function (devices) {
    const svg = $('hc-svg');
    const wrap = $('hc-wrap');
    if (!svg || !wrap) return;
    const W = wrap.clientWidth || 500;
    const H = wrap.clientHeight || 370;
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.innerHTML = '';

    const COLS = 5, ROWS = 5;
    const GAP = 1.03;
    const r = Math.min(
        W / (Math.sqrt(3) * (COLS * GAP + 0.5)),
        H / (1.5 * ROWS * GAP + 0.5)
    ) * 0.97;
    const hW = r * Math.sqrt(3);
    const hH = r * 2;
    const hSp = hW * GAP;
    const vSp = hH * 0.75 * GAP;

    const totW = COLS * hSp + hW * 0.5;
    const totH = ROWS * vSp + hH * 0.5;
    const ox = (W - totW) / 2 + hW * 0.5;
    const oy = (H - totH) / 2 + hH * 0.5 + 14;

    let nN = 0, nW = 0, nD = 0, nIdle = 0, nDown = 0;
    const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
    svg.appendChild(defs);

    const centers = [];
    for (let row = 0; row < ROWS; row++) {
        for (let col = 0; col < COLS; col++) {
            const cx = ox + col * hSp + (row % 2 === 1 ? hSp * 0.5 : 0);
            const cy = oy + row * vSp;
            centers.push({ cx, cy });
        }
    }

    const bgClipId = 'hc-bg-clip';
    const bgClipEl = document.createElementNS('http://www.w3.org/2000/svg', 'clipPath');
    bgClipEl.setAttribute('id', bgClipId);
    const bgRectEl = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    bgRectEl.setAttribute('x', '0'); bgRectEl.setAttribute('y', '0');
    bgRectEl.setAttribute('width', W); bgRectEl.setAttribute('height', H);
    bgClipEl.appendChild(bgRectEl); defs.appendChild(bgClipEl);

    const lineGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    lineGroup.setAttribute('clip-path', `url(#${bgClipId})`);

    const getC = (r, c) => (r >= 0 && r < ROWS && c >= 0 && c < COLS) ? centers[r * COLS + c] : null;
    const drawBgLine = (a, b) => {
        if (!a || !b) return;
        const l = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        l.setAttribute('x1', a.cx); l.setAttribute('y1', a.cy);
        l.setAttribute('x2', b.cx); l.setAttribute('y2', b.cy);
        l.setAttribute('stroke', 'rgba(0,0,0,0.04)');
        l.setAttribute('stroke-width', '0.5');
        lineGroup.appendChild(l);
    };

    for (let row = 0; row < ROWS; row++) {
        for (let col = 0; col < COLS; col++) {
            const me = centers[row * COLS + col];
            const isOdd = row % 2 === 1;
            drawBgLine(me, getC(row, col + 1));
            drawBgLine(me, getC(row + 1, isOdd ? col + 1 : col));
            drawBgLine(me, getC(row + 1, isOdd ? col : col - 1));
        }
    }
    svg.appendChild(lineGroup);

    for (let row = 0; row < ROWS; row++) {
        for (let col = 0; col < COLS; col++) {
            const idx = row * COLS + col;
            const dev = devices[idx] || null;
            const { cx, cy } = centers[idx];
            const status = dev ? (dev.current_status || 'Running') : 'Running';
            const score = dev ? (dev.anomaly_score ?? 0) : 0;
            const riskPct = Math.round(score * 100);

            let strokeColor, strokeW, txtColor, polyCls, statusLabel = null;

            if (status === 'Idle') {
                strokeColor = 'rgba(0,0,0,0.05)'; strokeW = '1'; txtColor = '#F0A030'; polyCls = ''; statusLabel = '待机'; nIdle++;
            } else if (status === 'Down') {
                strokeColor = 'rgba(0,0,0,0.05)'; strokeW = '1'; txtColor = '#FF7875'; polyCls = ''; statusLabel = '停机'; nDown++;
            } else {
                if (score > 0.75) {
                    strokeColor = 'rgba(245,34,45,0.28)'; strokeW = '1'; txtColor = '#F5222D'; polyCls = 'poly-high'; nD++;
                } else if (score > 0.40) {
                    strokeColor = 'rgba(0,0,0,0.05)'; strokeW = '1'; txtColor = '#FA8C16'; polyCls = ''; nW++;
                } else {
                    strokeColor = 'rgba(0,0,0,0.05)'; strokeW = '1'; txtColor = '#52C41A'; polyCls = ''; nN++;
                }
            }

            const clipId = `hcp${idx}`;
            const cp = document.createElementNS('http://www.w3.org/2000/svg', 'clipPath');
            cp.setAttribute('id', clipId);
            const ce = document.createElementNS('http://www.w3.org/2000/svg', 'ellipse');
            ce.setAttribute('cx', cx); ce.setAttribute('cy', cy);
            ce.setAttribute('rx', r * 0.76); ce.setAttribute('ry', r * 0.80);
            cp.appendChild(ce); defs.appendChild(cp);

            const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
            if (dev) {
                g.style.cursor = 'pointer';
                g.addEventListener('click', () => { window.location.href = `/devices/?device=${dev.device_id}`; });
            }

            const poly = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
            poly.setAttribute('points', hexPoints(cx, cy, r - 0.5));
            poly.setAttribute('fill', 'none');
            poly.setAttribute('stroke', strokeColor);
            poly.setAttribute('stroke-width', strokeW);
            if (polyCls) poly.setAttribute('class', polyCls);
            g.appendChild(poly);

            if (dev) {
                const tg = document.createElementNS('http://www.w3.org/2000/svg', 'g');
                tg.setAttribute('clip-path', `url(#${clipId})`);
                const fullName = dev.device_name || `DEV-${String(idx + 1).padStart(3, '0')}`;
                const lastDash = fullName.lastIndexOf('-');
                let line1 = fullName, line2 = null;
                if (lastDash > 0 && lastDash < fullName.length - 1) {
                    line1 = fullName.slice(0, lastDash);
                    line2 = fullName.slice(lastDash + 1);
                }
                const nameFontSize = Math.max(8, r * 0.21);
                const valFontSize = Math.max(10, r * 0.38);
                const lineH = nameFontSize * 1.4;
                const lineGap = nameFontSize * 0.7;
                const numNameLines = line2 ? 2 : 1;
                const totalH = numNameLines * lineH + lineGap + valFontSize;
                const blockT = cy - totalH / 2;

                const addText = (content, y, size, bold, color) => {
                    const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
                    t.setAttribute('x', cx); t.setAttribute('y', y);
                    t.setAttribute('text-anchor', 'middle'); t.setAttribute('dominant-baseline', 'middle');
                    t.setAttribute('fill', color); t.setAttribute('font-family', 'Roboto Mono, monospace');
                    t.setAttribute('font-size', size); if (bold) t.setAttribute('font-weight', '700');
                    t.textContent = content; tg.appendChild(t);
                };

                addText(line1, blockT + nameFontSize * 0.5, nameFontSize, false, '#7a8899');
                if (line2) addText(line2, blockT + lineH + nameFontSize * 0.5, nameFontSize, false, '#7a8899');
                const valY = blockT + numNameLines * lineH + lineGap + valFontSize * 0.5;
                addText(statusLabel ? statusLabel : riskPct + '%', valY, valFontSize, true, txtColor);
                g.appendChild(tg);
            }
            svg.appendChild(g);
        }
    }
    $('hc-n').textContent = nN; $('hc-w').textContent = nW; $('hc-d').textContent = nD;
    const idleEl = document.getElementById('hc-idle');
    const downEl = document.getElementById('hc-down');
    if (idleEl) idleEl.textContent = nIdle;
    if (downEl) downEl.textContent = nDown;
}

// ── 4. AI Dual-Mode Agent Logic ──
function initAgentSocket() {
    if (agentSocket && agentSocket.readyState === WebSocket.OPEN) return;
    const prot = window.location.protocol === 'https:' ? 'wss' : 'ws';
    agentSocket = new WebSocket(`${prot}://${window.location.host}/ws/copilot/`);

    agentSocket.onmessage = (e) => {
        const data = JSON.parse(e.data);
        if (data.type === 'log') {
            appendTerminalLog(data.message, data.timestamp);
        } else if (data.type === 'ask_response') {
            appendAiMessage(data.message, data.data_context);
        } else if (data.type === 'refresh') {
            if (window.pollAll) window.pollAll();
        }
    };

    agentSocket.onclose = () => {
        console.log('Agent socket closed. Reconnecting...');
        setTimeout(initAgentSocket, 3000);
    };
}

window.onAiMainTrigger = function (mode) {
    if (mode === 'copilot') {
        startCopilot();
    } else {
        openAskMode();
    }
};

function startCopilot() {
    isCopilotRunning = true;
    const overlay = $('copilot-overlay');
    if (overlay) overlay.style.display = 'flex';
    const term = $('cp-terminal');
    if (term) term.innerHTML = '';
    
    if (!agentSocket || agentSocket.readyState !== WebSocket.OPEN) {
        initAgentSocket();
        agentSocket.onopen = () => {
            agentSocket.send(JSON.stringify({ mode: 'copilot', action: 'start' }));
        };
    } else {
        agentSocket.send(JSON.stringify({ mode: 'copilot', action: 'start' }));
    }
}

function stopCopilot() {
    isCopilotRunning = false;
    const overlay = $('copilot-overlay');
    if (overlay) overlay.style.display = 'none';
    if (agentSocket) {
        agentSocket.send(JSON.stringify({ mode: 'copilot', action: 'stop' }));
    }
}

function appendTerminalLog(msg, time) {
    const term = $('cp-terminal');
    if (!term) return;
    const div = document.createElement('div');
    div.className = 'cp-log-item';
    div.style.marginBottom = '8px';
    div.style.fontSize = '12px';
    div.style.lineHeight = '1.6';

    let coloredMsg = msg;
    const labelColors = {
        '[System]': '#3ABFF8', '[Target]': '#FFB703', '[Action]': '#F87171',
        '[Data]': '#9CA3AF', '[Info]': '#34D399', '[AI]': '#C084FC'
    };

    Object.entries(labelColors).forEach(([tag, color]) => {
        const escapedTag = tag.replace(/\[/g, '\\[').replace(/\]/g, '\\]');
        const regex = new RegExp('(' + escapedTag + ')', 'g');
        coloredMsg = coloredMsg.replace(regex, `<span style="color:${color};font-weight:700;margin-right:8px;">$1</span>`);
    });

    const timeStr = `<span style="color:rgba(255,255,255,0.2);margin-right:12px;font-size:11px;">${time || new Date().toLocaleTimeString()}</span>`;
    div.innerHTML = `${timeStr}<span style="color:rgba(255,255,255,0.85);">${coloredMsg}</span>`;
    term.appendChild(div);
    term.scrollTop = term.scrollHeight;
}

function openAskMode() {
    const ov = $('ask-overlay');
    if (!ov) return;
    ov.style.display = 'flex';
    setTimeout(() => ov.classList.add('show'), 10);
    const input = $('ask-input');
    if (input) input.focus();
    if (!agentSocket || agentSocket.readyState !== WebSocket.OPEN) initAgentSocket();
}

function closeAskMode() {
    const ov = $('ask-overlay');
    if (!ov) return;
    ov.classList.remove('show');
    setTimeout(() => ov.style.display = 'none', 400);
}

window.sendAskMessage = function() {
    const input = $('ask-input');
    if (!input) return;
    const text = input.value.trim();
    if (!text) return;

    const container = $('ask-msg-container');
    const uDiv = document.createElement('div');
    uDiv.className = 'msg msg-user';
    uDiv.innerText = text;
    container.appendChild(uDiv);
    input.value = '';
    container.scrollTop = container.scrollHeight;

    const aiDiv = document.createElement('div');
    aiDiv.className = 'msg msg-ai';
    aiDiv.id = 'ai-typing';
    aiDiv.innerHTML = `<div style="display:flex;align-items:center;gap:8px;color:rgba(255,255,255,0.5);font-size:13px;">
        思考中 <div class="typing-dots"><span></span><span></span><span></span></div>
    </div>`;
    container.appendChild(aiDiv);
    container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' });

    if (agentSocket && agentSocket.readyState === WebSocket.OPEN) {
        agentSocket.send(JSON.stringify({ mode: 'ask', message: text }));
    }
}

function appendAiMessage(msg, context) {
    const typing = $('ai-typing');
    if (typing) typing.remove();

    const container = $('ask-msg-container');
    if (!container) return;
    const div = document.createElement('div');
    div.className = 'msg msg-ai';
    div.style.background = 'rgba(255,255,255,0.03)';
    div.style.border = '1px solid rgba(255,255,255,0.08)';

    let html = `<div class="md-content" style="color:rgba(255,255,255,0.9);">${marked.parse(msg)}</div>`;

    if (context && context.length > 0) {
        html += `<div style="margin-top:16px; display:flex; flex-direction:column; gap:10px;">`;
        context.forEach(d => {
            const isAlarm = d.current_status === '故障' || d.anomaly_score > 0.6;
            const statusColor = isAlarm ? '#F59E0B' : '#10B981';
            html += `
                <div style="background:rgba(0,0,0,0.3); border:1px solid rgba(255,255,255,0.05); border-radius:10px; padding:12px; border-left:4px solid ${statusColor};">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
                        <span style="font-weight:700; color:#fff; font-size:13px;">${d.device_name}</span>
                        <span style="color:${statusColor}; font-size:11px; font-weight:700; font-family:monospace;">${Math.round(d.anomaly_score * 100)}% RISK</span>
                    </div>
                    <div style="font-size:11px; color:rgba(255,255,255,0.5);">Status: <span style="color:${statusColor}">${d.current_status}</span></div>
                </div>`;
        });
        html += `</div>`;
    }

    div.innerHTML = html;
    container.appendChild(div);
    container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' });
}

// ── 5. Standardized Dashboard Engine (V3.0.1) ──
window.DashboardApp = {
    state: {
        isInitialized: false,
        oneTimeInited: false,
        pollInterval: null
    },

    ui: {
        init: function() {
            console.log('[Dashboard] Engine Triggered');
            
            // 1. One-time Global Init
            if (!DashboardApp.state.oneTimeInited) {
                document.addEventListener('keydown', DashboardApp.ui.handleGlobalKeys);
                window.addEventListener('resize', () => {
                    [window.cHourly, window.cTrend, window.cLine].forEach(c => c && c.resize());
                });
                DashboardApp.state.oneTimeInited = true;
            }

            // 2. Delayed DOM Init (Ensure layout is ready for ECharts)
            setTimeout(() => {
                const checkEl = document.getElementById('chart-hourly');
                if (!checkEl) return; 

                // Process DOM-dependent components
                if (window.cHourly) window.cHourly.dispose();
                if (window.cTrend) window.cTrend.dispose();
                if (window.cLine) window.cLine.dispose();
                
                initDashboardCharts();
                if (window.renderHoneycomb) window.renderHoneycomb(Array(25).fill(null));
                
                // Data Flow
                fetchTrend();
                window.pollAll();
                
                // Polling Lifecycle
                if (DashboardApp.state.pollInterval) clearInterval(DashboardApp.state.pollInterval);
                DashboardApp.state.pollInterval = setInterval(window.pollAll, 3000);
                
                initAgentSocket();
                DashboardApp.state.isInitialized = true;
                console.log('[Dashboard] Charts & Polling Active');
            }, 300);
        },

        handleGlobalKeys: function(e) {
            if (e.key === 'Escape') {
                if (isCopilotRunning) stopCopilot();
                closeAskMode();
            }
        }
    }
};

// Initial entry for full page load
document.addEventListener('DOMContentLoaded', () => {
    if (window.location.pathname.includes('/dashboard/')) {
        DashboardApp.ui.init();
    }
});
