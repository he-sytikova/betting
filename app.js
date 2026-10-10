/* Static football dashboard. GitHub Actions publishes data.json to GitHub Pages. */
const $ = (id) => document.getElementById(id);
let allPredictions = [];
let activeSource = 'all';
let sortKey = 'maxProbability';
let sortAscending = false;
const MIN_DISPLAY_PROBABILITY = 80;

function text(value, fallback = '—') {
  if (value === null || value === undefined || value === '') return fallback;
  return String(value);
}
function formatTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('ru-RU', { timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}
function probabilityParts(value) {
  if (value === null || value === undefined || value === '') return [];
  return String(value).split(/\s*\/\s*/).map((part) => {
    const match = part.match(/(\d+(?:[.,]\d+)?)\s*%/);
    return { part: part.trim(), probability: match ? Number.parseFloat(match[1].replace(',', '.')) : null };
  });
}
function maxProbability(item) {
  const doubleChance = doubleChanceProbabilities(item.probabilityOutcome);
  const fields = [
    item.probabilityOutcome,
    doubleChance.oneX,
    doubleChance.twelve,
    doubleChance.xTwo,
    item.totalGoals,
    item.underGoals,
    item.individualTotals,
    item.corners
  ];
  return fields.flatMap(probabilityParts)
    .reduce((max, entry) => entry.probability === null ? max : Math.max(max, entry.probability), 0);
}
function filterProbabilityText(value) {
  if (value === null || value === undefined || value === '') return value;
  const parts = probabilityParts(value);
  if (!parts.some((part) => part.probability !== null)) return value;
  const kept = parts.filter((part) => part.probability === null || part.probability >= MIN_DISPLAY_PROBABILITY);
  return kept.map((part) => part.part).join(' / ') || '—';
}
function doubleChanceProbabilities(value) {
  const matches = [...String(value || '').matchAll(/(?:П1|X|П2)\s+(\d+(?:[.,]\d+)?)\s*%/g)];
  const values = matches.map((match) => Number.parseFloat(match[1].replace(',', '.')));
  if (values.length !== 3 || values.some((number) => !Number.isFinite(number))) {
    return { oneX: '—', twelve: '—', xTwo: '—' };
  }
  const [home, draw, away] = values;
  return {
    oneX: `${(home + draw).toFixed(1)}%`,
    twelve: `${(home + away).toFixed(1)}%`,
    xTwo: `${(draw + away).toFixed(1)}%`
  };
}
function preparePrediction(item) {
  const peak = maxProbability(item);
  const doubleChance = doubleChanceProbabilities(item.probabilityOutcome);
  // Euro365 is a full match feed, not a list of pre-qualified picks:
  // keep all its events and market probabilities visible.
  const keepAllMarkets = item.source === 'Euro365';
  return {
    ...item,
    ...doubleChance,
    maxProbability: peak,
    probabilityOutcome: item.probabilityOutcome || '—',
    totalGoals: keepAllMarkets ? (item.totalGoals || '—') : filterProbabilityText(item.totalGoals),
    underGoals: keepAllMarkets ? (item.underGoals || '—') : filterProbabilityText(item.underGoals),
    individualTotals: keepAllMarkets ? (item.individualTotals || '—') : filterProbabilityText(item.individualTotals),
    corners: keepAllMarkets ? (item.corners || '—') : filterProbabilityText(item.corners)
  };
}
function sortPredictions(rows) {
  return [...rows].sort((a, b) => {
    if (sortKey === 'maxProbability') {
      const result = (a.maxProbability || 0) - (b.maxProbability || 0);
      return sortAscending ? result : -result;
    }
    let left = a[sortKey] ?? '';
    let right = b[sortKey] ?? '';
    if (sortKey === 'time') {
      left = Date.parse(left) || 0;
      right = Date.parse(right) || 0;
    } else {
      left = String(left).toLocaleLowerCase('ru');
      right = String(right).toLocaleLowerCase('ru');
    }
    const result = left < right ? -1 : left > right ? 1 : 0;
    return sortAscending ? result : -result;
  });
}
function addCell(row, value, className) {
  const cell = document.createElement('td');
  cell.textContent = text(value);
  if (className) cell.className = className;
  row.appendChild(cell);
}
function heatmapColor(probability) {
  // 80% is pale green; increasingly strong probabilities move toward dark green.
  const ratio = Math.max(0, Math.min(1, (probability - MIN_DISPLAY_PROBABILITY) / 20));
  const pale = [220, 252, 231];
  const dark = [20, 83, 45];
  const rgb = pale.map((start, index) => Math.round(start + (dark[index] - start) * ratio));
  return `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
}
function renderTable() {
  const tbody = $('table-body');
  tbody.replaceChildren();
  const qualified = allPredictions
    .map(preparePrediction)
    .filter((item) => item.maxProbability >= MIN_DISPLAY_PROBABILITY || item.source === 'Euro365')
    .filter((item) => activeSource === 'all' || item.source === activeSource);
  const rows = sortPredictions(qualified);
  const sourceLabel = activeSource === 'BSD' ? 'BSD' : activeSource === 'SStats' ? 'S-Stats' : activeSource === 'Euro365' ? 'Euro365' : activeSource === 'FB_DATA' ? 'FB_DATA' : '';
  $('empty-state').textContent = `Нет матчей${sourceLabel ? ` из источника ${sourceLabel}` : ''} для отображения.`;
  $('empty-state').classList.toggle('hidden', rows.length > 0);
  for (const item of rows) {
    const tr = document.createElement('tr');
    const isHighProbability = item.maxProbability >= MIN_DISPLAY_PROBABILITY;
    if (isHighProbability) tr.classList.add('high-probability-row');
    tr.style.backgroundColor = isHighProbability ? heatmapColor(item.maxProbability) : '';
    tr.style.color = isHighProbability && item.maxProbability >= 93 ? '#f8fafc' : '';
    tr.title = `Максимальная вероятность: ${item.maxProbability.toFixed(1)}%`;
    addCell(tr, formatTime(item.time));
    addCell(tr, item.league);
    addCell(tr, item.home);
    addCell(tr, item.away);
    addCell(tr, item.probabilityOutcome, 'prediction-cell');
    addCell(tr, item.oneX, 'prediction-cell');
    addCell(tr, item.twelve, 'prediction-cell');
    addCell(tr, item.xTwo, 'prediction-cell');
    addCell(tr, item.totalGoals, 'prediction-cell');
    addCell(tr, item.underGoals, 'prediction-cell');
    addCell(tr, item.actualResult, 'result-cell');
    tbody.appendChild(tr);
  }
}
function setSourceStatus(id, name, status) {
  const el = $(id);
  const count = Number(status?.count || 0);
  el.title = status?.message || status?.diagnostic || '';
  if (status?.ok) {
    el.textContent = `${name}: ${count} матчей`;
    el.className = 'status-badge ok';
  } else {
    el.textContent = `${name}: нет данных`;
    el.className = 'status-badge error';
    el.title = status?.message || status?.diagnostic || 'Подробности отсутствуют в ответе API';
  }
}
async function loadAll(requestRefresh = false) {
  const button = $('refresh-btn');
  const loading = $('loading');
  const errorBox = $('error-container');
  button.disabled = true;
  loading.classList.remove('hidden');
  errorBox.classList.add('hidden');
  $('status-bsd').textContent = 'BSD: загрузка…';
  $('status-sstats').textContent = 'SStats: загрузка…';
  $('status-euro365').textContent = 'Euro365: загрузка…';
  $('status-fbdata').textContent = 'FB_DATA: загрузка…';
  try {
    if (requestRefresh) {
      // GitHub Pages is static: refresh means re-read the latest published snapshot.
    }
    const response = await fetch(`./data.json?t=${Date.now()}`, { cache: 'no-store' });
    if (!response.ok) throw new Error(`Не удалось загрузить data.json (HTTP ${response.status})`);
    const data = await response.json();
    allPredictions = Array.isArray(data.predictions) ? data.predictions : [];
    setSourceStatus('status-bsd', 'BSD', data.sources?.bsd);
    setSourceStatus('status-sstats', 'SStats', data.sources?.sstats);
    setSourceStatus('status-euro365', 'Euro365', data.sources?.euro365);
    setSourceStatus('status-fbdata', 'FB_DATA', data.sources?.fbdata);
    const generatedAt = data.generatedAt ? new Date(data.generatedAt) : null;
    $('current-date').textContent = generatedAt && !Number.isNaN(generatedAt.getTime())
      ? `Матчи на ${data.date || 'сегодня'} · обновлено ${generatedAt.toLocaleString('ru-RU')}`
      : `Матчи на ${data.date || 'сегодня'}`;
    if (data.errors?.length) {
      $('error-message').textContent = data.errors.join(' · ');
      errorBox.classList.remove('hidden');
    }
    renderTable();
  } catch (error) {
    $('error-message').textContent = error.message || 'Не удалось загрузить данные.';
    errorBox.classList.remove('hidden');
    allPredictions = [];
    renderTable();
  } finally {
    loading.classList.add('hidden');
    button.disabled = false;
  }
}
document.addEventListener('DOMContentLoaded', () => {
  $('refresh-btn').addEventListener('click', () => loadAll(true));
  document.querySelectorAll('.source-filter-btn').forEach((button) => {
    button.addEventListener('click', () => {
      activeSource = button.dataset.source || 'all';
      document.querySelectorAll('.source-filter-btn').forEach((item) => {
        const active = item === button;
        item.classList.toggle('active', active);
        item.setAttribute('aria-pressed', String(active));
      });
      renderTable();
    });
  });
  document.querySelectorAll('th[data-sort]').forEach((th) => {
    th.addEventListener('click', () => {
      const key = th.dataset.sort;
      if (sortKey === key) sortAscending = !sortAscending;
      else { sortKey = key; sortAscending = true; }
      document.querySelectorAll('th[data-sort]').forEach((item) => {
        item.setAttribute('aria-sort', item === th ? (sortAscending ? 'ascending' : 'descending') : 'none');
      });
      renderTable();
    });
  });
  loadAll();
});
