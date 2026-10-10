const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (v) => '$' + Number(v).toFixed(2);

async function api(path, opts = {}) {
  const init = { ...opts };
  if (init.body && !(init.body instanceof FormData)) {
    init.headers = { 'Content-Type': 'application/json' };
    init.body = JSON.stringify(init.body);
  }
  const r = await fetch(path, init);
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = j.error || j.detail || msg; } catch (e) {}
    throw new Error(msg);
  }
  if (r.status === 204) return null;
  const ct = r.headers.get('content-type') || '';
  return ct.includes('json') ? r.json() : r.text();
}

function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove('show'), 1800);
}

let PROFILES = [], PRODUCTS = [], SETTINGS = {};

// ── tabs ────────────────────────────────────────────────────────────────
$$('nav button').forEach(b => b.onclick = () => {
  $$('nav button').forEach(x => x.classList.toggle('active', x === b));
  $$('.tab').forEach(t => t.classList.toggle('active', t.id === 'tab-' + b.dataset.tab));
  if (b.dataset.tab === 'pricing') renderRules();
});

async function loadAll() {
  [PROFILES, PRODUCTS, SETTINGS] = await Promise.all([api('/api/profiles'), api('/api/products'), api('/api/settings')]);
  renderProfiles(); renderProducts(); renderSettings(); renderSimButtons(); fillProductSelect();
}

// ── LIVE ────────────────────────────────────────────────────────────────
function renderTag(d) {
  $('#tag').innerHTML = `
    <div class="hdr"><span>FRESH PRODUCE</span><span>${esc(d.tag || '')}</span></div>
    <div class="name">${esc(d.product)}</div>
    <div class="unit">${esc(d.unit)}</div>
    ${d.badge ? `<div class="badge">${esc(d.badge)}</div>` : ''}
    ${d.was ? `<div class="was">${esc(d.was)}</div>` : ''}
    <div class="price">${esc(d.price)}</div>
    <div class="foot"><span>|||| ||| | |||| || |||</span><span>ONLINE</span></div>`;
}

function renderSimButtons() {
  const box = $('#simButtons');
  box.innerHTML = PROFILES.map(p =>
    `<button data-sim="${p.id}"><span class="swatch" style="background:${esc(p.color_hex)}"></span> ${esc(p.code)} · ${esc(p.name)}</button>`
  ).join('') + `<button data-sim="">Clear (default price)</button>`;
  $$('[data-sim]', box).forEach(b => b.onclick = async () => {
    const id = b.dataset.sim === '' ? null : Number(b.dataset.sim);
    await api('/api/simulate', { method: 'POST', body: { profile_id: id, seconds: 15 } });
    toast(id ? 'Simulating for 15 s' : 'Simulation cleared');
  });
}

async function pollLive() {
  try {
    const [st, d] = await Promise.all([api('/api/state'), api('/api/display')]);
    renderTag(d);
    $('#camStatus').textContent = st.camera_status;
    $('#camFps').textContent = `${st.mode} · ${st.fps} fps`;
    let active = d.profile_id ? `${d.profile_code} — ${d.profile_name}` : 'E — no profile (default price)';
    if (st.override_id) active += ` (simulated, ${Math.ceil(st.override_remaining)} s left)`;
    $('#activeProfile').textContent = active;
    const thrOf = p => st.mode === 'person' ? Number(SETTINGS.person_torso_fraction || 0.3) : p.min_fraction;
    $('#scores').innerHTML = PROFILES.filter(p => p.enabled).map(p => {
      const s = st.scores[p.id] || 0, thr = thrOf(p) || 0.01;
      const w = Math.min(s / (thr * 2), 1) * 100;
      const on = st.detected.includes(p.id);
      return `<div class="score"><div class="small">${esc(p.code)} · ${esc(p.name)} — ${(s * 100).toFixed(1)}% ${on ? '<b>● present</b>' : ''}</div>
        <div class="bar"><div class="fill" style="width:${w}%;background:${esc(p.color_hex)}"></div><div class="thr"></div></div></div>`;
    }).join('') || '<p class="muted small">No enabled profiles.</p>';
  } catch (e) { $('#camStatus').textContent = 'server unreachable'; }
}

