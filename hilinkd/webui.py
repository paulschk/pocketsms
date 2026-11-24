"""The web interface: one self-contained page, no build step, no CDN.

Everything (CSS, JS, icons) is inlined on purpose. The service is meant to live
on a LAN with no outbound dependencies, so loading a font or a framework from
somebody else's server would be both pointless and a privacy leak.
"""

from __future__ import annotations

from . import NAME, TAGLINE, __version__

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark light">
<title>{name} - SMS gateway</title>
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<style>
:root {{
  --bg: #0f1115; --panel: #161920; --panel-2: #1c2029; --line: #262b35;
  --text: #e7eaef; --muted: #8d97a6; --accent: #4c8dff; --accent-soft: #1e3a68;
  --ok: #4ade80; --warn: #fbbf24; --bad: #f87171; --radius: 10px;
  --shadow: 0 1px 2px rgba(0,0,0,.3), 0 8px 24px rgba(0,0,0,.18);
}}
@media (prefers-color-scheme: light) {{
  :root {{
    --bg: #f6f7f9; --panel: #fff; --panel-2: #f2f4f7; --line: #e2e5ea;
    --text: #16181d; --muted: #667085; --accent: #2563eb; --accent-soft: #dbe7ff;
    --shadow: 0 1px 2px rgba(16,24,40,.06), 0 8px 24px rgba(16,24,40,.06);
  }}
}}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; }}
body {{
  background: var(--bg); color: var(--text);
  font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  -webkit-font-smoothing: antialiased;
}}
a {{ color: var(--accent); }}
code, .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .92em; }}
.hidden {{ display: none !important; }}

/* ---------- login ---------- */
.login-wrap {{ min-height: 100vh; display: grid; place-items: center; padding: 24px; }}
.login {{
  width: 100%; max-width: 380px; background: var(--panel); border: 1px solid var(--line);
  border-radius: 14px; padding: 28px; box-shadow: var(--shadow);
}}
.brand {{ display: flex; align-items: center; gap: 11px; margin-bottom: 4px; }}
.brand svg {{ width: 30px; height: 30px; flex: none; }}
.brand h1 {{ font-size: 19px; margin: 0; letter-spacing: -.2px; }}
.tagline {{ color: var(--muted); font-size: 13px; margin: 2px 0 22px; }}
.login form {{ display: grid; gap: 12px; }}

/* ---------- shell ---------- */
header {{
  position: sticky; top: 0; z-index: 10; background: color-mix(in srgb, var(--bg) 88%, transparent);
  backdrop-filter: blur(8px); border-bottom: 1px solid var(--line);
}}
.bar {{
  max-width: 1040px; margin: 0 auto; padding: 12px 20px;
  display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
}}
.bar .brand h1 {{ font-size: 16px; }}
.bar .brand svg {{ width: 24px; height: 24px; }}
.spacer {{ flex: 1 1 auto; }}
.pill {{
  display: inline-flex; align-items: center; gap: 7px; padding: 5px 11px; font-size: 12.5px;
  background: var(--panel-2); border: 1px solid var(--line); border-radius: 999px; color: var(--muted);
}}
.pill b {{ color: var(--text); font-weight: 600; }}
.dot {{ width: 7px; height: 7px; border-radius: 50%; background: var(--muted); flex: none; }}
.dot.ok {{ background: var(--ok); }} .dot.warn {{ background: var(--warn); }}
.dot.bad {{ background: var(--bad); }}
main {{ max-width: 1040px; margin: 0 auto; padding: 22px 20px 60px; }}
nav.tabs {{ display: flex; gap: 3px; margin-bottom: 20px; border-bottom: 1px solid var(--line); flex-wrap: wrap; }}
nav.tabs button {{
  background: none; border: 0; border-bottom: 2px solid transparent; color: var(--muted);
  padding: 9px 14px; font: inherit; font-weight: 500; cursor: pointer; border-radius: 6px 6px 0 0;
}}
nav.tabs button:hover {{ color: var(--text); background: var(--panel-2); }}
nav.tabs button[aria-selected="true"] {{ color: var(--text); border-bottom-color: var(--accent); }}
nav.tabs .count {{ font-size: 11px; color: var(--muted); margin-left: 5px; }}

