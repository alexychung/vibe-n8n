// Minimal vanilla-JS frontend for vibe-n8n.
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

let hasOpenAI = false;

// ---------- tabs ----------
function showTab(name) {
  $$('.tab').forEach(t => t.classList.toggle('active', t.id === `tab-${name}`));
  $$('.panel').forEach(p => p.classList.toggle('active', p.id === `panel-${name}`));
  if (name === 'run') loadWorkflows();
}
$('#tab-plan').addEventListener('click', () => showTab('plan'));
$('#tab-run').addEventListener('click', () => showTab('run'));

// ---------- plan mode toggle ----------
function setPlanMode(mode) {
  $('#mode-brief').classList.toggle('active', mode === 'brief');
  $('#mode-interview').classList.toggle('active', mode === 'interview');
  $('#brief-view').classList.toggle('hidden', mode !== 'brief');
  $('#interview-view').classList.toggle('hidden', mode !== 'interview');
}
$('#mode-brief').addEventListener('click', () => setPlanMode('brief'));
$('#mode-interview').addEventListener('click', () => setPlanMode('interview'));

// ---------- workflows list ----------
let selectedWorkflow = null;

async function loadWorkflows() {
  const ul = $('#workflows');
  ul.innerHTML = '<li class="hint">Loading…</li>';
  try {
    const r = await fetch('/api/workflows');
    if (!r.ok) throw new Error(await r.text());
    const wfs = await r.json();
    ul.innerHTML = '';
    if (!wfs.length) { ul.innerHTML = '<li class="hint">No workflows found.</li>'; return; }
    wfs.sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''));
    for (const wf of wfs) {
      const li = document.createElement('li');
      li.dataset.id = wf.id;
      li.innerHTML = `
        <span class="name" title="${escapeHtml(wf.name || '(unnamed)')}">${escapeHtml(wf.name || '(unnamed)')}</span>
        <span class="badge ${wf.active ? 'active' : 'inactive'}">${wf.active ? 'active' : 'inactive'}</span>
      `;
      li.addEventListener('click', () => selectWorkflow(wf, li));
      ul.appendChild(li);
    }
  } catch (e) {
    ul.innerHTML = `<li class="hint">Error loading: ${escapeHtml(String(e))}</li>`;
  }
}

function selectWorkflow(wf, li) {
  selectedWorkflow = wf;
  $$('#workflows li').forEach(x => x.classList.remove('selected'));
  li.classList.add('selected');
  $('#wf-empty').classList.add('hidden');
  $('#wf-view').classList.remove('hidden');
  $('#wf-name').textContent = wf.name || '(unnamed)';
  const badge = $('#wf-active');
  badge.textContent = wf.active ? 'active' : 'inactive';
  badge.className = `badge ${wf.active ? 'active' : 'inactive'}`;
  $('#wf-edit').href = wf.edit_url;
  $('#run-result').classList.add('hidden');
  $('#run-body').value = '';
  $('#run-headers').value = '';
  const webhooks = wf.webhooks || (wf.webhook ? [wf.webhook] : []);
  if (webhooks.length) {
    $('#wf-runner').classList.remove('hidden');
    $('#wf-norunner').classList.add('hidden');
    const picker = $('#wf-webhook-picker');
    const pickerWrap = $('#wf-webhook-picker-wrap');
    picker.innerHTML = '';
    webhooks.forEach((w, i) => {
      const opt = document.createElement('option');
      opt.value = String(i);
      opt.textContent = `${w.method} /webhook/${w.path}` + (w.node_name ? ` (${w.node_name})` : '');
      picker.appendChild(opt);
    });
    pickerWrap.classList.toggle('hidden', webhooks.length <= 1);
    const updateInfo = () => {
      const w = webhooks[Number(picker.value) || 0];
      $('#wf-webhook-info').textContent = `${w.method} /webhook/${w.path}`;
    };
    picker.onchange = updateInfo;
    updateInfo();
  } else {
    $('#wf-runner').classList.add('hidden');
    $('#wf-norunner').classList.remove('hidden');
  }
}

