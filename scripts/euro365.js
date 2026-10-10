'use strict';

const BASE = 'https://api.euro365.bet';
const CACHE_MS = 60_000;
const DICTIONARY_TTL_MS = 60 * 60 * 1000;
let cachedRows = [];
let cachedAt = 0;
let cachedDate = '';
let lastAttemptAt = 0;
let dictionaryCache = null;
let dictionaryAt = 0;
let lastDiagnostics = 'Euro365: диагностика ещё не запускалась';

function obj(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
}
function first(...values) {
  return values.find(value => value !== undefined && value !== null && value !== '') ?? null;
}
function unwrapData(payload) {
  return payload && payload.data !== undefined ? payload.data : payload;
}
function eventEntries(payload) {
  const data = unwrapData(payload);
  if (Array.isArray(data)) {
    return data.map((event, index) => [String(first(event.id, event.event_id, index)), event]);
  }
  return Object.entries(obj(data)).filter(([, event]) => event && typeof event === 'object');
}
function dateFromEvent(event) {
  const raw = first(event.kickoff, event.start_time, event.startTime, event.date, event.ts);
  if (raw === null) return null;
  const numeric = Number(raw);
  const date = Number.isFinite(numeric)
    ? new Date(numeric < 100000000000 ? numeric * 1000 : numeric)
    : new Date(raw);
  return Number.isNaN(date.getTime()) ? null : date;
}
function moscowDayKey(date) {
  if (!date) return null;
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit'
  }).formatToParts(date);
  const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}
