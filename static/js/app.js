// ── State ──────────────────────────────────────────────────────────────────
const state = {
  bets: [],
  parlays: [],
  activeSport: 'ALL',
  scanInterval: null,
};

// ── API helpers ────────────────────────────────────────────────────────────
async function api(path, opts = {}) {
  const r = await fetch('/api' + path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

// ── Page routing ───────────────────────────────────────────────────────────
function showPage(name) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('page-' + name).classList.add('active');
  document.querySelector(`[data-page="${name}"]`).classList.add('active');
  document.getElementById('pageTitle').textContent =
    { dashboard: 'Dashboard', bets: '+EV Bets', parlays: 'Parlay Slips', builder: 'Parlay Builder' }[name];

  if (name === 'bets') renderBets();
  if (name === 'parlays') renderParlays();
}

document.querySelectorAll('.nav-item').forEach(el => {
  el.addEventListener('click', () => showPage(el.dataset.page));
});

// ── Sport filter ───────────────────────────────────────────────────────────
document.querySelectorAll('.sport-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.sport-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    state.activeSport = btn.dataset.sport;
    renderBets();
    renderDashBets();
  });
});

// ── Status polling ─────────────────────────────────────────────────────────
async function pollStatus() {
  try {
    const s = await api('/status');

    // API key dots
    document.getElementById('dotAnthropic').className = 'dot' + (s.has_anthropic_key ? ' active' : '');
    document.getElementById('dotOdds').className = 'dot' + (s.has_odds_key ? ' active' : '');

    if (s.last_scan) {
      const d = new Date(s.last_scan);
      document.getElementById('lastScan').textContent =
        `Last scan: ${d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}`;
    }

    if (s.scan_running) {
      document.getElementById('overlayProgress').textContent = s.scan_progress || 'Scanning...';
      document.getElementById('scanOverlay').style.display = 'flex';
    } else {
      document.getElementById('scanOverlay').style.display = 'none';
      if (s.ev_bets_count > 0 || s.parlays_count > 0) {
        await loadData();
      }
    }
  } catch (e) { /* silently ignore */ }
}

async function loadData() {
  try {
    const [betsResp, parlayResp] = await Promise.all([
      api('/bets'),
      api('/parlays'),
    ]);
    state.bets = betsResp.bets;
    state.parlays = parlayResp.parlays;
    renderAll();
  } catch (e) { console.error(e); }
}

// ── Render all ─────────────────────────────────────────────────────────────
function renderAll() {
  renderStatCards();
  renderDashBets();
  renderDashParlay();
  renderBets();
  renderParlays();
}

// ── Stat cards ─────────────────────────────────────────────────────────────
function renderStatCards() {
  const bets = state.bets;
  const parlays = state.parlays;

  document.getElementById('evBetsCount').textContent = bets.length;
  document.getElementById('parlaysCount').textContent = parlays.length;

  const topEv = bets.length ? Math.max(...bets.map(b => b.ev_pct)) : null;
  document.getElementById('topEv').textContent = topEv !== null ? `+${topEv.toFixed(1)}%` : '—';

  const topGrade = parlays.length ? parlays[0].grade : '—';
  document.getElementById('topGrade').textContent = topGrade;
}

// ── Dashboard bets list ────────────────────────────────────────────────────
function renderDashBets() {
  const container = document.getElementById('dashBetsList');
  let bets = state.bets;
  if (state.activeSport !== 'ALL') bets = bets.filter(b => b.sport === state.activeSport);
  bets = bets.slice(0, 6);

  if (!bets.length) {
    container.innerHTML = `<div class="empty-state"><i class="fa fa-search"></i><p>Click "Scan Now" or "Demo"</p></div>`;
    return;
  }
  container.innerHTML = bets.map((b, i) => `
    <div class="dash-bet-row" onclick="showAiModal(${i})">
      <div class="dash-bet-left">
        <span class="dash-bet-desc">${b.description}</span>
        <span class="dash-bet-meta">${b.teams} • ${b.sport}</span>
      </div>
      <div class="dash-bet-right">
        <span class="odds-tag ${b.best_odds > 0 ? 'odds-pos' : 'odds-neg'}">${b.best_odds > 0 ? '+' : ''}${b.best_odds}</span>
        <span class="ev-tag">+${b.ev_pct}%</span>
        <span class="grade grade-${b.grade.replace('+','p')}">${b.grade}</span>
      </div>
    </div>`).join('');
}

