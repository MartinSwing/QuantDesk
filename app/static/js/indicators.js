/* ============================================================
 * QuantDesk 主图/副图技术指标渲染（个股页 / 指数页共用）
 * 页面约定：
 *   - 副图选择下拉：#subSelect（amount/volume/macd/kdj/rsi/boll）
 *   - 图表容器：#klineChart
 * 用法：
 *   IND.apply(option, kline, indicators, sub, volUnit)
 *   option 为页面构建的基础配置（仅蜡烛主图），sub 为当前副图指标：
 *     amount=成交额(默认) / volume=成交量 / macd / kdj / rsi / boll(叠加主图)
 *   volUnit：成交量单位文案（个股"万手"、指数"亿手"）
 *   （setOption 时须传 notMerge=true）
 *
 * 索引规则（易错点）：
 *   - xAxis 数组与 grid 一一对应：grid i ↔ xAxis[i]
 *   - yAxis 不与 grid 一一对应：子图 y 轴的 yAxisIndex 必须取 push 前的
 *     option.yAxis.length，不能复用 grid 序号
 * ============================================================ */
const IND = (() => {
    const PANE_H = 130;      // 副图高度 px
    const PANE_GAP = 8;
    const MAIN_H = 320;      // 主图高度
    const ZOOM_H = 60;       // 底部 dataZoom 区域

    /* 涨跌着色的柱：按当日涨跌幅红绿 */
    function upDownBar(kline, valueOf, name, xi, yi) {
        return {
            name, type: 'bar', xAxisIndex: xi, yAxisIndex: yi,
            data: kline.map(d => {
                const v = valueOf(d);
                if (v == null) return null;
                return {value: v, itemStyle: {color: (d.pct_change ?? 0) >= 0 ? '#e53935' : '#2e7d32'}};
            }),
        };
    }

    function apply(option, kline, indicators, sub, volUnit) {
        sub = sub || 'amount';
        const hasPane = sub !== 'boll' || !indicators.boll;
        const totalH = MAIN_H + (hasPane ? PANE_H + PANE_GAP : 0) + ZOOM_H;
        $('#klineChart').height(totalH);

        // 主图 grid + 唯一价格 y 轴
        option.grid = [{left: 70, right: 40, top: 30, height: MAIN_H}];
        option.xAxis = [{
            type: 'category', data: kline.map(d => d.date), gridIndex: 0,
            axisLabel: {fontSize: 12}, axisLine: {lineStyle: {color: '#cfd8dc'}},
        }];
        option.yAxis = (option.yAxis || []).map(y => ({...y, gridIndex: y.gridIndex ?? 0}));
        option.series = option.series || [];

        // ---- BOLL 叠加主图 ----
        if (sub === 'boll' && indicators.boll) {
            const b = indicators.boll;
            const line = (name, data, color) => ({
                name, type: 'line', data, xAxisIndex: 0, yAxisIndex: 0,
                showSymbol: false, lineStyle: {color, width: 1}, itemStyle: {color},
                silent: true, emphasis: {disabled: true},
            });
            option.series.push(
                line('BOLL上轨', b.up, '#5c6bc0'),
                line('BOLL中轨', b.mid, '#f9a825'),
                line('BOLL下轨', b.low, '#5c6bc0'));
        }

        // ---- 副图 ----
        if (hasPane) {
            option.grid.push({left: 70, right: 40, top: 30 + MAIN_H + PANE_GAP, height: PANE_H});
            const xi = 1, yi = option.yAxis.length;   // yAxisIndex = 数组当前长度（关键）
            option.xAxis.push({
                type: 'category', data: kline.map(d => d.date), gridIndex: 1,
                axisLabel: {fontSize: 12}, axisLine: {lineStyle: {color: '#cfd8dc'}},
            });
            option.yAxis.push({gridIndex: 1, scale: true, axisLabel: {fontSize: 11},
                               splitLine: {lineStyle: {color: '#f5f5f5'}}});
            const line = (name, data, color) => ({
                name, type: 'line', data, xAxisIndex: xi, yAxisIndex: yi,
                showSymbol: false, lineStyle: {color, width: 1}, itemStyle: {color},
            });

            if (sub === 'amount') {
                option.series.push(upDownBar(kline, d => d.amount, '成交额(亿元)', xi, yi));
            } else if (sub === 'volume') {
                option.series.push(upDownBar(kline, d => d.volume, '成交量(' + (volUnit || '万手') + ')', xi, yi));
            } else if (sub === 'macd' && indicators.macd) {
                const m = indicators.macd;
                option.series.push({
                    name: 'MACD柱', type: 'bar', xAxisIndex: xi, yAxisIndex: yi,
                    data: m.hist.map(v => v == null ? null :
                        {value: v, itemStyle: {color: v >= 0 ? '#e53935' : '#2e7d32'}}),
                }, line('DIF', m.dif, '#1e88e5'), line('DEA', m.dea, '#f9a825'));
            } else if (sub === 'kdj' && indicators.kdj) {
                const k = indicators.kdj;
                option.series.push(
                    line('K', k.k, '#f9a825'), line('D', k.d, '#1e88e5'), line('J', k.j, '#ab47bc'));
            } else if (sub === 'rsi' && indicators.rsi) {
                const r = indicators.rsi;
                option.yAxis[option.yAxis.length - 1].min = 0;
                option.yAxis[option.yAxis.length - 1].max = 100;
                option.series.push(
                    line('RSI6', r.r6, '#e53935'), line('RSI12', r.r12, '#1e88e5'),
                    line('RSI24', r.r24, '#43a047'));
            } else {
                // 数据缺失兜底：空柱
                option.series.push({name: '暂无数据', type: 'bar', xAxisIndex: xi, yAxisIndex: yi, data: []});
            }
        }

        // dataZoom 绑定所有 x 轴（联动）
        const allX = Array.from({length: option.xAxis.length}, (_, i) => i);
        option.dataZoom = [
            {type: 'inside', xAxisIndex: allX},
            {type: 'slider', xAxisIndex: allX, height: 20, bottom: 10},
        ];
        return option;
    }

    return {apply};
})();
