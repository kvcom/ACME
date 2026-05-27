(() => {
  const queryEl = document.getElementById('query');
  const sendBtn = document.getElementById('send');
  const providerEl = document.getElementById('provider');
  const threadEl = document.getElementById('thread');
  const evidenceEl = document.getElementById('evidence-list');
  const proposedEl = document.getElementById('proposed-card');
  const traceEl = document.getElementById('trace-summary');
  const conversationRef = (document.body.dataset.conversationRef || 'CONV-DEMO');

  function escape(s) {
    return String(s ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  }

  function badgeClass(badge) {
    return 'badge badge-' + (badge || '').toLowerCase().replace(/\s+/g, '-');
  }

  function addUserTurn(text) {
    const div = document.createElement('div');
    div.className = 'turn turn-user';
    div.innerHTML = `<div class="answer">${escape(text)}</div>`;
    threadEl.appendChild(div);
    threadEl.scrollTop = threadEl.scrollHeight;
  }

  function startAssistantTurn() {
    const div = document.createElement('div');
    div.className = 'turn turn-assistant';
    div.innerHTML = `
      <div class="events" data-role="events"></div>
      <div class="answer" data-role="answer"></div>
      <div class="turn-meta" data-role="meta"></div>`;
    threadEl.appendChild(div);
    threadEl.scrollTop = threadEl.scrollHeight;
    return div;
  }

  function send() {
    const q = queryEl.value.trim();
    if (!q) return;
    addUserTurn(q);
    queryEl.value = '';
    sendBtn.disabled = true;
    const turn = startAssistantTurn();
    const eventsEl = turn.querySelector('[data-role="events"]');
    const answerEl = turn.querySelector('[data-role="answer"]');
    const metaEl = turn.querySelector('[data-role="meta"]');
    evidenceEl.innerHTML = '';
    proposedEl.innerHTML = '';

    const url = `/chat/stream?query=${encodeURIComponent(q)}&conversation_ref=${encodeURIComponent(conversationRef)}&provider=${encodeURIComponent(providerEl.value)}`;
    const es = new EventSource(url);

    function pushEvent(label, cls = '') {
      const line = document.createElement('div');
      line.className = 'ev ' + cls;
      line.textContent = label;
      eventsEl.appendChild(line);
    }

    es.addEventListener('planning', () => pushEvent('Planning…'));
    es.addEventListener('adversarial', e => {
      const d = JSON.parse(e.data);
      if (d.detected) pushEvent(`! adversarial pattern detected: ${(d.flags || []).join(', ')}`, 'err');
    });
    es.addEventListener('plan', e => {
      const d = JSON.parse(e.data);
      pushEvent(`plan • intent=${d.intent} • ${d.steps_count} step(s)`);
    });
    es.addEventListener('tool_start', e => {
      const d = JSON.parse(e.data);
      pushEvent(`→ ${d.tool}`);
    });
    es.addEventListener('tool_complete', e => {
      const d = JSON.parse(e.data);
      pushEvent(`✓ ${d.tool} (${d.latency_ms}ms)`, 'done');
    });
    es.addEventListener('tool_error', e => {
      const d = JSON.parse(e.data);
      pushEvent(`✗ ${d.tool}: ${d.error}`, 'err');
    });
    es.addEventListener('skill_start', e => {
      const d = JSON.parse(e.data);
      pushEvent(`→ skill: ${d.skill}`);
    });
    es.addEventListener('skill_complete', e => {
      const d = JSON.parse(e.data);
      pushEvent(`✓ skill: ${d.skill} (risk=${d.risk_level || 'n/a'}, ${d.latency_ms}ms)`, 'done');
    });
    es.addEventListener('rbac_denied', e => {
      const d = JSON.parse(e.data);
      pushEvent(`✗ RBAC denied: ${d.reason}`, 'err');
    });
    es.addEventListener('proposed_action', e => {
      const d = JSON.parse(e.data);
      pushEvent(`proposed: ${d.action_type} (${d.priority})`);
    });
    es.addEventListener('action_confirmed', e => {
      const d = JSON.parse(e.data);
      pushEvent(`✓ action confirmed: ${d.action_ref || d.existing_action_ref || 'ok'}`, 'done');
    });
    es.addEventListener('adversarial_block', e => {
      const d = JSON.parse(e.data);
      pushEvent(`✗ blocked: ${(d.flags || []).join(', ')}`, 'err');
    });

    es.addEventListener('done', e => {
      const r = JSON.parse(e.data);
      answerEl.textContent = r.answer;
      metaEl.innerHTML = `<span class="${badgeClass(r.badge)}">${escape(r.badge)}</span>
        · trace <a href="/traces/${escape(r.trace_ref)}">${escape(r.trace_ref)}</a>
        · ${r.total_tokens} tok · $${(r.cost_usd || 0).toFixed(4)} · ${r.latency_ms}ms · ${escape(r.provider)}`;
      (r.evidence || []).forEach(item => {
        const li = document.createElement('li');
        li.textContent = item;
        evidenceEl.appendChild(li);
      });
      if (r.proposed_action) {
        const p = r.proposed_action;
        proposedEl.innerHTML = `
          <div class="proposed-card">
            <h4>${escape(p.title)}</h4>
            <div class="proposed-meta">${escape(p.action_type)} · ${escape(p.priority)} · ${escape(p.issue_ref)}</div>
            <p style="font-size:12.5px;color:var(--muted);margin:0 0 10px">${escape(p.rationale)}</p>
            <div class="proposed-actions">
              <button class="primary" data-confirm="${escape(p.confirmation_token)}">Confirm</button>
              <button class="ghost" data-cancel="1">Cancel</button>
            </div>
          </div>`;
        proposedEl.querySelector('[data-confirm]').addEventListener('click', ev => confirmAction(ev.target.dataset.confirm));
        proposedEl.querySelector('[data-cancel]').addEventListener('click', () => cancelAction());
      }
      traceEl.innerHTML = `
        <div><span>Trace</span><strong><a href="/traces/${escape(r.trace_ref)}">${escape(r.trace_ref)}</a></strong></div>
        <div><span>Provider</span><strong>${escape(r.provider)}</strong></div>
        <div><span>Model</span><strong>${escape(r.model || 'n/a')}</strong></div>
        <div><span>Cost</span><strong>$${(r.cost_usd || 0).toFixed(4)}</strong></div>
        <div><span>Tokens</span><strong>${r.total_tokens}</strong></div>
        <div><span>Latency</span><strong>${r.latency_ms}ms</strong></div>
        <div><span>Risk</span><strong>${escape(r.risk_level || 'n/a')}</strong></div>`;
      sendBtn.disabled = false;
      es.close();
    });
    es.addEventListener('error', () => {
      sendBtn.disabled = false;
      es.close();
    });
  }

  async function confirmAction(token) {
    const resp = await fetch('/actions/confirm', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({conversation_ref: conversationRef, confirmation_token: token}),
    });
    const data = await resp.json();
    proposedEl.innerHTML = `<div class="proposed-card"><h4>Confirmed</h4>
      <pre class="mono">${escape(JSON.stringify(data, null, 2))}</pre></div>`;
  }

  async function cancelAction() {
    await fetch('/actions/cancel', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({conversation_ref: conversationRef}),
    });
    proposedEl.innerHTML = '<p style="color:var(--muted);font-size:12px">Action cancelled.</p>';
  }

  sendBtn.addEventListener('click', send);
  queryEl.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  });
})();
