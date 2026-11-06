/* La Ferme Diobia – ERP · scripts communs (même comportement que Teranga Sip Manager) */
(function () {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const NBSP = ' ';

  // ── Formats Sénégal ───────────────────────────────
  function grp(n) { return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, NBSP); }
  window.fcfa = v => { v = Math.round(Number(v) || 0); return (v < 0 ? '-' : '') + grp(Math.abs(v)) + ' FCFA'; };
  window.nb = (v, d = 2) => {
    v = Number(v) || 0;
    if (Math.abs(v - Math.round(v)) < 1e-9) return grp(Math.round(v));
    const [e, f] = v.toFixed(d).replace(/0+$/, '').split('.');
    return grp(e) + (f ? ',' + f : '');
  };
  window.num = v => { if (v === null || v === undefined) return 0; return parseFloat(String(v).replace(/[\s  ]/g, '').replace(',', '.')) || 0; };

  // ── Toast / notifications ─────────────────────────
  window.showToast = function (message, type) {
    const el = $('#inapp-notif');
    if (!el) return alert(message);
    const color = type === 'error' ? '#C62828' : type === 'warning' ? '#E65100' : '#2E7D32';
    $('#inapp-notif-icon').innerHTML = type === 'error' ? '⚠️' : type === 'warning' ? '⚠️' : '✅';
    $('#inapp-notif-title').textContent = message;
    $('#inapp-notif-body').textContent = '';
    el.style.borderLeftColor = color;
    el.style.display = 'flex';
    clearTimeout(window._nt);
    window._nt = setTimeout(() => { el.style.display = 'none'; }, type === 'error' ? 7000 : 3500);
  };

  // ── Modale de confirmation ────────────────────────
  let _cb = null;
  window.showConfirm = function (o) {
    $('#confirm-icon').textContent = o.icon || '⚠️';
    $('#confirm-title').textContent = o.title || 'Confirmer ?';
    $('#confirm-body').textContent = o.body || '';
    const b = $('#confirm-ok');
    b.textContent = o.okLabel || 'Confirmer';
    b.style.background = o.okColor || '#2E7D32';
    _cb = o.onOk || null;
    $('#confirm-modal').style.display = 'flex';
  };
  window.closeConfirm = function () { $('#confirm-modal').style.display = 'none'; _cb = null; };
  document.addEventListener('DOMContentLoaded', () => {
    const ok = $('#confirm-ok');
    if (ok) ok.onclick = async () => { const cb = _cb; closeConfirm(); if (cb) await cb(); };
    const m = $('#confirm-modal');
    if (m) m.addEventListener('click', e => { if (e.target === m) closeConfirm(); });
  });

  // ── Formulaires : envoi sans rechargement, anti double-clic ──
  async function submitAjax(form) {
    const btns = $$('button[type=submit], input[type=submit]', form);
    btns.forEach(b => { b.disabled = true; b.classList.add('is-loading'); b.dataset.label = b.innerHTML; b.innerHTML = '⏳ Enregistrement…'; });
    try {
      const res = await fetch(form.action || location.href, {
        method: 'POST', body: new FormData(form),
        headers: { 'X-Requested-With': 'fetch', 'Accept': 'application/json' }
      });
      let data = {};
      try { data = await res.json(); } catch (e) { data = { ok: false, error: 'Réponse inattendue du serveur (' + res.status + ').' }; }
      if (data.ok) {
        if (data.message) sessionStorage.setItem('flash', data.message);
        if (data.redirect) { location.href = data.redirect; return; }
        location.reload();
        return;
      }
      showToast(data.error || 'Opération refusée.', 'error');
      // un nouveau jeton pour pouvoir corriger et renvoyer
      if (data.form_token) $$('input[name=_ft]', form).forEach(i => i.value = data.form_token);
    } catch (e) {
      showToast('Connexion impossible. Vérifiez le réseau : rien n’a été enregistré.', 'error');
    } finally {
      btns.forEach(b => { b.disabled = false; b.classList.remove('is-loading'); if (b.dataset.label) b.innerHTML = b.dataset.label; });
    }
  }
  document.addEventListener('submit', e => {
    const f = e.target;
    if (f.dataset.confirm && !f.dataset.confirmed) {
      e.preventDefault();
      showConfirm({
        title: f.dataset.confirm, body: f.dataset.confirmBody || '', okLabel: f.dataset.confirmOk || 'Confirmer',
        okColor: f.dataset.confirmColor || '#2E7D32',
        onOk: () => { f.dataset.confirmed = '1'; f.requestSubmit ? f.requestSubmit() : f.submit(); delete f.dataset.confirmed; }
      });
      return;
    }
    if (f.hasAttribute('data-ajax')) { e.preventDefault(); submitAjax(f); return; }
    if (f.method && f.method.toLowerCase() === 'post') {
      $$('button[type=submit]', f).forEach(b => { setTimeout(() => { b.disabled = true; }, 0); });
    }
  });
  document.addEventListener('DOMContentLoaded', () => {
    const m = sessionStorage.getItem('flash');
    if (m) { sessionStorage.removeItem('flash'); showToast(m); }
  });

  // ── Lignes dynamiques ([data-lines] + <template>) ──
  window.addLine = function (containerSel) {
    const c = $(containerSel);
    const tpl = $('template', c.closest('[data-lines-wrap]') || document);
    const tbody = $('tbody', c) || c;
    const node = tpl.content.firstElementChild.cloneNode(true);
    tbody.appendChild(node);
    node.dispatchEvent(new Event('line:added', { bubbles: true }));
    const first = $('select, input', node);
    if (first) first.focus();
    return node;
  };
  document.addEventListener('click', e => {
    const rm = e.target.closest('.btn-remove-line');
    if (rm) {
      const tr = rm.closest('tr');
      const tbody = tr.parentElement;
      if ($$('tr', tbody).length > 1) tr.remove(); else $$('input', tr).forEach(i => i.value = '');
      tbody.dispatchEvent(new Event('input', { bubbles: true }));
    }
  });

  // ── Sidebar mobile & repliable ─────────────────────
  document.addEventListener('DOMContentLoaded', () => {
    const sb = $('.sidebar'), ov = $('#sidebar-overlay'), hb = $('#hamburger-btn');
    const open = () => { sb.classList.add('open'); ov.classList.add('visible'); document.body.style.overflow = 'hidden'; };
    const close = () => { sb.classList.remove('open'); ov.classList.remove('visible'); document.body.style.overflow = ''; };
    if (hb) hb.addEventListener('click', () => sb.classList.contains('open') ? close() : open());
    if (ov) ov.addEventListener('click', close);
    const col = $('#sidebar-collapse-btn');
    if (sb && localStorage.getItem('diobia-sidebar') === 'collapsed' && window.innerWidth > 900) sb.classList.add('collapsed');
    if (col) col.addEventListener('click', () => {
      sb.classList.toggle('collapsed');
      localStorage.setItem('diobia-sidebar', sb.classList.contains('collapsed') ? 'collapsed' : '');
    });
    // Thème
    const tt = $('#theme-toggle');
    const syncIcons = () => {
      const dark = document.documentElement.getAttribute('data-theme') === 'dark';
      if ($('#theme-icon-sun')) { $('#theme-icon-sun').style.display = dark ? 'none' : ''; $('#theme-icon-moon').style.display = dark ? '' : 'none'; }
    };
    syncIcons();
    if (tt) tt.addEventListener('click', () => {
      const dark = document.documentElement.getAttribute('data-theme') === 'dark';
      if (dark) { document.documentElement.removeAttribute('data-theme'); localStorage.setItem('diobia-theme', 'light'); }
      else { document.documentElement.setAttribute('data-theme', 'dark'); localStorage.setItem('diobia-theme', 'dark'); }
      syncIcons();
    });
    // Retour en haut
    const btt = $('#back-to-top');
    if (btt) window.addEventListener('scroll', () => btt.classList.toggle('visible', window.scrollY > 300), { passive: true });
    // Étiquettes des tableaux pour l'affichage en cartes sur mobile
    $$('.table-wrap table.table').forEach(t => {
      const heads = $$('thead th', t).map(th => th.textContent.trim());
      $$('tbody tr', t).forEach(tr => $$('td', tr).forEach((td, i) => { if (!td.dataset.label && heads[i]) td.dataset.label = heads[i]; }));
    });
  });

  // ── Recherche globale ─────────────────────────────
  document.addEventListener('DOMContentLoaded', () => {
    const input = $('#global-search'), box = $('#global-search-results');
    if (!input) return;
    let timer = null, idx = -1;
    input.addEventListener('input', () => {
      clearTimeout(timer);
      const q = input.value.trim();
      if (q.length < 2) { box.classList.remove('visible'); return; }
      timer = setTimeout(async () => {
        const r = await fetch('/api/recherche?q=' + encodeURIComponent(q));
        const d = await r.json();
        idx = -1;
        if (!d.sections.length) { box.innerHTML = '<div class="gsr-empty">Aucun résultat</div>'; box.classList.add('visible'); return; }
        box.innerHTML = d.sections.map(s => `<div class="gsr-section"><div class="gsr-title">${s.titre}</div>` +
          s.items.map(i => `<a class="gsr-item" href="${i.url}"><span class="gsr-item-icon">${i.icone}</span><span class="gsr-item-text"><div class="gsr-item-name">${i.nom}</div><div class="gsr-item-sub">${i.sub || ''}</div></span></a>`).join('') + '</div>').join('');
        box.classList.add('visible');
      }, 180);
    });
    input.addEventListener('keydown', e => {
      const items = $$('.gsr-item', box);
      if (e.key === 'ArrowDown') { e.preventDefault(); idx = Math.min(idx + 1, items.length - 1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); idx = Math.max(idx - 1, 0); }
      else if (e.key === 'Enter' && items[idx]) { e.preventDefault(); location.href = items[idx].href; return; }
      else if (e.key === 'Escape') { box.classList.remove('visible'); input.blur(); return; }
      items.forEach((it, i) => it.classList.toggle('active', i === idx));
    });
    document.addEventListener('click', e => { if (!e.target.closest('#global-search-wrap')) box.classList.remove('visible'); });
    document.addEventListener('keydown', e => {
      if (e.key === '/' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) { e.preventDefault(); input.focus(); }
    });
  });

  // ── Service worker (PWA, mise à jour automatique) ──
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js', { scope: '/' }).then(reg => {
        setInterval(() => reg.update(), 5 * 60 * 1000);
        reg.addEventListener('updatefound', () => {
          const sw = reg.installing;
          sw.addEventListener('statechange', () => { if (sw.state === 'installed' && navigator.serviceWorker.controller) sw.postMessage('SKIP_WAITING'); });
        });
      }).catch(() => {});
    });
  }

  // ── Graphiques (Chart.js) : palette vert / orange ──
  window.DIOBIA_COLORS = { vert: '#2E7D32', vertClair: '#81C784', orange: '#F7941D', orangeClair: '#FFCC80', gris: '#9CA3AF', rouge: '#E53935', bleu: '#1565C0' };
  window.chartDefaults = function () {
    if (!window.Chart) return;
    Chart.defaults.font.family = "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
    Chart.defaults.font.size = 11;
    Chart.defaults.color = getComputedStyle(document.documentElement).getPropertyValue('--text3') || '#6B7280';
    Chart.defaults.plugins.legend.labels.boxWidth = 10;
    Chart.defaults.plugins.tooltip.callbacks.label = ctx => ' ' + (ctx.dataset.label ? ctx.dataset.label + ' : ' : '') + (ctx.dataset.fcfa === false ? nb(ctx.parsed.y ?? ctx.parsed) : fcfa(ctx.parsed.y ?? ctx.parsed));
  };
})();