// ── Dashboard parlay ───────────────────────────────────────────────────────
function renderDashParlay() {
  const container = document.getElementById('dashParlay');
  if (!state.parlays.length) {
    container.innerHTML = `<div class="empty-state"><i class="fa fa-layer-group"></i><p>Run a scan to see parlays</p></div>`;
    return;
  }
  const p = state.parlays[0];
  const gradeColor = ['A+','A'].includes(p.grade) ? 'green' : ['B+','B'].includes(p.grade) ? 'yellow' : 'gray';
  container.innerHTML = `
    <div style="padding:16px 20px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
        <span class="parlay-grade ${gradeColor}" style="font-size:2rem;font-weight:900">${p.grade}</span>
        <div style="text-align:right">
          <div style="font-size:1.3rem;font-weight:800;color:var(--green)">${p.combined_american > 0 ? '+' : ''}${p.combined_american}</div>
          <div style="font-size:0.75rem;color:var(--text3)">${p.combined_decimal.toFixed(2)}x payout</div>
        </div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:14px">
        ${metricMini('Win%', p.simulated_win_prob + '%', 'var(--blue)')}
        ${metricMini('EV', '+' + p.ev_pct + '%', 'var(--green)')}
        ${metricMini('Stake', p.recommended_stake_pct + '%', 'var(--yellow)')}
      </div>
      ${p.legs.map((l,i) => `
        <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--border)">
          <span style="width:20px;height:20px;border-radius:50%;background:var(--bg4);display:flex;align-items:center;justify-content:center;font-size:0.72rem;font-weight:700;flex-shrink:0">${i+1}</span>
          <span style="flex:1;font-size:0.83rem;font-weight:600">${l.description}</span>
          <span class="odds-tag ${l.best_odds > 0 ? 'odds-pos' : ''}">${l.best_odds > 0 ? '+' : ''}${l.best_odds}</span>
        </div>`).join('')}
      <button class="btn btn-ghost btn-full" style="margin-top:12px" onclick="showPage('parlays')">
        View Full Analysis →
      </button>
    </div>`;
}

function metricMini(label, val, color) {
  return `<div style="background:var(--bg3);border-radius:8px;padding:10px;text-align:center">
    <div style="font-size:1rem;font-weight:800;color:${color}">${val}</div>
    <div style="font-size:0.7rem;color:var(--text3)">${label}</div>
  </div>`;
}

// ── Bets table ─────────────────────────────────────────────────────────────
function renderBets() {
  const tbody = document.getElementById('betsTableBody');
  let bets = [...state.bets];

  const sport = document.getElementById('betSportFilter')?.value;
  const grade = document.getElementById('betGradeFilter')?.value;
  const minEv = parseFloat(document.getElementById('minEvFilter')?.value) || 0;

  if (sport) bets = bets.filter(b => b.sport === sport);
  if (minEv) bets = bets.filter(b => b.ev_pct >= minEv);

  const gradeOrder = {'A+':6,'A':5,'B+':4,'B':3,'C':2,'D':1,'F':0};
  if (grade) {
    const threshold = gradeOrder[grade] || 0;
    bets = bets.filter(b => (gradeOrder[b.grade] || 0) >= threshold);
  }

  if (state.activeSport !== 'ALL') bets = bets.filter(b => b.sport === state.activeSport);

  if (!bets.length) {
    tbody.innerHTML = `<tr><td colspan="11" class="empty-row">No +EV bets found — run a scan or try demo</td></tr>`;
    return;
  }

  tbody.innerHTML = bets.map((b, i) => `
    <tr>
      <td><span class="sport-badge sport-${b.sport}">${b.sport}</span></td>
      <td class="bold">${b.description}</td>
      <td style="color:var(--text2);max-width:160px;overflow:hidden;text-overflow:ellipsis">${b.teams}</td>
      <td><span class="odds-tag ${b.best_odds > 0 ? 'odds-pos' : ''}">${b.best_odds > 0 ? '+' : ''}${b.best_odds}</span></td>
      <td class="bold">${b.model_prob}%</td>
      <td style="color:var(--text2)">${b.implied_prob}%</td>
      <td class="bold text-green">+${b.ev_pct}%</td>
      <td><span class="grade grade-${b.grade.replace('+','p')}">${b.grade}</span></td>
      <td style="color:${b.confidence==='high'?'var(--green)':b.confidence==='medium'?'var(--yellow)':'var(--text2)'}">${b.confidence}</td>
      <td>${b.sharp_money ? '<span class="sharp-tag" title="Sharp money detected">⚡</span>' : '—'}</td>
      <td>
        <button class="btn btn-sm btn-ghost" onclick="showAiModal(${i})">
          <i class="fa fa-robot"></i> AI
        </button>
      </td>
    </tr>`).join('');
}

