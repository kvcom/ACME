/* Shared OpenTelemetry trace popover.
 *
 * Used by the DB Explorer and the Trace viewer. Any `.otel-btn` element with
 * a `data-otel` (trace id) and `data-otel-url` (JSON endpoint) attribute will,
 * on click, open a popover showing the trace metadata + reconstructed span
 * timeline, with a link to the full decision-trace viewer.
 *
 * The endpoint differs per page so authorization stays correct:
 *   - DB Explorer (admin-only): /db-explorer/otel/{otel_trace_id}
 *   - Trace viewer (admin or owner): /traces/{trace_ref}/otel
 * Both return the same JSON shape.
 *
 * Self-contained: creates its own #otel-pop element and delegates clicks on
 * document, so a page only needs to (a) link app.css, (b) include this script,
 * and (c) render an `.otel-btn`.
 */
(function () {
  'use strict';

  function esc(s) {
    return String(s).replace(/[&<>"']/g, c => (
      {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]
    ));
  }

  let pop = document.getElementById('otel-pop');
  if (!pop) {
    pop = document.createElement('div');
    pop.id = 'otel-pop';
    document.body.appendChild(pop);
  }

  function close() { pop.classList.remove('open'); }

  function position(anchorEl) {
    const r = anchorEl.getBoundingClientRect();
    let left = r.left;
    let top = r.bottom + 6;
    const pw = pop.offsetWidth, ph = pop.offsetHeight;
    if (left + pw > window.innerWidth - 12) left = Math.max(12, window.innerWidth - pw - 12);
    if (top + ph > window.innerHeight - 12) top = Math.max(12, r.top - ph - 6);
    pop.style.left = left + 'px';
    pop.style.top = top + 'px';
  }

  function spanRows(spans) {
    if (!spans || !spans.length) return '<div class="empty-small">No spans recorded.</div>';
    return spans.map(s => {
      const ok = (s.status || 'ok') === 'ok';
      const ms = (s.latency_ms != null) ? `${s.latency_ms} ms` : '·';
      return `<div class="span">
        <span><span class="nm">${esc(s.event_name || '')}</span> <span class="ty">${esc(s.event_type || '')}</span></span>
        <span class="ms">${ms}</span>
        <span class="st ${ok ? 'ok' : 'bad'}">${esc(s.status || 'ok')}</span>
      </div>`;
    }).join('');
  }

  async function open(btn) {
    const url = btn.dataset.otelUrl;
    if (!url) return;
    pop.innerHTML = '<div class="loading">Loading trace…</div>';
    pop.classList.add('open');
    position(btn);
    try {
      const resp = await fetch(url);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const d = await resp.json();
      const cost = (d.estimated_cost_usd != null) ? `$${Number(d.estimated_cost_usd).toFixed(4)}` : '·';
      const model = (d.llm_provider || '') + (d.llm_model ? ' / ' + d.llm_model : '');
      pop.innerHTML = `
        <h3>OpenTelemetry Trace</h3>
        <div class="otel-id">${esc(d.otel_trace_id || '(none recorded)')}</div>
        <div class="otel-meta">
          <span>intent <b>${esc(d.detected_intent || '—')}</b></span>
          <span>status <b>${esc(d.final_status || '—')}</b></span>
          <span>model <b>${esc(model || '—')}</b></span>
          <span>total <b>${d.total_latency_ms != null ? d.total_latency_ms + ' ms' : '·'}</b></span>
          <span>llm <b>${d.llm_latency_ms != null ? d.llm_latency_ms + ' ms' : '·'}</b></span>
          <span>tools <b>${d.tool_latency_ms != null ? d.tool_latency_ms + ' ms' : '·'}</b></span>
          <span>tokens <b>${d.total_tokens != null ? d.total_tokens : '·'}</b></span>
          <span>cost <b>${cost}</b></span>
        </div>
        <h3>Spans (${(d.spans || []).length})</h3>
        ${spanRows(d.spans)}
        <div class="otel-foot">
          <button class="copy-btn" data-copy="${esc(d.otel_trace_id || '')}">Copy trace id</button>
          ${d.jaeger_url ? `<a class="otel-link" href="${esc(d.jaeger_url)}" target="_blank">Open in Jaeger ↗</a>` : ''}
          <a class="otel-link" href="/traces/${encodeURIComponent(d.trace_ref)}" target="_blank">Open full decision trace ↗</a>
        </div>`;
      position(btn);
      const copyBtn = pop.querySelector('.copy-btn');
      if (copyBtn) copyBtn.addEventListener('click', () => {
        if (navigator.clipboard) navigator.clipboard.writeText(copyBtn.dataset.copy || '');
        copyBtn.textContent = 'Copied ✓';
        setTimeout(() => { copyBtn.textContent = 'Copy trace id'; }, 1500);
      });
    } catch (err) {
      pop.innerHTML = `<div class="err">Could not load trace: ${esc(err.message)}</div>`;
    }
  }

  // Delegated open on any .otel-btn anywhere in the document.
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('.otel-btn');
    if (btn) { e.stopPropagation(); open(btn); return; }
    // Outside-click dismiss.
    if (pop.classList.contains('open') && !pop.contains(e.target)) close();
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
})();
