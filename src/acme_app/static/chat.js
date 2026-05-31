(() => {
  const queryEl = document.getElementById('query');
  const sendBtn = document.getElementById('send');
  const threadEl = document.getElementById('thread');
  const railEl   = document.getElementById('rail');
  const welcomeEl = document.getElementById('welcome');
  const composer = document.querySelector('.composer');
  const providerPill = document.getElementById('provider-pill');
  const providerPop  = document.getElementById('provider-pop');
  const providerNameEl = document.getElementById('provider-name');
  const sbSearch = document.getElementById('sb-search');

  if (!queryEl || !sendBtn || !threadEl || !composer) {
    console.warn('[acme] chat.js: critical element missing', {queryEl, sendBtn, threadEl, composer});
    return;
  }

  // Persisted model choice (localStorage), default to server-rendered value.
  // We store the model_key (e.g. "claude-sonnet-4") not the display label.
  const STORAGE_KEY = 'acme_model_key';
  const PREVIOUS_DEFAULT_MODEL_KEYS = new Set(['gpt-5.4-mini']);
  const serverDefaultModelKey = document.body.dataset.modelKey || 'claude-opus-4-8';
  let currentModelKey = (() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      return PREVIOUS_DEFAULT_MODEL_KEYS.has(stored) ? null : stored;
    } catch { return null; }
  })() || serverDefaultModelKey;
  if (!document.querySelector(`.provider-opt[data-model-key="${CSS.escape(currentModelKey)}"]`)) {
    currentModelKey = serverDefaultModelKey || document.querySelector('.provider-opt')?.dataset.modelKey || 'claude-opus-4-8';
    try { localStorage.setItem(STORAGE_KEY, currentModelKey); } catch {}
  }
  try { setProvider(currentModelKey); } catch (e) { console.warn('[acme] setProvider failed', e); }

  function escape(s) {
    return String(s ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  }
  function modelKey() { return currentModelKey || serverDefaultModelKey; }
  function convRef()  { return document.body.dataset.conversationRef || 'CONV-DEMO'; }
  const BADGE_CLASS = {
    'Grounded': 'grounded',
    'Partially Grounded': 'partial',
    'Needs Review': 'needsreview',
    'Permission Denied': 'denied',
    'Action Proposed': 'proposed',
    'Confirmation Required': 'proposed',
    'Action Created': 'created',
    'Action Cancelled': 'cancelled',
    'Insufficient Evidence': 'insufficient',
    'Conversational': 'neutral',
    'Clarification Required': 'clarify',
    'Adversarial Input Blocked': 'adversarial',
    'LLM Unavailable': 'needsreview',
    'Resolution Required': 'needsreview',
  };

  function badgeClass(b) {
    const suffix = BADGE_CLASS[b || ''] || 'neutral';
    return `badge badge-${suffix}`;
  }

  function setProvider(key) {
    currentModelKey = key;
    const opt = document.querySelector(`.provider-opt[data-model-key="${CSS.escape(key)}"]`);
    const label = opt ? (opt.dataset.label || key) : key;
    if (providerNameEl) providerNameEl.textContent = label;
    try { localStorage.setItem(STORAGE_KEY, key); } catch {}
    document.querySelectorAll('.provider-opt').forEach(el => {
      el.classList.toggle('active', el.dataset.modelKey === key);
    });
  }

  // ── Provider popover ──────────────────────────────────────────────────
  if (providerPill && providerPop) {
    providerPill.addEventListener('click', e => {
      e.stopPropagation();
      providerPop.classList.toggle('open');
    });
    document.querySelectorAll('.provider-opt').forEach(opt => {
      opt.addEventListener('click', e => {
        e.stopPropagation();
        setProvider(opt.dataset.modelKey);
        providerPop.classList.remove('open');
      });
    });
    document.addEventListener('click', e => {
      if (!providerPop.contains(e.target) && e.target !== providerPill) {
        providerPop.classList.remove('open');
      }
    });
  }

  // ── Sidebar search ────────────────────────────────────────────────────
  if (sbSearch) {
    sbSearch.addEventListener('input', () => {
      const q = sbSearch.value.trim().toLowerCase();
      document.querySelectorAll('#conv-list .conv-item').forEach(item => {
        const title = (item.dataset.convTitle || '').toLowerCase();
        item.style.display = !q || title.includes(q) ? '' : 'none';
      });
      document.querySelectorAll('#conv-list .conv-group').forEach(grp => {
        const anyVisible = [...grp.querySelectorAll('.conv-item')].some(i => i.style.display !== 'none');
        grp.style.display = anyVisible ? '' : 'none';
      });
    });
  }

  // ── Welcome prompt cards ──────────────────────────────────────────────
  document.querySelectorAll('.prompt-card').forEach(card => {
    card.addEventListener('click', () => {
      const prompt = card.dataset.prompt || '';
      queryEl.value = prompt;
      send();
    });
  });

  // ── Thread helpers ────────────────────────────────────────────────────
  function hideWelcome() { if (welcomeEl) welcomeEl.style.display = 'none'; }

  let autoFollow = true;
  let sending = false;

  function nearThreadBottom(threshold = 140) {
    return threadEl.scrollHeight - threadEl.scrollTop - threadEl.clientHeight < threshold;
  }

  function followThread(force = false) {
    if (!force && !autoFollow) return;
    requestAnimationFrame(() => {
      threadEl.scrollTop = threadEl.scrollHeight;
    });
  }

  function scrollToLatestOnLoad() {
    requestAnimationFrame(() => {
      threadEl.scrollTop = threadEl.scrollHeight;
    });
  }

  threadEl.addEventListener('scroll', () => {
    if (!sending) return;
    autoFollow = nearThreadBottom();
  }, {passive: true});

  function appendUserTurn(text) {
    const div = document.createElement('div');
    div.className = 'msg msg-user';
    div.innerHTML = `<div class="msg-body-user">${escape(text)}</div>`;
    threadEl.insertBefore(div, composer);
    // Anchor the new turn near the top of the visible thread so the user can
    // still see their prompt above the bot's response. ChatGPT-style.
    requestAnimationFrame(() => div.scrollIntoView({block: 'start', behavior: 'smooth'}));
    return div;
  }

  function startBotTurn() {
    const wrap = document.createElement('div');
    wrap.className = 'msg msg-bot';
    wrap.innerHTML = `
      <div class="msg-body-bot">
        <div class="plan-quiet" data-role="plan">
          <div class="ph"><span><b>Planning</b></span><span data-role="elapsed">…</span></div>
          <div class="ps" data-role="steps"></div>
        </div>
        <div data-role="answer-region"></div>
      </div>`;
    threadEl.insertBefore(wrap, composer);
    return wrap;
  }

  function highlightResponse(traceRef, active, scroll = false) {
    if (!traceRef) return;
    const turn = threadEl.querySelector(`.msg-bot[data-trace-ref="${CSS.escape(traceRef)}"]`);
    if (!turn) return;
    turn.classList.toggle('evidence-hover', Boolean(active));
    if (active && scroll) {
      turn.scrollIntoView({block: 'center', behavior: 'smooth'});
    }
  }

  // Pulsing dots under the plan card while the LLM is composing the answer.
  function ensureComposing(planEl) {
    if (planEl.querySelector('.composing')) return;
    const div = document.createElement('div');
    div.className = 'composing';
    div.innerHTML = '<span class="dot"></span><span class="dot"></span><span class="dot"></span><span class="label">thinking</span>';
    planEl.appendChild(div);
  }
  function removeComposing(planEl) {
    const el = planEl.querySelector('.composing');
    if (el) el.remove();
  }

  const activeTypewriters = new Set();

  function flushTypewriters() {
    activeTypewriters.forEach(job => job.finish());
  }
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) flushTypewriters();
  });

  // Type out the answer character-by-character. Background tabs throttle JS
  // timers heavily, so we finish any active typewriter when the page is hidden;
  // the completed response is then waiting when the user returns.
  function typewriter(el, text, speed = 12, onUpdate = null) {
    return new Promise(resolve => {
      el.textContent = '';
      let i = 0;
      let timer = null;
      let done = false;
      const fullText = String(text || '');
      const render = value => {
        if (onUpdate) {
          onUpdate(value);
        } else {
          el.textContent = value;
        }
      };
      const finish = () => {
        if (done) return;
        done = true;
        if (timer) clearTimeout(timer);
        render(fullText);
        activeTypewriters.delete(job);
        resolve();
      };
      const job = {finish};
      activeTypewriters.add(job);
      if (document.hidden) {
        finish();
        return;
      }
      function step() {
        if (done) return;
        if (document.hidden || i >= fullText.length) {
          finish();
          return;
        }
        const ch = fullText[i++];
        render(fullText.slice(0, i));
        // Briefly pause on sentence punctuation for a more natural cadence.
        const delay = /[.!?]/.test(ch) ? speed * 8
                    : /[,;:]/.test(ch) ? speed * 4
                    : speed;
        timer = setTimeout(step, delay);
      }
      step();
    });
  }

  // Some smaller local LLMs emit markdown markers inline without any line
  // breaks: "...today: ### Open Issues: 1. **X** - ..."
  // Normalise these into proper newline-anchored markdown before parsing.
  function normaliseMarkdown(text) {
    let t = String(text || '');
    // Header markers (#### / ### / ## / #) preceded by inline text → push to new line.
    t = t.replace(/([^\n])\s+(#{1,4}\s+[A-Z])/g, '$1\n\n$2');
    // Bullet ("- " or "* ") preceded by inline text → push to new line.
    t = t.replace(/([^\n])\s+([-*]\s+)(?=[A-Za-z*"`])/g, '$1\n$2');
    // Numbered list item ("1. ", "2. " ...) preceded by inline text → new line.
    t = t.replace(/([^\n])\s+(\d{1,2}\.\s+)(?=[A-Za-z*"`])/g, '$1\n$2');
    // Hyphenated inline detail lists "- item - item" within a single bullet
    // line shouldn't trigger; the lookahead on a capital letter/punct above
    // already guards against most false positives.
    // Collapse 3+ consecutive newlines.
    t = t.replace(/\n{3,}/g, '\n\n');
    return t;
  }

  // Minimal markdown → HTML for the bot answer. Handles headers (### / ## / #),
  // **bold**, *italic*, `code`, bullet lists and numbered lists, paragraph
  // breaks. We type the raw text first (so the caret animation is preserved),
  // then swap to the rendered HTML when the typewriter finishes.
  function renderMarkdown(text) {
    const normalised = normaliseMarkdown(text);
    let html = normalised.replace(/[&<>'"]/g, c => (
      {'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]
    ));
    html = html.replace(/^####\s+(.+)$/gm, '<h5>$1</h5>')
               .replace(/^###\s+(.+)$/gm, '<h4>$1</h4>')
               .replace(/^##\s+(.+)$/gm, '<h3>$1</h3>')
               .replace(/^#\s+(.+)$/gm, '<h2>$1</h2>');
    html = html.replace(/`([^`\n]+)`/g, '<code>$1</code>');
    html = html.replace(/\*\*([^*\n]+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/(?<![*\w])\*([^*\n]+?)\*(?!\*)/g, '<em>$1</em>');
    // Lists — handle "- " and numbered "1. " items.
    html = html.replace(/^\s*[-*]\s+(.+)$/gm, '<li>$1</li>');
    html = html.replace(/^\s*\d{1,2}\.\s+(.+)$/gm, '<li>$1</li>');
    html = html.replace(/(<li>[\s\S]*?<\/li>(?:\n<li>[\s\S]*?<\/li>)*)/g,
                        m => '<ul>' + m.replace(/\n/g, '') + '</ul>');
    const blocks = html.split(/\n{2,}/).map(b => {
      const trimmed = b.trim();
      if (!trimmed) return '';
      if (/^<(h\d|ul|ol|pre|blockquote)/.test(trimmed)) return trimmed;
      return '<p>' + trimmed.replace(/\n/g, '<br>') + '</p>';
    });
    return blocks.join('');
  }

  // Render markdown on bot messages that were rendered server-side from
  // conversation history (they bypass the typewriter / renderAnswerTyped path).
  function renderHistoricalAnswers() {
    threadEl.querySelectorAll('.msg-body-bot > p').forEach(p => {
      if (p.closest('.md-answer')) return;            // already rendered
      const raw = p.textContent;
      if (!raw || !/[*#`\-]/.test(raw)) return;       // no markdown to render
      const wrap = document.createElement('div');
      wrap.className = 'md-answer';
      wrap.innerHTML = renderMarkdown(raw);
      p.replaceWith(wrap);
    });
  }
  renderHistoricalAnswers();
  scrollToLatestOnLoad();

  function confirmDialog({title, message, confirmLabel = 'OK', cancelLabel = 'Cancel'} = {}) {
    return new Promise(resolve => {
      const overlay = document.createElement('div');
      overlay.className = 'confirm-modal-backdrop';
      overlay.innerHTML = `
        <div class="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="confirm-title">
          <div class="confirm-modal-head">
            <div>
              <div class="label" id="confirm-title">${escape(title || 'Confirm')}</div>
              <div class="confirm-modal-title">${escape(message || '')}</div>
            </div>
          </div>
          <div class="confirm-modal-foot">
            <button type="button" class="btn btn-sm" data-role="cancel">${escape(cancelLabel)}</button>
            <button type="button" class="btn btn-sm btn-primary" data-role="confirm">${escape(confirmLabel)}</button>
          </div>
        </div>`;

      const finish = value => {
        document.removeEventListener('keydown', onKey);
        overlay.remove();
        resolve(value);
      };
      const onKey = ev => {
        if (ev.key === 'Escape') finish(false);
      };
      overlay.addEventListener('click', ev => {
        if (ev.target === overlay) finish(false);
      });
      overlay.querySelector('[data-role="cancel"]').addEventListener('click', () => finish(false));
      overlay.querySelector('[data-role="confirm"]').addEventListener('click', () => finish(true));
      document.addEventListener('keydown', onKey);
      document.body.appendChild(overlay);
      requestAnimationFrame(() => overlay.querySelector('[data-role="confirm"]')?.focus());
    });
  }

  // Sidebar delete button — soft-delete (trace data preserved per D-015).
  document.querySelectorAll('.conv-delete').forEach(btn => {
    btn.addEventListener('click', async ev => {
      ev.preventDefault();
      ev.stopPropagation();
      const ref = btn.dataset.convRef;
      if (!ref) return;
      const ok = await confirmDialog({
        title: 'Hide conversation',
        message: 'Hide this conversation from the sidebar? Trace history is preserved for audit and remains available in Traces.',
        confirmLabel: 'Hide',
        cancelLabel: 'Cancel',
      });
      if (!ok) return;
      const resp = await fetch(`/conversations/${encodeURIComponent(ref)}`, {method: 'DELETE'});
      if (!resp.ok) {
        alert('Could not delete: ' + resp.status);
        return;
      }
      const wrap = btn.closest('.conv-item-wrap');
      if (wrap) wrap.remove();
      // If the user just deleted the active conversation, redirect to a fresh chat.
      if (ref === document.body.dataset.conversationRef) {
        location.href = '/chat';
      }
    });
  });

  // If the server restored a stale-pending proposed action from PostgreSQL,
  // put the Confirm card back inline on the matching answer. The HMAC token
  // has already been re-minted server-side and staged back into Redis.
  (function restorePendingAction() {
    const raw = document.body.dataset.pendingAction;
    if (!raw) return;
    try {
      const pa = JSON.parse(raw);
      if (pa && pa.confirmation_token && pa.action_type) {
        restoreInlinePendingAction(pa);
      }
    } catch (e) {
      console.warn('[acme] failed to parse pending_action', e);
    }
  })();

  document.querySelectorAll('[data-show-evidence]').forEach(btn => {
    btn.addEventListener('click', () => {
      try {
        addEvidenceGroup(JSON.parse(btn.dataset.showEvidence || '[]'), {
          label: 'Response',
          traceRef: btn.dataset.traceRef || '',
          focus: true,
        });
      } catch (e) {
        console.warn('[acme] failed to parse history evidence', e);
      }
    });
  });

  async function renderAnswerTyped(answerRegion, r) {
    const wrap = document.createElement('div');
    wrap.className = 'md-answer';
    answerRegion.appendChild(wrap);
    await typewriter(wrap, r.answer || '', 12, partial => {
      wrap.innerHTML = renderMarkdown(partial);
      followThread();
    });
    // Final render without the caret, using the complete normalised markdown.
    wrap.innerHTML = renderMarkdown(r.answer || '');
    followThread();
  }

  function renderStreamFailure(answerRegion, message) {
    const wrap = document.createElement('div');
    wrap.className = 'md-answer';
    wrap.innerHTML = renderMarkdown(
      `### Response interrupted\n\n- The request did not finish, so no trace was saved for this turn.\n- ${message || 'Please try the message again.'}`
    );
    answerRegion.appendChild(wrap);
    followThread();
  }

  function addStep(planEl, key, label, state) {
    const steps = planEl.querySelector('[data-role="steps"]');
    let row = steps.querySelector(`[data-key="${CSS.escape(key)}"]`);
    if (!row) {
      row = document.createElement('div');
      row.className = 'pl';
      row.dataset.key = key;
      row.innerHTML = `<span class="pi"></span><span data-role="label"></span>`;
      steps.appendChild(row);
    }
    row.className = 'pl ' + (state || '');
    row.querySelector('.pi').textContent = state === 'ok' ? '✓'
        : state === 'run' ? '◐'
        : state === 'fail' ? '×'
        : '○';
    row.querySelector('[data-role="label"]').innerHTML = label;
    return row;
  }

  // ── Right-rail panels ─────────────────────────────────────────────────
  let evidenceGroupCounter = 0;
  const evidenceRecordCache = new Map();
  let evidencePopoverTimer = null;

  function clearRail(options = {}) {
    if (!options.keepEvidence) {
      railEl.innerHTML = '';
      evidenceGroupCounter = 0;
      return;
    }
  }

  function evidenceParts(item) {
    const raw = String(item || '');
    if (raw.includes(':')) {
      const [kind, id] = raw.split(':', 2);
      return {kind, id};
    }
    if (/^ISS-\d+/i.test(raw)) return {kind: 'issue', id: raw.toUpperCase()};
    if (/^UPD-\d+/i.test(raw)) return {kind: 'update', id: raw.toUpperCase()};
    if (/^CUST[-_]/i.test(raw)) return {kind: 'customer', id: raw};
    return {kind: 'record', id: raw};
  }

  function evidenceKindLabel(kind) {
    const labels = {
      issue: 'issue',
      update: 'issue update',
      customer: 'customer',
      action: 'action',
      next_action: 'action',
      user: 'user + roles',
      action_policy: 'action policy',
      action_catalogue: 'action policy',
    };
    return labels[kind] || kind || 'record';
  }

  function ensureEvidencePopover() {
    let pop = document.querySelector('[data-role="evidence-popover"]');
    if (!pop) {
      pop = document.createElement('div');
      pop.className = 'evidence-popover';
      pop.dataset.role = 'evidence-popover';
      pop.addEventListener('mouseenter', () => {
        if (evidencePopoverTimer) clearTimeout(evidencePopoverTimer);
      });
      pop.addEventListener('mouseleave', scheduleEvidencePopoverHide);
      document.body.appendChild(pop);
    }
    return pop;
  }

  function scheduleEvidencePopoverHide() {
    if (evidencePopoverTimer) clearTimeout(evidencePopoverTimer);
    evidencePopoverTimer = setTimeout(hideEvidencePopover, 140);
  }

  function hideEvidencePopover() {
    if (evidencePopoverTimer) {
      clearTimeout(evidencePopoverTimer);
      evidencePopoverTimer = null;
    }
    const pop = document.querySelector('[data-role="evidence-popover"]');
    if (pop) pop.classList.remove('open');
  }

  function idealEvidencePopoverWidth(data = null) {
    const record = data?.record || {};
    const values = Object.values(record).map(value => (
      value === null || value === undefined ? '' :
      typeof value === 'object' ? JSON.stringify(value) : String(value)
    ));
    const longest = values.reduce((max, value) => Math.max(max, value.length), 0);
    const fields = values.length;
    if (longest > 120) return 660;
    if (longest > 72 || fields > 12) return 560;
    if (longest > 44 || fields > 8) return 480;
    return 400;
  }

  function positionEvidencePopover(pop, anchor, data = null) {
    const rect = anchor.getBoundingClientRect();
    const preferred = idealEvidencePopoverWidth(data);
    const spaceLeft = rect.left - 24;
    const spaceRight = window.innerWidth - rect.right - 24;
    const width = Math.max(340, Math.min(preferred, Math.max(spaceLeft, spaceRight)));
    const openLeft = spaceLeft >= spaceRight;
    const gap = 6;
    const left = openLeft
      ? Math.max(12, rect.left - width - gap)
      : Math.min(window.innerWidth - width - 12, rect.right + gap);
    const maxHeight = Math.min(520, Math.max(260, window.innerHeight - 24));
    const estimatedHeight = Math.min(maxHeight, pop.offsetHeight || 260);
    const top = Math.max(12, Math.min(rect.top, window.innerHeight - estimatedHeight - 12));
    pop.style.width = `${width}px`;
    pop.style.maxHeight = `${maxHeight}px`;
    pop.style.left = `${left}px`;
    pop.style.top = `${top}px`;
  }

  function renderEvidenceRecord(record) {
    const rows = Object.entries(record || {}).map(([key, value]) => {
      const text = value === null || value === undefined ? 'NULL'
        : typeof value === 'object' ? JSON.stringify(value)
        : String(value);
      return `<div class="evidence-popover-row">
        <span class="k">${escape(key)}</span>
        <span class="v">${escape(text)}</span>
      </div>`;
    }).join('');
    return rows || '<div class="dim">No fields returned.</div>';
  }

  async function showEvidencePopover(anchor) {
    const evidence = anchor.dataset.evidenceRef;
    if (!evidence) return;
    const pop = ensureEvidencePopover();
    positionEvidencePopover(pop, anchor);
    pop.innerHTML = `
      <div class="evidence-popover-head">
        <span class="label">Evidence record</span>
        <span class="mono dim">${escape(evidence)}</span>
      </div>
      <div class="evidence-popover-body"><span class="dim">Loading…</span></div>`;
    pop.classList.add('open');

    try {
      let data = evidenceRecordCache.get(evidence);
      if (!data) {
        const resp = await fetch(`/evidence/record?evidence=${encodeURIComponent(evidence)}`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        data = await resp.json();
        evidenceRecordCache.set(evidence, data);
      }
      pop.innerHTML = `
        <div class="evidence-popover-head">
          <span class="label">${escape(data.table || 'Record')}</span>
          <span class="mono dim">${escape(data.evidence || evidence)}</span>
        </div>
        <div class="evidence-popover-body">${renderEvidenceRecord(data.record)}</div>`;
      positionEvidencePopover(pop, anchor, data);
    } catch (e) {
      pop.innerHTML = `
        <div class="evidence-popover-head">
          <span class="label">Evidence record</span>
          <span class="mono dim">${escape(evidence)}</span>
        </div>
        <div class="evidence-popover-body"><span class="dim">Record not available.</span></div>`;
    }
  }

  function bindEvidenceItemPopover(itemEl) {
    itemEl.tabIndex = 0;
    itemEl.addEventListener('mouseenter', () => {
      if (evidencePopoverTimer) clearTimeout(evidencePopoverTimer);
      evidencePopoverTimer = setTimeout(() => showEvidencePopover(itemEl), 180);
    });
    itemEl.addEventListener('mouseleave', scheduleEvidencePopoverHide);
    itemEl.addEventListener('focus', () => showEvidencePopover(itemEl));
    itemEl.addEventListener('blur', scheduleEvidencePopoverHide);
  }

  function ensureEvidencePanel() {
    let panel = railEl.querySelector('[data-role="evidence-panel"]');
    if (!panel) {
      railEl.insertAdjacentHTML('afterbegin', `
        <div class="panel" data-role="evidence-panel">
          <div class="panel-header">
            <span class="label">Evidence</span>
            <span class="mono dim" style="font-size:10px;" data-role="ev-count">0 records</span>
          </div>
          <div class="panel-body" style="padding: 6px; display:grid; gap:8px;" data-role="ev-list"></div>
        </div>`);
      panel = railEl.querySelector('[data-role="evidence-panel"]');
    }
    return panel;
  }

  function updateEvidenceCount(panel) {
    const groups = [...panel.querySelectorAll('[data-role="ev-group"]')];
    const total = groups.reduce((sum, group) => sum + Number(group.dataset.count || 0), 0);
    panel.querySelector('[data-role="ev-count"]').textContent =
      `${total} record${total === 1 ? '' : 's'} · ${groups.length} response${groups.length === 1 ? '' : 's'}`;
  }

  function addEvidenceGroup(items, meta = {}) {
    if (!items || !items.length) return;
    const panel = ensureEvidencePanel();
    const list = panel.querySelector('[data-role="ev-list"]');
    const key = meta.traceRef || `local-${++evidenceGroupCounter}`;
    let group = list.querySelector(`[data-role="ev-group"][data-key="${CSS.escape(key)}"]`);
    const existingItems = group
      ? [...group.querySelectorAll('[data-evidence-ref]')].map(el => el.dataset.evidenceRef).filter(Boolean)
      : [];
    const mergedItems = [...new Set([...existingItems, ...items.map(item => String(item || ''))])].filter(Boolean);
    if (!group) {
      group = document.createElement('div');
      group.dataset.role = 'ev-group';
      group.dataset.key = key;
      group.tabIndex = 0;
      group.style.cssText = 'border:1px solid var(--border-subtle);border-radius:var(--radius-md);padding:8px 8px 6px;background:rgba(255,255,255,0.015);';
      list.appendChild(group);
    }
    group.dataset.traceRef = meta.traceRef || '';
    if (meta.traceRef && group.dataset.highlightBound !== '1') {
      group.dataset.highlightBound = '1';
      group.addEventListener('mouseenter', () => highlightResponse(group.dataset.traceRef, true, true));
      group.addEventListener('mouseleave', () => highlightResponse(group.dataset.traceRef, false));
      group.addEventListener('focus', () => highlightResponse(group.dataset.traceRef, true, true));
      group.addEventListener('blur', () => highlightResponse(group.dataset.traceRef, false));
    }
    group.dataset.count = String(mergedItems.length);
    const trace = meta.traceRef
      ? `<a href="/traces/${encodeURIComponent(meta.traceRef)}">${escape(meta.traceRef)}</a>`
      : '<span class="dim">unsaved</span>';
    group.innerHTML = `
      <div class="trace-meta" style="margin:0 0 6px;">
        <span>${escape(meta.label || 'Response')}</span>
        <span class="sep">·</span>${trace}
      </div>
      <div data-role="ev-items"></div>`;
    const itemList = group.querySelector('[data-role="ev-items"]');
    mergedItems.slice(0, 20).forEach(item => {
      const {kind, id} = evidenceParts(item);
      const li = document.createElement('div');
      li.className = 'ev-item';
      li.dataset.evidenceRef = String(item || '');
      li.innerHTML = `<span class="ref">${escape(id.slice(0, 14))}</span><span class="desc">${escape(evidenceKindLabel(kind))}</span>`;
      bindEvidenceItemPopover(li);
      itemList.appendChild(li);
    });
    updateEvidenceCount(panel);
    if (meta.focus) {
      group.scrollIntoView({block: 'nearest'});
      group.style.borderColor = 'var(--accent-border)';
      setTimeout(() => { group.style.borderColor = 'var(--border-subtle)'; }, 900);
    }
  }

  (function restoreConversationEvidence() {
    document.querySelectorAll('[data-show-evidence]').forEach((btn, index) => {
      try {
        addEvidenceGroup(JSON.parse(btn.dataset.showEvidence || '[]'), {
          label: `Response ${index + 1}`,
          traceRef: btn.dataset.traceRef || '',
        });
      } catch (e) {
        console.warn('[acme] failed to parse conversation evidence', e);
      }
    });
  })();

  function proposedActionHtml(pa) {
    const dueText = pa.due_at ? new Date(pa.due_at).toLocaleString() : '—';
    return `
      <div class="action-card action-card-inline" data-role="action-card" data-token="${escape(pa.confirmation_token)}">
        <div class="ah">
          <div>
            <div class="label" style="color: var(--accent-dim);">Confirmation required</div>
            <div class="mono" style="font-size:11px; color: var(--text-high); margin-top:2px;">${escape(pa.action_type)}</div>
          </div>
        </div>
        <div class="ab">
          <div class="action-row"><span class="k">Customer</span> <span class="v">${escape(pa.customer_name || '—')}</span></div>
          <div class="action-row"><span class="k">Issue</span>    <span class="v">${escape(pa.issue_ref)}</span></div>
          <div class="action-row"><span class="k">Priority</span> <span class="v" style="color: var(--color-danger-dim);">${escape(pa.priority)}</span></div>
          <div class="action-row"><span class="k">Due</span>      <span class="v">${escape(dueText)}</span></div>
          <div style="margin-top: 12px; padding-top: 10px; border-top: 1px dashed var(--border-subtle);">
            <div class="label" style="margin-bottom: 6px;">Why</div>
            <p style="font-size: 11px; color: var(--text-secondary); line-height: 1.5;">${escape(pa.rationale || '')}</p>
          </div>
          ${pa.evidence && pa.evidence.length ? `
          <div style="margin-top: 10px;">
            <div class="label" style="margin-bottom: 6px;">Evidence</div>
            <div style="font-family: var(--font-mono); font-size: 10px; color: var(--text-muted); line-height: 1.6;">
              ${pa.evidence.map(e => escape(e)).join(' · ')}
            </div>
          </div>` : ''}
        </div>
        <div class="af">
          <button class="btn btn-sm" data-cancel="1" data-token="${escape(pa.confirmation_token)}">Cancel</button>
          <button class="btn btn-sm btn-primary" data-confirm="${escape(pa.confirmation_token)}">Confirm</button>
        </div>
        <div data-role="ttl" style="padding: 6px 12px 10px; font-family: var(--font-mono); font-size: 9px; color: var(--text-dim); text-align: center;">
          confirmation expires in <span data-role="ttl-clock">…</span>
        </div>
      </div>`;
  }

  function bindProposedActionControls(root, pa) {
    root.querySelectorAll(`[data-confirm="${CSS.escape(pa.confirmation_token)}"]`).forEach(btn => {
      if (btn.dataset.bound === '1') return;
      btn.dataset.bound = '1';
      btn.addEventListener('click', e => confirmAction(e.currentTarget.dataset.confirm));
    });
    root.querySelectorAll(`[data-cancel][data-token="${CSS.escape(pa.confirmation_token)}"]`).forEach(btn => {
      if (btn.dataset.bound === '1') return;
      btn.dataset.bound = '1';
      btn.addEventListener('click', () => cancelAction(pa.confirmation_token));
    });
    startTtlFor(root, pa.expires_at);
  }

  function renderInlineProposedAction(answerRegion, pa) {
    if (answerRegion.querySelector(`[data-role="action-card"][data-token="${CSS.escape(pa.confirmation_token)}"]`)) {
      return;
    }
    answerRegion.insertAdjacentHTML('beforeend', proposedActionHtml(pa));
    bindProposedActionControls(answerRegion, pa);
  }

  function restoreInlinePendingAction(pa) {
    const selector = pa.trace_ref
      ? `.msg-bot[data-trace-ref="${CSS.escape(pa.trace_ref)}"] .msg-body-bot`
      : '.msg-bot:last-of-type .msg-body-bot';
    const target = threadEl.querySelector(selector)
      || [...threadEl.querySelectorAll('.msg-bot .msg-body-bot')].at(-1);
    if (!target) return;
    renderInlineProposedAction(target, pa);
  }

  function startTtlFor(root, expiresAt) {
    const clocks = root.querySelectorAll('[data-role="ttl-clock"]');
    if (!clocks.length || !expiresAt) return;
    const tick = () => {
      const left = expiresAt - Math.floor(Date.now() / 1000);
      if (left <= 0) {
        clocks.forEach(el => { el.textContent = 'expired'; });
        return;
      }
      const m = Math.floor(left / 60), s = left % 60;
      clocks.forEach(el => { el.textContent = `${m}m ${String(s).padStart(2, '0')}s`; });
      setTimeout(tick, 1000);
    };
    tick();
  }

  // ── In-thread banners ──────────────────────────────────────────────────
  function renderDenialBanner(answerRegion, r) {
    answerRegion.insertAdjacentHTML('beforeend', `
      <div style="border:1px solid var(--color-danger-bg); background: var(--color-danger-bg);
                  border-radius: var(--radius-md); padding: 12px 14px; margin-bottom: 14px;
                  display: grid; grid-template-columns: 28px 1fr; gap: 12px; align-items: start;">
        <div style="width:28px;height:28px;border-radius:50%;border:1px solid var(--color-danger-dim);
                    display:grid;place-items:center;color:var(--color-danger);font-family:var(--font-mono);font-size:14px;">⨯</div>
        <div>
          <b style="color: var(--color-danger-dim); text-transform: uppercase; letter-spacing: 0.10em;
                    font-size: 10px; font-weight: 500; display: block; margin-bottom: 4px;">Permission denied</b>
          <p style="color: var(--text-high); font-size: 13px; line-height: 1.55;">${escape(r.answer)}</p>
        </div>
      </div>`);
  }

  function renderAdversarialBanner(answerRegion, r) {
    answerRegion.insertAdjacentHTML('beforeend', `
      <div style="border:1px solid var(--color-danger-bg); background: var(--color-danger-bg);
                  border-radius: var(--radius-md); padding: 14px 16px; margin-bottom: 14px;
                  display: grid; grid-template-columns: 28px 1fr; gap: 14px; align-items: start;">
        <div style="width:28px;height:28px;border-radius:50%;border:1px solid var(--color-danger-dim);
                    display:grid;place-items:center;color:var(--color-danger);font-family:var(--font-mono);font-size:15px;">⨯</div>
        <div>
          <b style="color: var(--color-danger-dim); text-transform: uppercase; letter-spacing: 0.12em;
                    font-size: 10px; font-weight: 500; display: block; margin-bottom: 4px;">Adversarial input blocked</b>
          <p style="color: var(--text-high); font-size: 13px; line-height: 1.55;">${escape(r.answer)}</p>
        </div>
      </div>`);
  }

  function renderAnswer(answerRegion, r) {
    answerRegion.insertAdjacentHTML('beforeend', `<p>${escape(r.answer).replace(/\n/g, '<br>')}</p>`);
  }

  function renderClarificationOptions(answerRegion, r) {
    const options = r.clarification_options || [];
    if (!options.length) return;
    const answer = answerRegion.querySelector('.md-answer');
    if (answer) {
      const lists = answer.querySelectorAll('ul');
      const lastList = lists[lists.length - 1];
      if (lastList && lastList.querySelectorAll('li').length === options.length) {
        lastList.remove();
      }
    }
    const wrap = document.createElement('ol');
    wrap.className = 'clarification-options';
    wrap.style.cssText = 'display:grid;gap:7px;margin:10px 0 4px;padding-left:22px;max-width:720px;';
    options.forEach((opt, idx) => {
      const li = document.createElement('li');
      li.style.cssText = 'padding-left:4px;color:var(--text-dim);';
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn btn-sm';
      btn.style.cssText = [
        'justify-content:flex-start',
        'width:fit-content',
        'max-width:100%',
        'height:26px',
        'padding:4px 12px',
        'border:1px solid var(--border-medium)',
        'border-radius:var(--radius-full)',
        'background:transparent',
        'white-space:nowrap',
        'overflow:hidden',
        'text-overflow:ellipsis',
        'text-align:left',
        'line-height:1',
      ].join(';');
      btn.textContent = opt.description ? `${opt.label} · ${opt.description}` : opt.label;
      btn.title = opt.value || opt.label;
      btn.addEventListener('click', () => {
        wrap.querySelectorAll('button').forEach((b, buttonIndex) => {
          b.disabled = true;
          b.style.cursor = 'default';
          b.style.opacity = buttonIndex === idx ? '1' : '0.45';
          if (buttonIndex === idx) {
            b.style.borderColor = 'var(--accent-border)';
            b.style.color = 'var(--text-primary)';
          }
        });
        send(opt.value || opt.label);
      }, {once: true});
      li.appendChild(btn);
      wrap.appendChild(li);
    });
    answerRegion.appendChild(wrap);
  }

  function renderResolutionOptions(answerRegion, r, originalQuery) {
    const resolution = r.resolution_required;
    const options = resolution?.options || [];
    if (!options.length) return;
    const prompt = document.createElement('p');
    prompt.style.cssText = 'margin:10px 0 6px;color:var(--text-high);';
    prompt.textContent = 'Which decision should I use?';
    const wrap = document.createElement('ol');
    wrap.className = 'clarification-options resolution-options';
    wrap.style.cssText = 'display:grid;gap:7px;margin:6px 0 4px;padding-left:22px;max-width:720px;';
    options.forEach((opt, idx) => {
      const li = document.createElement('li');
      li.style.cssText = 'padding-left:4px;color:var(--text-dim);';
      const isOther = opt.key === 'other' || /^other\s*\/\s*clarify$/i.test(opt.label || '');
      const row = document.createElement('div');
      row.style.cssText = 'display:flex;align-items:center;gap:8px;max-width:100%;';
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn btn-sm';
      btn.style.cssText = [
        'justify-content:flex-start',
        'width:fit-content',
        'max-width:100%',
        'height:26px',
        'padding:4px 12px',
        'border:1px solid var(--border-medium)',
        'border-radius:var(--radius-full)',
        'background:transparent',
        'white-space:nowrap',
        'overflow:hidden',
        'text-overflow:ellipsis',
        'text-align:left',
        'line-height:1',
      ].join(';');
      btn.textContent = opt.label;
      btn.title = opt.reason || opt.label;
      if (isOther) btn.disabled = true;

      let input = null;
      if (isOther) {
        input = document.createElement('input');
        input.type = 'text';
        input.placeholder = 'Type clarification...';
        input.style.cssText = [
          'height:26px',
          'min-width:180px',
          'max-width:320px',
          'flex:1 1 220px',
          'border:1px solid var(--border-medium)',
          'border-radius:var(--radius-full)',
          'background:transparent',
          'color:var(--text-primary)',
          'font-family:var(--font-mono)',
          'font-size:10px',
          'padding:4px 10px',
          'outline:none',
        ].join(';');
        input.addEventListener('input', () => {
          btn.disabled = input.value.trim().length === 0;
        });
        input.addEventListener('keydown', e => {
          if (e.key === 'Enter' && input.value.trim()) btn.click();
        });
      }

      btn.addEventListener('click', () => {
        const customText = input ? input.value.trim() : '';
        if (isOther && !customText) {
          input?.focus();
          return;
        }
        wrap.querySelectorAll('button').forEach((b, buttonIndex) => {
          b.disabled = true;
          b.style.cursor = 'default';
          b.style.opacity = buttonIndex === idx ? '1' : '0.45';
          if (buttonIndex === idx) {
            b.style.borderColor = 'var(--accent-border)';
            b.style.color = 'var(--text-primary)';
          }
        });
        wrap.querySelectorAll('input').forEach(field => {
          field.disabled = true;
          field.style.opacity = '0.75';
        });
        if (isOther) {
          send(customText);
        } else {
          send(originalQuery, {route: opt.route, label: opt.label, showUser: false});
        }
      }, {once: true});
      row.appendChild(btn);
      if (input) row.appendChild(input);
      li.appendChild(row);
      wrap.appendChild(li);
    });
    answerRegion.appendChild(prompt);
    answerRegion.appendChild(wrap);
  }

  function renderTraceMeta(answerRegion, r) {
    const cost = (r.cost_usd || 0).toFixed(4);
    const sec = ((r.latency_ms || 0) / 1000).toFixed(1);
    const modelText = r.model || r.narration_model || r.provider || 'model';
    answerRegion.insertAdjacentHTML('beforeend', `
      <div class="trace-meta">
        <span class="${badgeClass(r.badge)}">${escape(r.badge)}</span>
        <span class="sep">·</span>
        <a href="/traces/${escape(r.trace_ref)}">${escape(r.trace_ref)}</a>
        <span class="sep">·</span>$${cost}
        <span class="sep">·</span>${r.total_tokens || 0} tokens
        <span class="sep">·</span>${sec}s
        <span class="sep">·</span>${escape(modelText)}
      </div>`);
    const turn = answerRegion.closest('.msg-bot');
    if (turn && r.trace_ref) turn.dataset.traceRef = r.trace_ref;
  }

  // ── Send ───────────────────────────────────────────────────────────────
  function send(queryOverride = null, resolution = null) {
    if (queryOverride && typeof queryOverride !== 'string') queryOverride = null;
    const q = (queryOverride || queryEl.value).trim();
    if (!q) return;
    sending = true;
    autoFollow = true;
    hideWelcome();
    if (!resolution || resolution.showUser !== false) {
      appendUserTurn(q);
    }
    if (!queryOverride) queryEl.value = '';
    sendBtn.disabled = true;
    clearRail({keepEvidence: true});

    const turn = startBotTurn();
    const planEl = turn.querySelector('[data-role="plan"]');
    const elapsedEl = turn.querySelector('[data-role="elapsed"]');
    const answerRegion = turn.querySelector('[data-role="answer-region"]');
    const start = Date.now();
    const tick = setInterval(() => {
      elapsedEl.textContent = `${((Date.now() - start) / 1000).toFixed(1)}s`;
    }, 100);

    let url = `/chat/stream?query=${encodeURIComponent(q)}&conversation_ref=${encodeURIComponent(convRef())}&model_key=${encodeURIComponent(modelKey())}`;
    if (resolution?.route) {
      url += `&resolution_route=${encodeURIComponent(resolution.route)}`;
      addStep(planEl, 'resolution', `human resolution <small>· ${escape(resolution.label || resolution.route)}</small>`, 'ok');
    }
    const es = new EventSource(url);
    let evidenceAccum = [];

    es.addEventListener('planning',  () => addStep(planEl, 'plan', '<b>Planning</b>', 'run'));
    es.addEventListener('plan',      e => {
      const d = JSON.parse(e.data);
      addStep(planEl, 'plan', `<b>Planning</b> · ${d.steps_count} step${d.steps_count===1?'':'s'} <small>· ${escape(d.intent)}</small>`, 'ok');
      // From here on, the LLM is at work composing the answer; show the dots.
      ensureComposing(planEl);
      followThread(true);
    });
    es.addEventListener('tool_start', e => {
      const d = JSON.parse(e.data);
      addStep(planEl, 't-' + d.tool, `${escape(d.tool)} <small>· running</small>`, 'run');
      followThread();
    });
    es.addEventListener('tool_complete', e => {
      const d = JSON.parse(e.data);
      addStep(planEl, 't-' + d.tool, `${escape(d.tool)} <small>· ${d.latency_ms}ms</small>`, 'ok');
      followThread();
    });
    es.addEventListener('tool_error', e => {
      const d = JSON.parse(e.data);
      addStep(planEl, 't-' + d.tool, `${escape(d.tool)} <small>· ${escape(d.error)}</small>`, 'fail');
      followThread();
    });
    es.addEventListener('tool_skipped', e => {
      const d = JSON.parse(e.data);
      addStep(planEl, 't-' + d.tool, `${escape(d.tool)} <small>· skipped · ${escape(d.reason)}</small>`, 'queued');
      followThread();
    });
    es.addEventListener('skill_start', e => {
      const d = JSON.parse(e.data);
      addStep(planEl, 's-' + d.skill, `skill: ${escape(d.skill)} <small>· running</small>`, 'run');
      followThread();
    });
    es.addEventListener('skill_complete', e => {
      const d = JSON.parse(e.data);
      addStep(planEl, 's-' + d.skill, `skill: ${escape(d.skill)} <small>· risk ${escape(d.risk_level || 'n/a')} · ${d.latency_ms}ms</small>`, 'ok');
      followThread();
    });
    es.addEventListener('rbac_denied', e => {
      const d = JSON.parse(e.data);
      addStep(planEl, 'rbac', `policy.rbac_check <small>· denied · ${escape(d.reason)}</small>`, 'fail');
      followThread();
    });
    es.addEventListener('proposed_action', () => {
      addStep(planEl, 'propose', `action.propose <small>· staged</small>`, 'ok');
      followThread();
    });
    es.addEventListener('adversarial_block', e => {
      const d = JSON.parse(e.data);
      addStep(planEl, 'adv', `adversarial.check <small>· ${(d.flags || []).join(', ')}</small>`, 'fail');
      followThread();
    });

    es.addEventListener('agent_error', e => {
      clearInterval(tick);
      removeComposing(planEl);
      const d = JSON.parse(e.data || '{}');
      addStep(planEl, 'stream-error', `response.error <small>· ${escape(d.error || 'request failed')}</small>`, 'fail');
      renderStreamFailure(answerRegion, escape(d.error || 'Please try the message again.'));
      sendBtn.disabled = false;
      sending = false;
      queryEl.focus();
      es.close();
    });

    es.addEventListener('done', async e => {
      clearInterval(tick);
      const r = JSON.parse(e.data);
      elapsedEl.textContent = `${((r.latency_ms || 0) / 1000).toFixed(1)}s · ${r.total_tokens || 0} tok · $${(r.cost_usd || 0).toFixed(4)}`;
      evidenceAccum = r.evidence || [];
      removeComposing(planEl);

      if (r.badge === 'Adversarial Input Blocked') {
        renderAdversarialBanner(answerRegion, r);
      } else if (r.badge === 'Permission Denied') {
        renderDenialBanner(answerRegion, r);
      } else {
        await renderAnswerTyped(answerRegion, r);
      }
      renderClarificationOptions(answerRegion, r);
      renderResolutionOptions(answerRegion, r, q);
      if (r.proposed_action) {
        renderInlineProposedAction(answerRegion, r.proposed_action);
      }
      renderTraceMeta(answerRegion, r);
      followThread();

      if (evidenceAccum.length && r.badge !== 'Adversarial Input Blocked') {
        addEvidenceGroup(evidenceAccum, {label: 'Latest response', traceRef: r.trace_ref, focus: true});
      }
      sendBtn.disabled = false;
      sending = false;
      queryEl.focus();
      es.close();
    });

    es.addEventListener('error', () => {
      clearInterval(tick);
      removeComposing(planEl);
      addStep(planEl, 'stream-error', 'stream.closed <small>· response interrupted</small>', 'fail');
      if (!answerRegion.textContent.trim()) {
        renderStreamFailure(answerRegion, 'The stream closed before the final response arrived. This can happen if the development server reloads while a request is running.');
      }
      sendBtn.disabled = false;
      sending = false;
      es.close();
    });
  }

  async function confirmAction(token) {
    const resp = await fetch('/actions/confirm', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({conversation_ref: convRef(), confirmation_token: token}),
    });
    const data = await resp.json();
    const actionRef = data.action_ref || data.existing_action_ref || '';
    const ok = resp.ok && (data.created || data.duplicate);
    const cards = document.querySelectorAll(`[data-role="action-card"][data-token="${CSS.escape(token)}"]`);
    const traceRef = cards[0]?.closest('.msg-bot')?.dataset.traceRef || '';
    cards.forEach(card => {
      const metaBadge = card.closest('.msg-body-bot')?.querySelector('.trace-meta .badge');
      const result = document.createElement('div');
      result.className = `action-result ${ok ? 'created' : 'denied'}`;
      result.innerHTML = ok ? `
        <div class="label">${data.duplicate ? 'Existing action' : 'Action created'}</div>
        <div class="action-result-title">${escape(actionRef || 'created')}</div>
        <p>Saved in <code>next_actions</code>. The action row has been added to Evidence.</p>
      ` : `
        <div class="label">Not created</div>
        <div class="action-result-title">Confirmation rejected</div>
        <p>${escape(data.detail || data.reason || 'The action was not created.')}</p>
      `;
      card.replaceWith(result);
      if (ok && metaBadge) {
        metaBadge.className = 'badge badge-created';
        metaBadge.textContent = 'Action Created';
      }
    });
    if (ok && actionRef) {
      addEvidenceGroup([`action:${actionRef}`], {
        label: 'Latest response',
        traceRef,
        focus: true,
      });
    }
  }

  async function cancelAction(token = null) {
    await fetch('/actions/cancel', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({conversation_ref: convRef(), confirmation_token: token}),
    });
    if (token) {
      document.querySelectorAll(`[data-role="action-card"][data-token="${CSS.escape(token)}"]`).forEach(card => {
        const result = document.createElement('div');
        result.className = 'action-result cancelled';
        result.innerHTML = `
          <div class="label">Cancelled</div>
          <div class="action-result-title">No action created</div>
          <p>The proposal was cancelled and will not return after refresh.</p>
        `;
        card.replaceWith(result);
        const metaBadge = result.closest('.msg-body-bot')?.querySelector('.trace-meta .badge');
        if (metaBadge) {
          metaBadge.className = 'badge badge-cancelled';
          metaBadge.textContent = 'Action Cancelled';
        }
      });
    }
    clearRail({keepEvidence: true});
  }

  // ── New conversation ─────────────────────────────────────────────────
  window.newConversation = function() {
    const ref = 'CONV-' + Math.random().toString(36).slice(2, 8).toUpperCase();
    location.href = `/chat?conversation_ref=${encodeURIComponent(ref)}`;
  };

  function hydrateHistoryOptions() {
    document.querySelectorAll('.history-options').forEach(el => {
      if (el.dataset.hydrated === '1') return;
      let options = [];
      try { options = JSON.parse(el.dataset.options || '[]'); } catch { options = []; }
      if (!options.length) return;
      const kind = el.dataset.choiceKind;
      const originalQuery = el.dataset.originalQuery || '';
      if (kind === 'resolution') {
        renderResolutionOptions(el, {resolution_required: {options}}, originalQuery);
      } else if (kind === 'clarification') {
        renderClarificationOptions(el, {clarification_options: options});
      }
      el.dataset.hydrated = '1';
    });
  }

  hydrateHistoryOptions();

  sendBtn.addEventListener('click', send);
  queryEl.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  });
})();
