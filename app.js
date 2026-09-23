/* Auto Rate Explorer - frontend.
 *
 * The premium shown anywhere in this UI comes from POST /api/quote, which is
 * pure arithmetic on the server. The LLM touches two things only: the vehicle
 * parse box, and the prose in "Why this rate". Both are labeled as such.
 */
'use strict';

const $ = (id) => document.getElementById(id);
const fmt = (n) => '$' + Math.round(n).toLocaleString('en-US');

/* Sequential blue ramp, light -> dark. Premium is continuous magnitude, so one
 * hue; never a rainbow. Steps 100-700 of the reference palette. */
const RAMP = ['#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95', '#0d366b'];

let map, layer, state = { quote: null, breaks: [], selectedFips: null };

/* ---------------------------------------------------------------- map ---- */

function initMap() {
  map = L.map('map', { zoomControl: true, scrollWheelZoom: true }).setView([40.06, -74.4], 8);
  /* Keyless OSM tiles. Desaturated in CSS (.basemap) so the sequential blue
     choropleth stays the figure and the basemap stays ground. */
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 19, className: 'basemap',
  }).addTo(map);
  $('ramp').innerHTML = RAMP.map((c) => `<i style="background:${c}"></i>`).join('');
}

/** Quantile breaks: with ~20-60 counties this spreads color evenly instead of
 *  letting one dense outlier flatten everything else into the lightest step. */
function quantileBreaks(values, n) {
  const s = [...values].sort((a, b) => a - b);
  const out = [];
  for (let i = 1; i < n; i++) out.push(s[Math.floor((i / n) * s.length)]);
  return out;
}

const colorFor = (v) => {
  let i = 0;
  while (i < state.breaks.length && v >= state.breaks[i]) i++;
  return RAMP[Math.min(i, RAMP.length - 1)];
};

const baseStyle = (f) => {
  const q = state.quote.byFips[f.properties.fips];
  const isSubject = f.properties.fips === state.selectedFips;
  return {
    fillColor: q ? colorFor(q.annual_premium) : '#cccccc',
    fillOpacity: 0.82,
    color: isSubject ? '#0b0b0b' : '#ffffff',   /* 2px surface gap between fills */
    weight: isSubject ? 2.5 : 1,
    opacity: 1,
  };
};

function drawChoropleth(geo, quote) {
  if (layer) layer.remove();
  const premiums = quote.counties.map((c) => c.annual_premium);
  state.breaks = quantileBreaks(premiums, RAMP.length);

  layer = L.geoJSON(geo, {
    style: baseStyle,
    onEachFeature: (feat, lyr) => {
      const q = quote.byFips[feat.properties.fips];
      if (!q) return;
      const vs = quote.statewide.population_weighted_mean;
      const d = ((q.annual_premium / vs - 1) * 100);
      lyr.bindTooltip(
        `<b>${q.county}</b><br>
         <span class="p">${fmt(q.annual_premium)}</span> <span class="m">/ yr</span><br>
         <span class="m">${d >= 0 ? '+' : ''}${d.toFixed(1)}% vs state avg</span><br>
         <span class="m">${Math.round(q.density).toLocaleString()} people/sq mi &middot; territory &times;${q.territory_relativity.toFixed(2)}</span>`,
        { className: 'county-tip', sticky: true }
      );
      lyr.on({
        mouseover: (e) => { e.target.setStyle({ weight: 2.5, color: '#0b0b0b', fillOpacity: 0.93 }); e.target.bringToFront(); },
        mouseout:  (e) => layer.resetStyle(e.target),
        click:     () => selectCounty(feat.properties.fips),
      });
    },
  }).addTo(map);

  map.fitBounds(layer.getBounds(), { padding: [18, 18] });
  $('lg-lo').textContent = fmt(quote.statewide.min);
  $('lg-hi').textContent = fmt(quote.statewide.max);
}

/* ------------------------------------------------------------ rendering -- */