$('#refresh-wf').addEventListener('click', loadWorkflows);

// ---------- run workflow ----------
$('#run-button').addEventListener('click', async () => {
  if (!selectedWorkflow) return;
  const mode = $('#run-mode').value;
  const bodyText = $('#run-body').value.trim();
  const headersText = $('#run-headers').value.trim();
  let body = null, headers = null;
  if (bodyText) {
    try { body = JSON.parse(bodyText); }
    catch (e) { alert(`Body is not valid JSON: ${e.message}`); return; }
  }
  if (headersText) {
    try { headers = JSON.parse(headersText); }
    catch (e) { alert(`Headers is not valid JSON: ${e.message}`); return; }
  }
  const btn = $('#run-button');
  btn.disabled = true;
  btn.textContent = 'Running…';
  try {
    const r = await fetch(`/api/workflows/${selectedWorkflow.id}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode, body, headers, webhook_index: Number($('#wf-webhook-picker').value || 0) }),
    });
    const data = await r.json();
    $('#run-result').classList.remove('hidden');
    if (!r.ok) {
      $('#run-status').textContent = `error ${r.status}`;
      $('#run-body-response').textContent = JSON.stringify(data, null, 2);
    } else {
      $('#run-status').textContent = data.status;
      $('#run-body-response').textContent = typeof data.body === 'string'
        ? data.body
        : JSON.stringify(data.body, null, 2);
    }
  } catch (e) {
    alert(`Request failed: ${e.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Run';
  }
});

// ---------- PM Agent plan (brief mode, SSE) ----------
$('#submit-plan').addEventListener('click', async () => {
  const brief = $('#brief').value.trim();
  if (!brief) { alert('Enter a brief first.'); return; }
  await runPlan({ brief });
});

async function runPlan(payload) {
  const log = $('#plan-log');
  log.textContent = '';
  $('#plan-result').classList.add('hidden');
  $('#build-box').classList.add('hidden');
  const btn = $('#submit-plan');
  const ivBtn = $('#iv-finish');
  if (btn) { btn.disabled = true; btn.textContent = 'Planning…'; }
  if (ivBtn) { ivBtn.disabled = true; ivBtn.textContent = 'Planning…'; }

  try {
    const r = await fetch('/api/plan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!r.ok) { log.textContent = `Error: ${await r.text()}`; return; }
    await consumeSSE(r.body, (evt, data) => {
      if (evt === 'log') {
        log.textContent += data.line + '\n';
        log.scrollTop = log.scrollHeight;
      } else if (evt === 'done') {
        log.textContent += `\n[exit ${data.exit_code}]\n`;
        if (data.spec) {
          $('#plan-result').classList.remove('hidden');
          $('#plan-spec-path').textContent = data.spec_path || '';
          $('#plan-spec').textContent = JSON.stringify(data.spec, null, 2);
          $('#build-spec').dataset.specPath = data.spec_path || '';
        }
      } else if (evt === 'error') {
        log.textContent += `\nERROR: ${data.message}\n`;
      }
    });
  } catch (e) {
    log.textContent += `\nRequest failed: ${e.message}\n`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Plan this workflow'; }
    if (ivBtn) { ivBtn.disabled = false; ivBtn.textContent = 'Finish interview → generate spec'; }
  }
}

$('#copy-spec').addEventListener('click', async () => {
  const text = $('#plan-spec').textContent;
  try {
    await navigator.clipboard.writeText(text);
    $('#copy-spec').textContent = 'Copied ✓';
    setTimeout(() => { $('#copy-spec').textContent = 'Copy JSON'; }, 1500);
  } catch (e) { alert(`Copy failed: ${e.message}`); }
});

// ---------- Build this (SSE) ----------
$('#build-spec').addEventListener('click', async () => {
  const specPath = $('#build-spec').dataset.specPath;
  if (!specPath) { alert('No spec available.'); return; }
  const log = $('#build-log');
  log.textContent = '';
  $('#build-box').classList.remove('hidden');
  $('#build-done').classList.add('hidden');
  const btn = $('#build-spec');
  btn.disabled = true;
  btn.textContent = 'Building…';
  try {
    const r = await fetch('/api/build', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ spec_path: specPath }),
    });
    if (!r.ok) { log.textContent = `Error: ${await r.text()}`; return; }
    await consumeSSE(r.body, (evt, data) => {
      if (evt === 'log') {
        log.textContent += data.line + '\n';
        log.scrollTop = log.scrollHeight;
      } else if (evt === 'done') {
        log.textContent += `\n[exit ${data.exit_code}]\n`;
        if (data.workflow_id) {
          $('#build-done').classList.remove('hidden');
          $('#build-wf-id').textContent = data.workflow_id;
          $('#build-wf-link').href = data.edit_url || '#';
        }
      }
    });
  } catch (e) {
    log.textContent += `\nRequest failed: ${e.message}\n`;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Build this →';
  }
});

