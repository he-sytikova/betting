'use strict';

const { arr, dateKey } = require('./build-data');
const BASE = 'https://api.sportmonks.com/v3/football';

function first(...values) {
  return values.find(value => value !== undefined && value !== null && value !== '') ?? null;
}
function norm(value) {
  return String(value ?? '').toUpperCase().replace(/[^A-Z0-9]+/g, '_').replace(/^_|_$/g, '');
}
function pct(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return null;
  let n = Number(value);
  if (n > 0 && n <= 1) n *= 100;
  return n;
}
function fmt(value) {
  const n = pct(value);
  return n === null ? null : `${n.toFixed(1)}%`;
}
function team(participant) {
  return participant ? String(first(participant.name, participant.short_code, participant.code, participant.id, '—')) : '—';
}
function valuesOf(prediction) {
  return prediction?.predictions && typeof prediction.predictions === 'object' ? prediction.predictions : {};
}
function getProb(obj, ...keys) {
  for (const key of keys) if (obj[key] !== undefined && obj[key] !== null) return obj[key];
  return null;
}
function normalizeFixture(fixture) {
  const participants = fixture.participants || [];
  const home = participants.find(p => p.meta?.location === 'home') || participants[0];
  const away = participants.find(p => p.meta?.location === 'away') || participants[1];
  const preds = fixture.predictions || [];
  let result = null;
  const goals = [], under = [], corners = [];
  for (const pred of preds) {
    const typeName = norm(first(pred.type?.developer_name, pred.type?.name, pred.name, ''));
    const vals = valuesOf(pred);
    if (typeName.includes('FULLTIME_RESULT') || typeName.includes('MATCH_WINNER') || typeName.includes('THREE_WAY')) {
      const h = getProb(vals, 'home', '1', 'home_win');
      const d = getProb(vals, 'draw', 'x');
      const a = getProb(vals, 'away', '2', 'away_win');
      if (h !== null || d !== null || a !== null) result = `П1 ${fmt(h) || '—'} / X ${fmt(d) || '—'} / П2 ${fmt(a) || '—'}`;
    } else if (typeName.includes('OVER_UNDER')) {
      const lineMatch = typeName.match(/(0_5|1_5|2_5|3_5|4_5|10_5|11_5|[4-9])/);
      const line = lineMatch ? lineMatch[1].replace('_', '.') : '';
      const over = getProb(vals, 'over', 'yes', 'over_probability');
      const underValue = getProb(vals, 'under', 'no', 'under_probability');
      if (line && over !== null) goals.push(`ТБ ${line}: ${fmt(over)}`);
      if (line && underValue !== null) under.push(`ТМ ${line}: ${fmt(underValue)}`);
      else if (line && over !== null) under.push(`ТМ ${line}: ${fmt(100 - pct(over))}`);
    } else if (typeName.includes('CORNERS')) {
      const lineMatch = typeName.match(/(4|5|6|7|8|9|10|11)(?:_5)?/);
      const line = lineMatch ? lineMatch[0].replace('_', '.') : '';
      const over = getProb(vals, 'over', 'yes');
      if (line && over !== null) corners.push(`ТБ угл. ${line}: ${fmt(over)}`);
    }
  }
  return {
    source: 'Sportmonks',
    eventId: fixture.id,
    time: first(fixture.starting_at, fixture.starting_at_utc, fixture.date),
    league: first(fixture.league?.name, fixture.league?.short_code, '—'),
    home: team(home), away: team(away), actualResult: '—',
    outcome: result || '—', probabilityOutcome: result || '—',
    totalGoals: goals.join(' / ') || '—', underGoals: under.join(' / ') || '—',
    individualTotals: '—', corners: corners.join(' / ') || '—', yellowCards: '—'
  };
}
async function fetchSportmonks() {
  const token = process.env.SPORTMONKS_TOKEN || '';
  if (!token) throw new Error('Не задан SPORTMONKS_TOKEN в GitHub Actions Secrets');
  const date = new Date().toISOString().slice(0, 10);
  const url = `${BASE}/fixtures/between/${date}/${date}?api_token=${encodeURIComponent(token)}&include=predictions.type;participants;league`;
  const response = await fetch(url, { signal: AbortSignal.timeout(15000) });
  const body = await response.text();
  if (!response.ok) throw new Error(`HTTP ${response.status}: ${body.slice(0, 180)}`);
  const payload = JSON.parse(body);
  return arr(payload?.data).filter(f => f.predictions?.length)
    .map(normalizeFixture).filter(row => row.time && dateKey(row.time) === date);
}
module.exports = { fetchSportmonks, normalizeFixture };
