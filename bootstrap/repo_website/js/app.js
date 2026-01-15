(function () {

  const baseTree = window.TREE_DATA;
  if (!baseTree) return;

  const elTree = document.getElementById('tree');
  const q = document.getElementById('q');
  const viewer = document.getElementById('viewer');
  if (!elTree || !q || !viewer) return;

  async function loadReportsNode() {
    // relativo ao studies.html
    const url = "./assets/tree/reports.json";
    try {
      const r = await fetch(url, { cache: "no-store" });
      if (!r.ok) return null;
      return await r.json();
    } catch (_) {
      return null;
    }
  }

  function deepClone(obj) {
    // suficiente p/ sua árvore (JSON puro)
    return JSON.parse(JSON.stringify(obj));
  }

  function setUrlParamNB(path) {
    try {
      const u = new URL(window.location.href);
      if (path) u.searchParams.set('nb', path);
      else u.searchParams.delete('nb');
      // se abriu notebook, remove pdf
      u.searchParams.delete('pdf');
      history.replaceState(null, '', u.toString());
    } catch (_) { }
  }

  function getUrlParamNB() {
    try {
      const u = new URL(window.location.href);
      return u.searchParams.get('nb') || '';
    } catch (_) {
      return '';
    }
  }

  function getUrlParamPDF() {
    try {
      const u = new URL(window.location.href);
      return u.searchParams.get('pdf') || '';
    } catch (_) {
      return '';
    }
  }

  function openByPath(path) {
    if (!path) return false;
    const a = elTree.querySelector(`a.file-notebook[data-path="${CSS.escape(path)}"]`);
    if (!a) return false;
    a.click();
    return true;
  }

  function openByPdf(url) {
    if (!url) return false;
    const a = elTree.querySelector(`a.file-pdf[data-pdf="${CSS.escape(url)}"]`);
    if (!a) return false;
    a.click();
    return true;
  }

  function openAncestors(el) {
    let cur = el.parentElement;
    while (cur && cur !== elTree) {
      if (cur.classList && cur.classList.contains('node')) {
        cur.classList.add('open');
      }
      cur = cur.parentElement;
    }
  }

  function setActiveLink(a) {
    if (!a) return;

    // limpa ativos anteriores (notebook + pdf)
    elTree.querySelectorAll('a.file-notebook.is-active, a.file-pdf.is-active')
      .forEach(x => x.classList.remove('is-active'));

    a.classList.add('is-active');
    openAncestors(a);
    a.scrollIntoView({ block: 'nearest' });

    // persistência só para notebook (como você já tinha)
    if (a.classList.contains('file-notebook')) {
      try { localStorage.setItem('active_nb', a.dataset.path || ''); } catch (_) { }
    }
  }

  function mkNode(node, { initialOpen = true } = {}) {
    const li = document.createElement('li');

    // mantém seu estilo original: dir vs file
    const isDir = (node.type === 'dir' || node.type === 'folder');
    li.className = 'node ' + (isDir ? 'dir' : 'file');

    const label = document.createElement('span');
    label.className = 'label';

    if (isDir) {
      label.textContent = node.name || node.title || '';

      label.onclick = () => li.classList.toggle('open');

      if (initialOpen) li.classList.add('open');

      li.appendChild(label);

      const ul = document.createElement('ul');
      ul.className = 'children';
      (node.children || []).forEach(ch => ul.appendChild(mkNode(ch, { initialOpen })));
      li.appendChild(ul);

    } else {
      // -------- NOTEBOOK
      if (node.nb_html) {
        const a = document.createElement('a');
        const base = (node.name || '').replace(/\.ipynb$/i, '');
        a.textContent = base;

        a.className = 'file-notebook';
        a.dataset.path = node.path || node.name;

        a.onclick = (e) => {
          e.preventDefault();
          viewer.src = node.nb_html;
          setActiveLink(a);
          setUrlParamNB(a.dataset.path);
        };

        label.appendChild(a);

      } else if (node.type === "pdf" && (node.path || node.url)) {
        const a = document.createElement('a');
        a.textContent = node.title || node.name || 'report.pdf';
        a.className = 'file-pdf';

        const relPath = node.path || node.url; // aceita ambos
        a.dataset.pdf = relPath;

        a.href = relPath; // link copiável

        a.onclick = (e) => {
          e.preventDefault();

          const resolved = new URL(relPath, window.location.href).toString();
          viewer.src = resolved;

          // marca ativo
          elTree.querySelectorAll('a.file-pdf.is-active')
            .forEach(x => x.classList.remove('is-active'));
          a.classList.add('is-active');
          openAncestors(a);
          a.scrollIntoView({ block: 'nearest' });

          // deep-link
          try {
            const u = new URL(window.location.href);
            u.searchParams.set('pdf', relPath);
            u.searchParams.delete('nb');
            history.replaceState(null, '', u.toString());
          } catch (_) { }
        };
        label.appendChild(a);
      } else {
        label.textContent = node.name || node.title || '';
      }

      li.appendChild(label);
    }
    return li;
  }

  function renderFactory(data) {
    return function render(filter = '') {
      elTree.innerHTML = '';
      const norm = s => (s || '').toLowerCase();

      function labelText(node) {
        return (node.title || node.name || '');
      }

      function pass(node) {
        if (!filter) return true;
        const t = norm(labelText(node));
        const p = norm(node.path || node.url || '');
        return t.includes(filter) || p.includes(filter);
      }

      function cloneFiltered(node) {
        // arquivos (notebook ou pdf)
        if (!(node.type === 'dir' || node.type === 'folder')) return pass(node) ? node : null;

        const kids = (node.children || []).map(cloneFiltered).filter(Boolean);
        if (kids.length) return { ...node, children: kids };
        return pass(node) ? { ...node, children: [] } : null;
      }

      const filtered = cloneFiltered(data);
      if (!filtered) { elTree.innerHTML = '<li class="node">Nothing found…</li>'; return; }

      (filtered.children || []).forEach(ch => elTree.appendChild(mkNode(ch, { initialOpen: true })));

      if (filter) {
        elTree.querySelectorAll('.node.file .label, .node.dir .label').forEach(lbl => {
          const txt = lbl.textContent || '';
          if (norm(txt).includes(filter)) openAncestors(lbl);
        });
      }
    }
  }

  // ===== INIT async: injeta Reports e só depois renderiza =====
  (async () => {
    const data = deepClone(baseTree);

    const reportsNode = await loadReportsNode();
    if (reportsNode) {
      data.children = data.children || [];
      data.children.push(reportsNode);
    }

    const render = renderFactory(data);

    q.addEventListener('input', (e) => render(e.target.value.trim().toLowerCase()));
    render();

    // deep-link prioridade: pdf > nb
    const pdfFromUrl = getUrlParamPDF();
    if (pdfFromUrl) {
      openByPdf(pdfFromUrl);
    } else {
      const nbFromUrl = getUrlParamNB();
      if (nbFromUrl) openByPath(nbFromUrl);
    }

    // restaura seleção (só notebook, como antes)
    try {
      const p = localStorage.getItem('active_nb');
      if (p) {
        const a = elTree.querySelector(`a.file-notebook[data-path="${CSS.escape(p)}"]`);
        if (a) setActiveLink(a);
      }
    } catch (_) { }

  })();

})();

