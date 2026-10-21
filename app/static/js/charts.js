// Shared chart helpers (Apache ECharts) for the results page, dashboard and Ask AI.
window.SO = (function () {
  const C = {
    navy: '#003660', blue: '#009ED8', blueD: '#0070BF', blueM: '#4DBBE4', blueL: '#B3E2F3', orange: '#F1600D',
    ink: '#1D292E', ink2: '#61696D', ink3: '#8E9699', line: '#E4E5E6', grey: '#BBBFC0', yellow: '#FFC528'
  };
  const SERIES = ['#003660', '#009ED8', '#F1600D', '#4DBBE4', '#61696D', '#0070BF', '#FFC528', '#8E9699', '#B3E2F3', '#7FCBEB', '#F59A63', '#3D6A8F'];
  const CAT = {
    plan: ['Plans', '#003660'], addon: ['Add-ons', '#009ED8'], usage: ['Out-of-plan', '#4DBBE4'], roaming: ['Roaming', '#F1600D'],
    hardware: ['Hardware', '#8E9699'], one_off: ['One-off', '#BBBFC0'], account: ['Account', '#B3E2F3'], other: ['Other', '#E4E5E6'],
    discount: ['Discounts', '#00875A']
  };
  const money = (v, dp) => v == null ? '-' : (v < 0 ? '-$' : '$') + Math.abs(v).toLocaleString('en-NZ', { minimumFractionDigits: dp || 0, maximumFractionDigits: dp || 0 });
  const base = {
    textStyle: { fontFamily: 'Figtree, sans-serif', color: C.ink2 },
    grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
    tooltip: { backgroundColor: '#fff', borderColor: C.line, borderWidth: 1, textStyle: { color: C.ink, fontSize: 13 }, extraCssText: 'border-radius:10px;box-shadow:0 8px 24px rgba(0,0,0,.08);' },
  };
  const axisX = (data) => ({ type: 'category', data, axisLine: { lineStyle: { color: C.line } }, axisTick: { show: false }, axisLabel: { color: C.ink2 } });
  const axisY = (fmt) => ({ type: 'value', splitLine: { lineStyle: { color: '#F1F1F2' } }, axisLabel: { color: C.ink3, formatter: fmt || (v => money(v)) } });
  const charts = [];
  function make(el, opt) {
    if (typeof el === 'string') el = document.getElementById(el);
    if (!el) return null;
    let ch = echarts.getInstanceByDom(el);
    if (!ch) { ch = echarts.init(el, null, { renderer: 'svg' }); charts.push(ch); }
    ch.setOption(Object.assign({}, base, opt), true);
    return ch;
  }
  window.addEventListener('resize', () => charts.forEach(c => c.resize()));

  function sparkline(values, w, h, color) {
    w = w || 110; h = h || 32; color = color || C.blue;
    const v = values.map(x => x == null ? null : +x);
    const nums = v.filter(x => x != null);
    if (nums.length < 2) return '<svg class="spark" viewBox="0 0 ' + w + ' ' + h + '"></svg>';
    const min = Math.min(...nums), max = Math.max(...nums), rng = (max - min) || 1;
    const pts = v.map((x, i) => x == null ? null : [(i / (v.length - 1)) * (w - 4) + 2, h - 3 - ((x - min) / rng) * (h - 6)]).filter(Boolean);
    const d = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
    const last = pts[pts.length - 1];
    return '<svg class="spark" viewBox="0 0 ' + w + ' ' + h + '"><path d="' + d + '" fill="none" stroke="' + color + '" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/><circle cx="' + last[0] + '" cy="' + last[1] + '" r="2.8" fill="' + color + '"/></svg>';
  }

  // Generic chart from the Ask AI JSON spec
  function fromSpec(el, spec) {
    const type = spec.type || 'bar';
    if (type === 'pie') {
      const s = (spec.series || [])[0] || { data: [] };
      return make(el, {
        color: SERIES, tooltip: Object.assign({}, base.tooltip, { trigger: 'item', valueFormatter: v => money(v, 2) }),
        title: { text: spec.title || '', textStyle: { fontSize: 13, color: C.navy }, left: 0 },
        series: [{ type: 'pie', radius: ['45%', '72%'], center: ['50%', '57%'], label: { fontSize: 11 }, data: (spec.labels || []).map((l, i) => ({ name: l, value: s.data[i] })) }]
      });
    }
    return make(el, {
      color: SERIES,
      title: { text: spec.title || '', textStyle: { fontSize: 13, color: C.navy }, left: 0 },
      grid: { left: 4, right: 10, top: 34, bottom: (spec.series || []).length > 1 ? 30 : 4, containLabel: true },
      legend: (spec.series || []).length > 1 ? { bottom: 0, icon: 'circle', itemWidth: 8, textStyle: { fontSize: 11 } } : undefined,
      tooltip: Object.assign({}, base.tooltip, { trigger: 'axis' }),
      xAxis: axisX(spec.labels || []), yAxis: axisY(v => v >= 1000 ? (v / 1000) + 'k' : v),
      series: (spec.series || []).map(s => ({ name: s.name, type: type === 'line' ? 'line' : 'bar', stack: type === 'stacked' ? 'a' : undefined, data: s.data, smooth: true, barMaxWidth: 26, itemStyle: { borderRadius: type === 'stacked' ? 0 : [4, 4, 0, 0] } }))
    });
  }

  return { C, SERIES, CAT, money, make, axisX, axisY, sparkline, fromSpec, base };
})();
