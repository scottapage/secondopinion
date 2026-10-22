(function () {
  const { money, make, axisX, axisY, sparkline, SERIES, C, PROV, provColor, provBadge, scoreRing, countUp, initials } = SO;
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
    $('bbTab').classList.toggle('hidden', !(A.services || []).length);
    renderHeader(); renderTab();
  }

  const monthsOf = (l) => Math.max(l.spend.filter(v => v != null).length, 1);

  // ---- Hero
  function renderHeader() {
    const k = A.kpi, provs = A.providers || [];
    const provText = provs.length > 1 ? provs.map(p => p.name).slice(0, -1).join(', ') + ' and ' + provs[provs.length - 1].name : (provs[0] ? provs[0].name : '');
    $('headline').innerHTML = '<span class="big-num" data-count="' + k.monthly + '">$0</span> a month across ' + k.lines + ' line' + (k.lines === 1 ? '' : 's') + (provText ? ' with ' + esc(provText) : '') + '.';
    const parts = [];
    parts.push('That’s ' + money(k.annual) + ' a year on mobile');
    if (k.devices_month) parts.push(money(k.devices_month) + '/mth of it is phone repayments');
    if (k.other_services) parts.push('plus ' + money(k.other_services) + '/mth of broadband and other services');
    $('subline').textContent = parts.join(' · ') + '. ' + k.bills + ' bill' + (k.bills === 1 ? '' : 's') + ', ' + A.month_labels[0] + (A.months.length > 1 ? ' to ' + A.month_labels[A.month_labels.length - 1] : '') + ', excl GST.';
    $('provRow').innerHTML = provs.map(p => '<span class="prov-chip" style="--pc:' + provColor(p.code) + '"><i></i>' + esc(p.name) + ' <b>' + money(p.monthly) + '</b><span>' + p.lines + ' line' + (p.lines === 1 ? '' : 's') + (p.accounts > 1 ? ', ' + p.accounts + ' accounts' : '') + '</span></span>').join('')
      + (provs.length > 1 ? '<span class="prov-note">Two providers means two bills, two logins and no team pricing across the whole team.</span>' : '');
    const s = A.score;
    $('scoreWrap').innerHTML = '<div class="score-ring">' + scoreRing(s.score, 148) + '<div class="score-txt"><b data-count="' + s.score + '" data-int="1">0</b><span>health score</span></div></div>' +
      '<div class="score-parts"><div class="grade">' + esc(s.grade) + '</div>' + s.parts.map(p => '<div class="sp"><div class="sp-h"><span>' + esc(p.label) + '</span><b>' + p.score + '/' + p.max + '</b></div><div class="sp-bar"><i style="width:' + (100 * p.score / p.max) + '%"></i></div><div class="sp-n">' + esc(p.note) + '</div></div>').join('') + '</div>';
    const delta = k.monthly_prev != null ? k.monthly - k.monthly_prev : null;
    const sw = A.switch || {};
    $('kpis').innerHTML = [
      ['Monthly run-rate', money(k.monthly), delta == null ? 'latest bill from each account' : (Math.abs(delta) < 1 ? 'no change' : '<span class="' + (delta > 0 ? 'delta-up' : 'delta-down') + '">' + (delta > 0 ? '+' : '') + money(delta) + '</span> vs previous bills'), true],
      ['Average per line', money(k.per_line, 2), k.lines + ' active lines'],
      ['Phones being paid off', money(sw.hardware_balance || 0), (A.devices || []).length + ' device' + ((A.devices || []).length === 1 ? '' : 's') + ', ' + money(k.devices_month, 2) + '/mth'],
      ['On 2degrees instead', money(sw.future_month || 0) + '<span class="tiny" style="display:inline">/mth</span>', (sw.saving_year > 0 ? 'save ' + money(sw.saving_year) + '/yr like-for-like' : 'like-for-like') + (sw.optimised_saving_year > sw.saving_year ? ', up to ' + money(sw.optimised_saving_year) + '/yr right-sized' : ''), false, 'orange'],
    ].map(x => '<div class="kpi' + (x[3] ? ' accent' : '') + (x[4] ? ' ' + x[4] : '') + '"><div class="label">' + x[0] + '</div><div class="value">' + x[1] + '</div><div class="sub">' + x[2] + '</div></div>').join('');
    countUp(document);
  }

  function renderTab() {
    document.querySelectorAll('[data-p]').forEach(s => s.classList.toggle('hidden', s.dataset.p !== tab));
    ({ overview: renderOverview, switch: renderSwitch, people: renderPeople, fit: renderFit, savings: renderSavings, broadband: renderBroadband, hardware: renderHardware, bills: renderBills })[tab]();
    setTimeout(() => document.querySelectorAll('[data-p="' + tab + '"] .chart').forEach(el => { const c = echarts.getInstanceByDom(el); c && c.resize(); }), 30);
  }
  function goTab(t) { document.querySelectorAll('#tabs button').forEach(x => x.classList.toggle('on', x.dataset.t === t)); tab = t; renderTab(); window.scrollTo({ top: $('tabs').offsetTop - 80, behavior: 'smooth' }); }
  document.querySelectorAll('#tabs button').forEach(b => b.onclick = () => goTab(b.dataset.t));
  $('fAccount').onchange = load;

  // ---- Overview
  const DEC_COL = ['#003660', '#009ED8', '#F1600D', '#8E9699', '#4DBBE4', '#BBBFC0'];
  function renderOverview() {
    const D = A.decoded;
    make('decodedChart', {
      tooltip: Object.assign({}, SO.base.tooltip, { trigger: 'axis', valueFormatter: v => money(v, 2) }),
      legend: { bottom: 0, icon: 'circle', itemWidth: 9, textStyle: { color: C.ink2, fontSize: 11 } },
      grid: { left: 8, right: 12, top: 12, bottom: 40, containLabel: true },
      xAxis: axisX(A.month_labels), yAxis: axisY(),
      series: D.labels.map((name, i) => ({ name, type: 'bar', stack: i === 5 ? 'f' : 's', barMaxWidth: 44, data: D.series[i], itemStyle: { color: DEC_COL[i], borderRadius: i === 0 ? [0, 0, 4, 4] : 0 } }))
    });
    renderSankey();
    renderSwitchCard();
    const notes = (A.notes || []).filter(n => n.kind !== 'fact'), facts = (A.notes || []).filter(n => n.kind === 'fact');
    $('notesCard').classList.toggle('hidden', !notes.length);
    $('notesList').innerHTML = notes.map(n => '<div class="note"><div class="h">' + esc(n.title) + '</div><p>' + esc(n.body) + '</p>' + (n.est_monthly_saving > 0 ? '<div class="save">Worth about ' + money(n.est_monthly_saving * 12) + ' a year</div>' : '') + '</div>').join('');
    $('factsCard').classList.toggle('hidden', !facts.length);
    $('factsList').innerHTML = facts.map(n => '<div class="fact"><div class="bulb">💡</div><div><div class="h">' + esc(n.title) + '</div><p>' + esc(n.body) + '</p></div></div>').join('');
    renderPeopleCards();
  }

  function renderSankey() {
    const nodes = [], links = [], seen = new Set();
    const node = (name, color, depth) => { if (!seen.has(name)) { seen.add(name); nodes.push({ name, itemStyle: { color }, depth }); } };
    const link = (s, t, v) => { if (v > 0.5) links.push({ source: s, target: t, value: +v.toFixed(2) }); };
    const lines = A.lines.slice(0, 14);
    const CAT = { plan: ['Plans', '#003660'], addon: ['Add-ons', '#009ED8'], extras: ['Roaming & extras', '#F1600D'] };
    lines.forEach(l => {
      const n = monthsOf(l), prov = l.carrier;
      node(prov, provColor(l.carrier_code), 0);
      const plan = l.plan_cost || 0, addon = Math.max(l.addon / n, 0), extras = (l.roaming + l.usage) / n;
      [['plan', plan], ['addon', addon], ['extras', extras]].forEach(([k, v]) => {
        if (v > 0.5) { node(CAT[k][0], CAT[k][1], 1); link(prov, CAT[k][0], v); node(l.short, provColor(l.carrier_code), 2); link(CAT[k][0], l.short, v); }
      });
    });
    (A.services || []).forEach(s => { node(s.carrier, provColor(s.carrier_code), 0); node('Broadband', '#4DBBE4', 1); link(s.carrier, 'Broadband', s.avg); });
    if (A.kpi.devices_month > 0.5) { const prov = (A.providers[0] || {}).name; node(prov, provColor((A.providers[0] || {}).code), 0); node('Devices', '#8E9699', 1); link(prov, 'Devices', A.kpi.devices_month); }
    make('sankeyChart', {
      tooltip: Object.assign({}, SO.base.tooltip, { trigger: 'item', formatter: p => p.dataType === 'edge' ? esc(p.data.source) + ' → ' + esc(p.data.target) + ': <b>' + money(p.data.value, 2) + '/mth</b>' : '<b>' + esc(p.name) + '</b>' }),
      series: [{ type: 'sankey', data: nodes, links, left: 4, right: 128, top: 8, bottom: 8, nodeWidth: 14, nodeGap: 10, nodeAlign: 'left', draggable: false,
        label: { color: C.ink, fontSize: 11, formatter: p => p.name.length > 18 ? p.name.slice(0, 17) + '…' : p.name },
        lineStyle: { color: 'gradient', opacity: 0.35, curveness: 0.5 }, emphasis: { focus: 'adjacency' } }]
    });
  }

  function renderSwitchCard() {
    const sw = A.switch;
    const already = sw.lines.every(l => l.already);
    const best = Math.max(sw.saving_year, sw.optimised_saving_year);
    $('switchCard').innerHTML =
      '<div class="sw-left"><div class="eyebrow light">' + (already ? 'Right-sizing on 2degrees' : 'If you brought it all to 2degrees') + '</div>' +
      '<div class="sw-big">' + (best > 0 ? '<span data-count="' + best + '">$0</span> a year' : 'Same price, one bill') + '</div>' +
      '<p>' + (sw.saving_year > 0 ? 'Like-for-like plans come to <b>' + money(sw.future_month) + '/mth</b> instead of <b>' + money(sw.current_month) + '</b>.' : 'Like-for-like plans come to <b>' + money(sw.future_month) + '/mth</b> against <b>' + money(sw.current_month) + '</b> today.') +
      (sw.optimised_saving_year > sw.saving_year ? ' Right-sized, where usage allows, <b>' + money(sw.optimised_month) + '/mth</b> — ' + money(sw.optimised_saving_year) + ' a year.' : '') + '</p>' +
      '<div class="sw-actions"><a class="btn white" href="/offer">Make me an offer</a><button class="link-btn light" id="toSwitch">See it line by line →</button></div></div>' +
      '<div class="sw-right"><div class="sw-cmp"><div><span>Today</span><b data-count="' + sw.current_month + '">$0</b><i>plans + add-ons /mth</i></div><div class="arrow">→</div><div><span>2degrees</span><b data-count="' + sw.future_month + '">$0</b><i>like-for-like /mth</i></div></div>' +
      '<ul class="gains">' + (sw.roaming && sw.roaming.saving_year > 0 ? '<li>Roaming about ' + money(sw.roaming.saving_year) + '/yr cheaper (' + sw.roaming.days_other + ' days at $' + sw.roaming.rate + '/day' + (sw.roaming.days_au ? ', Australia free' : '') + ')</li>' : '') + sw.gains.slice(0, 3).map(g => '<li>' + esc(g) + '</li>').join('') + '</ul></div>';
    $('toSwitch').onclick = () => goTab('switch');
    countUp($('switchCard'));
    const ch = sw.chamber;
    let chEl = $('chamberCard');
    if (!chEl) { chEl = document.createElement('div'); chEl.id = 'chamberCard'; chEl.className = 'card chamber'; $('switchCard').insertAdjacentElement('afterend', chEl); }
    chEl.style.marginTop = '20px';
    chEl.innerHTML = ch ? '<div class="ch-l"><div class="eyebrow">Member of a Chamber of Commerce?</div><h3>' + ch.mobile_pct + '% off mobile for life, ' + ch.mobile_free_months + ' months free' + (ch.fibre_pct ? ' \u2014 and ' + ch.fibre_pct + '% off fibre' : '') + '</h3><p class="small muted">2degrees\u2019 current offer for NZ Chambers of Commerce members. Applied to your like-for-like setup it comes to <b>' + money(ch.month) + '/mth</b>, about <b>' + money(ch.saving_year) + ' a year</b> less than today, and roughly <b>' + money(ch.first_year_saving) + '</b> in the first year with the free months.' + (ch.conditions ? ' <span class="tiny" style="display:inline">' + esc(ch.conditions) + '</span>' : '') + '</p></div><div class="ch-r"><b data-count="' + ch.first_year_saving + '">$0</b><span>first-year saving</span></div>' : '';
    if (ch) countUp(chEl);
  }

  function renderPeopleCards() {
    const max = Math.max(...A.lines.map(l => l.avg), 1);
    $('peopleGrid').innerHTML = A.lines.map(l => {
      const n = monthsOf(l), extras = (l.roaming + l.usage + l.addon) / n;
      return '<div class="person" data-n="' + esc(l.number) + '"><div class="p-top"><div class="avatar" style="background:' + provColor(l.carrier_code) + '">' + esc(initials(l.label)) + '</div><div class="p-name"><b>' + esc(l.label) + '</b><span>' + esc(l.display) + '</span></div>' + provBadge(l.carrier_code, l.carrier) + '</div>' +
        '<div class="p-plan">' + esc(l.plan || 'Plan not shown') + (l.allowance_gb ? ' · ' + (l.allowance_gb === -1 ? 'unlimited' : l.allowance_gb + ' GB') : '') + '</div>' +
        '<div class="p-row"><div class="p-cost"><b>' + money(l.avg, 2) + '</b><span>/mth</span></div>' + sparkline(l.spend, 90, 28, provColor(l.carrier_code)) + '</div>' +
        '<div class="p-bar"><i style="width:' + (100 * l.avg / max) + '%;background:' + provColor(l.carrier_code) + '"></i></div>' +
        '<div class="p-foot">' + (l.avg_data_gb != null ? '<span>' + l.avg_data_gb + ' GB/mth</span>' : '') + (extras > 0.5 ? '<span class="warn">' + money(extras, 2) + ' extras</span>' : '') + l.flags.map(f => '<span class="flag ' + f.split(' ')[0] + '">' + esc(f) + '</span>').join('') + '</div></div>';
    }).join('');
    $('peopleGrid').querySelectorAll('.person').forEach(el => el.onclick = () => openLine(A.lines.find(l => l.number === el.dataset.n)));
  }

  // ---- Switch tab
  function renderSwitch() {
    const sw = A.switch;
    $('switchTable').innerHTML = '<tr><th>Service</th><th>Now</th><th class="r">$/mth</th><th>On 2degrees</th><th class="r">$/mth</th><th class="r">Change</th></tr>' +
      sw.lines.map(l => '<tr><td class="who">' + esc(l.user || l.display) + '<span class="tiny">' + esc(l.display) + '</span></td><td>' + provBadge(l.carrier_code, l.carrier) + '<div class="small">' + esc(l.plan || '') + '</div></td><td class="r num">' + money(l.current, 2) + '</td>' +
        '<td>' + provBadge('2degrees', '2degrees') + '<div class="small">' + esc(l.rec) + (l.role ? ' <span class="tiny" style="display:inline">(' + esc(l.role) + ')</span>' : '') + '</div><div class="tiny">' + esc(l.why) + (l.alt ? ' \u00b7 or ' + esc(l.alt) + ' at ' + money(l.alt_price) : '') + '</div></td>' +
        '<td class="r num">' + money(l.price, 2) + '</td><td class="r num ' + (l.saving > 0.5 ? 'delta-down' : l.saving < -0.5 ? 'delta-up' : '') + '">' + (l.saving > 0.5 ? '−' + money(l.saving, 2) : l.saving < -0.5 ? '+' + money(-l.saving, 2) : 'same') + '</td></tr>').join('') +
      sw.services.map(s => '<tr><td class="who">' + esc(s.type) + '<span class="tiny">' + esc(s.address || '') + '</span></td><td>' + provBadge(s.carrier_code || '', s.carrier) + '<div class="small">' + esc(s.plan || '') + '</div></td><td class="r num">' + money(s.current, 2) + '</td><td>' + provBadge('2degrees', '2degrees') + '<div class="small">' + esc(s.rec) + '</div>' + (s.promo ? '<div class="tiny promo">' + esc(s.promo.text) + ' \u2014 saves ' + money(s.promo.saving_year) + ' in year one</div>' : '') + '</td><td class="r num">' + money(s.price, 2) + '</td><td class="r num ' + (s.saving > 0.5 ? 'delta-down' : s.saving < -0.5 ? 'delta-up' : '') + '">' + (s.saving > 0.5 ? '−' + money(s.saving, 2) : s.saving < -0.5 ? '+' + money(-s.saving, 2) : 'same') + '</td></tr>').join('') +
      '<tr class="total"><td colspan="2"><b>Total plans + add-ons</b></td><td class="r num"><b>' + money(sw.current_month, 2) + '</b></td><td></td><td class="r num"><b>' + money(sw.future_month, 2) + '</b></td><td class="r num"><b>' + (sw.saving_month >= 0 ? '−' : '+') + money(Math.abs(sw.saving_month), 2) + '</b></td></tr>';
    $('switchTotals').innerHTML = '<div class="eyebrow light">Per year</div><div class="sw-big">' + money(sw.saving_year) + '</div><p>like-for-like' + (sw.optimised_saving_year > sw.saving_year ? ', or <b>' + money(sw.optimised_saving_year) + '</b> right-sized' : '') + (sw.chamber ? ', or <b>' + money(sw.chamber.saving_year) + '</b> as a Chamber of Commerce member (' + money(sw.chamber.first_year_saving) + ' in year one)' : '') + '</p>' + (sw.promos ? '<p class="small" style="margin-top:10px"><b>Current promotions:</b> ' + sw.promos.items.map(i => esc(i.plan) + ' \u2014 ' + esc(i.text)).join('; ') + '. First year <b>' + money(sw.promos.first_year_total) + '</b>, ' + money(sw.promos.first_year_saving) + ' less than today.</p>' : '') +
      '<p class="small" style="margin-top:12px;opacity:.85">Unchanged by switching: usage extras (' + money(sw.extras_month, 2) + '/mth) and device repayments (' + money(A.kpi.devices_month, 2) + '/mth, ' + money(sw.hardware_balance) + ' remaining' + (sw.hardware_balance > 0 ? ' — a sign-on credit can clear this' : '') + ').</p>' +
      '<a class="btn white" href="/offer" style="margin-top:16px">Make me an offer</a>';
    $('portingList').innerHTML = sw.porting.have.map(h => '<div class="port ok">✓ ' + esc(h) + ' <span class="tiny" style="display:inline">on your bills</span></div>').join('') + sw.porting.need.map(h => '<div class="port need">○ ' + esc(h) + '</div>').join('');
    const CATN = { included: 'Included value', roaming: 'Roaming', discount: 'Discounts & partnerships', device: 'Devices', support: 'Support' };
    const groups = {};
    (sw.benefits || []).forEach(b => (groups[b.category] = groups[b.category] || []).push(b));
    $('gainsList').innerHTML = (sw.roaming ? '<li class="roam"><b>Your roaming: ' + money(sw.roaming.now_month, 2) + '/mth now, about ' + money(sw.roaming.future_month, 2) + ' on 2degrees</b><div class="tiny">' + sw.roaming.days_other + ' days outside Australia at $' + sw.roaming.rate + '/day' + (sw.roaming.days_au ? ', ' + sw.roaming.days_au + ' days in Australia free' : '') + (sw.roaming.saving_year > 0 ? ' \u2014 about ' + money(sw.roaming.saving_year) + ' a year' : '') + '</div></li>' : '') +
      Object.keys(CATN).filter(k => groups[k]).map(k => '<li class="grp"><span class="tiny">' + CATN[k] + '</span>' + groups[k].map(b => '<div><b>' + esc(b.title) + '</b>' + (b.value ? ' <span class="val">' + esc(b.value) + '</span>' : '') + (b.detail ? '<div class="tiny">' + esc(b.detail) + '</div>' : '') + '</div>').join('') + '</li>').join('') +
      (!(sw.benefits || []).length ? sw.gains.map(g => '<li>' + esc(g) + '</li>').join('') : '');
  }

  // ---- Broadband & other services
  function renderBroadband() {
    const S = A.services || [];
    make('bbChart', {
      tooltip: Object.assign({}, SO.base.tooltip, { trigger: 'axis', valueFormatter: v => v == null ? '-' : money(v, 2) }),
      legend: { bottom: 0, icon: 'circle', itemWidth: 9 }, grid: { left: 8, right: 16, top: 16, bottom: 36, containLabel: true },
      xAxis: axisX(A.month_labels), yAxis: axisY(), color: SERIES,
      series: S.map(s => ({ name: (s.address || s.type).slice(0, 30), type: 'bar', stack: 's', barMaxWidth: 46, data: s.spend }))
    });
    $('bbTable').innerHTML = '<tr><th>Service</th><th>Provider</th><th>Address</th><th>Plan</th><th class="r">Avg / month</th></tr>' +
      S.map(s => '<tr><td>' + esc(s.type) + '</td><td>' + provBadge(s.carrier_code, s.carrier) + '</td><td>' + esc(s.address || '-') + '</td><td>' + esc(s.plan || '-') + (s.speed ? '<span class="tiny">' + esc(s.speed) + '</span>' : '') + '</td><td class="r num"><b>' + money(s.avg, 2) + '</b></td></tr>').join('');
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
    const cols = [['label', 'Person / number'], ['carrier', 'Provider'], ['plan', 'Plan'], ['avg', 'Avg / month', 'r'], ['trend', 'Trend'], ['avg_data_gb', 'Data / month', 'r'], ['extras', 'Extras / month', 'r'], ['flags', 'Flags']];
    let rows = A.lines.map(l => Object.assign({ extras: (l.roaming + l.usage + l.addon) / monthsOf(l) }, l));
    if (search) rows = rows.filter(l => (l.label + ' ' + l.number + ' ' + (l.plan || '') + ' ' + l.carrier).toLowerCase().includes(search));
    rows.sort((a, b) => { const x = a[sortKey], y = b[sortKey]; return (typeof x === 'string' || typeof y === 'string' ? String(x || '').localeCompare(String(y || '')) : (x || 0) - (y || 0)) * sortDir; });
    $('lineTable').innerHTML = '<tr>' + cols.map(c => '<th class="' + (c[2] || '') + (c[0] !== 'trend' && c[0] !== 'flags' ? ' sort' : '') + '" data-k="' + c[0] + '">' + c[1] + (sortKey === c[0] ? (sortDir < 0 ? ' ↓' : ' ↑') : '') + '</th>').join('') + '</tr>' +
      rows.map(l => '<tr class="click" data-n="' + esc(l.number) + '"><td class="who">' + esc(l.user || l.display) + '<span class="tiny">' + esc(l.display) + '</span></td><td>' + provBadge(l.carrier_code, l.carrier) + '<span class="tiny">' + esc(l.account || '') + '</span></td><td>' + esc(l.plan || '-') + '</td>' +
        '<td class="r num"><b>' + money(l.avg, 2) + '</b></td><td>' + sparkline(l.spend) + '</td>' +
        '<td class="r num">' + (l.avg_data_gb == null ? '-' : l.avg_data_gb + ' GB') + (l.allowance_gb ? '<span class="tiny">of ' + (l.allowance_gb === -1 ? 'unlimited' : l.allowance_gb + ' GB') + '</span>' : '') + '</td>' +
        '<td class="r num">' + (l.extras > 0.005 ? money(l.extras, 2) : '-') + '</td>' +
        '<td>' + l.flags.map(f => '<span class="flag ' + f.split(' ')[0] + '">' + f + '</span>').join('') + '</td></tr>').join('');
    $('lineTable').querySelectorAll('th.sort').forEach(th => th.onclick = () => { const k = th.dataset.k; sortDir = sortKey === k ? -sortDir : -1; sortKey = k; renderLineTable(); });
    $('lineTable').querySelectorAll('tr.click').forEach(tr => tr.onclick = () => openLine(A.lines.find(l => l.number === tr.dataset.n)));
  }

  function openLine(l) {
    if (!l) return;
    const n = monthsOf(l);
    const sw = (A.switch.lines || []).find(x => x.number === l.number);
    $('drawerBody').innerHTML = '<div class="eyebrow" style="margin-bottom:6px">' + provBadge(l.carrier_code, l.carrier) + ' · ' + esc(l.display) + (l.account ? ' · account ' + esc(l.account) : '') + '</div><h2>' + esc(l.user || l.display) + '</h2><p class="muted">' + esc(l.plan || 'Plan not shown') + '</p>' +
      '<div class="mini-kpis"><div><b>' + money(l.avg, 2) + '</b><span>avg per month</span></div><div><b>' + (l.avg_data_gb == null ? '-' : l.avg_data_gb + ' GB') + '</b><span>data per month</span></div><div><b>' + money((l.roaming + l.usage + l.addon) / n, 2) + '</b><span>extras per month</span></div></div>' +
      (l.flags.length ? '<p>' + l.flags.map(f => '<span class="flag ' + f.split(' ')[0] + '">' + f + '</span>').join('') + '</p>' : '') +
      (sw ? '<div class="card flat" style="margin-top:14px;padding:14px"><b>On 2degrees:</b> ' + esc(sw.rec) + (sw.role ? ' (' + esc(sw.role) + ')' : '') + ' at ' + money(sw.price, 2) + '/mth vs ' + money(sw.current, 2) + ' now' + (sw.alt ? ' — or ' + esc(sw.alt) + ' at ' + money(sw.alt_price) + ' if usage fits' : '') + '<div class="tiny">' + esc(sw.why) + '</div></div>' : '') +
      '<h3 style="margin-top:18px">Cost by month</h3><div id="dSpend" class="chart short"></div><h3 style="margin-top:10px">Data by month</h3><div id="dData" class="chart short"></div>';
    $('drawer').classList.remove('hidden'); $('drawerBg').classList.remove('hidden');
    make('dSpend', { tooltip: Object.assign({}, SO.base.tooltip, { trigger: 'axis', valueFormatter: v => money(v, 2) }), xAxis: axisX(A.month_labels), yAxis: axisY(), series: [{ type: 'bar', data: l.spend, itemStyle: { color: provColor(l.carrier_code), borderRadius: [4, 4, 0, 0] }, barMaxWidth: 34 }] });
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
    if (!pts.length) $('fitChart').innerHTML = '<p class="muted" style="padding:30px 0">Your bills don’t show data usage, so we can’t plot plan fit yet. A usage report from your provider’s business portal fills this in.</p>';
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
    const now = new Date();
    make('hwChart', {
      tooltip: Object.assign({}, SO.base.tooltip, { trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: ps => { const p = ps[0]; const end = new Date(now.getFullYear(), now.getMonth() + p.value, 1); return esc(p.name) + '<br><b>' + p.value + ' payments left</b> — free in ' + end.toLocaleDateString('en-NZ', { month: 'short', year: 'numeric' }); } }),
      grid: { left: 8, right: 60, top: 10, bottom: 8, containLabel: true },
      xAxis: { type: 'value', name: 'months left', splitLine: { lineStyle: { color: '#F1F1F2' } }, axisLabel: { color: C.ink3 } },
      yAxis: { type: 'category', data: withMonths.map(x => (x.number || '') + ' ' + (x.description || '').slice(0, 26)), axisTick: { show: false }, axisLine: { show: false }, axisLabel: { color: C.ink } },
      series: [{ type: 'bar', data: withMonths.map(x => x.months_left), itemStyle: { color: C.navy, borderRadius: [0, 6, 6, 0] }, barMaxWidth: 20, label: { show: true, position: 'right', formatter: p => { const end = new Date(now.getFullYear(), now.getMonth() + p.value, 1); return p.value + ' mths · ' + end.toLocaleDateString('en-NZ', { month: 'short', year: '2-digit' }); }, color: C.ink2 } }]
    });
    $('hwTable').innerHTML = '<tr><th>Line</th><th>Device</th><th class="r">Monthly</th><th class="r">Payments left</th><th class="r">Balance</th></tr>' + d.map(x => '<tr><td>' + esc(x.number) + '</td><td>' + esc(x.description) + '</td><td class="r num">' + money(x.monthly, 2) + '</td><td class="r">' + (x.months_left == null ? '-' : x.months_left) + '</td><td class="r num">' + money(x.balance) + '</td></tr>').join('');
  }

  // ---- Bills
  function renderBills() {
    const inv = (A && A.invoices) || [];
    $('billTable').innerHTML = '<tr><th>File</th><th>Provider</th><th>Account</th><th>Period</th><th class="r">Total excl GST</th><th>Status</th></tr>' +
      inv.slice().reverse().map(i => '<tr><td>' + (i.status === 'done' || i.status === 'duplicate' ? '<a href="/invoice/' + i.id + '.pdf" target="_blank">' + esc(i.filename) + '</a>' : esc(i.filename)) + '</td><td>' + (i.carrier ? provBadge(i.carrier, carrierName[i.carrier] || i.carrier) : '-') + '</td><td>' + esc(i.account_name || '') + '<span class="tiny">' + esc(i.account_number || '') + '</span></td><td>' + esc(i.period_month || '-') + '</td><td class="r num">' + money(i.total_excl_gst, 2) + '</td><td><span class="status ' + i.status + '">' + i.status + '</span>' + (i.error ? '<span class="tiny">' + esc(i.error) + '</span>' : '') + '</td></tr>').join('');
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
