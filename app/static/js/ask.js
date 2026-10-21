(function () {
  const $ = (id) => document.getElementById(id);
  const fab = $('askFab'), panel = $('askPanel'), body = $('askBody'), form = $('askForm'), q = $('askQ');
  let loaded = false, busy = false;
  const SUGGEST = ['Who are my most expensive users?', 'How much are we spending on roaming?', 'Which lines could go on a cheaper plan?', 'How has our spend changed over time?'];

  function render(md) {
    // Pull out ```chart blocks, render the rest as sanitised markdown
    const charts = [];
    const text = md.replace(/```chart\s*([\s\S]*?)```/g, (_, json) => { charts.push(json); return '\n\n<div class="mchart" data-i="' + (charts.length - 1) + '"></div>\n\n'; });
    const html = DOMPurify.sanitize(marked.parse(text), { ADD_ATTR: ['data-i'] });
    return { html, charts };
  }

  function add(role, content) {
    const el = document.createElement('div');
    el.className = 'msg ' + role;
    body.appendChild(el);
    if (role === 'user') el.textContent = content;
    else {
      const r = render(content);
      el.innerHTML = r.html;
      el.querySelectorAll('.mchart').forEach(c => {
        try { SO.fromSpec(c, JSON.parse(r.charts[+c.dataset.i])); } catch (e) { c.remove(); }
      });
    }
    body.scrollTop = body.scrollHeight;
    return el;
  }

  function suggestions() {
    const s = document.createElement('div');
    s.className = 'suggest';
    SUGGEST.forEach(t => { const b = document.createElement('button'); b.type = 'button'; b.textContent = t; b.onclick = () => { s.remove(); ask(t); }; s.appendChild(b); });
    body.appendChild(s);
  }

  async function open() {
    panel.classList.remove('hidden'); fab.classList.add('hidden'); q.focus();
    if (loaded) return;
    loaded = true;
    const hist = await (await fetch('/api/chat/history')).json();
    if (!hist.length) {
      add('assistant', "Hi! Ask me anything about your mobile bills - who's spending what, roaming, plans, trends. I'll answer from your bill data.");
      suggestions();
    } else hist.forEach(h => add(h.role, h.content));
  }

  async function ask(text) {
    if (busy || !text.trim()) return;
    busy = true;
    add('user', text);
    const t = document.createElement('div');
    t.className = 'msg assistant';
    t.innerHTML = '<span class="typing"><i></i><i></i><i></i></span>';
    body.appendChild(t); body.scrollTop = body.scrollHeight;
    try {
      const r = await fetch('/api/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ q: text }) });
      const j = await r.json();
      t.remove(); add('assistant', j.answer || 'Sorry, something went wrong.');
    } catch (e) {
      t.remove(); add('assistant', 'Sorry, I couldn\'t reach the server. Please try again.');
    }
    busy = false;
  }

  fab.onclick = open;
  $('askClose').onclick = () => { panel.classList.add('hidden'); fab.classList.remove('hidden'); };
  form.onsubmit = (e) => { e.preventDefault(); const v = q.value; q.value = ''; ask(v); };
})();