// ---------- Interactive interview ----------
let ivState = null; // {description, inferred, questions: [{text, answer}]}

$('#iv-start').addEventListener('click', async () => {
  const desc = $('#iv-description').value.trim();
  if (!desc) { alert('Give a short description first.'); return; }
  const btn = $('#iv-start');
  btn.disabled = true;
  btn.textContent = 'Asking…';
  try {
    const r = await fetch('/api/interview/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ description: desc }),
    });
    if (!r.ok) { alert(`Error: ${await r.text()}`); return; }
    const data = await r.json();
    ivState = {
      description: desc,
      inferred: data.inferred || {},
      questions: (data.questions || []).map(q => ({ text: q, answer: '' })),
    };
    renderInterview();
    $('#iv-chat').classList.remove('hidden');
  } catch (e) {
    alert(`Request failed: ${e.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Start interview';
  }
});

function renderInterview() {
  $('#iv-inferred').textContent = JSON.stringify(ivState.inferred, null, 2);
  const ol = $('#iv-questions');
  ol.innerHTML = '';
  if (ivState.questions.length === 0) {
    ol.innerHTML = '<li class="hint">No follow-up questions — you can finish the interview.</li>';
    $('#iv-finish').disabled = false;
    return;
  }
  ivState.questions.forEach((q, idx) => {
    const li = document.createElement('li');
    li.innerHTML = `
      <div class="iv-q">${escapeHtml(q.text)}</div>
      <textarea class="iv-a" data-idx="${idx}" rows="2" placeholder="Your answer…">${escapeHtml(q.answer)}</textarea>
      <div class="mic-row small">
        <button class="mic iv-mic" data-idx="${idx}">🎙</button>
        <span class="iv-mic-status"></span>
      </div>
    `;
    ol.appendChild(li);
  });
  ol.querySelectorAll('.iv-a').forEach(ta => {
    ta.addEventListener('input', e => {
      const idx = +e.target.dataset.idx;
      ivState.questions[idx].answer = e.target.value;
      updateFinishState();
    });
  });
  ol.querySelectorAll('.iv-mic').forEach(b => {
    if (!hasOpenAI) { b.disabled = true; return; }
    b.addEventListener('click', e => {
      const idx = +b.dataset.idx;
      const ta = ol.querySelector(`.iv-a[data-idx="${idx}"]`);
      const status = b.parentElement.querySelector('.iv-mic-status');
      recordIntoTextarea(b, ta, status);
    });
  });
  updateFinishState();
}

function updateFinishState() {
  const allAnswered = ivState.questions.every(q => q.answer.trim().length > 0);
  $('#iv-finish').disabled = !allAnswered;
}

$('#iv-finish').addEventListener('click', async () => {
  const btn = $('#iv-finish');
  btn.disabled = true;
  btn.textContent = 'Consolidating…';
  const answers = {};
  ivState.questions.forEach((q, i) => { answers[`q${i+1}`] = q.answer; });
  try {
    const r = await fetch('/api/interview/finish', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        description: ivState.description,
        inferred: ivState.inferred,
        answers,
      }),
    });
    if (!r.ok) { alert(`Error: ${await r.text()}`); btn.disabled = false; btn.textContent = 'Finish interview → generate spec'; return; }
    const data = await r.json();
    await runPlan({ requirements_path: data.requirements_path });
  } catch (e) {
    alert(`Request failed: ${e.message}`);
    btn.disabled = false;
    btn.textContent = 'Finish interview → generate spec';
  }
});

// ---------- SSE parser ----------
async function consumeSSE(stream, onEvent) {
  const reader = stream.getReader();
  const decoder = new TextDecoder('utf-8');
  let buf = '';
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf('\n\n')) !== -1) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let eventName = 'message';
      const dataLines = [];
      for (const line of chunk.split('\n')) {
        if (line.startsWith('event:')) eventName = line.slice(6).trim();
        else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
      }
      const raw = dataLines.join('\n');
      let data;
      try { data = JSON.parse(raw); } catch { data = raw; }
      onEvent(eventName, data);
    }
  }
}

// ---------- mic helpers ----------
let activeRecorder = null;

async function recordIntoTextarea(btn, textarea, statusEl) {
  if (activeRecorder && activeRecorder.state === 'recording') {
    activeRecorder.stop();
    return;
  }
  if (!navigator.mediaDevices || !window.MediaRecorder) {
    alert('Your browser does not support recording.'); return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const chunks = [];
    const rec = new MediaRecorder(stream);
    activeRecorder = rec;
    rec.addEventListener('dataavailable', e => { if (e.data.size > 0) chunks.push(e.data); });
    rec.addEventListener('stop', async () => {
      stream.getTracks().forEach(t => t.stop());
      btn.classList.remove('recording');
      btn.textContent = btn.dataset.originalText || '🎙';
      activeRecorder = null;
      const blob = new Blob(chunks, { type: rec.mimeType || 'audio/webm' });
      statusEl.textContent = 'Transcribing…';
      try {
        const form = new FormData();
        const ext = (blob.type.split('/')[1] || 'webm').split(';')[0];
        form.append('audio', blob, `audio.${ext}`);
        const r = await fetch('/api/stt', { method: 'POST', body: form });
        if (!r.ok) { statusEl.textContent = `STT error: ${await r.text()}`; return; }
        const data = await r.json();
        const added = data.text || '';
        textarea.value = (textarea.value ? textarea.value.replace(/\s*$/, '') + ' ' : '') + added;
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
        statusEl.textContent = `Added ${added.length} chars`;
      } catch (e) {
        statusEl.textContent = `STT error: ${e.message}`;
      }
    });
    btn.dataset.originalText = btn.textContent;
    btn.classList.add('recording');
    btn.textContent = '■ Stop';
    statusEl.textContent = 'Recording…';
    rec.start();
  } catch (e) {
    statusEl.textContent = `Mic error: ${e.message}`;
  }
}

// Wire up the two fixed mic buttons (brief + interview description).
$('#mic').addEventListener('click', () => {
  recordIntoTextarea($('#mic'), $('#brief'), $('#mic-status'));
});
$('#iv-mic-desc').addEventListener('click', () => {
  recordIntoTextarea($('#iv-mic-desc'), $('#iv-description'), $('#iv-mic-desc-status'));
});

// ---------- utils ----------
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

// preload config
fetch('/api/config').then(r => r.json()).then(cfg => {
  hasOpenAI = !!cfg.has_openai;
  if (!cfg.has_openai) {
    $('#mic').disabled = true;
    $('#iv-mic-desc').disabled = true;
    $('#mic-status').textContent = 'OPENAI_API_KEY not set — voice disabled.';
    $('#iv-mic-desc-status').textContent = 'Voice disabled.';
  }
  if (!cfg.has_anthropic) {
    $('#submit-plan').disabled = true;
    $('#iv-start').disabled = true;
    $('#submit-plan').title = 'ANTHROPIC_API_KEY not set';
  }
}).catch(() => {});