async function pollPreview() {
  const img = $('#preview');
  if (SETTINGS.preview_mode === 'off' || !$('#tab-live').classList.contains('active')) {
    $('#previewNote').textContent = SETTINGS.preview_mode === 'off' ? '(off)' : ''; return;
  }
  try {
    const r = await fetch('/api/preview.jpg?t=' + Date.now(), { cache: 'no-store' });
    if (r.status === 200) {
      const url = URL.createObjectURL(await r.blob());
      const old = img.src; img.src = url; if (old.startsWith('blob:')) URL.revokeObjectURL(old);
      $('#previewNote').textContent = `(${SETTINGS.preview_mode})`;
    } else { $('#previewNote').textContent = '(waiting for camera)'; }
  } catch (e) {}
}

// ── PROFILES ────────────────────────────────────────────────────────────
function profileCard(p) {
  return `<div class="card" data-pid="${p.id}">
    <div class="profhead"><span class="swatch" style="width:34px;height:34px;background:${esc(p.color_hex)}"></span>
      <span class="code">${esc(p.code || '?')}</span><b>${esc(p.name)}</b></div>
    ${p.photo ? `<img class="photo" src="/photos/${esc(p.photo)}" alt="">` : ''}
    <div class="form">
      <label>Code <input name="code" value="${esc(p.code)}" maxlength="4"></label>
      <label>Name / shirt <input name="name" value="${esc(p.name)}"></label>
      <label class="wide">Description <textarea name="description">${esc(p.description)}</textarea></label>
      <label>Shirt color <input name="color_hex" type="color" value="${esc(p.color_hex)}"></label>
      <label>Priority (higher wins) <input name="priority" type="number" value="${p.priority}"></label>
      <label>Hue tolerance (0-90) <input name="hue_tol" type="number" value="${p.hue_tol}"></label>
      <label>Min saturation (0-255) <input name="sat_min" type="number" value="${p.sat_min}"></label>
      <label>Min brightness (0-255) <input name="val_min" type="number" value="${p.val_min}"></label>
      <label>Trigger: fraction of zone <input name="min_fraction" type="number" step="0.002" value="${p.min_fraction}"></label>
      <label>Gray: max saturation <input name="gray_sat_max" type="number" value="${p.gray_sat_max}"></label>
      <label>Gray: brightness ± <input name="gray_val_tol" type="number" value="${p.gray_val_tol}"></label>
      <label>Enabled <select name="enabled"><option value="1" ${p.enabled ? 'selected' : ''}>yes</option><option value="0" ${!p.enabled ? 'selected' : ''}>no</option></select></label>
      <label>Shirt photo <input type="file" accept="image/*" data-photo></label>
    </div>
    <p class="muted small">Gray/white/black colors use the "Gray" settings; colorful shirts use hue/saturation/brightness.</p>
    <div class="btnrow"><button class="primary" data-save>Save</button><button class="danger" data-del>Delete</button></div>
  </div>`;
}

function renderProfiles() {
  const list = $('#profileList');
  list.innerHTML = PROFILES.map(profileCard).join('');
  $$('[data-pid]', list).forEach(card => {
    const id = Number(card.dataset.pid);
    const collect = () => Object.fromEntries($$('[name]', card).map(i => [i.name, i.value]));
    $('[data-save]', card).onclick = async () => {
      await api(`/api/profiles/${id}`, { method: 'PUT', body: collect() });
      toast('Customer saved'); await loadAll();
    };
    $('[data-del]', card).onclick = async () => {
      if (!confirm('Delete this customer profile and its pricing rules?')) return;
      await api(`/api/profiles/${id}`, { method: 'DELETE' }); await loadAll();
    };
    $('[data-photo]', card).onchange = async (e) => {
      const f = e.target.files[0]; if (!f) return;
      const fd = new FormData(); fd.append('file', f);
      try {
        const r = await api(`/api/profiles/${id}/photo`, { method: 'POST', body: fd });
        const keep = collect(); keep.color_hex = r.suggested_hex;
        await api(`/api/profiles/${id}`, { method: 'PUT', body: keep });
        toast(`Photo saved · color set to ${r.suggested_hex}`); await loadAll();
      } catch (err) { toast('Upload failed: ' + err.message); }
    };
  });
}