function renderHero(q) {
  const s = q.subject_county;
  if (!s) {
    $('hero').innerHTML = `<div class="muted" style="text-align:left">Rated ${q.statewide.county_count} counties in ${q.state_name}. Enter a city to see yours, or click the map.</div>`;
    return;
  }
  const up = s.vs_state_mean_pct >= 0;
  $('hero').innerHTML = `
    <div class="amt">${fmt(s.annual_premium)}<span>/yr</span></div>
    <div class="where">${s.county}, ${q.state}</div>
    <div class="delta">
      ${up ? '+' : ''}${s.vs_state_mean_pct}% vs the ${q.state} average of ${fmt(q.statewide.population_weighted_mean)}
      &middot; ${s.rank_in_state}<sup>${ord(s.rank_in_state)}</sup> priciest of ${q.statewide.county_count}
    </div>`;
}

const ord = (n) => (n % 100 >= 11 && n % 100 <= 13) ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' }[n % 10] || 'th');

function renderFactors(q) {
  const s = q.subject_county;
  const v = q.vehicle.components;
  const flag = (m) => m ? '' : ' <span class="assumed">assumed</span>';
  const rows = [
    [`${q.state} base rate`, 'statewide average, full coverage', fmt(q.state_base_rate)],
    [`Territory &mdash; ${s ? s.county : '—'}`,
     s ? `${Math.round(s.density).toLocaleString()} people/sq mi` : 'select a county',
     s ? '×' + s.territory_relativity.toFixed(3) : '—'],
    [`Vehicle &mdash; ${v.make_tier.name}`, 'manufacturer repair-cost tier' + flag(v.make_tier.matched), '×' + v.make_tier.factor.toFixed(3)],
    [`Body class &mdash; ${v.body_class.name.replace(/_/g, ' ')}`, 'liability &amp; damage propensity' + flag(v.body_class.matched), '×' + v.body_class.factor.toFixed(3)],
    [`Vehicle age &mdash; ${v.vehicle_age.years} yr`, 'replacement cost curve' + flag(v.vehicle_age.matched), '×' + v.vehicle_age.factor.toFixed(3)],
    [`Driver age &mdash; ${q.driver.age ?? 'n/a'}`, 'loss frequency by age' + flag(q.driver.matched), '×' + q.driver.factor.toFixed(3)],
    [`Coverage`, q.coverage.level.replace(/_/g, ' '), '×' + q.coverage.factor.toFixed(3)],
  ];
  let html = rows.map(([l, sub, f]) =>
    `<tr><td class="lbl">${l}<div class="sub">${sub}</div></td><td class="f">${f}</td></tr>`).join('');
  if (s) html += `<tr class="total"><td>Annual premium</td><td class="f">${fmt(s.annual_premium)}</td></tr>`;
  $('factors').innerHTML = html;
}

function renderStats(q) {
  const s = q.statewide;
  $('stats').innerHTML = `
    <div class="stat"><div class="k">Cheapest county</div><div class="v">${fmt(s.cheapest.premium)}</div><div class="n">${s.cheapest.county}</div></div>
    <div class="stat"><div class="k">Priciest county</div><div class="v">${fmt(s.priciest.premium)}</div><div class="n">${s.priciest.county}</div></div>
    <div class="stat"><div class="k">Spread</div><div class="v">${s.spread_pct}%</div><div class="n">priciest over cheapest</div></div>
    <div class="stat"><div class="k">State average</div><div class="v">${fmt(s.population_weighted_mean)}</div><div class="n">population-weighted</div></div>`;
}

/* -------------------------------------------------------------- actions -- */

async function api(path, opts) {
  const r = await fetch(path, opts);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(j.error || r.statusText), { payload: j, status: r.status });
  return j;
}

function selectCounty(fips) {
  const q = state.quote;
  if (!q) return;
  const c = q.byFips[fips];
  if (!c) return;
  const rank = q.counties.findIndex((x) => x.fips === fips) + 1;
  q.subject_county = Object.assign({}, c, {
    rank_in_state: rank,
    vs_state_mean_pct: +(((c.annual_premium / q.statewide.population_weighted_mean) - 1) * 100).toFixed(1),
  });
  state.selectedFips = fips;
  if (layer) layer.setStyle(baseStyle);
  renderHero(q); renderFactors(q);
  $('btn-explain').disabled = false;
  $('explain').className = 'muted';
  $('explain').textContent = 'Run "Explain this rate" for this county.';
}

