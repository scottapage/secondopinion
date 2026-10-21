(function () {
  const { money, make, axisX, axisY, sparkline, SERIES, C } = SO;
  const $ = (id) => document.getElementById(id);
  const TICK = '<svg class="tick" viewBox="0 0 20 20"><circle cx="10" cy="10" r="10" fill="#E3F4EC"/><path d="M6 10.5l2.6 2.6L14 7.6" fill="none" stroke="#00875A" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const WARN = '<svg class="tick" viewBox="0 0 20 20"><circle cx="10" cy="10" r="10" fill="#FEF2E5"/><path d="M10 5.5v5.5M10 14v.3" stroke="#F1600D" stroke-width="2.2" stroke-linecap="round"/></svg>';
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const carrierName = { spark: 'Spark', one: 'One NZ', '2degrees': '2degrees' };

  function progressRow(i) {
    let icon = '<span class="spinner"></span>', meta = i.status === 'queued' ? 'Waiting...' : 'Reading the bill...';
    if (i.status === 'done') { icon = TICK; meta = [carrierName[i.carrier], i.account_name, i.line_count + ' lines', money(i.total_excl_gst, 2)].filter(Boolean).join(' · '); }
    if (i.status === 'duplicate') { icon = TICK; meta = 'Already analysed'; }
    if (i.status === 'rejected' || i.status === 'failed') { icon = WARN; meta = i.error || 'Could not be read'; }
    return '<div class="prog">' + icon + '<div style="flex:1;min-width:0"><div class="name">' + esc(i.filename) + '</div><div class="meta">' + esc(meta) + '</div></div></div>';
  }

  async function poll() {
    const r = await fetch('/api/submission/' + SO_TOKEN);
    if (!r.ok) { setTimeout(poll, 4000); return; }
    const j = await r.json();
    $('progress').innerHTML = j.invoices.map(progressRow).join('');
    const n = j.invoices.length, done = j.invoices.filter(i => !['queued', 'processing'].includes(i.status)).length;
    $('procTitle').textContent = j.pending ? 'Reading your bills... (' + done + ' of ' + n + ')' : 'Done';
    if (j.pending) { setTimeout(poll, 2500); return; }
    if (SO_LOGGED_IN) { window.location = '/dashboard'; return; }
    loadAnalysis(j.invoices);
  }

  let A, metric = 'spend', selected = new Set();

  async function loadAnalysis(subInvoices) {
    A = await (await fetch('/api/analysis')).json();
    if (!A.has_data) {
      $('processing').classList.add('hidden'); $('nothing').classList.remove('hidden');
      $('problems').innerHTML = subInvoices.map(progressRow).join('');
      return;
    }
    $('processing').classList.add('hidden'); $('analysis').classList.remove('hidden');
    const k = A.kpi;
    $('headline').textContent = 'You spend ' + money(k.monthly) + ' a month on ' + k.lines + ' mobile line' + (k.lines === 1 ? '' : 's') + '.';
    const accts = A.accounts.map(a => a.carrier + ' ' + (a.number || '')).join(', ');
    $('subline').textContent = 'Based on ' + k.bills + ' bill' + (k.bills === 1 ? '' : 's') + ' (' + A.month_labels[0] + (A.months.length > 1 ? ' to ' + A.month_labels[A.month_labels.length - 1] : '') + ') · ' + accts;
    const delta = k.monthly_prev ? (k.monthly - k.monthly_prev) : null;
    $('kpis').innerHTML = [
      ['Monthly spend', money(k.monthly), delta == null ? 'excl GST' : (Math.abs(delta) < 1 ? '<span>No change</span>' : '<span class="' + (delta > 0 ? 'delta-up' : 'delta-down') + '">' + (delta > 0 ? '+' : '') + money(delta) + '</span>') + ' vs last month', true],
      ['Mobile lines', k.lines, 'on your latest bill'],
      ['Average per line', money(k.per_line), 'per month'],
      ['Yearly run-rate', money(k.annual), 'at current spend'],
    ].map(x => '<div class="kpi' + (x[3] ? ' accent' : '') + '"><div class="label">' + x[0] + '</div><div class="value">' + x[1] + '</div><div class="sub">' + x[2] + '</div></div>').join('');

    A.lines.slice(0, Math.min(6, A.lines.length)).forEach(l => selected.add(l.number));
    renderChips(); renderChart(); renderTable(); renderInsights();
    document.querySelectorAll('#metricSeg button').forEach(b => b.onclick = () => {
      document.querySelectorAll('#metricSeg button').forEach(x => x.classList.remove('on')); b.classList.add('on');
      metric = b.dataset.m; renderChart();
    });
  }

  function color(i) { return SERIES[i % SERIES.length]; }

  function renderChips() {
    const max = 24;
    $('chips').innerHTML = A.lines.slice(0, max).map((l, i) =>
      '<button class="lchip' + (selected.has(l.number) ? ' on' : '') + '" data-n="' + esc(l.number) + '"><span class="sw" style="background:' + (selected.has(l.number) ? color(i) : '#E4E5E6') + '"></span>' + esc(l.label) + '</button>').join('')
      + (A.lines.length > 1 ? '<button class="lchip" id="allBtn">' + (selected.size === Math.min(A.lines.length, max) ? 'Clear' : 'Select all') + '</button>' : '');
    $('chips').querySelectorAll('.lchip[data-n]').forEach(b => b.onclick = () => {
      const n = b.dataset.n; selected.has(n) ? selected.delete(n) : selected.add(n); renderChips(); renderChart();
    });
    const all = $('allBtn');
    if (all) all.onclick = () => {
      if (selected.size === Math.min(A.lines.length, max)) selected.clear(); else A.lines.slice(0, max).forEach(l => selected.add(l.number));
      renderChips(); renderChart();
    };
  }

  function renderChart() {
    const lines = A.lines.map((l, i) => Object.assign({ _c: color(i) }, l)).filter(l => selected.has(l.number));
    const val = (l, mi) => metric === 'data' ? l.data[mi] : metric === 'share' ? (l.spend[mi] != null && A.total_month[mi] ? +(100 * l.spend[mi] / A.total_month[mi]).toFixed(1) : null) : l.spend[mi];
    const fmt = metric === 'data' ? (v => v + ' GB') : metric === 'share' ? (v => v + '%') : (v => money(v));
    if (A.months.length === 1) {
      // One bill: compare people side by side
      const sorted = lines.slice().sort((a, b) => (val(a, 0) || 0) - (val(b, 0) || 0));
      make('lineChart', {
        tooltip: Object.assign({}, SO.base.tooltip, { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: fmt }),
        grid: { left: 8, right: 60, top: 10, bottom: 8, containLabel: true },
        xAxis: { type: 'value', splitLine: { lineStyle: { color: '#F1F1F2' } }, axisLabel: { color: C.ink3, formatter: fmt } },
        yAxis: { type: 'category', data: sorted.map(l => l.label), axisTick: { show: false }, axisLine: { show: false }, axisLabel: { color: C.ink } },
        series: [{ type: 'bar', data: sorted.map(l => ({ value: val(l, 0), itemStyle: { color: l._c, borderRadius: [0, 6, 6, 0] } })), barMaxWidth: 22, label: { show: true, position: 'right', formatter: p => fmt(p.value), color: C.ink, fontWeight: 600 } }]
      });
      return;
    }
    make('lineChart', {
      tooltip: Object.assign({}, SO.base.tooltip, { trigger: 'axis', valueFormatter: v => v == null ? '-' : fmt(v) }),
      grid: { left: 8, right: 16, top: 16, bottom: 8, containLabel: true },
      xAxis: axisX(A.month_labels), yAxis: axisY(fmt),
      series: lines.map(l => ({ name: l.label, type: 'line', smooth: 0.25, symbolSize: 7, connectNulls: true, data: A.months.map((m, i) => val(l, i)), itemStyle: { color: l._c }, lineStyle: { width: 2.5 } }))
    });
  }

  function renderTable() {
    const head = '<tr><th>Person / number</th><th>Plan</th><th class="r">Avg / month</th>' + (A.months.length > 1 ? '<th>Trend</th>' : '') + '<th class="r">Data used</th></tr>';
    const max = Math.max(...A.lines.map(l => l.avg), 1);
    $('lineTable').innerHTML = head + A.lines.map(l =>
      '<tr><td class="who">' + esc(l.label) + '<span class="tiny">' + esc(l.display) + ' · ' + esc(l.carrier) + '</span></td>' +
      '<td>' + esc(l.plan || '-') + '</td>' +
      '<td class="r num"><div class="bar-cell" style="justify-content:flex-end"><div class="bar" style="width:' + Math.max(4, 90 * l.avg / max) + 'px"></div><b>' + money(l.avg, 2) + '</b></div></td>' +
      (A.months.length > 1 ? '<td>' + sparkline(l.spend) + '</td>' : '') +
      '<td class="r num">' + (l.avg_data_gb == null ? '-' : l.avg_data_gb + ' GB') + '</td></tr>').join('');
  }

  function renderInsights() {
    const fake = ['Line ending 0101 · $65.00/mth', 'Line ending 0102 · 18% of allowance', 'Line ending 0104 · $27.40/mth extras'];
    $('insights').innerHTML = A.insights.map(i =>
      '<div class="insight locked"><div class="k">' + esc(i.title) + '</div><div class="h">' + esc(i.headline) + '</div><div class="b">' + esc(i.body) + '</div>' +
      '<ul class="blur">' + fake.map(f => '<li><span>' + f + '</span></li>').join('') + '</ul>' +
      '<div class="lock"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#003660" stroke-width="2.2"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg><a href="/register?next=/dashboard">Unlock the detail and savings</a></div></div>').join('');
  }

  poll();
})();