// ── Parlays ────────────────────────────────────────────────────────────────
function renderParlays() {
  const container = document.getElementById('parlaysList');
  if (!state.parlays.length) {
    container.innerHTML = `<div class="empty-state card"><i class="fa fa-layer-group"></i><p>No parlays yet — run a scan</p></div>`;
    return;
  }

  container.innerHTML = state.parlays.map((p, pi) => {
    const gradeColor = ['A+','A'].includes(p.grade) ? 'green' : ['B+','B'].includes(p.grade) ? 'yellow' : 'gray';
    const cardGrade = ['A+','A'].includes(p.grade) ? 'a' : ['B+','B'].includes(p.grade) ? 'b' : 'c';
    const verdictClass = p.ai_confidence ?
      (p.ai_confidence === 'high' ? 'STRONG' : p.ai_confidence === 'medium' ? 'MODERATE' : 'PASS') : 'UNAVAILABLE';

    return `
    <div class="parlay-card grade-${cardGrade}">
      <div class="parlay-header">
        <div class="parlay-grade-badge">
          <span class="parlay-grade ${gradeColor}">${p.grade}</span>
          <div>
            <div style="font-weight:700">Parlay #${pi + 1} &nbsp;<span style="color:var(--text3);font-size:0.78rem">Score ${p.composite_score}/100</span></div>
            <div style="font-size:0.75rem;color:var(--text3)">${p.legs.length} legs · ${p.legs.map(l => l.sport).join(' + ')}</div>
          </div>
        </div>
        <div style="display:flex;gap:10px;align-items:center">
          ${p.ai_confidence ? `<span class="ai-verdict verdict-${verdictClass}">${verdictClass} PLAY</span>` : ''}
          <div style="text-align:right">
            <div style="font-size:1.4rem;font-weight:900;color:var(--green)">${p.combined_american > 0 ? '+' : ''}${p.combined_american}</div>
            <div style="font-size:0.72rem;color:var(--text3)">${p.combined_decimal.toFixed(2)}x return</div>
          </div>
        </div>
      </div>

      <div class="parlay-stats">
        ${parlayStatBox('Win Prob', p.simulated_win_prob + '%', 'var(--blue)')}
        ${parlayStatBox('EV%', '+' + p.ev_pct + '%', 'var(--green)')}
        ${parlayStatBox('Correlation', p.correlation_score, p.correlation_score < 0.2 ? 'var(--green)' : p.correlation_score < 0.4 ? 'var(--yellow)' : 'var(--red)')}
        ${parlayStatBox('Stake', p.recommended_stake_pct + '%', 'var(--yellow)')}
        ${parlayStatBox('$Stake', '$' + p.recommended_stake_dollars.toFixed(0), 'var(--text)')}
      </div>

      <div class="parlay-legs">
        ${p.legs.map((l, i) => `
          <div class="parlay-leg">
            <span class="leg-num">${i+1}</span>
            <div class="leg-info">
              <div class="leg-desc">${l.description}</div>
              <div class="leg-teams">${l.teams}</div>
            </div>
            <div class="leg-meta">
              <span class="sport-badge sport-${l.sport}">${l.sport}</span>
              <span class="odds-tag ${l.best_odds > 0 ? 'odds-pos' : ''}">${l.best_odds > 0 ? '+' : ''}${l.best_odds}</span>
              <span style="color:var(--text2);font-size:0.82rem">${l.model_prob}%</span>
              <span class="ev-tag">+${l.ev_pct}%</span>
              <span class="grade grade-${l.grade.replace('+','p')}">${l.grade}</span>
            </div>
          </div>`).join('')}
      </div>

      <div class="parlay-footer">
        <div class="stake-info">
          <span class="stake-label">Recommended Stake (Fractional Kelly)</span>
          <span class="stake-val">${p.recommended_stake_pct}% of bankroll = $${p.recommended_stake_dollars.toFixed(2)}</span>
        </div>
        <div style="display:flex;gap:8px">
          ${p.ai_analysis ? `<button class="btn btn-sm btn-ghost" onclick="showParlayAiModal(${pi})"><i class="fa fa-robot"></i> Full AI Analysis</button>` : ''}
        </div>
      </div>

      ${p.ai_analysis ? `
      <div class="ai-section">
        <div class="ai-header"><i class="fa fa-robot"></i> Claude AI Synopsis</div>
        <div class="ai-text">${p.ai_analysis.slice(0, 500)}${p.ai_analysis.length > 500 ? '...' : ''}</div>
      </div>` : ''}
    </div>`;
  }).join('');
}