$('#addProfile').onclick = async () => {
  const used = new Set(PROFILES.map(p => p.code));
  const code = 'FGHIJKLMNOPQRSTUVWXYZ'.split('').find(c => !used.has(c)) || '?';
  await api('/api/profiles', { method: 'POST', body: { code, name: 'New shirt color', color_hex: '#7B1FA2' } });
  await loadAll(); toast('Customer added — set its color and pricing');
};

// ── PRODUCTS ────────────────────────────────────────────────────────────
function renderProducts() {
  $('#productRows').innerHTML = PRODUCTS.map(p => `<tr data-prod="${p.id}">
    <td><input type="radio" name="ondisplay" ${p.on_display ? 'checked' : ''}></td>
    <td><input data-f="name" value="${esc(p.name)}"></td>
    <td><input data-f="unit" value="${esc(p.unit)}"></td>
    <td><input data-f="default_price" type="number" step="0.01" value="${p.default_price}"></td>
    <td class="btnrow"><button data-save>Save</button><button class="danger" data-del>✕</button></td></tr>`).join('');
  $$('[data-prod]').forEach(row => {
    const id = Number(row.dataset.prod);
    const body = () => Object.fromEntries($$('[data-f]', row).map(i => [i.dataset.f, i.value]));
    $('[data-save]', row).onclick = async () => { await api(`/api/products/${id}`, { method: 'PUT', body: body() }); toast('Product saved'); await loadAll(); };
    $('[data-del]', row).onclick = async () => { if (confirm('Delete product?')) { await api(`/api/products/${id}`, { method: 'DELETE' }); await loadAll(); } };
    $('input[type=radio]', row).onchange = async () => { await api(`/api/products/${id}`, { method: 'PUT', body: { ...body(), on_display: 1 } }); toast('Now shown on the tag'); await loadAll(); };
  });
}
$('#addProduct').onclick = async () => { await api('/api/products', { method: 'POST', body: { name: 'New product', unit: 'each', default_price: 1 } }); await loadAll(); };

// ── PRICING ─────────────────────────────────────────────────────────────
function fillProductSelect() {
  const sel = $('#pricingProduct'), cur = sel.value;
  sel.innerHTML = PRODUCTS.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join('');
  if (cur && PRODUCTS.some(p => String(p.id) === cur)) sel.value = cur;
  if ($('#tab-pricing').classList.contains('active')) renderRules();
}
$('#pricingProduct').onchange = renderRules;

function calc(product, r) {
  let price = (r.base_price === '' || r.base_price == null) ? product.default_price : Number(r.base_price);
  const steps = [`base ${fmt(price)}`];
  for (const m of r.markups) { price *= 1 + Number(m.percent || 0) / 100; steps.push(`${Number(m.percent) >= 0 ? '+' : ''}${m.percent}% ${m.label}`); }
  price = Math.round(price * 100 + 1e-7) / 100;
  const d = Number(r.shown_discount_pct || 0);
  const was = d > 0 && d < 100 ? Math.round(price / (1 - d / 100) * 100 + 1e-7) / 100 : null;
  return { price, was, steps };
}

