/**
 * Event Center Engine
 * V2.2.3 [Modularized]
 */

window.EventApp = {
    state: {
        statsChart: null,
        trendChart: null,
        resizeObserver: null,
        oneTimeInited: false,
        // Chart data to be initialized from template
        data: {
            stats: [],
            trendLabels: [],
            trendValues: []
        }
    },

    charts: {
        init: function() {
            const { stats, trendLabels, trendValues } = EventApp.state.data;
            console.log('[EventApp] Charts Init Start. Data:', { statsLen: stats.length, trendLen: trendValues.length });

            // 1. Stats Chart (Horizontal Bar)
            const statsEl = document.getElementById('statsChart');
            if (statsEl) {
                console.log('[EventApp] Initializing Stats Chart');
                EventApp.state.statsChart = echarts.init(statsEl);
                EventApp.state.statsChart.setOption({
                    grid: { left: '2%', right: '2%', top: '5%', bottom: '5%', containLabel: false },
                    xAxis: { type: 'value', show: false, splitLine: { show: false } },
                    yAxis: [
                        {
                            type: 'category',
                            data: ['进给速度异常报警', '主轴过载物理报警', '主轴过电流物理报警'],
                            axisLine: { show: false }, axisTick: { show: false },
                            axisLabel: {
                                inside: true,
                                color: '#606266',
                                fontSize: 13,
                                align: 'left',
                                verticalAlign: 'bottom',
                                padding: [0, 0, 12, 0]
                            }
                        },
                        {
                            type: 'category',
                            data: stats,
                            position: 'right',
                            axisLine: { show: false }, axisTick: { show: false },
                            axisLabel: {
                                inside: true,
                                color: '#606266',
                                fontSize: 14,
                                align: 'right',
                                verticalAlign: 'bottom',
                                padding: [0, 0, 12, 0],
                                fontFamily: 'monospace'
                            }
                        }
                    ],
                    series: [{
                        type: 'bar',
                        data: stats,
                        barWidth: 12,
                        showBackground: true,
                        backgroundStyle: { color: '#f3f6fa', borderRadius: 5 },
                        itemStyle: {
                            color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
                                { offset: 0, color: '#409EFF' },
                                { offset: 1, color: '#a0cfff' }
                            ]),
                            borderRadius: 5
                        }
                    }]
                });
            }

            // 2. Trend Chart (Area Line)
            const trendEl = document.getElementById('trendChart');
            if (trendEl) {
                console.log('[EventApp] Initializing Trend Chart');
                EventApp.state.trendChart = echarts.init(trendEl);
                EventApp.state.trendChart.setOption({
                    grid: { left: '3%', right: '8%', top: '15%', bottom: '10%', containLabel: true },
                    tooltip: {
                        trigger: 'axis',
                        backgroundColor: '#fff',
                        borderColor: '#e4e7ed',
                        textStyle: { color: '#f56c6c', fontSize: 11, fontFamily: 'monospace' }
                    },
                    xAxis: {
                        type: 'category',
                        boundaryGap: false,
                        data: trendLabels,
                        axisLine: { show: false },
                        axisTick: { show: false },
                        axisLabel: { color: '#9ca3af', fontSize: 10, fontFamily: 'monospace', interval: 5 }
                    },
                    yAxis: {
                        type: 'value',
                        min: 0,
                        splitLine: { show: false },
                        axisLabel: { color: '#9ca3af', fontSize: 10, fontFamily: 'monospace' }
                    },
                    series: [{
                        type: 'line',
                        smooth: true,
                        showSymbol: false,
                        lineStyle: { width: 2, color: '#409EFF' },
                        areaStyle: {
                            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                                { offset: 0, color: 'rgba(64,158,255,0.3)' },
                                { offset: 1, color: 'rgba(64,158,255,0)' }
                            ])
                        },
                        markPoint: {
                            symbol: 'circle', symbolSize: 8,
                            itemStyle: { color: '#F5222D' },
                            data: [{ type: 'max', name: '峰值', label: { formatter: p => '峰值 ' + p.value, color: '#F5222D', fontSize: 10, offset: [0, -12] } }]
                        },
                        data: trendValues
                    }]
                });
            }
        }
    },

    filters: {
        applyFilter: function() {
            const typeFilter = document.getElementById('type-filter');
            const searchInput = document.getElementById('ev-search');
            if (!typeFilter || !searchInput) return;

            const typeQ = typeFilter.value;
            const searchQ = searchInput.value.toLowerCase().trim();
            const trs = document.querySelectorAll('#ev-tbody tr');

            trs.forEach(tr => {
                if (tr.children.length === 1 && tr.innerText.includes('暂无')) return; 
                const deviceCell = tr.querySelector('.device-cell');
                const deviceName = deviceCell ? deviceCell.textContent.toLowerCase() : '';
                let rawTypeStr = tr.children[2] ? tr.children[2].textContent : '';
                rawTypeStr = rawTypeStr.trim();

                let matches = true;
                if (typeQ === 'HIGH_CURRENT' && !rawTypeStr.includes('过电流')) matches = false;
                if (typeQ === 'HIGH_POWER' && !rawTypeStr.includes('过载')) matches = false;
                if (typeQ === 'LOW_VELOCITY' && !rawTypeStr.includes('速度')) matches = false;
                if (searchQ && !deviceName.includes(searchQ)) matches = false;

                tr.style.display = matches ? '' : 'none';
            });
        }
    },

    utils: {
        // Observers managed in ui.init
    },

    ui: {
        init: function() {
            console.log('[EventApp] Engine Triggered');
            
            // 1. One-time Global Init
            if (!EventApp.state.oneTimeInited) {
                window.addEventListener('resize', () => {
                    if (EventApp.state.statsChart) EventApp.state.statsChart.resize();
                    if (EventApp.state.trendChart) EventApp.state.trendChart.resize();
                });
                if (window.ResizeObserver) {
                    EventApp.state.resizeObserver = new ResizeObserver(() => {
                        if (EventApp.state.statsChart) EventApp.state.statsChart.resize();
                        if (EventApp.state.trendChart) EventApp.state.trendChart.resize();
                    });
                }
                EventApp.state.oneTimeInited = true;
            }

            // 2. Fast DOM Init
            setTimeout(() => {
                const statsEl = document.getElementById('statsChart');
                const trendEl = document.getElementById('trendChart');
                if (!statsEl || !trendEl) return;

                if (EventApp.state.resizeObserver) {
                    EventApp.state.resizeObserver.observe(statsEl);
                    EventApp.state.resizeObserver.observe(trendEl);
                }

                if (EventApp.state.statsChart) EventApp.state.statsChart.dispose();
                if (EventApp.state.trendChart) EventApp.state.trendChart.dispose();
                
                EventApp.charts.init();

                // 立即触发一次强制调整以确保渲染正确
                const forceResize = () => {
                    if (EventApp.state.statsChart) EventApp.state.statsChart.resize();
                    if (EventApp.state.trendChart) EventApp.state.trendChart.resize();
                };
                requestAnimationFrame(forceResize);
                setTimeout(forceResize, 100); 
            }, 50);
        }
    }
};

document.addEventListener('DOMContentLoaded', () => {
    if (window.location.pathname.includes('/events/')) {
        EventApp.ui.init();
    }
});
