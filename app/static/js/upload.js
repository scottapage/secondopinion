(function () {
  const drop = document.getElementById('drop');
  const inner = document.getElementById('dropInner');
  const input = document.getElementById('fileInput');
  const list = document.getElementById('fileList');
  const actions = document.getElementById('dropActions');
  const btn = document.getElementById('analyseBtn');
  const err = document.getElementById('dropErr');
  let files = [];

  const fmtSize = (b) => b > 1048576 ? (b / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(b / 1024)) + ' KB';

  function render() {
    list.innerHTML = '';
    files.forEach((f, i) => {
      const row = document.createElement('div');
      row.className = 'file';
      row.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#003660" stroke-width="2"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/></svg>' +
        '<span class="name"></span><span class="tiny">' + fmtSize(f.size) + '</span><button class="x" type="button" aria-label="Remove">&times;</button>';
      row.querySelector('.name').textContent = f.name;
      row.querySelector('.x').onclick = () => { files.splice(i, 1); render(); };
      list.appendChild(row);
    });
    actions.classList.toggle('hidden', files.length === 0);
    btn.textContent = files.length > 1 ? 'Analyse my ' + files.length + ' bills' : 'Analyse my bill';
  }

  function add(fl) {
    err.classList.add('hidden');
    for (const f of fl) {
      const isPdf = f.type === 'application/pdf' || /\.pdf$/i.test(f.name);
      if (!isPdf) { showErr('"' + f.name + '" isn\'t a PDF. We accept PDF bills only.'); continue; }
      if (f.size > 50 * 1048576) { showErr('"' + f.name + '" is over 50 MB.'); continue; }
      if (!files.some(x => x.name === f.name && x.size === f.size)) files.push(f);
    }
    render();
  }
  function showErr(m) { err.textContent = m; err.classList.remove('hidden'); }

  inner.addEventListener('click', () => input.click());
  input.addEventListener('change', () => { add(input.files); input.value = ''; });
  ['dragenter', 'dragover'].forEach(e => drop.addEventListener(e, ev => { ev.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(e => drop.addEventListener(e, ev => { ev.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', ev => add(ev.dataTransfer.files));
  // Allow dropping anywhere on the page
  window.addEventListener('dragover', ev => ev.preventDefault());
  window.addEventListener('drop', ev => { ev.preventDefault(); if (ev.target.closest && !ev.target.closest('#drop')) add(ev.dataTransfer.files); });

  btn.addEventListener('click', async () => {
    if (!files.length) return;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner" style="border-color:rgba(255,255,255,.35);border-top-color:#fff"></span> Uploading';
    const fd = new FormData();
    files.forEach(f => fd.append('files', f, f.name));
    try {
      const r = await fetch('/api/upload', { method: 'POST', body: fd });
      if (!r.ok) throw new Error('Upload failed');
      const j = await r.json();
      window.location = j.redirect;
    } catch (e) {
      showErr('Sorry, the upload failed. Please try again.');
      btn.disabled = false;
      render();
    }
  });
})();