/* ---------- cards and forms ---------- */
.grid {{ display: grid; gap: 18px; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }}
.card {{
  background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius);
  padding: 18px; box-shadow: var(--shadow);
}}
.card h2 {{
  font-size: 12px; text-transform: uppercase; letter-spacing: .07em; color: var(--muted);
  margin: 0 0 14px; font-weight: 600;
}}
label {{ display: block; font-size: 12.5px; color: var(--muted); margin: 0 0 5px; }}
input, textarea, select {{
  width: 100%; background: var(--panel-2); color: var(--text); border: 1px solid var(--line);
  border-radius: 8px; padding: 9px 11px; font: inherit;
}}
input:focus, textarea:focus, select:focus {{
  outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft);
}}
textarea {{ resize: vertical; min-height: 92px; }}
.field + .field {{ margin-top: 12px; }}
button.btn {{
  border: 1px solid transparent; border-radius: 8px; padding: 9px 15px; font: inherit;
  font-weight: 600; cursor: pointer; background: var(--accent); color: #fff;
}}
button.btn:hover {{ filter: brightness(1.08); }}
button.btn.ghost {{ background: var(--panel-2); color: var(--text); border-color: var(--line); }}
button.btn.danger {{ background: transparent; color: var(--bad); border-color: var(--line); }}
button.btn:disabled {{ opacity: .55; cursor: progress; }}
.row {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }}
.row > .grow {{ flex: 1 1 200px; }}
.note {{ font-size: 13px; color: var(--muted); margin-top: 10px; }}
.note.ok {{ color: var(--ok); }} .note.bad {{ color: var(--bad); }}
.kv {{ display: grid; grid-template-columns: auto 1fr; gap: 7px 16px; font-size: 14px; }}
.kv dt {{ color: var(--muted); }}
.kv dd {{ margin: 0; text-align: right; }}

/* ---------- lists ---------- */
ul.messages {{ list-style: none; margin: 0; padding: 0; }}
ul.messages li {{ padding: 12px 0; border-bottom: 1px solid var(--line); }}
ul.messages li:last-child {{ border-bottom: 0; }}
.msg-head {{ display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }}
.msg-head .who {{ font-weight: 600; }}
.msg-head time {{ color: var(--muted); font-size: 12.5px; }}
.msg-body {{ white-space: pre-wrap; margin-top: 4px; overflow-wrap: anywhere; }}
table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
th, td {{ text-align: left; padding: 9px 10px; border-bottom: 1px solid var(--line); }}
th {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .05em; }}
td.mono {{ color: var(--muted); }}
.empty {{ color: var(--muted); font-size: 14px; padding: 8px 0; }}
.once {{
  background: var(--panel-2); border: 1px dashed var(--line); border-radius: 8px;
  padding: 10px 12px; margin-top: 12px; overflow-wrap: anywhere;
}}
footer {{ max-width: 1040px; margin: 0 auto; padding: 0 20px 40px; color: var(--muted); font-size: 12.5px; }}
</style>
</head>
<body>

<div id="login" class="login-wrap">
  <div class="login">
    <div class="brand">
      <svg viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="7" fill="#2b6cb0"/>
      <path d="M9 21V11h3.6c2.2 0 3.4 1 3.4 2.6 0 1.2-.7 2-1.8 2.3 1.3.2 2.2 1.1 2.2 2.5 0 1.7-1.3 2.6-3.6 2.6H9zm2.4-6h1c.9 0 1.4-.4 1.4-1.1S13.3 13 12.4 13h-1v2zm0 4.2h1.2c.9 0 1.5-.4 1.5-1.1 0-.7-.5-1.1-1.5-1.1h-1.2v2.2z" fill="#fff"/></svg>
      <h1>{name}</h1>
    </div>
    <p class="tagline">{tagline}</p>
    <form id="login-form">
      <div class="field"><label for="u">Username</label><input id="u" autocomplete="username" required></div>
      <div class="field"><label for="p">Password</label><input id="p" type="password" autocomplete="current-password" required></div>
      <button class="btn" type="submit" id="login-btn">Sign in</button>
      <div class="note bad hidden" id="login-error"></div>
    </form>
  </div>
</div>

