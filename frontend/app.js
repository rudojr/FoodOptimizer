/**
 * FoodOptimizer · frontend/app.js
 * Gọi API backend, render thực đơn tháng, xử lý Local Repair
 * Tuân thủ secure coding: không dùng innerHTML với dữ liệu untrusted,
 * sử dụng textContent và createElement để build DOM.
 */
'use strict';

// ─── Config ─────────────────────────────────────────────────────────────────
const API_BASE = '';   // Cùng origin với backend

const WEEKS = ['w1', 'w2', 'w3', 'w4'];
const DAYS  = ['mon', 'tue', 'wed', 'thu', 'fri'];
const SLOTS = ['M1', 'M2', 'R', 'C', 'CO', 'Q'];

const WEEK_LABELS = { w1: 'Tuần 1', w2: 'Tuần 2', w3: 'Tuần 3', w4: 'Tuần 4' };
const DAY_LABELS  = { mon: 'Thứ 2', tue: 'Thứ 3', wed: 'Thứ 4', thu: 'Thứ 5', fri: 'Thứ 6' };
const SLOT_LABELS = { M1: 'Món mặn 1', M2: 'Món mặn 2', R: 'Món rau', C: 'Món canh', CO: 'Cơm', Q: 'Quà chiều' };

// ─── App State ───────────────────────────────────────────────────────────────
const STATE = {
  menu:          null,   // MonthMenu { w1, w2, w3, w4 } từ API
  violations:    [],     // Violation[] (tất cả tuần, tagged với week)
  score:         0,
  solverInfo:    null,
  dataStats:     null,
  pendingRepair: null,   // { week, day, slot }
  activeWeek:    'w1',
};

// ─── DOM refs ────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

const el = {
  btnOptimize:    $('btn-optimize'),
  btnAutorepair:  $('btn-autorepair'),
  btnPrint:       $('btn-print'),
  menuBody:       $('menu-body'),
  constraintList: $('constraint-list'),
  scoreNum:       $('score-num'),
  scoreStatus:    $('score-status'),
  ringFg:         $('ring-fg'),
  solverBar:      $('solver-bar'),
  solverStatus:   $('solver-status'),
  solverScore:    $('solver-score'),
  solverTime:     $('solver-time'),
  statsBody:      $('stats-body'),
  dataDl:         $('data-dl'),
  modalBg:        $('modal-bg'),
  modalClose:     $('modal-close'),
  modalTitle:     $('modal-title'),
  modalCurWrap:   $('modal-current-wrap'),
  altLoading:     $('alt-loading'),
  altList:        $('alt-list'),
  toastArea:      $('toast-area'),
};

// ─── API Helpers ─────────────────────────────────────────────────────────────

async function apiPost(path, body) {
  const res = await fetch(API_BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ message: res.statusText }));
    throw new Error(err?.detail?.message || err?.message || `HTTP ${res.status}`);
  }
  return res.json();
}

