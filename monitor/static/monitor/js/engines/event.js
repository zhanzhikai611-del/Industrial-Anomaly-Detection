/**
 * Event Center Engine
 * V2.2.3 [Modularized]
 */

window.EventApp = {
    state: {
        statsChart: null,
        trendChart: null,
        resizeObserver: null,
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

            // 1. Stats Chart (Horizontal Bar)
            const statsEl = document.getElementById('statsChart');
            if (statsEl && stats.length > 0) {
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
            if (trendEl && trendLabels.length > 0) {
                EventApp.state.trendChart = echarts.init(trendEl);
                EventApp.state.trendChart.setOption({
                    grid: { left: '3%', right: '8%', top: '15%', bottom: '10%', containLabel: true },
                    tooltip: {
                        trigger: 'axis',
                        formatter: '峰值 {c} @ {b}',
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
        initResize: function() {
            window.addEventListener('resize', () => {
                if (EventApp.state.statsChart) EventApp.state.statsChart.resize();
                if (EventApp.state.trendChart) EventApp.state.trendChart.resize();
            });

            if (window.ResizeObserver) {
                EventApp.state.resizeObserver = new ResizeObserver(() => {
                    if (EventApp.state.statsChart) EventApp.state.statsChart.resize();
                    if (EventApp.state.trendChart) EventApp.state.trendChart.resize();
                });

                const statsDiv = document.getElementById('statsChart');
                if (statsDiv) EventApp.state.resizeObserver.observe(statsDiv);

                const trendDiv = document.getElementById('trendChart');
                if (trendDiv) EventApp.state.resizeObserver.observe(trendDiv);
            }
        }
    }
};

// Application entry point
document.addEventListener('DOMContentLoaded', () => {
    // Note: EventApp.state.data must be populated before calling this
    EventApp.charts.init();
    EventApp.utils.initResize();
});