async function renderRules() {
  const pid = Number($('#pricingProduct').value);
  const product = PRODUCTS.find(p => p.id === pid);
  if (!product) { $('#ruleList').innerHTML = '<p class="muted">Add a product first.</p>'; return; }
  const rules = await api(`/api/rules?product_id=${pid}`);
  const byProfile = Object.fromEntries(rules.map(r => [r.profile_id, r]));
  const defaultCard = `<div class="card"><div class="profhead"><span class="code">E</span><b>No profile (everyone else)</b></div>
    <p>Shows the product default price: <b>${fmt(product.default_price)}</b></p>
    <p class="muted small">Edit it on the Products tab.</p></div>`;
  $('#ruleList').innerHTML = defaultCard + PROFILES.map(p => {
    const r = byProfile[p.id];
    return `<div class="card" data-rule="${p.id}">
      <div class="profhead"><span class="swatch" style="background:${esc(p.color_hex)}"></span><span class="code">${esc(p.code)}</span><b>${esc(p.name)}</b></div>
      ${r ? '' : '<p class="muted small">No rule yet — this customer sees the default price.</p>'}
      <div class="form">
        <label>Base price ($) <input data-f="base_price" type="number" step="0.01" value="${r && r.base_price != null ? r.base_price : ''}" placeholder="${product.default_price}"></label>
        <label>Shown (fake) discount % <input data-f="shown_discount_pct" type="number" step="1" value="${r ? r.shown_discount_pct : 0}"></label>
        <label class="wide">Badge on tag <input data-f="badge" value="${esc(r ? r.badge : '')}" placeholder="e.g. EMPLOYEE PRICE -20%"></label>
      </div>
      <div class="small muted">Markups (compounded)</div>
      <div data-markups></div>
      <button data-addm class="small">+ markup</button>
      <div class="result" data-result></div>
      <div class="btnrow" style="margin-top:8px"><button class="primary" data-save>Save — live now</button>${r ? '<button class="danger" data-del>Remove rule</button>' : ''}</div>
    </div>`;
  }).join('');

  $$('[data-rule]').forEach(card => {
    const profId = Number(card.dataset.rule);
    const r = byProfile[profId] || { markups: [] };
    const mbox = $('[data-markups]', card);
    const addRow = (m = { label: '', percent: 0 }) => {
      const row = document.createElement('div'); row.className = 'markup';
      row.innerHTML = `<input data-ml placeholder="reason" value="${esc(m.label)}"><input data-mp type="number" step="1" value="${m.percent}"><button title="remove">✕</button>`;
      $('button', row).onclick = () => { row.remove(); update(); };
      $$('input', row).forEach(i => i.oninput = update);
      mbox.appendChild(row);
    };
    const read = () => ({
      base_price: $('[data-f=base_price]', card).value,
      shown_discount_pct: $('[data-f=shown_discount_pct]', card).value,
      badge: $('[data-f=badge]', card).value,
      markups: $$('.markup', mbox).map(row => ({ label: $('[data-ml]', row).value, percent: Number($('[data-mp]', row).value || 0) })),
    });
    const update = () => {
      const c = calc(product, read());
      $('[data-result]', card).innerHTML = `Real price: <b>${fmt(c.price)}</b>` +
        (c.was ? ` · tag shows <s>${fmt(c.was)}</s> → ${fmt(c.price)}` : '') +
        `<div class="muted small">${esc(c.steps.join(' → '))}</div>`;
    };
    r.markups.forEach(addRow);
    $$('[data-f]', card).forEach(i => i.oninput = update);
    $('[data-addm]', card).onclick = () => { addRow(); update(); };
    $('[data-save]', card).onclick = async () => { await api(`/api/rules/${pid}/${profId}`, { method: 'PUT', body: read() }); toast('Price is live'); renderRules(); };
    const del = $('[data-del]', card);
    if (del) del.onclick = async () => { await api(`/api/rules/${pid}/${profId}`, { method: 'DELETE' }); renderRules(); };
    update();
  });
}

// ── SETTINGS ────────────────────────────────────────────────────────────
function renderSettings() {
  $$('#settingsForm [name]').forEach(i => { if (SETTINGS[i.name] !== undefined) i.value = SETTINGS[i.name]; });
}
$('#saveSettings').onclick = async () => {
  const body = Object.fromEntries($$('#settingsForm [name]').map(i => [i.name, i.value]));
  SETTINGS = await api('/api/settings', { method: 'PUT', body }); toast('Settings saved');
};
$('#ovFind').onclick = async () => {
  const out = $('#ovResult'); out.textContent = 'Asking camera…';
  try {
    const r = await api('/api/onvif/discover', { method: 'POST', body: { host: $('#ovHost').value.trim(), port: $('#ovPort').value, user: $('#ovUser').value, password: $('#ovPass').value } });
    if (!r.streams.length) { out.textContent = 'Camera answered but listed no streams.'; return; }
    out.innerHTML = r.streams.map((s, i) => `<div class="kv"><span>${esc(s.name)} ${s.width ? `(${s.width}×${s.height})` : ''}${i === 0 ? ' — recommended' : ''}</span><button data-uri="${esc(s.uri)}">Use</button></div>`).join('');
    $$('[data-uri]', out).forEach(b => b.onclick = () => { $('#settingsForm [name=camera_url]').value = b.dataset.uri; toast('URL filled in — press Save settings'); });
  } catch (e) { out.textContent = 'Failed: ' + e.message + ' (try port 8899, 8000 or 80)'; }
};

loadAll().then(() => { pollLive(); setInterval(pollLive, 700); setInterval(pollPreview, 700); });