function parlayStatBox(label, val, color) {
  return `<div class="parlay-stat">
    <span class="parlay-stat-val" style="color:${color}">${val}</span>
    <span class="parlay-stat-lbl">${label}</span>
  </div>`;
}

// ── Scan button ────────────────────────────────────────────────────────────
document.getElementById('scanBtn').addEventListener('click', async () => {
  const sports = [...document.querySelectorAll('.sport-check:checked')].map(c => c.value);
  if (!sports.length) return alert('Select at least one sport.');

  const req = {
    sports,
    bankroll: parseFloat(document.getElementById('bankrollInput').value) || 1000,
    legs: parseInt(document.getElementById('legsSelect').value) || 3,
    ai_analysis: document.getElementById('aiToggle').checked,
  };

  document.getElementById('scanOverlay').style.display = 'flex';
  document.getElementById('overlayProgress').textContent = 'Starting scan...';

  try {
    await api('/scan', { method: 'POST', body: JSON.stringify(req) });
    // Poll until done
    if (state.scanInterval) clearInterval(state.scanInterval);
    state.scanInterval = setInterval(pollStatus, 1500);
  } catch (e) {
    document.getElementById('scanOverlay').style.display = 'none';
    alert('Scan error: ' + e.message);
  }
});

// ── Demo button ────────────────────────────────────────────────────────────
document.getElementById('demoBtn').addEventListener('click', async () => {
  document.getElementById('scanOverlay').style.display = 'flex';
  document.getElementById('overlayProgress').textContent = 'Loading demo data...';
  try {
    await api('/demo');
    await loadData();
  } catch (e) {
    alert('Demo error: ' + e.message);
  } finally {
    document.getElementById('scanOverlay').style.display = 'none';
  }
});

// ── AI Modal ───────────────────────────────────────────────────────────────
async function showAiModal(betIndex) {
  document.getElementById('aiModal').style.display = 'flex';
  document.getElementById('aiModalBody').textContent = 'Asking Claude AI...';
  try {
    const r = await api(`/analyze/${betIndex}`, { method: 'POST' });
    document.getElementById('aiModalBody').textContent = r.analysis || 'No analysis returned.';
  } catch (e) {
    document.getElementById('aiModalBody').textContent = 'Error: ' + e.message;
  }
}

function showParlayAiModal(pi) {
  const p = state.parlays[pi];
  if (!p?.ai_analysis) return;
  document.getElementById('aiModal').style.display = 'flex';
  document.getElementById('aiModalBody').textContent = p.ai_analysis;
}

function closeModal() {
  document.getElementById('aiModal').style.display = 'none';
}

// ── Custom Builder ─────────────────────────────────────────────────────────
let legCount = 0;

function addBuilderLeg() {
  legCount++;
  const id = legCount;
  const container = document.getElementById('customLegs');
  const div = document.createElement('div');
  div.className = 'builder-leg';
  div.id = `builder-leg-${id}`;
  div.innerHTML = `
    <div class="builder-leg-header">
      <span class="builder-leg-title">LEG ${id}</span>
      <button class="btn-remove" onclick="removeBuilderLeg(${id})"><i class="fa fa-times"></i></button>
    </div>
    <div class="builder-leg-grid">
      <div>
        <label>Sport</label>
        <select class="input bl-sport" data-id="${id}">
          <option>NBA</option><option>NFL</option><option>MLB</option><option>NHL</option>
        </select>
      </div>
      <div>
        <label>Description</label>
        <input class="input bl-desc" data-id="${id}" placeholder="e.g. Lakers ML" />
      </div>
      <div>
        <label>Odds (American)</label>
        <input class="input bl-odds" data-id="${id}" type="number" placeholder="-110" value="-110" />
      </div>
      <div>
        <label>Teams</label>
        <input class="input bl-teams" data-id="${id}" placeholder="Team A vs Team B" />
      </div>
      <div>
        <label>Your Model Prob%</label>
        <input class="input bl-prob" data-id="${id}" type="number" placeholder="55" min="1" max="99" value="55" />
      </div>
      <div>
        <label>Bet Type</label>
        <select class="input bl-type" data-id="${id}">
          <option value="moneyline">Moneyline</option>
          <option value="spread">Spread</option>
          <option value="total">Total</option>
          <option value="prop">Player Prop</option>
        </select>
      </div>
    </div>`;
  container.appendChild(div);
}