<div id="app" class="hidden">
  <header>
    <div class="bar">
      <div class="brand">
        <svg viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="7" fill="#2b6cb0"/>
        <path d="M9 21V11h3.6c2.2 0 3.4 1 3.4 2.6 0 1.2-.7 2-1.8 2.3 1.3.2 2.2 1.1 2.2 2.5 0 1.7-1.3 2.6-3.6 2.6H9zm2.4-6h1c.9 0 1.4-.4 1.4-1.1S13.3 13 12.4 13h-1v2zm0 4.2h1.2c.9 0 1.5-.4 1.5-1.1 0-.7-.5-1.1-1.5-1.1h-1.2v2.2z" fill="#fff"/></svg>
        <h1>{name}</h1>
      </div>
      <span class="spacer"></span>
      <span class="pill" id="modem-pill"><span class="dot" id="modem-dot"></span><span id="modem-text">checking...</span></span>
      <span class="pill" id="user-pill"></span>
      <button class="btn ghost" id="logout">Sign out</button>
    </div>
  </header>

  <main>
    <nav class="tabs" role="tablist">
      <button role="tab" data-tab="compose" aria-selected="true">Compose</button>
      <button role="tab" data-tab="inbox" aria-selected="false">Inbox <span class="count" id="in-count"></span></button>
      <button role="tab" data-tab="sent" aria-selected="false">Sent <span class="count" id="out-count"></span></button>
      <button role="tab" data-tab="verify" aria-selected="false">Verification</button>
      <button role="tab" data-tab="settings" aria-selected="false">Settings</button>
    </nav>

    <section id="tab-compose" class="grid">
      <div class="card">
        <h2>Send a message</h2>
        <div class="field"><label for="to">Phone number</label>
          <input id="to" placeholder="+33612345678 or 0612345678" autocomplete="off"></div>
        <div class="field"><label for="text">Message</label>
          <textarea id="text" maxlength="918" placeholder="Type your message"></textarea></div>
        <div class="row" style="margin-top:14px">
          <button class="btn" id="send">Send</button>
          <span class="note" id="send-note"></span>
        </div>
      </div>
      <div class="card">
        <h2>Modem</h2>
        <dl class="kv" id="modem-details"><dt>Status</dt><dd>loading...</dd></dl>
      </div>
    </section>

    <section id="tab-inbox" class="hidden"><div class="card">
      <div class="row" style="margin-bottom:12px"><h2 style="margin:0">Received messages</h2>
        <span class="spacer" style="flex:1"></span>
        <button class="btn ghost" id="refresh-in">Refresh</button></div>
      <ul class="messages" id="inbox"></ul>
    </div></section>

    <section id="tab-sent" class="hidden"><div class="card">
      <div class="row" style="margin-bottom:12px"><h2 style="margin:0">Sent messages</h2>
        <span class="spacer" style="flex:1"></span>
        <button class="btn ghost" id="refresh-out">Refresh</button></div>
      <ul class="messages" id="sent"></ul>
    </div></section>

    <section id="tab-verify" class="grid">
      <div class="card">
        <h2>Send a verification code</h2>
        <p class="note" style="margin-top:0">Another service calls the API for this. The form is here
          so you can test the flow by hand.</p>
        <div class="field"><label for="otp-phone">Phone number</label>
          <input id="otp-phone" placeholder="+33612345678" autocomplete="off" value="{admin_phone}"></div>
        <div class="field"><label for="otp-service">Service name (optional)</label>
          <input id="otp-service" placeholder="caddy, grafana, ..."></div>
        <div class="row" style="margin-top:14px"><button class="btn" id="otp-send">Send code</button></div>
        <div id="otp-start-result"></div>
      </div>
      <div class="card">
        <h2>Check a code</h2>
        <div class="field"><label for="otp-code">Code received by SMS</label>
          <input id="otp-code" inputmode="numeric" autocomplete="one-time-code" placeholder="123456"></div>
        <div class="row" style="margin-top:14px"><button class="btn ghost" id="otp-verify">Verify</button></div>
        <div id="otp-verify-result"></div>
      </div>
    </section>

    <section id="tab-settings" class="hidden">
      <div class="grid">
        <div class="card" id="card-keys">
          <h2>API keys</h2>
          <div class="row">
            <input class="grow" id="key-label" placeholder="label, e.g. grafana">
            <select id="key-scope" style="width:auto">
              <option value="otp">otp</option><option value="sms">sms</option><option value="full">full</option>
            </select>
            <button class="btn" id="key-create">Create</button>
          </div>
          <div id="key-once"></div>
          <table style="margin-top:14px"><thead><tr><th>Label</th><th>Scope</th><th>Key</th><th></th></tr></thead>
            <tbody id="keys"></tbody></table>
        </div>
        <div class="card" id="card-users">
          <h2>Accounts</h2>
          <div class="row">
            <input class="grow" id="new-user" placeholder="username">
            <input class="grow" id="new-pass" type="password" placeholder="password (8+ chars)">
            <button class="btn" id="user-create">Add</button>
          </div>
          <table style="margin-top:14px"><thead><tr><th>User</th><th>Role</th><th></th></tr></thead>
            <tbody id="users"></tbody></table>
        </div>
        <div class="card">
          <h2>Change my password</h2>
          <div class="field"><label for="pw-current">Current password</label>
            <input id="pw-current" type="password" autocomplete="current-password"></div>
          <div class="field"><label for="pw-new">New password</label>
            <input id="pw-new" type="password" autocomplete="new-password"></div>
          <div class="row" style="margin-top:14px"><button class="btn ghost" id="pw-save">Update</button>
            <span class="note" id="pw-note"></span></div>
        </div>
      </div>
    </section>
  </main>
  <footer>{name} {version} &middot; {tagline}</footer>