async function runQuote() {
  const btn = $('btn-quote');
  btn.disabled = true; btn.textContent = 'Rating…';
  $('quote-err').innerHTML = '';
  try {
    const body = {
      city: $('city').value.trim() || null,
      state: $('state').value,
      year: $('year').value.trim() || null,
      make: $('make').value.trim() || null,
      model: $('model').value.trim() || null,
      driver_age: $('age').value.trim() || null,
      coverage: $('coverage').value,
    };
    const q = await api('/api/quote', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    q.byFips = Object.fromEntries(q.counties.map((c) => [c.fips, c]));
    // If the engine snapped a typo onto a known model ("Hurrican" -> "Huracan"), show it.
    const bc = q.vehicle && q.vehicle.components && q.vehicle.components.body_class;
    if (bc && bc.matched && bc.model && bc.model !== $('model').value.trim()) {
      $('model').value = bc.model;
    }
    state.quote = q;
    state.selectedFips = q.subject_county ? q.subject_county.fips : null;

    const geo = await api('/api/geojson/' + q.state);
    drawChoropleth(geo, q);
    renderHero(q); renderFactors(q); renderStats(q);

    syncUrl();
    $('btn-explain').disabled = !q.subject_county;
    $('explain').className = 'muted';
    $('explain').textContent = q.subject_county
      ? 'Ready — ask the model to explain this rate.'
      : 'Click a county on the map to select it.';
  } catch (e) {
    const p = e.payload || {};
    $('quote-err').innerHTML = p.error === 'ambiguous_city'
      ? `<span class="err">"${p.city}" exists in ${p.states.join(', ')} — pick the state.</span>`
      : `<span class="err">${p.error === 'unknown_city' ? `No city named "${p.city}" in that state.` : e.message}</span>`;
  } finally {
    btn.disabled = false; btn.textContent = 'Rate across the state';
  }
}

async function parseVehicle() {
  const text = $('veh').value.trim();
  if (!text) return;
  const btn = $('btn-parse');
  btn.disabled = true;
  $('parse-hint').innerHTML = '<span class="spinner"></span> local model parsing…';
  try {
    const r = await api('/api/parse', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (r.year) $('year').value = r.year;
    if (r.make) $('make').value = r.make;
    if (r.model) $('model').value = r.model;
    const via = r.source === 'llm' ? 'llama3.2 (local)' : 'regex fallback — model unavailable';
    $('parse-hint').innerHTML = `Parsed <b>${[r.year, r.make, r.model].filter(Boolean).join(' ') || '—'}</b> <span style="color:var(--text-3)">via ${via}</span>`;
  } catch (e) {
    $('parse-hint').innerHTML = `<span class="err">Parse failed: ${e.message}</span>`;
  } finally { btn.disabled = false; }
}

async function explain() {
  const q = state.quote;
  if (!q || !q.subject_county) return;
  const btn = $('btn-explain');
  btn.disabled = true; btn.textContent = 'Thinking…';
  $('explain').className = '';
  $('explain').innerHTML = '<div class="skel" style="width:96%"></div><div class="skel" style="width:88%"></div><div class="skel" style="width:70%"></div>';
  try {
    const r = await api('/api/explain', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        quote: q, year: $('year').value, make: $('make').value, model: $('model').value,
      }),
    });
    const llmUsed = r.source === 'llm';
    $('explain').innerHTML =
      `<div class="explain">${escapeHtml(r.text)}</div>
       <div class="src">
         <span class="dot ${llmUsed ? 'on' : 'off'}"></span>
         ${llmUsed ? 'Written by llama3.2:3b running locally' : 'Model unavailable — deterministic template'}
       </div>
       <details class="audit">
         <summary>Show the exact facts the model was given</summary>
         <pre>${escapeHtml(JSON.stringify(r.facts_sent, null, 2))}</pre>
         <div class="src">Every figure above was computed by the rating engine before the model saw it. The model was instructed to use only these numbers.</div>
       </details>`;
  } catch (e) {
    $('explain').innerHTML = `<span class="err">${e.message}</span>`;
  } finally { btn.disabled = false; btn.textContent = 'Explain this rate'; }
}