async function apiGet(path) {
  const res = await fetch(API_BASE + path);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// ─── Settings ────────────────────────────────────────────────────────────────

function getSettings() {
  return {
    ruleSet: parseInt($('rule-set-select')?.value) || 1,
    nWeeks:  parseInt($('n-weeks-select')?.value)  || 4,
    timeout: parseFloat($('timeout-select')?.value) || 20,
  };
}

// ─── Optimize ────────────────────────────────────────────────────────────────

async function runOptimize() {
  const { ruleSet, nWeeks, timeout } = getSettings();

  el.btnOptimize.disabled = true;
  el.btnOptimize.textContent = '⚡ Đang giải...';
  showToast('Đang chạy CP-SAT solver...', 'info');

  try {
    const data = await apiPost('/api/optimize', {
      timeout_seconds:   timeout,
      allow_dish_repeat: true,
      rule_set:          ruleSet,
      n_weeks:           nWeeks,
    });

    STATE.menu       = data.menu;
    STATE.violations = data.violations;
    STATE.score      = data.score;
    STATE.solverInfo = { status: data.status, time_ms: data.solve_time_ms, stats: data.stats };
    STATE.activeWeek = 'w1';

    renderWeekTabs();
    renderMenu();
    renderConstraints();
    renderSolverBar();
    renderStats();

    const errCount = STATE.violations.filter(v => v.severity === 'error').length;
    el.btnAutorepair.disabled  = errCount === 0;
    el.btnAutorepair.textContent = '🔧 Sửa tuần này (MPP)';

    if (errCount === 0) {
      showToast(`✅ Tối ưu thành công! Score: ${data.score}`, 'ok');
    } else {
      showToast(`⚠️ ${errCount} vi phạm còn lại – thử "Sửa tuần này"`, 'warn');
    }
  } catch (e) {
    showToast('❌ Lỗi: ' + e.message, 'error');
  } finally {
    el.btnOptimize.disabled = false;
    el.btnOptimize.textContent = '⚡ Tối ưu hóa (COP)';
  }
}

// ─── Auto Repair ─────────────────────────────────────────────────────────────

async function runAutoRepair() {
  const weekKey = STATE.activeWeek;
  if (!STATE.menu?.[weekKey]) return;

  el.btnAutorepair.disabled = true;
  el.btnAutorepair.textContent = '🔧 Đang sửa...';
  showToast(`Đang sửa ${WEEK_LABELS[weekKey]}...`, 'info');

  try {
    const data = await apiPost('/api/auto-repair', {
      menu: STATE.menu[weekKey],
      max_iterations: 20,
    });

    // Cập nhật tuần đang sửa trong MonthMenu
    STATE.menu = { ...STATE.menu, [weekKey]: data.menu };

    // Re-validate tuần vừa sửa; giữ nguyên vi phạm của tuần khác
    const weekViolations = await validateWeek(weekKey);
    STATE.violations = [
      ...STATE.violations.filter(v => v.week !== weekKey),
      ...weekViolations,
    ];

    renderWeekTabs();
    renderMenu(data.changes);
    renderConstraints();
    renderStats();

    const fixed = data.violations_before - data.violations_after;
    const totalErr = STATE.violations.filter(v => v.severity === 'error').length;
    showToast(
      `🔧 ${WEEK_LABELS[weekKey]}: sửa ${fixed} vi phạm, ${data.changes.length} món đổi`,
      data.violations_after === 0 ? 'ok' : 'warn',
    );

    el.btnAutorepair.disabled = totalErr === 0;
  } catch (e) {
    showToast('❌ Lỗi: ' + e.message, 'error');
  } finally {
    el.btnAutorepair.textContent = '🔧 Sửa tuần này (MPP)';
  }
}

// ─── Validate ────────────────────────────────────────────────────────────────

async function validateWeek(weekKey) {
  const weekMenu = STATE.menu?.[weekKey];
  if (!weekMenu) return [];
  try {
    const data = await apiPost('/api/validate', { menu: weekMenu });
    return (data.violations || []).map(v => ({ ...v, week: weekKey }));
  } catch { return []; }
}

// ─── Repair (single slot) ────────────────────────────────────────────────────

async function openRepairModal(week, day, slot) {
  STATE.pendingRepair = { week, day, slot };

  el.modalTitle.textContent =
    `Thay thế: ${SLOT_LABELS[slot]} · ${DAY_LABELS[day]} · ${WEEK_LABELS[week]}`;
  el.modalBg.hidden = false;
  document.body.style.overflow = 'hidden';

  const weekMenu = STATE.menu?.[week];
  const cur = weekMenu?.[day]?.[slot];
  el.modalCurWrap.replaceChildren();
  if (cur) el.modalCurWrap.appendChild(buildCurrentCard(cur, slot));

  el.altList.replaceChildren();
  el.altLoading.hidden = false;

  try {
    const data = await apiPost('/api/repair', { menu: weekMenu, day, slot });
    el.altLoading.hidden = true;
    renderAlternatives(data.alternatives, week, day, slot);
  } catch (e) {
    el.altLoading.hidden = true;
    const p = document.createElement('p');
    p.className = 'no-alt';
    p.textContent = 'Không tải được danh sách thay thế: ' + e.message;
    el.altList.appendChild(p);
  }
}

function closeModal() {
  el.modalBg.hidden = true;
  document.body.style.overflow = '';
  STATE.pendingRepair = null;
}

async function applyAlternative(altDish, week, day, slot) {
  closeModal();

  // Cập nhật state
  if (!STATE.menu[week]) STATE.menu[week] = {};
  if (!STATE.menu[week][day]) STATE.menu[week][day] = {};
  STATE.menu[week][day][slot] = altDish;

  // Re-validate tuần bị ảnh hưởng; giữ vi phạm tuần khác + vi phạm tháng (week=null)
  const weekViolations = await validateWeek(week);
  STATE.violations = [
    ...STATE.violations.filter(v => v.week !== week),
    ...weekViolations,
  ];

  renderWeekTabs();
  renderMenu();
  renderConstraints();
  renderStats();

  const totalErr = STATE.violations.filter(v => v.severity === 'error').length;
  el.btnAutorepair.disabled = totalErr === 0;
  showToast(`✅ Đã thay thế: ${altDish.name}`, 'ok');
}

// ─── Render Week Tabs ─────────────────────────────────────────────────────────

function renderWeekTabs() {
  WEEKS.forEach(wk => {
    const btn = document.querySelector(`.week-tab[data-week="${wk}"]`);
    if (!btn) return;

    const hasData = !!(STATE.menu?.[wk]);
    btn.disabled = !hasData;
    btn.classList.toggle('active', wk === STATE.activeWeek);

    // Badge lỗi
    btn.querySelector('.week-tab-badge')?.remove();
    if (hasData) {
      const errCount = STATE.violations.filter(
        v => v.week === wk && v.severity === 'error'
      ).length;
      if (errCount > 0) {
        const badge = document.createElement('span');
        badge.className = 'week-tab-badge';
        badge.textContent = String(errCount);
        btn.appendChild(badge);
      }
    }
  });
}

function switchWeek(weekKey) {
  STATE.activeWeek = weekKey;
  renderWeekTabs();
  renderMenu();
}

// ─── Render Menu ─────────────────────────────────────────────────────────────

function renderMenu(changes = []) {
  if (!STATE.menu) return;

  const weekMenu = STATE.menu[STATE.activeWeek];
  if (!weekMenu) return;

  // Vi phạm của tuần đang hiển thị
  const violatedSet = new Set();
  STATE.violations
    .filter(v => !v.week || v.week === STATE.activeWeek)
    .forEach(v => {
      if (v.day && v.slot) violatedSet.add(`${v.day}:${v.slot}`);
    });

  const changedSet = new Set(changes.map(c => `${c.day}:${c.slot}`));
  const tbody = el.menuBody;
  tbody.replaceChildren();

  SLOTS.forEach(slot => {
    const tr = document.createElement('tr');

    // Cột nhãn loại món
    const tdCat = document.createElement('td');
    tdCat.className = 'td-cat';
    tdCat.setAttribute('scope', 'row');
    const bar = document.createElement('span');
    bar.className = 'cat-bar';
    bar.style.background = getCatColor(slot);
    bar.setAttribute('aria-hidden', 'true');
    tdCat.appendChild(bar);
    tdCat.appendChild(document.createTextNode(SLOT_LABELS[slot]));
    tr.appendChild(tdCat);

    // Cột từng ngày
    DAYS.forEach(day => {
      const td = document.createElement('td');
      td.className = 'td-dish';

      const dish = weekMenu[day]?.[slot];
      if (dish) {
        const key = `${day}:${slot}`;
        const card = buildDishCard(dish, slot, day,
          violatedSet.has(key), changedSet.has(key));
        td.appendChild(card);
      } else {
        const empty = document.createElement('div');
        empty.className = 'dish-card ' + slot;
        empty.style.opacity = '.3';
        const n = document.createElement('span');
        n.className = 'dish-name';
        n.textContent = '—';
        empty.appendChild(n);
        td.appendChild(empty);
      }

      tr.appendChild(td);
    });

    tbody.appendChild(tr);
  });
}

function buildDishCard(dish, slot, day, violated, changed) {
  const card = document.createElement('div');
  card.className = `dish-card ${slot}${violated ? ' violated' : ''} pop`;
  card.setAttribute('role', 'button');
  card.setAttribute('tabindex', '0');
  card.setAttribute('aria-label', `${dish.name} – nhấn để thay thế`);
  if (changed) card.style.outline = '2px solid rgba(108,99,255,.6)';

  const hint = document.createElement('span');
  hint.className = 'edit-hint';
  hint.setAttribute('aria-hidden', 'true');
  hint.textContent = '✏️';
  card.appendChild(hint);

  const nameEl = document.createElement('span');
  nameEl.className = 'dish-name';
  nameEl.textContent = dish.name;   // safe: textContent
  card.appendChild(nameEl);

  const tagsEl = buildTags(dish, violated);
  card.appendChild(tagsEl);

  const onClick = () => openRepairModal(STATE.activeWeek, day, slot);
  card.addEventListener('click', onClick);
  card.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') onClick(); });

  return card;
}

function buildTags(dish, violated) {
  const wrap = document.createElement('div');
  wrap.className = 'dish-tags';

  const addTag = (text, cls) => {
    const t = document.createElement('span');
    t.className = `tag ${cls}`;
    t.textContent = text;
    wrap.appendChild(t);
  };

  if (dish.is_fried)   addTag('Chiên', 'tag-fried');
  if (dish.is_vien)    addTag('Viên',  'tag-vien');
  if (dish.has_fish)   addTag('Cá',    'tag-fish');
  if (dish.has_shrimp) addTag('Tôm',   'tag-shrimp');
  if (dish.has_egg)    addTag('Trứng', 'tag-egg');
  if (dish.has_beef)   addTag('Bò',    'tag-beef');
  if (dish.has_milk)   addTag('Sữa',   'tag-milk');
  if (dish.preferred)  addTag('★',     'tag-pref');
  if (violated)        addTag('⚠',     'tag-err');

  return wrap;
}

// ─── Render Constraints ───────────────────────────────────────────────────────

function renderConstraints() {
  const list = el.constraintList;
  list.replaceChildren();

  const errors   = STATE.violations.filter(v => v.severity === 'error');
  const warnings = STATE.violations.filter(v => v.severity === 'warning');
  const total    = errors.length;

  // Score ring (max 20 lỗi = 4 tuần × 5 ngày)
  const circumference = 163.4;
  const maxViols = 20;
  const ratio = Math.max(0, 1 - total / maxViols);
  el.ringFg.style.strokeDashoffset = circumference * (1 - ratio);
  el.ringFg.style.stroke = total === 0 ? 'var(--ok)' : total <= 4 ? 'var(--warn)' : 'var(--err)';

  el.scoreNum.textContent    = total === 0 ? '✓' : String(total);
  el.scoreStatus.textContent = total === 0 ? 'Hợp lệ' : `${total} lỗi`;

  if (total === 0 && warnings.length === 0) {
    const li = document.createElement('li');
    li.className = 'c-item ok';
    const icon = document.createElement('span');
    icon.className = 'c-icon';
    icon.textContent = '✅';
    const txt = document.createElement('span');
    txt.textContent = 'Tất cả ràng buộc đã thỏa mãn';
    li.appendChild(icon); li.appendChild(txt);
    list.appendChild(li);
    return;
  }

  [...errors, ...warnings].forEach(v => {
    const li = document.createElement('li');
    li.className = `c-item ${v.severity === 'error' ? 'err' : 'warn'}`;
    li.setAttribute('role', 'listitem');

    const icon = document.createElement('span');
    icon.className = 'c-icon';
    icon.textContent = v.severity === 'error' ? '❌' : '⚠️';

    const txt = document.createElement('span');
    const weekPrefix = v.week ? `[${WEEK_LABELS[v.week]}] ` : '';
    txt.textContent = weekPrefix + v.message;   // safe: textContent

    li.appendChild(icon);
    li.appendChild(txt);
    list.appendChild(li);
  });
}

// ─── Render Solver Bar ────────────────────────────────────────────────────────

function renderSolverBar() {
  const info = STATE.solverInfo;
  if (!info) return;
  el.solverBar.hidden = false;

  const statusMap = {
    optimal: '🏆 Tối ưu toàn cục',
    feasible: '✅ Nghiệm hợp lệ',
    timeout: '⏱ Timeout',
  };
  el.solverStatus.textContent = statusMap[info.status] || info.status;
  el.solverScore.textContent  = `Score: ${STATE.score}`;
  el.solverTime.textContent   = `Thời gian giải: ${info.time_ms} ms`;
}

// ─── Render Stats ─────────────────────────────────────────────────────────────

function renderStats() {
  const info = STATE.solverInfo?.stats;
  const body = el.statsBody;
  body.replaceChildren();

  if (!info) {
    const p = document.createElement('p');
    p.className = 'c-empty';
    p.textContent = 'Chưa có thực đơn';
    body.appendChild(p);
    return;
  }

  const comRangText = info.com_rang_weeks?.length > 0
    ? info.com_rang_weeks.map(w => WEEK_LABELS[w]).join(', ')
    : '—';
  const comGaText = info.com_ga_weeks?.length > 0
    ? info.com_ga_weeks.map(w => WEEK_LABELS[w]).join(', ')
    : '—';

  const rows = [
    ['Điểm preference', `${STATE.score}`, 'stat-badge'],
    ['Tổng món viên',   `${info.vien_count}`],
    ['Thịt bò',         info.beef_used   ? '✓ Có' : 'Không dùng'],
    ['Tôm',             info.shrimp_used ? '✓ Có' : 'Không dùng'],
    ['Cơm rang',        comRangText],
    ['Cơm gà',          comGaText],
    ['Món ưu tiên',     `${info.preferred_count} món`],
  ];

  rows.forEach(([label, value, cls]) => {
    const row = document.createElement('div');
    row.className = 'stat-row';
    const lEl = document.createElement('span');
    lEl.className = 'stat-label';
    lEl.textContent = label;
    const vEl = document.createElement('span');
    vEl.className = cls || 'stat-val';
    vEl.textContent = value;
    row.appendChild(lEl);
    row.appendChild(vEl);
    body.appendChild(row);
  });

  // Món chiên theo tuần
  if (info.fried_per_week) {
    const weekRow = document.createElement('div');
    weekRow.className = 'stat-row';
    weekRow.style.flexDirection = 'column';
    weekRow.style.gap = '4px';
    const lEl = document.createElement('span');
    lEl.className = 'stat-label';
    lEl.textContent = 'Món chiên/tuần:';
    weekRow.appendChild(lEl);
    WEEKS.forEach(wk => {
      const count = info.fried_per_week[wk];
      if (count == null) return;
      const sub = document.createElement('div');
      sub.style.cssText = 'display:flex;justify-content:space-between;font-size:10.5px';
      const dl = document.createElement('span');
      dl.style.color = 'var(--txt2)';
      dl.textContent = WEEK_LABELS[wk];
      const dv = document.createElement('span');
      dv.style.fontWeight = '700';
      dv.style.color = count >= 8 ? 'var(--err)' : 'var(--txt)';
      dv.textContent = `${count} món`;
      sub.appendChild(dl);
      sub.appendChild(dv);
      weekRow.appendChild(sub);
    });
    body.appendChild(weekRow);
  }
}

// ─── Render Alternatives (in modal) ──────────────────────────────────────────

function renderAlternatives(alternatives, week, day, slot) {
  el.altList.replaceChildren();

  if (!alternatives || alternatives.length === 0) {
    const p = document.createElement('p');
    p.className = 'no-alt';
    p.textContent = 'Không tìm được món thay thế hợp lệ nào.';
    el.altList.appendChild(p);
    return;
  }

  alternatives.forEach(alt => {
    const card = document.createElement('button');
    card.className = 'alt-card';
    card.type = 'button';
    card.setAttribute('aria-label', `Chọn ${alt.dish.name}`);

    const info = document.createElement('div');
    info.className = 'alt-info';

    const name = document.createElement('div');
    name.className = 'alt-name';
    name.textContent = alt.dish.name;   // safe: textContent

    const sub = document.createElement('div');
    sub.className = 'alt-sub';

    const scoreSpan = document.createElement('span');
    scoreSpan.className = 'alt-score';
    scoreSpan.textContent = `Score: ${alt.score}`;

    const violSpan = document.createElement('span');
    violSpan.className = alt.remaining_violations === 0 ? 'alt-viols-ok' : 'alt-viols-err';
    violSpan.textContent = alt.remaining_violations === 0
      ? '✅ Không vi phạm'
      : `⚠ ${alt.remaining_violations} vi phạm`;

    sub.appendChild(scoreSpan);
    sub.appendChild(violSpan);

    const tags = buildTags(alt.dish, false);
    tags.style.marginTop = '4px';

    info.appendChild(name);
    info.appendChild(sub);
    info.appendChild(tags);

    const arrow = document.createElement('span');
    arrow.className = 'alt-arrow';
    arrow.textContent = '→';
    arrow.setAttribute('aria-hidden', 'true');

    card.appendChild(info);
    card.appendChild(arrow);

    card.addEventListener('click', () => applyAlternative(alt.dish, week, day, slot));
    el.altList.appendChild(card);
  });
}

function buildCurrentCard(dish, slot) {
  const card = document.createElement('div');
  card.className = 'cur-card';

  const label = document.createElement('div');
  label.className = 'cur-label';
  label.textContent = `Hiện tại · ${SLOT_LABELS[slot]}`;

  const name = document.createElement('div');
  name.className = 'cur-name';
  name.textContent = dish.name;

  const tags = buildTags(dish, false);
  tags.className = 'cur-tags';

  card.appendChild(label);
  card.appendChild(name);
  card.appendChild(tags);
  return card;
}

// ─── Toast ────────────────────────────────────────────────────────────────────

function showToast(msg, type = 'info', duration = 3500) {
  const t = document.createElement('div');
  t.className = `toast ${type}`;
  t.textContent = msg;   // safe: textContent
  el.toastArea.appendChild(t);
  setTimeout(() => {
    t.classList.add('out');
    t.addEventListener('animationend', () => t.remove(), { once: true });
  }, duration);
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function getCatColor(slot) {
  return {
    M1: '#ff6b8a', M2: '#ff9d4f', R: '#52d47a',
    C: '#4da8ff',  CO: '#c07dff', Q: '#f5c842',
  }[slot] || '#888';
}

// ─── Load data stats on startup ───────────────────────────────────────────────

async function loadDataStats() {
  try {
    const health = await apiGet('/health');
    if (!health.data_loaded) {
      showToast('⚠️ Dữ liệu chưa tải xong, thử lại sau.', 'warn');
      return;
    }
    const stats = health.stats;
    STATE.dataStats = stats;

    const dl = el.dataDl;
    dl.replaceChildren();

    const items = [
      ['Tổng số món',   `${stats.total}`],
      ['Món mặn (M)',   `${stats.by_category?.M ?? '—'}`],
      ['Món rau (R)',   `${stats.by_category?.R ?? '—'}`],
      ['Món canh (C)',  `${stats.by_category?.C ?? '—'}`],
      ['Cơm (CO)',      `${stats.by_category?.CO ?? '—'}`],
      ['Quà chiều (Q)', `${stats.by_category?.Q ?? '—'}`],
      ['Món ưu tiên',   `${stats.preferred_count ?? '—'}`],
      ['Món chiên',     `${stats.fried_count ?? '—'}`],
    ];

    items.forEach(([k, v]) => {
      const div = document.createElement('div');
      const dt = document.createElement('dt');
      dt.textContent = k;
      const dd = document.createElement('dd');
      dd.textContent = v;
      div.appendChild(dt);
      div.appendChild(dd);
      dl.appendChild(div);
    });
  } catch (e) {
    showToast('Không kết nối được API: ' + e.message, 'error');
  }
}

// ─── Event Listeners ──────────────────────────────────────────────────────────

el.btnOptimize.addEventListener('click', runOptimize);
el.btnAutorepair.addEventListener('click', runAutoRepair);
el.btnPrint.addEventListener('click', () => window.print());

el.modalClose.addEventListener('click', closeModal);
el.modalBg.addEventListener('click', e => {
  if (e.target === el.modalBg) closeModal();
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && !el.modalBg.hidden) closeModal();
});

document.querySelectorAll('.week-tab').forEach(btn => {
  btn.addEventListener('click', () => {
    if (!btn.disabled) switchWeek(btn.dataset.week);
  });
});

// ─── Init ─────────────────────────────────────────────────────────────────────

loadDataStats();