</div>

<script>
const CSRF = {{'X-Requested-With': '{name}'}};
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c =>
  ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));

async function api(path, options) {{
  const opts = Object.assign({{credentials: 'same-origin'}}, options || {{}});
  if (opts.method && opts.method !== 'GET') opts.headers = Object.assign({{}}, CSRF, opts.headers || {{}});
  const res = await fetch(path, opts);
  const raw = await res.text();
  let data = null;
  try {{ data = raw ? JSON.parse(raw) : null; }} catch (e) {{ data = null; }}
  if (!res.ok) throw new Error((data && (data.detail || data.error)) || ('HTTP ' + res.status));
  return data;
}}

const post = (path, body) => api(path, {{
  method: 'POST', headers: {{'Content-Type': 'application/json'}},
  body: JSON.stringify(body || {{}}),
}});

function show(section) {{ $('login').classList.toggle('hidden', section !== 'login');
  $('app').classList.toggle('hidden', section !== 'app'); setTab(section); }}

function setTab(tab) {{
  document.querySelectorAll('nav.tabs button').forEach(b =>
    b.setAttribute('aria-selected', String(b.dataset.tab === tab)));
  ['compose', 'inbox', 'sent', 'verify', 'settings'].forEach(name =>
    $('tab-' + name).classList.toggle('hidden', name !== tab));
  if (tab === 'inbox') loadMessages('inbox');
  if (tab === 'sent') loadMessages('sent');
  if (tab === 'settings') {{ loadKeys(); loadUsers(); }}
}}

document.querySelectorAll('nav.tabs button').forEach(button =>
  button.addEventListener('click', () => setTab(button.dataset.tab)));

/* ---------- session ---------- */
async function boot() {{
  try {{
    const me = await api('/api/v1/auth/me');
    $('user-pill').textContent = me.user.username + (me.user.is_admin ? ' (admin)' : '');
    $('card-keys').classList.toggle('hidden', !me.user.is_admin);
    $('card-users').classList.toggle('hidden', !me.user.is_admin);
    show('app');
    refreshStatus();
    loadMessages('inbox');
  }} catch (e) {{ show('login'); }}
}}

$('login-form').addEventListener('submit', async (event) => {{
  event.preventDefault();
  $('login-btn').disabled = true;
  $('login-error').classList.add('hidden');
  try {{
    await post('/api/v1/auth/login', {{username: $('u').value, password: $('p').value}});
    $('p').value = '';
    await boot();
  }} catch (e) {{
    $('login-error').textContent = e.message;
    $('login-error').classList.remove('hidden');
  }} finally {{ $('login-btn').disabled = false; }}
}});

$('logout').addEventListener('click', async () => {{
  try {{ await post('/api/v1/auth/logout'); }} catch (e) {{ /* leaving anyway */ }}
  location.reload();
}});

