(function () {
  const { money, make, axisX, axisY, sparkline, SERIES, CAT, C } = SO;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const carrierName = { spark: 'Spark', one: 'One NZ', '2degrees': '2degrees' };
  let A = null, tab = 'overview', heatMetric = 'spend', sortKey = 'avg', sortDir = -1, search = '';

  async function load() {
    const params = new URLSearchParams();
    if (SO_AS) params.set('as_email', SO_AS);
    if ($('fAccount').value) params.set('account', $('fAccount').value);
    A = await (await fetch('/api/analysis?' + params)).json();
    const pending = (A.invoices || []).filter(i => ['queued', 'processing'].includes(i.status)).length;
    $('pendingNote').classList.toggle('hidden', !pending);
    if (pending) { $('pendingNote').textContent = 'Analysing ' + pending + ' bill' + (pending > 1 ? 's' : '') + '... this page will update automatically.'; setTimeout(load, 5000); }
    if (!A.has_data) { $('emptyState').classList.toggle('hidden', !!pending); $('dash').classList.add('hidden'); renderBills(); return; }
    $('emptyState').classList.add('hidden'); $('dash').classList.remove('hidden');
    if ($('fAccount').options.length === 1 && A.accounts.length > 1) {
      A.accounts.forEach(a => { const o = document.createElement('option'); o.value = a.number || ''; o.textContent = a.carrier + ' ' + (a.number || '') + (a.name ? ' - ' + a.name : ''); $('fAccount').appendChild(o); });
    }
    if (A.accounts.length < 2) $('fAccount').classList.add('hidden');
    renderHeader(); renderTab();
  }

  function renderHeader() {
    const k = A.kpi;
    $('headline').textContent = money(k.monthly) + ' a month across ' + k.lines + ' line' + (k.lines === 1 ? '' : 's');
    $('subline').textContent = k.bills + ' bill' + (k.bills === 1 ? '' : 's') + ' analysed · ' + A.month_labels[0] + (A.months.length > 1 ? ' to ' + A.month_labels[A.month_labels.length - 1] : '') + ' · all amounts excl GST';
    const delta = k.monthly_prev != null ? k.monthly - k.monthly_prev : null;
    $('kpis').innerHTML = [
      ['Latest monthly spend', money(k.monthly), delta == null ? 'Add another month to see the trend' : (Math.abs(delta) < 1 ? '<span>No change</span>' : '<span class="' + (delta > 0 ? 'delta-up' : 'delta-down') + '">' + (delta > 0 ? '+' : '') + money(delta) + '</span>') vs previous month', true],
      ['Average per line', money(k.per_line, 2), k.lines + ' active lines'],
      ['Yearly run-rate', money(k.annual), 'at current spend'],
      ['Potential savings', money(k.savings_year), 'per year, from ' + A.insights.filter(i => i.saving > 0).length + ' findings'],
    ].map(x => '<div class="kpi' + (x[3] ? ' accent' : '') + '"><div class="label">' + x[0] + '</div><div class="value">' + x[1] + '</div><div class="sub">' + x[2] + '</div></div>').join('');
  }

  function renderTab() {
    document.querySelectorAll('[data-p]').forEach(s => s.classList.toggle('hidden', s.dataset.p !== tab));
    ({ overview: renderOverview, people: renderPeople, fit: renderFit, savings: renderSavings, hardware: renderHardware, bills: renderBills })[tab]();
    setTimeout(() => document.querySelectorAll('[data-p="' + tab + '"] .chart').forEach(el => { const c = echarts.getInstanceByDom(el); c && c.resize(); }), 30);
  }
  document.querySelectorAll('#tabs button').forEach(b => b.onclick = () => {
    document.querySelectorAll('#tabs button').forEach(x => x.classList.remove('on')); b.classList.add('on'); tab = b.dataset.t; renderTab();
  });
  $('fAccount').onchange = load;

  // ---- Overview
  function renderOverview() {
    const cats = Object.keys(CAT).filter(c => A.category_month[c] && A.category_month[c].some(v => Math.abs(v) > 0.005));
    make('catChart', {
      tooltip: Object.assign({}, SO.base.tooltip, { trigger: 'axis', valueFormatter: v => money(v, 2) }),
      legend: { bottom: 0, icon: 'circle', itemWidth: 9, textStyle: { color: C.ink2 } },
      grid: { left: 8, right: 16, top: 16, bottom: 36, containLabel: true },
      xAxis: axisX(A.month_labels), yAxis: axisY(),
      series: cats.map(c => ({ name: CAT[c][0], type: 'bar', stack: c === 'discount' ? 'd' : 's', barMaxWidth: 46, data: A.category_month[c], itemStyle: { color: CAT[c][1] } }))
    });
    const last = A.months.length - 1;
    const mix = cats.filter(c => c !== 'discount').map(c => ({ name: CAT[c][0], value: +(A.category_month[c][last] || 0).toFixed(2), itemStyle: { color: CAT[c][1] } })).filter(d => d.value > 0);
    const total = mix.reduce((s, d) => s + d.value, 0);
    $('mixNote').textContent = A.month_labels[last] + ' bill, ' + money(total) + ' before discounts';
    const planShare = mix.find(d => d.name === 'Plans');
    make('mixChart', {
      tooltip: Object.assign({}, SO.base.tooltip, { trigger: 'item', valueFormatter: v => money(v, 2) }),
      title: { text: planShare ? Math.round(100 * planShare.value / total) + '%' : '', subtext: planShare ? 'on plans' : '', left: 'center', top: '38%', textStyle: { fontSize: 28, fontWeight: 800, color: C.navy }, subtextStyle: { color: C.ink2, fontSize: 13 } },
      series: [{ type: 'pie', radius: ['55%', '80%'], center: ['50%', '48%'], avoidLabelOverlap: true, label: { show: false }, data: mix, itemStyle: { borderColor: '#fff', borderWidth: 2 } }],
      legend: { bottom: 0, icon: 'circle', itemWidth: 9, textStyle: { color: C.ink2 } }
    });
    // Pareto: cumulative share of spend by line
    const sorted = A.lines.slice().sort((a, b) => b.avg - a.avg);
    const sum = sorted.reduce((s, l) => s + l.avg, 0) || 1;
    let run = 0;
    const cum = sorted.map(l => (run += l.avg, +(100 * run / sum).toFixed(1)));
    make('paretoChart', {
      tooltip: Object.assign({}, SO.base.tooltip, { trigger: 'axis' }),
      grid: { left: 8, right: 8, top: 24, bottom: 8, containLabel: true },
      xAxis: Object.assign(axisX(sorted.map(l => l.label)), { axisLabel: { show: sorted.length <= 12, color: C.ink2, interval: 0, rotate: 30, fontSize: 11 } }),
      yAxis: [axisY(), { type: 'value', max: 100, splitLine: { show: false }, axisLabel: { formatter: '{value}%', color: C.ink3 } }],
      series: [
        { name: 'Avg monthly cost', type: 'bar', data: sorted.map(l => l.avg), itemStyle: { color: C.blue, borderRadius: [4, 4, 0, 0] }, barMaxWidth: 28, tooltip: { valueFormatter: v => money(v, 2) } },
        { name: 'Cumulative share', type: 'line', yAxisIndex: 1, data: cum, smooth: true, symbol: 'none', lineStyle: { color: C.orange, width: 2.5 }, itemStyle: { color: C.orange }, tooltip: { valueFormatter: v => v + '%' } }
      ]
    });
    $('saveBig').textContent = money(A.kpi.savings_year);
    $('saveNote').textContent = 'a year (' + money(A.kpi.savings_month) + '/month) - an estimate based on your bills';
    $('saveList').innerHTML = A.insights.filter(i => i.saving > 0).map(i => '<div style="display:flex;justify-content:space-between;padding:8px 0;border-top:1px solid rgba(181,71,10,.15);font-size:14px"><span>' + esc(i.title) + '</span><b>' + money(i.saving * 12) + '/yr</b></div>').join('');
  }

  // ---- People & lines
  function renderPeople() {
    const lines = A.lines.slice(0, 60);
    const vals = [];
    lines.forEach((l, yi) => A.months.forEach((m, xi) => {
      const v = heatMetric === 'spend' ? l.spend[xi] : l.data[xi];
      if (v != null) vals.push([xi, yi, v]);
    }));
    const max = Math.max(1, ...vals.map(v => v[2]));
    const el = $('heatChart');
    el.style.height = Math.max(260, 34 * lines.length + 80) + 'px';
    const ch = make(el, {
      tooltip: Object.assign({}, SO.base.tooltip, { formatter: p => esc(lines[p.value[1]].label) + '<br>' + A.month_labels[p.value[0]] + ': <b>' + (heatMetric === 'spend' ? money(p.value[2], 2) : p.value[2] + ' GB') + '</b>' }),
      grid: { left: 8, right: 16, top: 8, bottom: 50, containLabel: true },
      xAxis: Object.assign(axisX(A.month_labels), { position: 'top', splitArea: { show: false } }),
      yAxis: { type: 'category', data: lines.map(l => l.label), inverse: true, axisTick: { show: false }, axisLine: { show: false }, axisLabel: { color: C.ink } },
      visualMap: { min: 0, max, calculable: false, orient: 'horizontal', left: 'center', bottom: 0, itemWidth: 12, itemHeight: 160, text: [heatMetric === 'spend' ? money(max) : max.toFixed(0) + ' GB', '0'], inRange: { color: ['#F3FAFD', '#B3E2F3', '#4DBBE4', '#009ED8', '#003660'] }, textStyle: { color: C.ink2 } },
      series: [{ type: 'heatmap', data: vals.map(v => ({ value: v, label: { color: v[2] > max * 0.45 ? '#fff' : '#1D292E' } })), label: { show: A.months.length <= 8, formatter: p => heatMetric === 'spend' ? '$' + Math.round(p.value[2]) : p.value[2].toFixed(1), fontSize: 11 }, itemStyle: { borderColor: '#fff', borderWidth: 3, borderRadius: 4 } }]
    });
    ch.off('click'); ch.on('click', p => openLine(lines[p.value[1]]));
    ch.resize();
    renderLineTable();
  }
  document.querySelectorAll('#heatSeg button').forEach(b => b.onclick = () => {
    document.querySelectorAll('#heatSeg button').forEach(x => x.classList.remove('on')); b.classList.add('on'); heatMetric = b.dataset.m; renderPeople();
  });
  $('lineSearch').oninput = (e) => { search = e.target.value.toLowerCase(); renderLineTable(); };

  function renderLineTable() {
    const cols = [['label', 'Person / number'], ['plan', 'Plan'], ['avg', 'Avg / month', 'r'], ['trend', 'Trend'], ['avg_data_gb', 'Data / month', 'r'], ['extras', 'Extras / month', 'r'], ['flags', 'Flags']];
    const n = Math.max(A.months.length, 1);
    let rows = A.lines.map(l => Object.assign({ extras: (l.roaming + l.usage + l.addon) / n }, l));
    if (search) rows = rows.filter(l => (l.label + ' ' + l.number + ' ' + (l.plan || '')).toLowerCase().includes(search));
    rows.sort((a, b) => { const x = a[sortKey], y = b[sortKey]; return (typeof x === 'string' || typeof y === 'string' ? String(x || '').localeCompare(String(y || '')) : (x || 0) - (y || 0)) * sortDir; });
    $('lineTable').innerHTML = '<tr>' + cols.map(c => '<th class="' + (c[2] || '') + (c[0] !== 'trend' && c[0] !== 'flags' ? ' sort' : '') + '" data-k="' + c[0] + '">' + c[1] + (sortKey === c[0] ? (sortDir < 0 ? ' ↓' : ' ↑') : '') + '</th>').join('') + '</tr>' +
      rows.map(l => '<tr class="click" data-n="' + esc(l.number) + '"><td class="who">' + esc(l.label) + '<span class="tiny">' + esc(l.display) + ' · ' + esc(l.carrier) + '</span></td><td>' + esc(l.plan || '-') + '</td>' +
        '<td class="r num"><b>' + money(l.avg, 2) + '</b></td><td>' + sparkline(l.spend) + '</td>' +
        '<td class="r num">' + (l.avg_data_gb == null ? '-' : l.avg_data_gb + ' GB') + (l.allowance_gb ? '<span class="tiny">of ' + (l.allowance_gb === -1 ? 'unlimited' : l.allowance_gb + ' GB') + '</span>' : '') + '</td>' +
        '<td class="r num">' + (l.extras > 0.005 ? money(l.extras, 2) : '-') + '</td>' +
        '<td>' + l.flags.map(f => '<span class="flag ' + f + '">' + f + '</span>').join('') + '</td></tr>').join('');
    $('lineTable').querySelectorAll('th.sort').forEach(th => th.onclick = () => { const k = th.dataset.k; sortDir = sortKey === k ? -sortDir : -1; sortKey = k; renderLineTable(); });
    $('lineTable').querySelectorAll('tr.click').forEach(tr => tr.onclick = () => openLine(A.lines.find(l => l.number === tr.dataset.n)));
  }

  function openLine(l) {
    if (!l) return;
    const n = Math.max(A.months.length, 1);
    $('drawerBody').innerHTML = '<div class="eyebrow" style="margin-bottom:6px">' + esc(l.carrier) + ' · ' + esc(l.display) + '</div><h2>' + esc(l.label) + '</h2><p class="muted">' + esc(l.plan || 'Plan not shown') + '</p>' +
      '<div class="mini-kpis"><div><b>' + money(l.avg, 2) + '</b><span>avg per month</span></div><div><b>' + (l.avg_data_gb == null ? '-' : l.avg_data_gb + ' GB') + '</b><span>data per month</span></div><div><b>' + money((l.roaming + l.usage + l.addon) / n, 2) + '</b><span>extras per month</span></div></div>' +
      (l.flags.length ? '<p>' + l.flags.map(f => '<span class="flag ' + f + '">' + f + '</span>').join('') + '</p>' : '') +
      '<h3 style="margin-top:18px">Cost by month</h3><div id="dSpend" class="chart short"></div><h3 style="margin-top:10px">Data by month</h3><div id="dData" class="chart short"></div>';
    $('drawer').classList.remove('hidden'); $('drawerBg').classList.remove('hidden');
    make('dSpend', { tooltip: Object.assign({}, SO.base.tooltip, { trigger: 'axis', valueFormatter: v => money(v, 2) }), xAxis: axisX(A.month_labels), yAxis: axisY(), series: [{ type: 'bar', data: l.spend, itemStyle: { color: C.navy, borderRadius: [4, 4, 0, 0] }, barMaxWidth: 34 }] });
    const allowance = l.allowance_gb && l.allowance_gb > 0 ? l.allowance_gb : null;
    make('dData', { tooltip: Object.assign({}, SO.base.tooltip, { trigger: 'axis', valueFormatter: v => v == null ? '-' : v + ' GB' }), xAxis: axisX(A.month_labels), yAxis: axisY(v => v + ' GB'),
      series: [{ type: 'bar', data: l.data, itemStyle: { color: C.blue, borderRadius: [4, 4, 0, 0] }, barMaxWidth: 34, markLine: allowance ? { symbol: 'none', data: [{ yAxis: allowance, name: 'Allowance' }], lineStyle: { color: C.orange, type: 'dashed' }, label: { formatter: 'Allowance ' + allowance + ' GB', color: C.orange } } : undefined }] });
  }
  const closeDrawer = () => { $('drawer').classList.add('hidden'); $('drawerBg').classList.add('hidden'); };
  $('drawerClose').onclick = closeDrawer; $('drawerBg').onclick = closeDrawer;
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDrawer(); });

  // ---- Plan fit
  function renderFit() {
    const pts = A.lines.filter(l => l.util != null || (l.allowance_gb === -1 && l.avg_data_gb != null));
    const colour = (l) => l.flags.includes('idle') ? C.orange : l.flags.includes('oversized') ? C.blueM : l.flags.includes('undersized') ? '#E0A800' : C.navy;
    const maxAvg = Math.max(1, ...A.lines.map(l => l.avg));
    const ch = make('fitChart', {
      tooltip: Object.assign({}, SO.base.tooltip, { formatter: p => { const l = p.data.l; return '<b>' + esc(l.label) + '</b><br>' + esc(l.plan || '') + '<br>' + money(l.avg, 2) + '/mth · ' + (l.util != null ? Math.round(l.util * 100) + '% of ' + l.allowance_gb + ' GB' : l.avg_data_gb + ' GB on unlimited'); } }),
      grid: { left: 8, right: 24, top: 24, bottom: 30, containLabel: true },
      xAxis: { type: 'value', name: 'Share of data allowance used', nameLocation: 'middle', nameGap: 30, nameTextStyle: { color: C.ink2 }, splitLine: { lineStyle: { color: '#F1F1F2' } }, axisLabel: { formatter: v => v + '%', color: C.ink3 } },
      yAxis: Object.assign(axisY(), { name: 'Avg monthly cost', nameTextStyle: { color: C.ink2 } }),
      series: [{
        type: 'scatter', data: pts.map(l => ({ value: [l.util != null ? Math.round(l.util * 100) : 100, l.avg], l, symbolSize: 12 + 26 * (l.avg / maxAvg), itemStyle: { color: colour(l), opacity: .85, borderColor: '#fff', borderWidth: 1.5 } })),
        markArea: { silent: true, itemStyle: { color: 'rgba(0,158,216,.06)' }, data: [[{ xAxis: 0 }, { xAxis: 30 }]] },
        markLine: { silent: true, symbol: 'none', lineStyle: { color: C.orange, type: 'dashed' }, label: { formatter: 'Allowance', color: C.orange }, data: [{ xAxis: 100 }] }
      }]
    });
    ch.off('click'); ch.on('click', p => openLine(p.data.l));
    const plans = {};
    A.lines.forEach(l => { const k = (l.carrier || '') + ' · ' + (l.plan || 'Unknown plan'); (plans[k] = plans[k] || []).push(l); });
    $('planTable').innerHTML = '<tr><th>Plan</th><th class="r">Lines</th><th class="r">Avg cost</th><th class="r">Avg data</th></tr>' + Object.entries(plans).sort((a, b) => b[1].length - a[1].length).map(([k, ls]) => {
      const d = ls.filter(l => l.avg_data_gb != null);
      return '<tr><td>' + esc(k) + '</td><td class="r">' + ls.length + '</td><td class="r num">' + money(ls.reduce((s, l) => s + l.avg, 0) / ls.length, 2) + '</td><td class="r num">' + (d.length ? (d.reduce((s, l) => s + l.avg_data_gb, 0) / d.length).toFixed(1) + ' GB' : '-') + '</td></tr>';
    }).join('');
    const capped = A.lines.filter(l => l.util != null).sort((a, b) => b.util - a.util);
    $('utilList').innerHTML = capped.length ? capped.map(l => '<div style="display:flex;align-items:center;gap:12px;padding:7px 0;border-bottom:1px solid #F1F1F2;font-size:14px"><span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + esc(l.label) + '</span><div class="util' + (l.util > 1 ? ' over' : '') + '"><span style="width:' + Math.min(100, l.util * 100) + '%"></span></div><b class="num" style="width:48px;text-align:right">' + Math.round(l.util * 100) + '%</b></div>').join('') : '<p class="muted small">No capped data plans found.</p>';
  }

  // ---- Savings
  function renderSavings() {
    $('insights').innerHTML = A.insights.map(i => '<div class="insight"><div class="k">' + esc(i.title) + '</div><div class="h">' + esc(i.headline) + '</div><div class="b">' + esc(i.body) + '</div>' +
      (i.saving > 0 ? '<div class="save">Save up to ' + money(i.saving * 12) + ' a year</div>' : '') +
      (i.items.length ? '<ul>' + i.items.map(it => '<li><span><b>' + esc(it.label) + '</b><br><span class="d">' + esc(it.number) + ' · ' + esc(it.detail || '') + '</span></span><span class="num" style="text-align:right;white-space:nowrap">' + esc(it.value) + '</span></li>').join('') + '</ul>' : '') + '</div>').join('');
  }

  // ---- Hardware
  function renderHardware() {
    const d = A.devices;
    const total = d.reduce((s, x) => s + (x.balance || 0), 0), monthly = d.reduce((s, x) => s + (x.monthly || 0), 0);
    $('hwTotal').innerHTML = d.length ? '<div class="tiny">Remaining to pay</div><div style="font-size:26px;font-weight:800;color:#003660">' + money(total) + '</div><div class="tiny">' + money(monthly, 2) + '/month</div>' : '';
    if (!d.length) { $('hwChart').innerHTML = '<p class="muted" style="padding:20px 0">No hardware repayments found on your bills.</p>'; $('hwTable').innerHTML = ''; return; }
    const withMonths = d.filter(x => x.months_left != null);
    make('hwChart', {
      tooltip: Object.assign({}, SO.base.tooltip, { trigger: 'axis', axisPointer: { type: 'shadow' } }),
      grid: { left: 8, right: 40, top: 10, bottom: 8, containLabel: true },
      xAxis: { type: 'value', name: 'months left', splitLine: { lineStyle: { color: '#F1F1F2' } }, axisLabel: { color: C.ink3 } },
      yAxis: { type: 'category', data: withMonths.map(x => (x.number || '') + ' ' + (x.description || '').slice(0, 26)), axisTick: { show: false }, axisLine: { show: false }, axisLabel: { color: C.ink } },
      series: [{ type: 'bar', data: withMonths.map(x => x.months_left), itemStyle: { color: C.navy, borderRadius: [0, 6, 6, 0] }, barMaxWidth: 20, label: { show: true, position: 'right', formatter: p => p.value + ' mths', color: C.ink2 } }]
    });
    $('hwTable').innerHTML = '<tr><th>Line</th><th>Device</th><th class="r">Monthly</th><th class="r">Payments left</th><th class="r">Balance</th></tr>' + d.map(x => '<tr><td>' + esc(x.number) + '</td><td>' + esc(x.description) + '</td><td class="r num">' + money(x.monthly, 2) + '</td><td class="r">' + (x.months_left == null ? '-' : x.months_left) + '</td><td class="r num">' + money(x.balance) + '</td></tr>').join('');
  }

  // ---- Bills
  function renderBills() {
    const inv = (A && A.invoices) || [];
    $('billTable').innerHTML = '<tr><th>File</th><th>Carrier</th><th>Account</th><th>Period</th><th class="r">Total excl GST</th><th>Status</th></tr>' +
      inv.slice().reverse().map(i => '<tr><td>' + (i.status === 'done' || i.status === 'duplicate' ? '<a href="/invoice/' + i.id + '.pdf" target="_blank">' + esc(i.filename) + '</a>' : esc(i.filename)) + '</td><td>' + esc(carrierName[i.carrier] || '-') + '</td><td>' + esc(i.account_name || '') + '<span class="tiny">' + esc(i.account_number || '') + '</span></td><td>' + esc(i.period_month || '-') + '</td><td class="r num">' + money(i.total_excl_gst, 2) + '</td><td><span class="status ' + i.status + '">' + i.status + '</span>' + (i.error ? '<span class="tiny">' + esc(i.error) + '</span>' : '') + '</td></tr>').join('');
  }

  // ---- Add more bills from the dashboard
  $('addBills').onchange = async (e) => {
    const fd = new FormData();
    [...e.target.files].forEach(f => fd.append('files', f, f.name));
    if (![...fd.keys()].length) return;
    const r = await fetch('/api/upload', { method: 'POST', body: fd });
    e.target.value = '';
    if (r.ok) load();
  };

  load();
})();