(() => {
  'use strict';

  const ENC_URL = './assets/img/profile.enc.json';

  const OBF = 'YWE5YjQ4ODQ1MTkyNDJiZjQzYTE5Y2Y3NzZlNWE3NGEyYjVkNDI4MjllNDU4MjA0ZTc2MTFlNDIzYmYwZjc2Ng==';

  const log = (...a) => console.log('[avatar]', ...a);
  const errlog = (...a) => console.error('[avatar]', ...a);
  // function safeAtob(s) { s = (s || '').toString().trim().replace(/[\r\n\s]/g, '').replace(/-/g, '+').replace(/_/g, '/'); while (s.length % 4) s += '='; return atob(s); }
  // const b64ToU8 = (b64) => Uint8Array.from(atob(b64), c => c.charCodeAt(0));
  function normB64(s) {
    s = (s || '').toString().trim()
      .replace(/[\r\n\s]/g, '')
      .replace(/-/g, '+')
      .replace(/_/g, '/');
    while (s.length % 4) s += '=';
    return s;
  }

  function safeAtob(s) {
    return atob(normB64(s));
  }

  const b64ToU8 = (b64) => Uint8Array.from(atob(normB64(b64)), c => c.charCodeAt(0));
  const hexToBytes = (hex) => new Uint8Array((hex.match(/.{1,2}/g) || []).map(h => parseInt(h, 16)));

  async function loadProtectedAvatar() {
    log('handler iniciou. readyState=', document.readyState);

    // 1) Garantir que o <img> existe
    const img = document.getElementById('avatar') || document.querySelector('img.avatar');
    if (!img) { errlog('NÃO ENCONTREI <img id="avatar"> nesta página. Abortando.'); return; }
    log('encontrei <img id="avatar">');

    try {
      const keyHex = safeAtob(OBF).split('').reverse().join('');
      const keyBytes = hexToBytes(keyHex);
      log('keyBytes len=', keyBytes.length);
      if (keyBytes.length !== 32) throw new Error('key length != 32 bytes');

      log('fetch', ENC_URL);
      const resp = await fetch(ENC_URL, { cache: 'no-store' });
      log('status', resp.status, resp.statusText);
      if (!resp.ok) throw new Error(`fetch falhou: ${resp.status} ${resp.statusText}`);
      const payload = await resp.json().catch(async e => {
        const raw = await resp.text().catch(() => '<sem corpo>');
        errlog('falha parse JSON; amostra:', raw.slice(0, 200));
        throw e;
      });
      log('payload OK. keys=', Object.keys(payload));

      const iv = b64ToU8(payload.iv);
      const tag = b64ToU8(payload.tag);
      const ct = b64ToU8(payload.ciphertext);
      log('lens iv/tag/ct:', iv.length, tag.length, ct.length);

      const combo = new Uint8Array(ct.length + tag.length);
      combo.set(ct, 0); combo.set(tag, ct.length);

      const cryptoKey = await crypto.subtle.importKey('raw', keyBytes, 'AES-GCM', false, ['decrypt']);
      log('importKey OK');
      const plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, cryptoKey, combo.buffer);
      log('decrypt OK; bytes=', plain.byteLength);

      const blob = new Blob([plain], { type: payload.mime || 'image/jpeg' });
      const url = URL.createObjectURL(blob);
      img.onload = () => log('img onload ✓');
      img.onerror = (e) => errlog('img onerror', e);
      img.src = url;
      log('src definido →', url.slice(0, 60) + '…');

    } catch (e) {
      errlog('FALHA NO HANDLER:', e);
    }
  }

  window.loadProtectedAvatar = loadProtectedAvatar;

  if (document.readyState === 'loading') {
    log('aguardando DOMContentLoaded…');
    document.addEventListener('DOMContentLoaded', () => {
      log('DOMContentLoaded disparado');
      loadProtectedAvatar();
    }, { once: true });
  } else {
    log('DOM já pronto; executando handler agora');
    loadProtectedAvatar();
  }
})();