/* ---------- modem status ---------- */
const CONNECTION = {{
  '900': ['warn', 'registering'], '901': ['ok', 'data up'], '902': ['warn', 'no data'],
  '903': ['warn', 'disconnecting'], '904': ['bad', 'no service'],
}};

async function refreshStatus() {{
  try {{
    const data = await api('/api/v1/status');
    const info = data.information || {{}}, status = data.status || {{}};
    const [state, label] = CONNECTION[status.ConnectionStatus] || ['warn', 'unknown'];
    const signal = status.SignalStrength ? status.SignalStrength + '/5' : 'no signal';
    $('modem-dot').className = 'dot ' + state;
    $('modem-text').textContent = (info.DeviceName || 'modem') + ' - ' + label;
    const rows = [
      ['Device', info.DeviceName || '-'],
      ['Firmware', info.SoftwareVersion || '-'],
      ['Operator', data.operator || '-'],
      ['Connection', label],
      ['Signal', signal],
      ['Battery', (status.BatteryPercent || '?') + '%'],
      ['SIM', (data.sim && data.sim.SimState) === '257' ? 'ready' : 'check'],
      ['WAN IP', status.WanIPAddress || '-'],
      ['Received', (data.messages && data.messages.in) || 0],
      ['Sent', (data.messages && data.messages.out) || 0],
      ['Last poll', (data.poll && data.poll.last) || 'pending'],
    ];
    $('modem-details').innerHTML = rows.map(r =>
      '<dt>' + esc(r[0]) + '</dt><dd>' + esc(r[1]) + '</dd>').join('');
    $('in-count').textContent = (data.messages && data.messages.in) || '';
    $('out-count').textContent = (data.messages && data.messages.out) || '';
  }} catch (e) {{
    $('modem-dot').className = 'dot bad';
    $('modem-text').textContent = e.message;
  }}
}}

/* ---------- messages ---------- */
async function loadMessages(box) {{
  const target = box === 'inbox' ? 'inbox' : 'sent';
  try {{
    const data = await api('/api/v1/messages?box=' + target + '&limit=100');
    const items = data.messages || [];
    $(target).innerHTML = items.length ? items.map(m =>
      '<li><div class="msg-head"><span class="who mono">' + esc(m.phone) + '</span>' +
      '<time>' + esc(m.created_at.replace('T', ' ').slice(0, 16)) + '</time>' +
      (m.status && m.status !== 'ok' && m.status !== 'accepted'
        ? '<span class="pill"><span class="dot bad"></span>' + esc(m.status) + '</span>' : '') +
      '</div><div class="msg-body">' + esc(m.text) + '</div></li>').join('')
      : '<li class="empty">Nothing here yet.</li>';
  }} catch (e) {{ $(target).innerHTML = '<li class="empty">' + esc(e.message) + '</li>'; }}
}}

$('refresh-in').addEventListener('click', () => loadMessages('inbox'));
$('refresh-out').addEventListener('click', () => loadMessages('sent'));

$('send').addEventListener('click', async () => {{
  const note = $('send-note');
  note.className = 'note';
  note.textContent = 'sending...';
  try {{
    const result = await post('/api/v1/messages', {{to: $('to').value, text: $('text').value}});
    note.className = 'note ok';
    note.textContent = 'Accepted by the modem (message #' + result.id + ')';
    $('text').value = '';
    refreshStatus();
    loadMessages('sent');
  }} catch (e) {{ note.className = 'note bad'; note.textContent = e.message; }}
}});

/* ---------- two-step verification ---------- */
let challenge = null;

$('otp-send').addEventListener('click', async () => {{
  const box = $('otp-start-result');
  box.innerHTML = '<div class="note">sending...</div>';
  try {{
    const result = await post('/api/v1/otp', {{phone: $('otp-phone').value, service: $('otp-service').value}});
    challenge = result.challenge;
    box.innerHTML = '<div class="once"><b>Code sent</b> to ' + esc(result.phone) +
      '<br>challenge <code>' + esc(result.challenge) + '</code> &middot; ' +
      result.digits + ' digits &middot; valid ' + Math.round(result.expires_in / 60) + ' min' +
      '<br><span class="note">An API client would use this id with POST /api/v1/otp/verify.</span></div>';
  }} catch (e) {{ box.innerHTML = '<div class="note bad">' + esc(e.message) + '</div>'; }}
}});

