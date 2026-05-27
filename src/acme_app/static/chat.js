const sendBtn = document.getElementById('send');
const queryEl = document.getElementById('query');
const providerEl = document.getElementById('provider');
const eventsEl = document.getElementById('events');
const answerEl = document.getElementById('answer');
const traceEl = document.getElementById('trace');
const proposedEl = document.getElementById('proposed');

sendBtn?.addEventListener('click', () => {
  const q = queryEl.value.trim();
  if (!q) return;
  eventsEl.textContent = 'Planning...';
  answerEl.textContent = '';
  traceEl.textContent = '';
  proposedEl.textContent = '';
  const es = new EventSource(`/chat/stream?query=${encodeURIComponent(q)}&provider=${encodeURIComponent(providerEl.value)}`);

  es.addEventListener('planning', e => {
    eventsEl.textContent += `\n${JSON.parse(e.data).status}`;
  });

  es.addEventListener('tool_complete', e => {
    const data = JSON.parse(e.data);
    eventsEl.textContent += `\n✓ ${data.tool}`;
  });

  es.addEventListener('proposed_action', e => {
    proposedEl.textContent = JSON.stringify(JSON.parse(e.data), null, 2);
  });

  es.addEventListener('final_response', e => {
    const data = JSON.parse(e.data);
    answerEl.textContent = `${data.answer}\nBadge: ${data.badge}`;
  });

  es.addEventListener('trace', e => {
    traceEl.textContent = JSON.stringify(JSON.parse(e.data), null, 2);
    es.close();
  });
});
