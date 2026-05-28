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
  let currentModelKey = (() => {
    try { return localStorage.getItem(STORAGE_KEY); } catch { return null; }
  })() || document.body.dataset.modelKey || 'stub';
  try { setProvider(currentModelKey); } catch (e) { console.warn('[acme] setProvider failed', e); }

  function escape(s) {
    return String(s ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  }
  function modelKey() { return currentModelKey || 'stub'; }
  function convRef()  { return document.body.dataset.conversationRef || 'CONV-DEMO'; }
  function badgeClass(b) { return 'badge badge-' + (b || '').toLowerCase().replace(/[\s_]+/g, '-').replace(/-+/g, '-'); }

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
  function typewriter(el, text, speed = 12) {
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
        // Insert text node before caret so caret stays at the end.
        caret.previousSibling
          ? (caret.previousSibling.nodeValue = before)
          : el.insertBefore(document.createTextNode(before), caret);
        // Briefly pause on sentence punctuation for a more natural cadence.
        const delay = /[.!?]/.test(ch) ? speed * 8
                    : /[,;:]/.test(ch) ? speed * 4
                    : speed;
        setTimeout(step, delay);
      }
      step();
    });
  }

  async function renderAnswerTyped(answerRegion, r) {
    const p = document.createElement('p');
    answerRegion.appendChild(p);
    await typewriter(p, r.answer || '', 12);
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

  function ensureEvidencePanel() {
    let panel = railEl.querySelector('[data-role="evidence-panel"]');
    if (!panel) {
      railEl.insertAdjacentHTML('beforeend', `
        <div class="panel" data-role="evidence-panel">
          <div class="panel-header">
            <span class="label">Evidence</span>
            <span class="mono dim" style="font-size:10px;" data-role="ev-count">0 records · live</span>
          </div>
          <div class="panel-body" style="padding: 6px;" data-role="ev-list"></div>
        </div>`);
      panel = railEl.querySelector('[data-role="evidence-panel"]');
    }
    return panel;
  }

  function setEvidence(items) {
    if (!items || !items.length) return;
    const panel = ensureEvidencePanel();
    const list = panel.querySelector('[data-role="ev-list"]');
    list.innerHTML = '';
    items.slice(0, 20).forEach(item => {
      const [kind, id] = String(item).includes(':') ? String(item).split(':', 2) : ['ref', String(item)];
      const li = document.createElement('div');
      li.className = 'ev-item';
      li.innerHTML = `<span class="ref">${escape(id.slice(0, 14))}</span><span class="desc">${escape(kind)}</span>`;
      list.appendChild(li);
    });
    panel.querySelector('[data-role="ev-count"]').textContent = `${items.length} record${items.length === 1 ? '' : 's'}`;
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

  function renderTraceMeta(answerRegion, r) {
    const cost = (r.cost_usd || 0).toFixed(4);
    const sec = ((r.latency_ms || 0) / 1000).toFixed(1);
    answerRegion.insertAdjacentHTML('beforeend', `
      <div class="trace-meta">
        <span class="${badgeClass(r.badge)}">${escape(r.badge)}</span>
        <span class="sep">·</span>
        <a href="/traces/${escape(r.trace_ref)}">${escape(r.trace_ref)}</a>
        <span class="sep">·</span>$${cost}
        <span class="sep">·</span>${r.total_tokens || 0} tokens
        <span class="sep">·</span>${sec}s
        <span class="sep">·</span>${escape(r.provider)}
      </div>`);
  }

  // ── Send ───────────────────────────────────────────────────────────────
  function send() {
    const q = queryEl.value.trim();
    if (!q) return;
    hideWelcome();
    appendUserTurn(q);
    queryEl.value = '';
    sendBtn.disabled = true;
    clearRail();

    const turn = startBotTurn();
    const planEl = turn.querySelector('[data-role="plan"]');
    const elapsedEl = turn.querySelector('[data-role="elapsed"]');
    const answerRegion = turn.querySelector('[data-role="answer-region"]');
    const start = Date.now();
    const tick = setInterval(() => {
      elapsedEl.textContent = `${((Date.now() - start) / 1000).toFixed(1)}s elapsed`;
    }, 100);

    const url = `/chat/stream?query=${encodeURIComponent(q)}&conversation_ref=${encodeURIComponent(convRef())}&model_key=${encodeURIComponent(modelKey())}`;
    const es = new EventSource(url);
    let evidenceAccum = [];

    es.addEventListener('planning',  () => addStep(planEl, 'plan', '<b>Planning</b>', 'run'));
    es.addEventListener('plan',      e => {
      const d = JSON.parse(e.data);
      addStep(planEl, 'plan', `<b>Planning</b> · ${d.steps_count} step${d.steps_count===1?'':'s'} <small>· ${escape(d.intent)}</small>`, 'ok');
      // From here on, the LLM is at work composing the answer; show the dots.
      ensureComposing(planEl);
    });
    es.addEventListener('tool_start', e => {
      const d = JSON.parse(e.data);
      addStep(planEl, 't-' + d.tool, `${escape(d.tool)} <small>· running</small>`, 'run');
    });
    es.addEventListener('tool_complete', e => {
      const d = JSON.parse(e.data);
      addStep(planEl, 't-' + d.tool, `${escape(d.tool)} <small>· ${d.latency_ms}ms</small>`, 'ok');
    });
    es.addEventListener('tool_error', e => {
      const d = JSON.parse(e.data);
      addStep(planEl, 't-' + d.tool, `${escape(d.tool)} <small>· ${escape(d.error)}</small>`, 'fail');
    });
    es.addEventListener('skill_start', e => {
      const d = JSON.parse(e.data);
      addStep(planEl, 's-' + d.skill, `skill: ${escape(d.skill)} <small>· running</small>`, 'run');
    });
    es.addEventListener('skill_complete', e => {
      const d = JSON.parse(e.data);
      addStep(planEl, 's-' + d.skill, `skill: ${escape(d.skill)} <small>· risk ${escape(d.risk_level || 'n/a')} · ${d.latency_ms}ms</small>`, 'ok');
    });
    es.addEventListener('rbac_denied', e => {
      const d = JSON.parse(e.data);
      addStep(planEl, 'rbac', `policy.rbac_check <small>· denied · ${escape(d.reason)}</small>`, 'fail');
    });
    es.addEventListener('proposed_action', () => {
      addStep(planEl, 'propose', `action.propose <small>· staged</small>`, 'ok');
    });
    es.addEventListener('adversarial_block', e => {
      const d = JSON.parse(e.data);
      addStep(planEl, 'adv', `adversarial.check <small>· ${(d.flags || []).join(', ')}</small>`, 'fail');
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
      renderTraceMeta(answerRegion, r);

      if (r.proposed_action) {
        renderProposedActionCard(r.proposed_action);
      } else if (evidenceAccum.length && r.badge !== 'Permission Denied' && r.badge !== 'Adversarial Input Blocked') {
        setEvidence(evidenceAccum);
      }

      sendBtn.disabled = false;
      queryEl.focus();
      es.close();
    });

    es.addEventListener('error', () => {
      clearInterval(tick);
      removeComposing(planEl);
      sendBtn.disabled = false;
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

  sendBtn.addEventListener('click', send);
  queryEl.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  });
})();