function removeBuilderLeg(id) {
  document.getElementById(`builder-leg-${id}`)?.remove();
}

async function submitCustomParlay() {
  const legs = [];
  document.querySelectorAll('.builder-leg').forEach(div => {
    const id = div.id.replace('builder-leg-', '');
    legs.push({
      sport: div.querySelector('.bl-sport').value,
      description: div.querySelector('.bl-desc').value || 'Custom Bet',
      odds: parseInt(div.querySelector('.bl-odds').value) || -110,
      model_prob: parseFloat(div.querySelector('.bl-prob').value) || 55,
      bet_type: div.querySelector('.bl-type').value,
      teams: div.querySelector('.bl-teams').value || '',
      game_id: 'custom_' + id,
    });
  });

  if (legs.length < 2) return alert('Add at least 2 legs to build a parlay.');

  const result = document.getElementById('builderResult');
  result.innerHTML = `<div class="empty-state"><div class="spinner"></div><p>Analyzing...</p></div>`;

  try {
    const p = await api('/parlay/custom', {
      method: 'POST',
      body: JSON.stringify({
        legs,
        bankroll: parseFloat(document.getElementById('builderBankroll').value) || 1000,
        ai_analysis: document.getElementById('builderAiToggle').checked,
      }),
    });

    const gradeColor = ['A+','A'].includes(p.grade) ? 'var(--green)' : ['B+','B'].includes(p.grade) ? 'var(--yellow)' : 'var(--text2)';

    result.innerHTML = `
      <div style="border-bottom:1px solid var(--border);padding:16px 20px;display:flex;align-items:center;justify-content:space-between">
        <span style="font-size:2rem;font-weight:900;color:${gradeColor}">${p.grade}</span>
        <div style="text-align:right">
          <div style="font-size:1.5rem;font-weight:800;color:var(--green)">${p.combined_american > 0 ? '+' : ''}${p.combined_american}</div>
          <div style="font-size:0.75rem;color:var(--text3)">${p.combined_decimal.toFixed(2)}x</div>
        </div>
      </div>
      ${metric('Simulated Win Prob', p.simulated_win_prob + '%', 'var(--blue)')}
      ${metric('Expected Value', '+' + p.ev_pct + '%', 'var(--green)')}
      ${metric('Correlation Score', p.correlation_score, p.correlation_score < 0.2 ? 'var(--green)' : 'var(--yellow)')}
      ${metric('Recommended Stake', p.recommended_stake_pct + '% ($' + p.recommended_stake_dollars.toFixed(0) + ')', 'var(--yellow)')}
      ${p.ai_analysis ? `
      <div style="padding:16px 20px;border-top:1px solid var(--border)">
        <div class="ai-header"><i class="fa fa-robot"></i> Claude AI Verdict</div>
        <div class="ai-text" style="max-height:200px;overflow-y:auto">${p.ai_analysis}</div>
      </div>` : ''}`;
  } catch (e) {
    result.innerHTML = `<div class="empty-state"><p style="color:var(--red)">Error: ${e.message}</p></div>`;
  }
}

function metric(label, val, color) {
  return `<div class="result-metric">
    <span class="result-metric-label">${label}</span>
    <span class="result-metric-val" style="color:${color}">${val}</span>
  </div>`;
}

// ── Init ───────────────────────────────────────────────────────────────────
(async function init() {
  await pollStatus();
  setInterval(pollStatus, 3000);

  // Add first builder leg by default
  addBuilderLeg();
  addBuilderLeg();
  addBuilderLeg();
})();
