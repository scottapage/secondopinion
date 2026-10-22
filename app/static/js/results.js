(function () {
  const { money, make, axisX, axisY, sparkline, SERIES, C, provColor, provBadge, scoreRing, countUp, ev } = SO;
  const $ = (id) => document.getElementById(id);
  const TICK = '<svg class="tick" viewBox="0 0 20 20"><circle cx="10" cy="10" r="10" fill="#E3F4EC"/><path d="M6 10.5l2.6 2.6L14 7.6" fill="none" stroke="#00875A" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const WARN = '<svg class="tick" viewBox="0 0 20 20"><circle cx="10" cy="10" r="10" fill="#FEF2E5"/><path d="M10 5.5v5.5M10 14v.3" stroke="#F1600D" stroke-width="2.2" stroke-linecap="round"/></svg>';
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  function progressRow(i) {
    let icon = '<span class="spinner"></span>', meta = i.status === 'queued' ? 'Waiting...' : 'Reading the bill...';
    if (i.status === 'done') { icon = TICK; meta = [i.carrier_label || SO.provName(i.carrier), i.account_name, i.line_count ? i.line_count + ' line' + (i.line_count === 1 ? '' : 's') : 'broadband', money(i.total_excl_gst, 2)].filter(Boolean).join(' · '); }
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
    A = await (await fetch(SO_EXAMPLE ? '/api/example' : '/api/analysis')).json();
    if (!A.has_data) {
      $('processing').classList.add('hidden'); $('nothing').classList.remove('hidden');
      $('problems').innerHTML = subInvoices.map(progressRow).join('');
      return;
    }
    $('processing').classList.add('hidden'); $('analysis').classList.remove('hidden');
    const k = A.kpi, provs = A.providers || [], sw = A.switch || {};
    const provText = provs.length > 1 ? provs.map(p => p.name).slice(0, -1).join(', ') + ' and ' + provs[provs.length - 1].name : (provs[0] ? provs[0].name : '');
    const bbOnly = !k.lines;
    $('headline').innerHTML = bbOnly ? 'You spend <span class="big-num" data-count="' + k.other_services + '">$0</span> a month on broadband' + (provText ? ' with ' + esc(provText) : '') + '.'
      : 'You spend <span class="big-num" data-count="' + k.monthly + '">$0</span> a month across ' + k.lines + ' line' + (k.lines === 1 ? '' : 's') + (provText ? ' with ' + esc(provText) : '') + '.';
    const parts = [bbOnly ? 'That\u2019s ' + money(k.other_services * 12) + ' a year' : 'That\u2019s ' + money(k.annual) + ' a year on mobile'];
    if (k.devices_month) parts.push(money(k.devices_month) + '/mth of it is phone repayments');
    if (k.other_services && !bbOnly) parts.push('plus ' + money(k.other_services) + '/mth of broadband and other services');
    $('subline').textContent = parts.join(' \u00b7 ') + '. ' + k.bills + ' bill' + (k.bills === 1 ? '' : 's') + ', ' + A.month_labels[0] + (A.months.length > 1 ? ' to ' + A.month_labels[A.month_labels.length - 1] : '') + ', excl GST.';
    $('provRow').innerHTML = provs.map(p => '<span class="prov-chip" style="--pc:' + provColor(p.code) + '"><i></i>' + esc(p.name) + ' <b>' + money(p.monthly) + '</b><span>' + (p.lines ? p.lines + ' line' + (p.lines === 1 ? '' : 's') : (p.services || 0) + ' broadband') + '</span></span>').join('');
    const s = A.score;
    $('scoreWrap').innerHTML = '<div class="score-ring">' + scoreRing(s.score, 148) + '<div class="score-txt"><b data-count="' + s.score + '" data-int="1">0</b><span>health score</span></div></div>' +
      '<div class="score-parts"><div class="grade">' + esc(s.grade) + '</div>' + s.parts.map(p => '<div class="sp"><div class="sp-h"><span>' + esc(p.label) + '</span><b>' + p.score + '/' + p.max + '</b></div><div class="sp-bar"><i style="width:' + (100 * p.score / p.max) + '%"></i></div>' + (SO_EXAMPLE ? '<div class="sp-n">' + esc(p.note) + '</div>' : '') + '</div>').join('') + (SO_EXAMPLE ? '' : '<div class="sp-n" style="margin-top:8px">Create an account to see what\u2019s behind each score.</div>') + '</div>';
    const delta = k.monthly_prev != null ? (k.monthly - k.monthly_prev) : null;
    $('kpis').innerHTML = [
      ['Monthly run-rate', money(k.monthly), delta == null ? 'latest bill from each account' : (Math.abs(delta) < 1 ? '<span>No change</span>' : '<span class="' + (delta > 0 ? 'delta-up' : 'delta-down') + '">' + (delta > 0 ? '+' : '') + money(delta) + '</span>') + ' vs previous bills', true],
      bbOnly ? ['Broadband connections', (A.services || []).length, 'across ' + A.accounts.length + ' account' + (A.accounts.length === 1 ? '' : 's')] : ['Mobile lines', k.lines, 'across ' + A.accounts.length + ' account' + (A.accounts.length === 1 ? '' : 's')],
      ['Phones being paid off', money(sw.hardware_balance || 0), (A.devices || []).length + ' device' + ((A.devices || []).length === 1 ? '' : 's')],
      ['On 2degrees instead', money(sw.future_month || 0) + '<span class="tiny" style="display:inline">/mth</span>', (sw.saving_year > 0 ? 'save ' + money(sw.saving_year) + '/yr like-for-like' : 'like-for-like') + (sw.optimised_saving_year > sw.saving_year ? ', up to ' + money(sw.optimised_saving_year) + '/yr right-sized' : ''), false, 'orange'],
    ].map(x => '<div class="kpi' + (x[3] ? ' accent' : '') + (x[4] ? ' ' + x[4] : '') + '"><div class="label">' + x[0] + '</div><div class="value">' + x[1] + '</div><div class="sub">' + x[2] + '</div></div>').join('');
    const best = Math.max(sw.saving_year || 0, sw.optimised_saving_year || 0);
    $('switchCard').innerHTML = '<div class="sw-left"><div class="eyebrow light">If you brought it all to 2degrees</div><div class="sw-big">' + (best > 0 ? '<span data-count="' + best + '">$0</span> a year' : 'Same price, one bill') + '</div><p>Like-for-like plans come to <b>' + money(sw.future_month) + '/mth</b> against <b>' + money(sw.current_month) + '</b> today' + (sw.optimised_saving_year > sw.saving_year ? ', or <b>' + money(sw.optimised_month) + '/mth</b> right-sized where usage allows' : '') + '. Every number on your bills is enough to move it - no forms.' + (sw.chamber ? ' Chamber of Commerce members: <b>' + sw.chamber.mobile_pct + '% off for life</b> and ' + sw.chamber.mobile_free_months + ' months free, about ' + money(sw.chamber.first_year_saving) + ' in year one.' : '') + '</p><div class="sw-actions"><a class="btn white" href="' + (SO_EXAMPLE ? '/' : '/offer') + '">' + (SO_EXAMPLE ? 'Analyse my own bills' : 'Make me an offer') + '</a>' + (SO_EXAMPLE ? '' : '<a class="link-btn light" href="/register?next=/dashboard">See it line by line \u2192</a>') + '</div></div>' +
      '<div class="sw-right"><div class="sw-cmp"><div><span>Today</span><b data-count="' + sw.current_month + '">$0</b><i>plans + add-ons /mth</i></div><div class="arrow">\u2192</div><div><span>2degrees</span><b data-count="' + sw.future_month + '">$0</b><i>like-for-like /mth</i></div></div><ul class="gains">' + (sw.gains || []).slice(0, 3).map(g => '<li>' + esc(g) + '</li>').join('') + '</ul></div>';
    const D = A.decoded, DEC_COL = ['#003660', '#009ED8', '#F1600D', '#8E9699', '#4DBBE4', '#BBBFC0'];
    make('decodedChart', { tooltip: Object.assign({}, SO.base.tooltip, { trigger: 'axis', valueFormatter: v => money(v, 2) }), legend: { bottom: 0, icon: 'circle', itemWidth: 9, textStyle: { color: C.ink2, fontSize: 11 } }, grid: { left: 8, right: 12, top: 12, bottom: 40, containLabel: true }, xAxis: axisX(A.month_labels), yAxis: axisY(),
      series: D.labels.map((name, i) => ({ name, type: 'bar', stack: i === 5 ? 'f' : 's', barMaxWidth: 44, data: D.series[i], itemStyle: { color: DEC_COL[i] } })) });
    renderSankey();
    countUp(document);

    if (bbOnly) {
      document.querySelectorAll('.mobile-only').forEach(el => el.classList.add('hidden'));
      $('lineTable').innerHTML = '<tr><th>Service</th><th>Provider</th><th>Address</th><th>Plan</th><th class="r">Avg / month</th></tr>' + (A.services || []).map(s => '<tr><td>' + esc(s.type) + '</td><td>' + provBadge(s.carrier_code, s.carrier) + '</td><td>' + esc(s.address || '-') + '</td><td>' + esc(s.plan || '-') + (s.speed ? '<span class="tiny">' + esc(s.speed) + '</span>' : '') + '</td><td class="r num"><b>' + ev(s.src, [s.src && s.src.amount], money(s.avg, 2), s.address || s.type) + '</b></td></tr>').join('');
      $('lineTableTitle').textContent = 'Your broadband';
    } else {
      A.lines.slice(0, Math.min(6, A.lines.length)).forEach(l => selected.add(l.number));
      renderChips(); renderChart(); renderTable();
    }
    renderInsights();
    const facts = (A.notes || []).filter(n => n.kind === 'fact'), plainNotes = (A.notes || []).filter(n => n.kind !== 'fact');
    if (facts.length && facts[0].body) {
      $('factsCard').classList.remove('hidden');
      $('factsList').innerHTML = facts.map(n => '<div class="fact"><div class="bulb">\ud83d\udca1</div><div><div class="h">' + esc(n.title) + '</div><p>' + esc(n.body) + '</p></div></div>').join('');
    }
    if (plainNotes.length && plainNotes[0].body) {
      $('notesFull').classList.remove('hidden');
      $('notesList').innerHTML = plainNotes.map(n => '<div class="note"><div class="h">' + esc(n.title) + '</div><p>' + esc(n.body) + '</p>' + (n.est_monthly_saving > 0 ? '<div class="save">Worth about ' + money(n.est_monthly_saving * 12) + ' a year</div>' : '') + '</div>').join('');
    } else if (plainNotes.length) {
      $('notesTeaser').classList.remove('hidden');
      $('notesTitles').innerHTML = plainNotes.map(n => '<div style="padding:6px 0;border-top:1px solid #F1F1F2;font-weight:600;color:#003660">' + esc(n.title) + '</div>').join('');
    }
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
    const head = '<tr><th>Person / number</th><th>Provider</th><th>Plan</th><th class="r">Avg / month</th>' + (A.months.length > 1 ? '<th>Trend</th>' : '') + '<th class="r">Data used</th></tr>';
    const max = Math.max(...A.lines.map(l => l.avg), 1);
    $('lineTable').innerHTML = head + A.lines.map(l =>
      '<tr><td class="who">' + esc(l.user || l.display) + '<span class="tiny">' + esc(l.display) + '</span></td><td>' + provBadge(l.carrier_code, l.carrier) + '</td>' +
      '<td>' + esc(l.plan || '-') + '</td>' +
      '<td class="r num"><div class="bar-cell" style="justify-content:flex-end"><div class="bar" style="width:' + Math.max(4, 90 * l.avg / max) + 'px"></div><b>' + ev(l.src, [l.number, l.src && l.src.total], money(l.avg, 2), (l.user || '') + ' · ' + l.display) + '</b></div></td>' +
      (A.months.length > 1 ? '<td>' + sparkline(l.spend) + '</td>' : '') +
      '<td class="r num">' + (l.avg_data_gb == null ? '-' : l.avg_data_gb + ' GB') + '</td></tr>').join('');
  }

  function renderSankey() {
    const nodes = [], links = [], seen = new Set();
    const node = (name, color, depth) => { if (!seen.has(name)) { seen.add(name); nodes.push({ name, itemStyle: { color }, depth }); } };
    const link = (s, t, v) => { if (v > 0.5) links.push({ source: s, target: t, value: +v.toFixed(2) }); };
    const CATS = { plan: ['Plans', '#003660'], addon: ['Add-ons', '#009ED8'], extras: ['Roaming & extras', '#F1600D'] };
    A.lines.slice(0, 14).forEach(l => {
      const n = Math.max(l.spend.filter(v => v != null).length, 1);
      node(l.carrier, provColor(l.carrier_code), 0);
      [['plan', l.plan_cost || 0], ['addon', Math.max(l.addon / n, 0)], ['extras', (l.roaming + l.usage) / n]].forEach(([k, v]) => {
        if (v > 0.5) { node(CATS[k][0], CATS[k][1], 1); link(l.carrier, CATS[k][0], v); node(l.short, provColor(l.carrier_code), 2); link(CATS[k][0], l.short, v); }
      });
    });
    (A.services || []).forEach(s => { node(s.carrier, provColor(s.carrier_code), 0); node('Broadband', '#4DBBE4', 1); link(s.carrier, 'Broadband', s.avg); });
    if (A.kpi.devices_month > 0.5 && A.providers[0]) { node(A.providers[0].name, provColor(A.providers[0].code), 0); node('Devices', '#8E9699', 1); link(A.providers[0].name, 'Devices', A.kpi.devices_month); }
    make('sankeyChart', { tooltip: Object.assign({}, SO.base.tooltip, { trigger: 'item', formatter: p => p.dataType === 'edge' ? esc(p.data.source) + ' \u2192 ' + esc(p.data.target) + ': <b>' + money(p.data.value, 2) + '/mth</b>' : '<b>' + esc(p.name) + '</b>' }),
      series: [{ type: 'sankey', data: nodes, links, left: 4, right: 128, top: 8, bottom: 8, nodeWidth: 14, nodeGap: 10, nodeAlign: 'left', draggable: false, label: { color: C.ink, fontSize: 11, formatter: p => p.name.length > 22 ? p.name.slice(0, 21) + '\u2026' : p.name }, lineStyle: { color: 'gradient', opacity: 0.35, curveness: 0.5 }, emphasis: { focus: 'adjacency' } }] });
  }

  function renderInsights() {
    if (!A.teaser) {
      $('insights').innerHTML = A.insights.map(i => '<div class="insight"><div class="k">' + esc(i.title) + '</div><div class="h">' + esc(i.headline) + '</div><div class="b">' + esc(i.body) + '</div>' +
        (i.saving > 0 ? '<div class="save">Save up to ' + money(i.saving * 12) + ' a year</div>' : '') +
        (i.items.length ? '<ul>' + i.items.map(it => '<li><span><b>' + esc(it.label) + '</b><br><span class="d">' + esc(it.detail || '') + '</span></span><span class="num" style="text-align:right">' + ev(it.src, it.terms, esc(it.value), it.label) + '</span></li>').join('') + '</ul>' : '') + '</div>').join('');
      return;
    }
    const fake = ['Line ending 0101 · $65.00/mth', 'Line ending 0102 · 18% of allowance', 'Line ending 0104 · $27.40/mth extras'];
    $('insights').innerHTML = A.insights.map(i =>
      '<div class="insight locked"><div class="k">' + esc(i.title) + '</div><div class="h">' + esc(i.headline) + '</div><div class="b">' + esc(i.body) + '</div>' +
      '<ul class="blur">' + fake.map(f => '<li><span>' + f + '</span></li>').join('') + '</ul>' +
      '<div class="lock"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#003660" stroke-width="2.2"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg><a href="/register?next=/dashboard">Unlock the detail and savings</a></div></div>').join('');
  }

  const form = $('emailForm');
  if (form) form.onsubmit = async (e) => {
    e.preventDefault();
    const btn = form.querySelector('button'); btn.disabled = true;
    const r = await fetch('/api/email-summary', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: $('summaryEmail').value }) });
    const j = await r.json().catch(() => ({}));
    $('emailMsg').innerHTML = r.ok ? '<span style="color:#00875A;font-weight:600">Sent - check your inbox. Click the link in the email to save your analysis.</span>' : '<span style="color:#B5470A">' + esc(j.error || 'Something went wrong.') + '</span>';
    btn.disabled = false;
  };

  if (SO_EXAMPLE) loadAnalysis([]); else poll();
})();
