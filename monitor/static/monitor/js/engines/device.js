/**
 * Device Monitoring Engine
 * V2.2.3 [Modularized]
 */

window.DeviceApp = {
    state: {
        allDevices: [],
        filteredDevices: [], // 新增：供 Alpine x-for 直接使用
        activeFilter: 'all',
        searchTerm: '',      // 新增：搜索状态
        sortMode: 'risk_desc', // 新增：排序状态
        currentPage: 1,      // 新增：分页相关
        pageSize: 10,
        repairDevId: null,
        modalChartInst: null,
        modalPollTimer: null,
        isInitialized: false,
        oneTimeInited: false,
        pollInterval: null
    },

    engine: {
        init: async function() {
            console.log('[DeviceApp] Initializing Shell & Data engine...');
            window.$ = window.$ || (id => document.getElementById(id));

            // 1. One-time Global Init
            if (!DeviceApp.state.oneTimeInited) {
                window.addEventListener('resize', () => { 
                    if (DeviceApp.state.modalChartInst) DeviceApp.state.modalChartInst.resize(); 
                });
                document.addEventListener('click', () => {
                    document.querySelectorAll('.status-dropdown.show, .sort-dropdown.show').forEach(m => m.classList.remove('show'));
                });
                DeviceApp.state.oneTimeInited = true;
            }

            // 2. Initial Data Load
            await DeviceApp.engine.fetchDevices();
            
            // 处理搜索参数跳转
            const urlParams = new URLSearchParams(window.location.search);
            const q = urlParams.get('q');
            if (q) {
                DeviceApp.state.searchTerm = q;
                DeviceApp.engine.sortAndRender();
            }

            // 3. Background polling (JSON ONLY now)
            if (DeviceApp.state.pollInterval) clearInterval(DeviceApp.state.pollInterval);
            DeviceApp.state.pollInterval = setInterval(DeviceApp.engine.fetchDevices, 5000);

            DeviceApp.state.isInitialized = true;
        },

        fetchDevices: async function() {
            try {
                const res = await fetch('/api/device-matrix/');
                const d = await res.json();
                if (d.status !== 'ok') return;
                DeviceApp.state.allDevices = d.data;


                DeviceApp.engine.updateFilterCounts();
                DeviceApp.engine.sortAndRender();
            } catch (e) {
                console.warn('[DeviceApp.fetchDevices]', e);
            }
        },

        updateFilterCounts: function() {
            // 已由 Alpine.js 状态驱动接管，见 sortAndRender
        },

        setFilter: function(f) {
            DeviceApp.state.activeFilter = f;
            DeviceApp.state.currentPage = 1; // 切换过滤重置分页
            DeviceApp.engine.sortAndRender();
        },

        sortAndRender: function() {
            const list = DeviceApp.state.allDevices || [];
            if (!list.length) return;

            const keyword = (DeviceApp.state.searchTerm || '').trim().toLowerCase();
            const filter = DeviceApp.state.activeFilter;
            const sortMode = DeviceApp.state.sortMode;

            let results = [...list];

            // 1. Apply Search
            if (keyword) {
                results = results.filter(d => d.device_name.toLowerCase().includes(keyword));
            }

            // 2. Apply Risk Filters
            if (filter === 'high') results = results.filter(d => d.current_status === 'Running' && (d.anomaly_score ?? 0) > 0.75);
            else if (filter === 'med') results = results.filter(d => d.current_status === 'Running' && (d.anomaly_score ?? 0) > 0.45 && (d.anomaly_score ?? 0) <= 0.75);
            else if (filter === 'low') results = results.filter(d => d.current_status === 'Running' && (d.anomaly_score ?? 0) <= 0.45);

            // 3. Apply Sorting
            if (sortMode === 'risk_desc') results.sort((a, b) => (b.anomaly_score ?? 0) - (a.anomaly_score ?? 0));
            else if (sortMode === 'risk_asc') results.sort((a, b) => (a.anomaly_score ?? 0) - (b.anomaly_score ?? 0));
            else if (sortMode === 'oee_desc') results.sort((a, b) => (b.oee_val ?? 0) - (a.oee_val ?? 0));
            else if (sortMode === 'name_asc') results.sort((a, b) => a.device_name.localeCompare(b.device_name));

            // 4. Client-side Pagination
            const total = results.length;
            const size = DeviceApp.state.pageSize;
            const maxPage = Math.max(1, Math.ceil(total / size));
            
            // 安全修正当前页
            if (DeviceApp.state.currentPage > maxPage) DeviceApp.state.currentPage = maxPage;
            const current = DeviceApp.state.currentPage;
            
            const startIdx = (current - 1) * size;
            const endIdx = Math.min(startIdx + size, total);
            const paginated = results.slice(startIdx, endIdx);

            DeviceApp.state.filteredDevices = paginated;

            // 5. Notify Alpine via custom event (Alpine v3 compatible)
            window.dispatchEvent(new CustomEvent('devices-updated', {
                detail: {
                    devices: paginated,
                    activeF: filter,
                    counts: DeviceApp.engine.getUpdateCountsInternal(),
                    pagination: {
                        total: total,
                        current: current,
                        size: size,
                        maxPage: maxPage,
                        start: total > 0 ? startIdx + 1 : 0,
                        end: endIdx,
                        pages: Array.from({length: maxPage}, (_, i) => i + 1)
                    }
                }
            }));
        },

        gotoPage: function(p) {
            if (p < 1) return;
            DeviceApp.state.currentPage = p;
            DeviceApp.engine.sortAndRender();
        },

        getUpdateCountsInternal: function() {
            const list = DeviceApp.state.allDevices || [];
            const running = list.filter(x => x.current_status === 'Running');
            return {
                all: list.length,
                high: running.filter(x => (x.anomaly_score ?? 0) > 0.75).length,
                med: running.filter(x => (x.anomaly_score ?? 0) > 0.45 && (x.anomaly_score ?? 0) <= 0.75).length,
                low: running.filter(x => (x.anomaly_score ?? 0) <= 0.45).length
            };
        },

        filterTable: function() {
            DeviceApp.state.currentPage = 1;
            DeviceApp.engine.sortAndRender();
        },


        toggleStatusMenu: function(event, devId) {
            event.stopPropagation();
            const menu = $(`status-menu-${devId}`);
            if (!menu) return;
            const isShown = menu.classList.contains('show');
            document.querySelectorAll('.status-dropdown.show').forEach(m => m.classList.remove('show'));
            if (!isShown) menu.classList.add('show');
        },

        selectStatus: function(devId, newStatus) {
            document.querySelectorAll('.status-dropdown.show').forEach(m => m.classList.remove('show'));
            DeviceApp.engine.updateDeviceStatus(devId, newStatus);
        },

        updateDeviceStatus: async function(devId, newStatus) {
            try {
                const csrf = DeviceApp.utils.getCsrf();
                const res = await fetch(`/api/device/${devId}/status/`, {
                    method: 'POST',
                    headers: { 'X-CSRFToken': csrf, 'Content-Type': 'application/json' },
                    body: JSON.stringify({ status: newStatus })
                });
                const data = await res.json();
                if (data.status === 'ok') {
                    await DeviceApp.engine.fetchDevices();
                } else {
                    alert('状态更新失败: ' + data.message);
                }
            } catch (e) {
                console.error(e);
                alert('网络或访问错误');
            }
        },

        toggleSortMenu: function(event) {
            event.stopPropagation();
            const menu = $('sort-dropdown-menu');
            if (!menu) return;
            const isShown = menu.classList.contains('show');
            document.querySelectorAll('.status-dropdown.show, .sort-dropdown.show').forEach(m => m.classList.remove('show'));
            if (!isShown) menu.classList.add('show');
        },

        selectSort: function(val, label) {
            DeviceApp.state.sortMode = val;
            DeviceApp.state.currentPage = 1; // 切换排序重置分页
            if ($('sort-pill-text')) $('sort-pill-text').textContent = label;
            document.querySelectorAll('.sort-dropdown-item').forEach(item => {
                item.classList.toggle('active', item.dataset.val === val);
            });
            DeviceApp.engine.sortAndRender();
            if ($('sort-dropdown-menu')) $('sort-dropdown-menu').classList.remove('show');
        }
    },

    modals: {
        openAIModal: function(devId) {
            const dev = DeviceApp.state.allDevices.find(d => d.device_id === devId);
            if (!dev) return;
            if ($('ai-modal-title')) $('ai-modal-title').textContent = `AI 智能诊断  ·  ${dev.device_name}`;
            if ($('ai-modal')) $('ai-modal').classList.add('show');
            
            // SPA 防消失修复：由于 DOM 已被 HTMX 替换，必须清理旧实例并重新绑定当前节点
            const chartDom = $('modal-chart');
            if (chartDom) {
                if (DeviceApp.state.modalChartInst) {
                    DeviceApp.state.modalChartInst.dispose();
                }
                DeviceApp.state.modalChartInst = echarts.init(chartDom);
            }
            DeviceApp.state.modalChartInst.setOption({
                tooltip: { trigger: 'axis', axisPointer: { type: 'cross' }, backgroundColor: '#fff', borderColor: '#e4e7ed', textStyle: { fontSize: 12 } },
                legend: {
                    bottom: '0%', left: '0%', icon: 'roundRect', itemWidth: 24, itemHeight: 2,
                    textStyle: { fontSize: 12 }, data: [
                        { name: '电流', itemStyle: { color: '#409EFF' } },
                        { name: 'AI预测', itemStyle: { color: '#f56c6c' } }
                    ]
                },
                grid: { left: '5%', right: '12%', bottom: '15%', containLabel: true },
                xAxis: { type: 'category', boundaryGap: false, data: [], axisLine: { show: false }, axisTick: { show: false }, splitLine: { show: false }, axisLabel: { color: '#6b7280', fontSize: 10, fontFamily: 'monospace' } },
                yAxis: [
                    { type: 'value', name: '电流(A)', position: 'left', axisLine: { show: false }, axisTick: { show: false }, splitLine: { lineStyle: { color: '#f0f2f5' } }, axisLabel: { color: '#4a5565', fontSize: 10 }, nameTextStyle: { color: '#4a5565', fontSize: 10, padding: [0, 0, 0, 0] } },
                    { type: 'value', name: 'AI预测(%)', position: 'right', nameGap: 15, offset: 10, min: 0, max: 100, axisLine: { show: false }, axisTick: { show: false }, splitLine: { show: false }, axisLabel: { color: '#ff6467', fontSize: 10 }, nameTextStyle: { color: '#ff6467', fontSize: 10, padding: [0, 0, 0, 0] } }
                ],
                series: [
                    { name: '电流', type: 'line', smooth: true, symbol: 'none', yAxisIndex: 0, lineStyle: { color: '#409EFF', width: 1.5 }, data: [] },
                    {
                        name: 'AI预测', type: 'line', smooth: true, symbol: 'none', yAxisIndex: 1, lineStyle: { color: '#f56c6c', width: 1.5 },
                        markLine: { symbol: ['none', 'none'], data: [{ yAxis: 75, lineStyle: { color: '#f56c6c', type: 'dashed', width: 1, opacity: 0.35 }, label: { show: false } }] },
                        data: []
                    }
                ]
            }, true);

            ['sk-current', 'sk-power', 'sk-ai', 'sk-oee'].forEach(id => {
                const el = $(id);
                if (el) el.textContent = '--';
            });

            clearInterval(DeviceApp.state.modalPollTimer);
            DeviceApp.modals.pollModalStream(devId);
            DeviceApp.state.modalPollTimer = setInterval(() => DeviceApp.modals.pollModalStream(devId), 3000);
        },

        pollModalStream: async function(devId) {
            try {
                const res = await fetch(`/api/stream/${devId}/?limit=40`);
                const d = await res.json();
                if (d.status !== 'ok') return;
                
                if (DeviceApp.state.modalChartInst) {
                    const fmtTs = d.timestamps.map(ts => {
                        const t = new Date(ts);
                        return `${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}:${String(t.getSeconds()).padStart(2, '0')}`;
                    });
                    DeviceApp.state.modalChartInst.setOption({
                        xAxis: { data: fmtTs },
                        series: [{ data: d.spindle_current }, { data: d.anomaly_score }]
                    });
                }

                if (d.timestamps.length > 0) {
                    const n = d.timestamps.length;
                    const cur = d.spindle_current[n - 1];
                    const pwr = d.spindle_power ? d.spindle_power[n - 1] : '--';
                    const ai = d.anomaly_score[n - 1];
                    const oee = d.oee ? d.oee[n - 1] : '--';

                    if ($('sk-current')) $('sk-current').textContent = cur != null ? cur.toFixed(3) : '--';
                    if ($('sk-power')) $('sk-power').textContent = pwr != null ? (typeof pwr === 'number' ? pwr.toFixed(1) : pwr) : '--';
                    if ($('sk-ai')) $('sk-ai').textContent = ai != null ? ai.toFixed(1) : '--';
                    if ($('sk-oee')) $('sk-oee').textContent = oee != null ? (typeof oee === 'number' ? oee.toFixed(1) : oee) : '--';
                }
            } catch (e) {
                console.warn('[DeviceApp.pollModalStream]', e);
            }
        },

        closeModal: function(id) {
            const modal = $(id);
            if (modal) modal.classList.remove('show');
            if (id === 'ai-modal') {
                clearInterval(DeviceApp.state.modalPollTimer);
                DeviceApp.state.modalPollTimer = null;
            }
        },

        openRepairModal: function(devId, devName) {
            DeviceApp.state.repairDevId = devId;
            if ($('repair-modal')) $('repair-modal').classList.add('show');
        },

        submitRepair: async function() {
            if (!DeviceApp.state.repairDevId) return;
            const btn = $('repair-confirm-btn');
            if (!btn) return;
            
            btn.disabled = true;
            btn.textContent = '处理中…';
            try {
                const csrf = DeviceApp.utils.getCsrf();
                const desc = $('repair-desc') ? $('repair-desc').value : '';
                const res = await fetch(`/api/device/${DeviceApp.state.repairDevId}/reset/`, {
                    method: 'POST',
                    headers: { 'X-CSRFToken': csrf, 'Content-Type': 'application/json' },
                    body: JSON.stringify({ note: desc })
                });
                const data = await res.json();
                if (res.ok && data.status === 'ok') {
                    DeviceApp.modals.closeModal('repair-modal');
                    DeviceApp.utils.showRepairToast();
                    
                    let ticks = 0;
                    const fastPoll = setInterval(async () => {
                        await DeviceApp.engine.fetchDevices();
                        if (++ticks >= 15) clearInterval(fastPoll);
                    }, 1000);
                } else {
                    alert('处理失败：' + (data.message || res.status));
                }
            } catch (e) {
                console.warn('[DeviceApp.submitRepair]', e);
                alert('网络请求失败，请重试');
            }
            btn.disabled = false;
            btn.textContent = '确认下发';
        }
    },

    utils: {
        getCsrf: function() {
            return document.cookie.split(';').map(c => c.trim()).find(c => c.startsWith('csrftoken='))?.split('=')[1] || '';
        },

        showRepairToast: function() {
            let toast = $('repair-toast');
            if (!toast) {
                toast = document.createElement('div');
                toast.id = 'repair-toast';
                toast.style.cssText = `
                    position:fixed; bottom:32px; right:32px; z-index:9999;
                    background:#fff; border-radius:8px; padding:14px 18px;
                    box-shadow:0 4px 20px rgba(0,0,0,.12); border-left:4px solid #67c23a;
                    display:flex; align-items:flex-start; gap:10px;
                    font-size:13px; color:#303133; max-width:280px;
                    opacity:0; transition:opacity .3s;
                `;
                toast.innerHTML = `
                    <div style="color:#67c23a;margin-top:1px;">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
                    </div>
                    <div>
                        <div style="font-weight:600;margin-bottom:3px;">成功下发处理信息</div>
                        <div style="color:#909399;font-size:12px;">设备已切换至待机，可手动恢复运行</div>
                    </div>`;
                document.body.appendChild(toast);
            }
            toast.style.opacity = '1';
            setTimeout(() => { if (toast) toast.style.opacity = '0'; }, 5000);
        }
    }
};

// Initialization
document.addEventListener('DOMContentLoaded', DeviceApp.engine.init);