function eventLeague(event) {
  const tournament = first(event.tournament, event.league, event.competition);
  if (typeof tournament === 'string') return tournament;
  if (Array.isArray(event.t)) return first(event.t[1], event.tournament_name, event.league_name, '—');
  return first(tournament?.name, tournament?.title, event.league_name, event.tournament_name, '—');
}
function normalizeDictionary(payload) {
  const data = obj(unwrapData(payload));
  return {
    markets: obj(data.markets),
    outcomes: obj(data.outcomes),
    full: obj(data.markets_full)
  };
}
async function requestJson(path, timeoutMs = 4000) {
  const key = process.env.EURO365_API_KEY || '';
  if (!key) throw new Error('Не задан EURO365_API_KEY в GitHub Actions Secrets');
  const response = await fetch(BASE + path, {
    headers: { 'X-API-Key': key, Accept: 'application/json' },
    signal: AbortSignal.timeout(timeoutMs)
  });
  const body = await response.text();
  if (!response.ok) {
    let detail = body.slice(0, 160);
    try { detail = JSON.parse(body).error || detail; } catch {}
    if (response.status === 429) throw new Error('Euro365: достигнут лимит запросов (429); повторите позже');
    throw new Error('Euro365 HTTP ' + response.status + ': ' + detail);
  }
  let payload;
  try { payload = JSON.parse(body); } catch { throw new Error('Euro365: API вернул не JSON'); }
  if (payload && payload.success === false) throw new Error('Euro365: ' + (payload.error || 'API вернул success=false'));
  return payload;
}
async function getDictionary() {
  if (dictionaryCache && Date.now() - dictionaryAt < DICTIONARY_TTL_MS) return dictionaryCache;
  const payload = await requestJson('/v1/markets?sport=1&nested=1&lang=en', 3500);
  dictionaryCache = normalizeDictionary(payload);
  dictionaryAt = Date.now();
  return dictionaryCache;
}
function priceFromTuple(tuple, line) {
  if (!Array.isArray(tuple) || tuple.length < 2) return null;
  const price = Number(tuple[0]) / 100;
  const flags = Number(tuple[1]);
  if (!Number.isFinite(price) || price <= 1 || flags !== 0 || Number(line?.s || 0) !== 0) return null;
  return price;
}
function outcomeName(id, dictionary) {
  return String(dictionary.outcomes[String(id)] || '').trim().toLowerCase();
}
function impliedProbabilities(entries) {
  const usable = entries.filter(entry => entry && Number.isFinite(entry.odds) && entry.odds > 1);
  const sum = usable.reduce((total, entry) => total + 1 / entry.odds, 0);
  if (!sum) return new Map();
  return new Map(usable.map(entry => [entry.name, (1 / entry.odds / sum) * 100]));
}
function readLine(line, dictionary) {
  const result = [];
  for (const [id, tuple] of Object.entries(line || {})) {
    if (id === 's' || id === 'ls') continue;
    const name = outcomeName(id, dictionary);
    const odds = priceFromTuple(tuple, line);
    if (!name || odds === null) continue;
    result.push({ name, odds });
  }
  return result;
}
function lineForMarket(market, targetLine) {
  const lines = obj(market);
  const keys = Object.keys(lines).filter(key => key !== 's' && key !== 'ls');
  const target = String(targetLine);
  const exact = keys.find(key => key.toLowerCase() === ('s' + target).toLowerCase() || key.replace(/^s/, '') === target);
  if (exact) return lines[exact];
  if (targetLine === null && lines.s) return lines.s;
  return null;
}
function probabilityLabel(entries, labels) {
  const byName = new Map(entries.map(entry => [entry.name.replace(/[^a-z0-9]/g, ''), entry]));
  const selected = labels.map(label => {
    const normalized = label.replace(/[^a-z0-9]/g, '');
    return [...byName.entries()].find(([name]) => name === normalized || name.includes(normalized))?.[1] || null;
  });
  if (selected.some(item => !item)) return null;
  const probs = impliedProbabilities(selected.map((entry, index) => ({ ...entry, name: labels[index] })));
  return labels.map(label => (probs.get(label) || 0).toFixed(1) + '%');
}
function marketProbabilities(oddsForEvent, dictionary, marketIds, lineKey, labels) {
  const groups = obj(oddsForEvent);
  for (const id of marketIds) {
    const market = groups[String(id)];
    if (!market) continue;
    const line = lineForMarket(market, lineKey);
    if (!line || Number(line.s || 0) !== 0) continue;
    const entries = readLine(line, dictionary);
    const probabilities = probabilityLabel(entries, labels);
    if (probabilities) return probabilities;
  }
  return null;
}
function normalizeEuro365(eventId, event, oddsForEvent, dictionary, targetDate) {
  const kickoff = dateFromEvent(event);
  if (!kickoff || moscowDayKey(kickoff) !== targetDate) return null;
  const home = first(event.h, event.home, event.home_name, event.homeTeam, event.home_team, '—');
  const away = first(event.a, event.away, event.away_name, event.awayTeam, event.away_team, '—');
  const marketNames = Object.entries(dictionary.markets).map(([id, name]) => [id, String(name).toLowerCase()]);
  const idsFor = pattern => marketNames.filter(([, name]) => pattern.test(name)).map(([id]) => id);
  const result = marketProbabilities(oddsForEvent, dictionary, ['1001', ...idsFor(/1x2|match result|match winner/)], null, ['1', 'x', '2']);
  const totalOverUnder = marketProbabilities(oddsForEvent, dictionary, ['1018', '1007', ...idsFor(/total goals.*over.*under|goals over.*under/)], '2.5', ['over', 'under']);
  const cornerOverUnder = marketProbabilities(oddsForEvent, dictionary, idsFor(/total corners.*over.*under|corners over.*under/), '9.5', ['over', 'under']);
  const status = String(first(event.status, event.state, event.live === true ? 'live' : '')).toLowerCase();
  const score = first(event.score, event.sc, null);
  const homeScore = first(event.home_score, event.homeScore, score && score.home, Array.isArray(score) ? score[0] : null);
  const awayScore = first(event.away_score, event.awayScore, score && score.away, Array.isArray(score) ? score[1] : null);
  const actualResult = homeScore !== null && awayScore !== null
    ? ((status.includes('live') || status.includes('progress') ? 'LIVE · ' : status.includes('end') || status.includes('finish') ? 'ФТ · ' : '') +
      homeScore + ':' + awayScore + ' · ' +
      (Number(homeScore) > Number(awayScore) ? 'П1' : Number(homeScore) < Number(awayScore) ? 'П2' : 'X') +
      ' · голов: ' + (Number(homeScore) + Number(awayScore)))
    : status.includes('live') || status.includes('progress') ? 'Матч идёт'
    : status.includes('end') || status.includes('finish') ? 'Завершён' : '—';
  return {
    source: 'Euro365',
    eventId,
    time: kickoff.toISOString(),
    league: eventLeague(event),
    home: String(home),
    away: String(away),
    actualResult,
    outcome: result ? (Number(result[0].replace('%', '')) > Number(result[1].replace('%', '')) && Number(result[0].replace('%', '')) > Number(result[2].replace('%', '')) ? 'П1' :
      Number(result[2].replace('%', '')) > Number(result[0].replace('%', '')) && Number(result[2].replace('%', '')) > Number(result[1].replace('%', '')) ? 'П2' :
      Number(result[1].replace('%', '')) > Number(result[0].replace('%', '')) && Number(result[1].replace('%', '')) > Number(result[2].replace('%', '')) ? 'X' : '—') : '—',
    probabilityOutcome: result ? 'П1 ' + result[0] + ' / X ' + result[1] + ' / П2 ' + result[2] : '—',
    totalGoals: totalOverUnder ? 'ТБ 2.5: ' + totalOverUnder[0] : '—',
    underGoals: totalOverUnder ? 'ТМ 2.5: ' + totalOverUnder[1] : '—',
    individualTotals: '—',
    corners: cornerOverUnder ? 'ТБ угл. 9.5: ' + cornerOverUnder[0] : '—',
    yellowCards: '—'
  };
}
async function fetchEuro365(targetDate) {
  const now = Date.now();
  if (cachedAt && cachedDate === targetDate && now - cachedAt < CACHE_MS) return cachedRows;
  if (lastAttemptAt && now - lastAttemptAt < CACHE_MS) {
    if (cachedAt && cachedDate === targetDate) return cachedRows;
    throw new Error('Euro365: обновление ограничено до одного раза в минуту для соблюдения лимита API');
  }
  lastAttemptAt = now;
  const diagnostics = { targetDate, live: 0, prematch: 0, unique: 0, targetDateEvents: 0, oddsEvents: 0, rows: 0, errors: [] };
  const key = process.env.EURO365_API_KEY || '';
  if (!key) throw new Error('Не задан EURO365_API_KEY в GitHub Actions Secrets');
  const [liveResult, prematchResult, scoresResult] = await Promise.allSettled([
    requestJson('/v1/live?sport=1', 4000),
    requestJson('/v1/prematch?sport=1', 4000),
    requestJson('/v1/scores', 3000)
  ]);
  const eventMap = new Map();
  for (const [name, result] of [['live', liveResult], ['prematch', prematchResult]]) {
    if (result.status !== 'fulfilled') {
      diagnostics.errors.push(name + ': ' + String(result.reason?.message || result.reason));
      continue;
    }
    const entries = eventEntries(result.value);
    diagnostics[name] = entries.length;
    for (const [id, event] of entries) eventMap.set(id, event);
  }
  diagnostics.unique = eventMap.size;
  if (!eventMap.size) {
    const failures = [];
    if (liveResult.status === 'rejected') failures.push('live: ' + liveResult.reason.message);
    if (prematchResult.status === 'rejected') failures.push('prematch: ' + prematchResult.reason.message);
    if (failures.length) throw new Error('Euro365: событий не получено; ' + failures.join('; '));
  }
  if (scoresResult.status === 'fulfilled') {
    const scores = obj(unwrapData(scoresResult.value));
    for (const [id, event] of eventMap) {
      const score = scores[id];
      if (!score || typeof score !== 'object') continue;
      eventMap.set(id, {
        ...event,
        home_score: first(score.home, event.home_score),
        away_score: first(score.away, event.away_score),
        status: first(score.status, event.status),
        kickoff: first(score.kickoff, event.kickoff)
      });
    }
  }
  const targetEvents = [...eventMap.entries()]
    .filter(([, event]) => moscowDayKey(dateFromEvent(event)) === targetDate)
    .sort((a, b) => {
      const aLive = /live|progress|inplay/i.test(String(a[1].status || '')) ? 0 : 1;
      const bLive = /live|progress|inplay/i.test(String(b[1].status || '')) ? 0 : 1;
      return aLive - bLive || dateFromEvent(a[1]) - dateFromEvent(b[1]);
    });
  diagnostics.targetDateEvents = targetEvents.length;
  if (!targetEvents.length) {
    lastDiagnostics = `Euro365: live=${diagnostics.live}, prematch=${diagnostics.prematch}, уникальных=${diagnostics.unique}, за ${targetDate}=0${diagnostics.errors.length ? '; ошибки: ' + diagnostics.errors.join(' | ') : ''}`;
    cachedRows = [];
    cachedDate = targetDate;
    cachedAt = Date.now();
    return cachedRows;
  }
  const dictionary = await getDictionary();
  // Fetch odds in manageable batches so every event for the day is included,
  // rather than silently dropping all events after the first 100.
  const oddsData = {};
  const oddsBatchSize = 150;
  for (let start = 0; start < targetEvents.length; start += oddsBatchSize) {
    const batch = targetEvents.slice(start, start + oddsBatchSize);
    const ids = batch.map(([id]) => id);
    const oddsPayload = await requestJson('/v1/odds?ids=' + encodeURIComponent(ids.join(',')), 4500);
    Object.assign(oddsData, obj(unwrapData(oddsPayload)));
  }
  diagnostics.oddsEvents = Object.keys(oddsData).length;
  const rows = targetEvents.map(([id, event]) => normalizeEuro365(id, event, oddsData[id], dictionary, targetDate)).filter(Boolean);
  diagnostics.rows = rows.length;
  lastDiagnostics = `Euro365: live=${diagnostics.live}, prematch=${diagnostics.prematch}, уникальных=${diagnostics.unique}, за ${targetDate}=${diagnostics.targetDateEvents}, odds=${diagnostics.oddsEvents}, строк=${diagnostics.rows}${diagnostics.errors.length ? '; ошибки: ' + diagnostics.errors.join(' | ') : ''}`;
  cachedRows = rows;
  cachedDate = targetDate;
  cachedAt = Date.now();
  return cachedRows;
}

function getEuro365Diagnostics() { return lastDiagnostics; }

module.exports = { fetchEuro365, getEuro365Diagnostics, normalizeEuro365, impliedProbabilities, normalizeDictionary };