const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/* ---------------------------------------------------------- autocomplete -- */

function wireAutocomplete() {
  const box = $('ac'), input = $('city');
  let items = [], sel = -1, timer;

  const close = () => { box.hidden = true; sel = -1; };
  const choose = (i) => {
    const it = items[i]; if (!it) return;
    input.value = it.name;
    $('state').value = it.state;
    $('city-hint').innerHTML = `<b>${it.name}, ${it.state}</b> → ${it.county}`;
    close();
  };

  input.addEventListener('input', () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) return close();
    timer = setTimeout(async () => {
      try {
        items = await api(`/api/places/search?q=${encodeURIComponent(q)}&state=${$('state').value}`);
        if (!items.length) return close();
        box.innerHTML = items.map((it, i) =>
          `<div data-i="${i}">${it.name} <span class="c">${it.state} &middot; ${it.county}</span></div>`).join('');
        box.hidden = false;
      } catch { close(); }
    }, 130);
  });

  box.addEventListener('mousedown', (e) => {
    const d = e.target.closest('[data-i]'); if (d) { e.preventDefault(); choose(+d.dataset.i); }
  });
  input.addEventListener('keydown', (e) => {
    if (box.hidden) return;
    const n = box.children.length;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      sel = (sel + (e.key === 'ArrowDown' ? 1 : -1) + n) % n;
      [...box.children].forEach((c, i) => c.classList.toggle('sel', i === sel));
    } else if (e.key === 'Enter' && sel >= 0) { e.preventDefault(); choose(sel); }
    else if (e.key === 'Escape') close();
  });
  input.addEventListener('blur', () => setTimeout(close, 120));
}

/* ------------------------------------------------------------- deep link -- */

/** Reflect the current inputs in the URL so a quote can be shared or reloaded. */
function syncUrl() {
  const p = new URLSearchParams();
  const put = (k, v) => { if (v) p.set(k, v); };
  put('city', $('city').value.trim());
  put('state', $('state').value);
  put('year', $('year').value.trim());
  put('make', $('make').value.trim());
  put('model', $('model').value.trim());
  put('age', $('age').value.trim());
  if ($('coverage').value !== 'full_coverage') put('coverage', $('coverage').value);
  history.replaceState(null, '', p.toString() ? '?' + p : location.pathname);
}

/** Restore inputs from the URL. Returns true if enough was present to rate. */
function restoreFromUrl() {
  const p = new URLSearchParams(location.search);
  if (![...p.keys()].length) return false;
  const set = (k, id) => { if (p.get(k)) $(id).value = p.get(k); };
  set('state', 'state'); set('city', 'city'); set('year', 'year');
  set('make', 'make'); set('model', 'model'); set('age', 'age');
  set('coverage', 'coverage');
  return !!(p.get('state') || p.get('city'));
}

/* ------------------------------------------------------------------ init -- */

async function init() {
  initMap();
  wireAutocomplete();

  const states = await api('/api/states');
  $('state').innerHTML = states.map((s) =>
    `<option value="${s.code}"${s.code === 'NJ' ? ' selected' : ''}>${s.code} — ${s.name}</option>`).join('');

  try {
    const h = await api('/api/health');
    const up = h.llm.available;
    $('dot-llm').className = 'dot ' + (up ? 'on' : 'off');
    $('llm-label').textContent = up ? h.llm.model + ' local' : 'model offline — fallbacks active';
    $('data-label').textContent = `${h.counties.toLocaleString()} counties · ${h.places.toLocaleString()} places`;
  } catch { $('data-label').textContent = 'data unavailable'; }

  $('btn-quote').addEventListener('click', runQuote);
  $('btn-parse').addEventListener('click', parseVehicle);
  $('btn-explain').addEventListener('click', explain);
  $('veh').addEventListener('keydown', (e) => { if (e.key === 'Enter') parseVehicle(); });
  ['year', 'make', 'model', 'age'].forEach((id) =>
    $(id).addEventListener('keydown', (e) => { if (e.key === 'Enter') runQuote(); }));

  if (restoreFromUrl()) runQuote();
}

init();
