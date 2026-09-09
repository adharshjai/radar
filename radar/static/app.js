(function () {
  const post = (url, body) => fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) }).then(r => r.json());

  function removeCard(card) {
    card.classList.add('removing');
    setTimeout(() => {
      const section = card.closest('.section');
      card.remove();
      if (section) {
        const n = section.querySelectorAll('.card').length;
        const count = section.querySelector('.count');
        if (count) count.textContent = n;
      }
    }, 250);
  }

  document.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-act]');
    if (btn) {
      e.preventDefault();
      const card = btn.closest('.card');
      const uid = card.dataset.uid;
      const act = btn.dataset.act;
      if (act === 'snooze-menu') {
        const menu = btn.parentElement.querySelector('.snooze-menu');
        document.querySelectorAll('.snooze-menu').forEach(m => { if (m !== menu) m.hidden = true; });
        menu.hidden = !menu.hidden;
        return;
      }
      if (act === 'note') {
        const current = card.querySelector('.note')?.textContent.replace(/^📝\s*/, '') || '';
        const note = prompt('Note for this item:', current);
        if (note === null) return;
        await post(`/api/item/${encodeURIComponent(uid)}/state`, { action: 'note', note });
        location.reload();
        return;
      }
      if (act === 'delete') {
        if (!confirm('Delete this task?')) return;
        await fetch(`/api/item/${encodeURIComponent(uid)}`, { method: 'DELETE' });
        removeCard(card);
        return;
      }
      if (act === 'snooze') {
        await post(`/api/item/${encodeURIComponent(uid)}/state`, { action: 'snooze', hours: btn.dataset.hours, until: btn.dataset.until });
        removeCard(card);
        return;
      }
      if (act === 'done' || act === 'reopen') {
        const siteIds = (card.dataset.siteIds || '').split(',').filter(Boolean);
        await post(`/api/item/${encodeURIComponent(uid)}/state`, { action: act, site_ids: siteIds });
        if (act === 'reopen') location.reload(); else removeCard(card);
        return;
      }
    } else if (!e.target.closest('.snooze-menu')) {
      document.querySelectorAll('.snooze-menu').forEach(m => m.hidden = true);
    }
    const sc = e.target.closest('[data-site-check]');
    if (sc) { await post(`/sites/${sc.dataset.siteCheck}/check`); location.reload(); }
    const sh = e.target.closest('[data-site-hide]');
    if (sh) { await post(`/sites/${sh.dataset.siteHide}/hide`); sh.closest('tr').remove(); }
  });

  document.addEventListener('change', async (e) => {
    const sel = e.target.closest('select[data-every]');
    if (sel) await post(`/sites/${sel.dataset.every}/every`, { days: parseInt(sel.value, 10) });
  });

  const addForm = document.getElementById('add-task');
  if (addForm) addForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(addForm);
    const body = Object.fromEntries(fd.entries());
    const r = await post('/api/item', body);
    if (r.ok) location.reload();
  });

  // ---- sync
  const syncBtn = document.getElementById('sync-btn');
  const syncInfo = document.getElementById('sync-info');
  const logBox = document.getElementById('sync-log');
  let polling = null;
  function poll() {
    fetch('/api/sync/status').then(r => r.json()).then(s => {
      logBox.hidden = false;
      logBox.querySelector('pre').textContent = (s.log || []).join('\n') || 'starting…';
      logBox.scrollTop = logBox.scrollHeight;
      if (s.syncing) { syncBtn.textContent = 'Syncing…'; syncBtn.disabled = true; }
      else {
        clearInterval(polling); polling = null;
        syncBtn.textContent = 'Sync now'; syncBtn.disabled = false;
        syncInfo.textContent = 'synced just now';
        setTimeout(() => location.reload(), 900);
      }
    });
  }
  if (syncBtn) syncBtn.addEventListener('click', async () => {
    const r = await post('/api/sync');
    if (r.ok === false) { alert(r.error || 'Sync failed to start'); return; }
    if (!polling) polling = setInterval(poll, 1500);
    poll();
  });
  if (syncBtn && syncBtn.textContent.trim() === 'Syncing…' && !polling) polling = setInterval(poll, 1500);
  const liveLog = document.getElementById('live-log');
  if (liveLog) setInterval(() => fetch('/api/sync/status').then(r => r.json()).then(s => { liveLog.textContent = (s.log || []).join('\n'); }), 2000);

  // ---- "new" tracking: after you've looked at Today for a bit, remember the visit
  window.radarVisited = function () {
    const mark = () => navigator.sendBeacon ? navigator.sendBeacon('/visited', new Blob([], { type: 'application/json' })) : fetch('/visited', { method: 'POST' });
    setTimeout(mark, 45000);
    document.addEventListener('visibilitychange', () => { if (document.hidden) mark(); });
    window.addEventListener('pagehide', mark);
  };
})();
