const $ = (id) => document.getElementById(id);
let allMarkets = [];
function val(v) { return v === null || v === undefined || v === '' || v === '—' ? '—' : String(v); }
function fmtTime(v) {
  if (!v) return '—';
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('ru-RU', {timeZone:'Europe/Moscow',day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'});
}
function setSourceStatus(id, name, status) {
  const el = $(id);
  if (!el) return;
  const count = Number(status?.count || 0);
  el.title = status?.message || status?.diagnostic || '';
  if (status?.ok) {
    el.textContent = name + ': ' + count + ' матчей';
    el.className = 'status-badge ok';
  } else {
    el.textContent = name + ': нет данных';
    el.className = 'status-badge error';
  }
}
function colorMarketCells(row) {
  for (const cell of row.cells) {
    cell.style.backgroundColor = '#fff';
    cell.style.color = '#000';
    if (cell.cellIndex < 5) continue;
    const nums = cell.textContent.split('%').map(part => {
      const pieces = part.trim().split(' ');
      return Number.parseFloat(pieces[pieces.length - 1].replace(',', '.'));
    }).filter(Number.isFinite);
    if (!nums.length) continue;
    const highest = Math.max(...nums);
    if (highest > 80) {
      const ratio = Math.max(0, Math.min(1, (highest - 80) / 20));
      const pale = [220, 252, 231], dark = [20, 83, 45];
      cell.style.backgroundColor = 'rgb(' + pale.map((start, i) => Math.round(start + (dark[i] - start) * ratio)).join(', ') + ')';
      cell.title = 'Вероятность: ' + highest.toFixed(1) + '%';
    }
  }
}
function render() {
  const type = $('market-type').value;
  const source = $('market-source').value;
  const query = $('market-search').value.trim().toLocaleLowerCase('ru');
  const rows = allMarkets.filter(x => {
    const hasCorners = val(x.corners) !== '—';
    const hasCards = val(x.yellowCards) !== '—';
    if (type === 'corners' && !hasCorners) return false;
    if (type === 'cards' && !hasCards) return false;
    if (source !== 'all' && x.source !== source) return false;
    const haystack = [x.league,x.home,x.away,x.source].join(' ').toLocaleLowerCase('ru');
    return !query || haystack.includes(query);
  }).sort((a,b) => (Date.parse(a.time)||0) - (Date.parse(b.time)||0));
  const tbody = $('markets-body');
  tbody.replaceChildren();
  for (const x of rows) {
    const tr = document.createElement('tr');
    for (const v of [fmtTime(x.time),val(x.league),val(x.home),val(x.away),val(x.source),val(x.corners),val(x.yellowCards)]) {
      const td = document.createElement('td');
      td.textContent = v;
      tr.appendChild(td);
    }
    colorMarketCells(tr);
    tbody.appendChild(tr);
  }
  $('markets-count').textContent = 'Матчей с прогнозами: ' + rows.length;
  $('markets-empty').classList.toggle('hidden', rows.length > 0);
}
async function load() {
  try {
    const response = await fetch('./data.json?t=' + Date.now(), {cache:'no-store'});
    if (!response.ok) throw new Error('Не удалось загрузить data.json (HTTP ' + response.status + ')');
    const data = await response.json();
    allMarkets = Array.isArray(data.predictions) ? data.predictions : [];
    setSourceStatus('status-bsd','BSD',data.sources?.bsd);
    setSourceStatus('status-sstats','SStats',data.sources?.sstats);
    setSourceStatus('status-euro365','Euro365',data.sources?.euro365);
    setSourceStatus('status-fbdata','FB_DATA',data.sources?.fbdata);
    setSourceStatus('status-apifootball','Api-Football',data.sources?.apifootball);
    const generated = data.generatedAt ? new Date(data.generatedAt) : null;
    $('markets-date').textContent = 'Матчи на ' + (data.date || 'сегодня') + (generated && !Number.isNaN(generated.getTime()) ? ' · обновлено ' + generated.toLocaleString('ru-RU') : '');
    render();
  } catch (e) {
    $('markets-error').textContent = e.message || 'Ошибка загрузки данных.';
    $('markets-error').classList.remove('hidden');
    $('markets-body').replaceChildren();
    $('markets-empty').classList.add('hidden');
  }
}
document.addEventListener('DOMContentLoaded', () => {
  ['market-type','market-source'].forEach(id => $(id).addEventListener('change',render));
  $('market-search').addEventListener('input',render);
  const refresh = $('refresh-btn');
  if (refresh) refresh.addEventListener('click', () => load());
  load();
});