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
  const serverDefaultModelKey = document.body.dataset.modelKey || 'stub';
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
  function modelKey() { return currentModelKey || 'stub'; }
  function convRef()  { return document.body.dataset.conversationRef || 'CONV-DEMO'; }
  const BADGE_CLASS = {
    'Grounded': 'grounded',
    'Partially Grounded': 'partial',
    'Needs Review': 'needsreview',
    'Permission Denied': 'denied',
    'Action Proposed': 'proposed',
    'Action Created': 'created',
    'Action Cancelled': 'cancelled',
    'Insufficient Evidence': 'insufficient',
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

  // Type out the answer character-by-character with a blinking caret.
  function typewriter(el, text, speed = 12, onUpdate = null) {
    return new Promise(resolve => {
      el.textContent = '';
      const caret = document.createElement('span');
      caret.className = 'typing-caret';
      el.appendChild(caret);
      let i = 0;
      function step() {
        if (i >= text.length) {
          caret.remove();
          return resolve();
        }
        const ch = text[i++];
        const before = text.slice(0, i);
        if (onUpdate) {
          onUpdate(before, caret);
        } else {
          // Insert text node before caret so caret stays at the end.
          caret.previousSibling
            ? (caret.previousSibling.nodeValue = before)
            : el.insertBefore(document.createTextNode(before), caret);
        }
        // Briefly pause on sentence punctuation for a more natural cadence.
        const delay = /[.!?]/.test(ch) ? speed * 8
                    : /[,;:]/.test(ch) ? speed * 4
                    : speed;
        setTimeout(step, delay);
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

  // Sidebar delete button — soft-delete (trace data preserved per D-015).
  document.querySelectorAll('.conv-delete').forEach(btn => {
    btn.addEventListener('click', async ev => {
      ev.preventDefault();
      ev.stopPropagation();
      const ref = btn.dataset.convRef;
      if (!ref) return;
      if (!confirm('Hide this conversation from the sidebar?\n\nThe full trace history (events, tool calls, RBAC decisions) is preserved for audit and can still be found via /traces.')) return;
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
  // render the Confirm card on page load. The HMAC token has already been
  // re-minted server-side and the proposal staged back into Redis, so the
  // existing /actions/confirm path works without changes.
  (function restorePendingAction() {
    const raw = document.body.dataset.pendingAction;
    if (!raw) return;
    try {
      const pa = JSON.parse(raw);
      if (pa && pa.confirmation_token && pa.action_type) {
        renderProposedActionCard(pa);
      }
    } catch (e) {
      console.warn('[acme] failed to parse pending_action', e);
    }
  })();

  (function restoreLatestEvidence() {
    if (document.body.dataset.pendingAction) return;
    const raw = document.body.dataset.latestEvidence;
    if (!raw) return;
    try {
      const evidence = JSON.parse(raw);
      if (evidence && evidence.length) {
        setEvidence(evidence, {
          label: 'Latest response',
          traceRef: document.body.dataset.latestEvidenceTraceRef || '',
        });
      }
    } catch (e) {
      console.warn('[acme] failed to parse latest evidence', e);
    }
  })();

  document.querySelectorAll('[data-show-evidence]').forEach(btn => {
    btn.addEventListener('click', () => {
      try {
        setEvidence(JSON.parse(btn.dataset.showEvidence || '[]'), {
          label: 'Selected response',
          traceRef: btn.dataset.traceRef || '',
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
    await typewriter(wrap, r.answer || '', 12, (partial, caret) => {
      wrap.innerHTML = renderMarkdown(partial);
      wrap.appendChild(caret);
      followThread();
    });
    // Final render without the caret, using the complete normalised markdown.
    wrap.innerHTML = renderMarkdown(r.answer || '');
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
  function clearRail() { railEl.innerHTML = ''; }

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

  function ensureEvidencePanel() {
    let panel = railEl.querySelector('[data-role="evidence-panel"]');
    if (!panel) {
      railEl.insertAdjacentHTML('beforeend', `
        <div class="panel" data-role="evidence-panel">
          <div class="panel-header">
            <span class="label">Evidence</span>
            <span class="mono dim" style="font-size:10px;" data-role="ev-count">0 records · live</span>
          </div>
          <div class="panel-body panel-divider" style="padding: 8px 12px; display:none;" data-role="ev-context"></div>
          <div class="panel-body" style="padding: 6px;" data-role="ev-list"></div>
        </div>`);
      panel = railEl.querySelector('[data-role="evidence-panel"]');
    }
    return panel;
  }

  function setEvidence(items, meta = {}) {
    if (!items || !items.length) return;
    const panel = ensureEvidencePanel();
    const list = panel.querySelector('[data-role="ev-list"]');
    const context = panel.querySelector('[data-role="ev-context"]');
    list.innerHTML = '';
    items.slice(0, 20).forEach(item => {
      const {kind, id} = evidenceParts(item);
      const li = document.createElement('div');
      li.className = 'ev-item';
      li.innerHTML = `<span class="ref">${escape(id.slice(0, 14))}</span><span class="desc">${escape(kind)}</span>`;
      list.appendChild(li);
    });
    panel.querySelector('[data-role="ev-count"]').textContent = `${items.length} record${items.length === 1 ? '' : 's'}`;
    if (context) {
      const bits = [];
      if (meta.label) bits.push(escape(meta.label));
      if (meta.traceRef) bits.push(`<a href="/traces/${encodeURIComponent(meta.traceRef)}">${escape(meta.traceRef)}</a>`);
      context.style.display = bits.length ? '' : 'none';
      context.innerHTML = bits.length
        ? `<div class="trace-meta" style="margin-top:0;">${bits.join('<span class="sep">·</span>')}</div>`
        : '';
    }
  }

  function renderProposedActionCard(pa) {
    clearRail();
    const dueText = pa.due_at ? new Date(pa.due_at).toLocaleString() : '—';
    railEl.insertAdjacentHTML('beforeend', `
      <div class="action-card" data-role="action-card">
        <div class="ah">
          <div>
            <div class="label" style="color: var(--accent-dim);">Action proposed</div>
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
          <button class="btn btn-sm" data-cancel="1">Cancel</button>
          <button class="btn btn-sm btn-primary" data-confirm="${escape(pa.confirmation_token)}">Confirm</button>
        </div>
        <div data-role="ttl" style="padding: 6px 12px 10px; font-family: var(--font-mono); font-size: 9px; color: var(--text-dim); text-align: center;">
          confirmation expires in <span data-role="ttl-clock">…</span>
        </div>
      </div>`);
    railEl.querySelector('[data-confirm]').addEventListener('click', e => confirmAction(e.currentTarget.dataset.confirm));
    railEl.querySelector('[data-cancel]').addEventListener('click', cancelAction);
    startTtl(pa.expires_at);
  }

  function startTtl(expiresAt) {
    const el = railEl.querySelector('[data-role="ttl-clock"]');
    if (!el || !expiresAt) return;
    const tick = () => {
      const left = expiresAt - Math.floor(Date.now() / 1000);
      if (left <= 0) { el.textContent = 'expired'; return; }
      const m = Math.floor(left / 60), s = left % 60;
      el.textContent = `${m}m ${String(s).padStart(2, '0')}s`;
      setTimeout(tick, 1000);
    };
    tick();
  }

  function renderRoleReference(role) {
    clearRail();
    const matrix = {
      sales_user: [['read customers · issues · updates', true], ['request recommendations', true],
                   ['create next actions', false], ['update issue status', false]],
      support_user: [['read customers · issues · updates', true], ['propose & confirm support actions', true],
                     ['update issue status', true], ['cancel actions', false]],
      admin: [['read everything', true], ['propose, confirm, cancel any action', true],
              ['reveal redacted query in trace', true]],
    }[role] || [];
    railEl.insertAdjacentHTML('beforeend', `
      <div class="panel">
        <div class="panel-header">
          <span class="label">Your role</span>
          <span class="mono dim" style="font-size:10px;">${escape(role)}</span>
        </div>
        <div class="panel-body" style="font-family: var(--font-mono); font-size: 11px; line-height: 1.7;">
          <div style="display: grid; grid-template-columns: 14px 1fr; gap: 8px;">
            ${matrix.map(([t, ok]) => `
              <span style="color: ${ok ? 'var(--color-active)' : 'var(--text-dim)'};">${ok ? '✓' : '⨯'}</span>
              <span style="color: ${ok ? 'var(--text-high)' : 'var(--text-muted)'};">${escape(t)}</span>
            `).join('')}
          </div>
        </div>
      </div>`);
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
    clearRail();

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
        renderRoleReference(document.body.dataset.role);
      } else {
        await renderAnswerTyped(answerRegion, r);
      }
      renderClarificationOptions(answerRegion, r);
      renderResolutionOptions(answerRegion, r, q);
      renderTraceMeta(answerRegion, r);
      followThread();

      if (r.proposed_action) {
        renderProposedActionCard(r.proposed_action);
      } else if (evidenceAccum.length && r.badge !== 'Permission Denied' && r.badge !== 'Adversarial Input Blocked') {
        setEvidence(evidenceAccum, {label: 'Latest response', traceRef: r.trace_ref});
      }

      sendBtn.disabled = false;
      sending = false;
      queryEl.focus();
      es.close();
    });

    es.addEventListener('error', () => {
      clearInterval(tick);
      removeComposing(planEl);
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
    const card = railEl.querySelector('[data-role="action-card"]');
    if (!card) return;
    const ttl = card.querySelector('[data-role="ttl"]');
    if (ttl) ttl.remove();
    const af = card.querySelector('.af');
    if (af) {
      const ok = data.created || data.duplicate;
      af.innerHTML = `<span class="${ok ? 'badge badge-created' : 'badge badge-denied'}" style="flex:1;text-align:center;justify-content:center;">${ok ? '✓ ' + (data.action_ref || data.existing_action_ref || 'created') : (data.detail || 'rejected')}</span>`;
    }
  }

  async function cancelAction() {
    await fetch('/actions/cancel', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({conversation_ref: convRef()}),
    });
    clearRail();
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