$('otp-verify').addEventListener('click', async () => {{
  const box = $('otp-verify-result');
  box.innerHTML = '<div class="note">checking...</div>';
  try {{
    const result = await post('/api/v1/otp/verify', {{challenge: challenge, code: $('otp-code').value}});
    box.innerHTML = '<div class="note ok">Verified - ' + esc(result.phone) +
      (result.service ? ' for ' + esc(result.service) : '') + '</div>';
  }} catch (e) {{ box.innerHTML = '<div class="note bad">' + esc(e.message) + '</div>'; }}
}});

/* ---------- settings ---------- */
async function loadKeys() {{
  try {{
    const data = await api('/api/v1/keys');
    $('keys').innerHTML = (data.keys || []).map(k =>
      '<tr><td>' + esc(k.label) + '</td><td class="mono">' + esc(k.scope) + '</td>' +
      '<td class="mono">' + esc(k.display) + '</td>' +
      '<td style="text-align:right"><button class="btn danger" data-key="' + k.id + '">Revoke</button></td></tr>'
    ).join('') || '<tr><td class="empty" colspan="4">No API keys yet.</td></tr>';
    $('keys').querySelectorAll('[data-key]').forEach(button =>
      button.addEventListener('click', async () => {{
        await api('/api/v1/keys/' + button.dataset.key, {{method: 'DELETE'}});
        loadKeys();
      }}));
  }} catch (e) {{ $('keys').innerHTML = '<tr><td class="empty">' + esc(e.message) + '</td></tr>'; }}
}}

$('key-create').addEventListener('click', async () => {{
  try {{
    const result = await post('/api/v1/keys', {{label: $('key-label').value, scope: $('key-scope').value}});
    $('key-once').innerHTML = '<div class="once"><b>Copy this now, it is shown once:</b><br>' +
      '<code>' + esc(result.key) + '</code></div>';
    $('key-label').value = '';
    loadKeys();
  }} catch (e) {{ $('key-once').innerHTML = '<div class="note bad">' + esc(e.message) + '</div>'; }}
}});

async function loadUsers() {{
  try {{
    const data = await api('/api/v1/users');
    $('users').innerHTML = (data.users || []).map(u =>
      '<tr><td>' + esc(u.username) + '</td><td>' + (u.is_admin ? 'admin' : 'user') + '</td>' +
      '<td style="text-align:right"><button class="btn danger" data-user="' + u.id + '">Delete</button></td></tr>'
    ).join('') || '<tr><td class="empty" colspan="3">No accounts.</td></tr>';
    $('users').querySelectorAll('[data-user]').forEach(button =>
      button.addEventListener('click', async () => {{
        await api('/api/v1/users/' + button.dataset.user, {{method: 'DELETE'}});
        loadUsers();
      }}));
  }} catch (e) {{ $('users').innerHTML = '<tr><td class="empty">' + esc(e.message) + '</td></tr>'; }}
}}

$('user-create').addEventListener('click', async () => {{
  try {{
    await post('/api/v1/users', {{username: $('new-user').value, password: $('new-pass').value}});
    $('new-user').value = ''; $('new-pass').value = '';
    loadUsers();
  }} catch (e) {{ alert(e.message); }}
}});

$('pw-save').addEventListener('click', async () => {{
  const note = $('pw-note');
  try {{
    await post('/api/v1/auth/password', {{current: $('pw-current').value, new: $('pw-new').value}});
    note.className = 'note ok'; note.textContent = 'Password updated';
    $('pw-current').value = ''; $('pw-new').value = '';
  }} catch (e) {{ note.className = 'note bad'; note.textContent = e.message; }}
}});

boot();
setInterval(refreshStatus, 15000);
setInterval(() => {{
  if (!$('tab-inbox').classList.contains('hidden')) loadMessages('inbox');
}}, 20000);
</script>
</body>
</html>
"""


def page() -> str:
    """Render the single page for this build."""
    return PAGE.format(name=NAME, tagline=TAGLINE, version=__version__,
                       admin_phone="")
