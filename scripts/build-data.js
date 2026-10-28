'use strict';

const fs = require('node:fs');

const BSD_BASE = 'https://sports.bzzoiro.com/api/v2';
const SSTATS_BASE = 'https://api.sstats.net';
const { fetchEuro365, getEuro365Diagnostics } = require('./euro365');
let date = new Date().toISOString().slice(0, 10);
const errors = [];
let apiFootballCache = null;
const sourceStatus = {
  bsd: { ok: false, count: 0, message: '', diagnostic: '' },
  sstats: { ok: false, count: 0, message: '', diagnostic: '' },
  euro365: { ok: false, count: 0, message: '', diagnostic: '' },
  fbdata: { ok: false, count: 0, message: '', diagnostic: '' },
  apifootball: { ok: false, count: 0, message: '', diagnostic: '' },
};

function arr(payload, depth = 0) {
  if (Array.isArray(payload)) return payload;
  if (!payload || typeof payload !== 'object' || depth > 4) return [];
  for (const key of ['results', 'Results', 'data', 'Data', 'items', 'Items', 'games', 'Games', 'events', 'Events', 'predictions', 'Predictions', 'matches', 'Matches', 'records', 'value']) {
    if (Array.isArray(payload[key])) return payload[key];
  }
  for (const value of Object.values(payload)) {
    if (Array.isArray(value)) return value;
  }
  for (const key of ['data', 'Data', 'result', 'Result', 'response', 'Response']) {
    if (payload[key] && typeof payload[key] === 'object') {
      const nested = arr(payload[key], depth + 1);
      if (nested.length) return nested;
    }
  }
  return [];
}
function first(...values) {
  return values.find(v => v !== undefined && v !== null && v !== '') ?? null;
}
function keyNorm(value) {
  return String(value).replace(/[^a-z0-9]/gi, '').toLowerCase();
}
function pick(obj, ...keys) {
  if (!obj || typeof obj !== 'object') return null;
  const wanted = new Set(keys.map(keyNorm));
  for (const [key, value] of Object.entries(obj)) {
    if (wanted.has(keyNorm(key)) && value !== undefined && value !== null && value !== '') return value;
  }
  return null;
}
function deepPick(obj, keys, depth = 0) {
  if (!obj || typeof obj !== 'object' || depth > 5) return null;
  const direct = pick(obj, ...keys);
  if (direct !== null) return direct;
  for (const value of Object.values(obj)) {
    if (value && typeof value === 'object') {
      const found = deepPick(value, keys, depth + 1);
      if (found !== null) return found;
    }
  }
  return null;
}
function percent(value) {
  if (value === undefined || value === null || !Number.isFinite(Number(value))) return null;
  let number = Number(value);
  if (number > 0 && number < 1) number *= 100;
  return `${number.toFixed(1)}%`;
}
function percentUnder(overValue) {
  if (overValue === undefined || overValue === null || !Number.isFinite(Number(overValue))) return null;
  const over = Number(overValue);
  const scale = over <= 1 ? 1 : 100;
  return percent(Math.max(0, Math.min(scale, scale - over)));
}
function poissonOver(lambda, threshold) {
  const mean = Number(lambda);
  if (!Number.isFinite(mean) || mean < 0 || mean > 12) return null;
  let probability = Math.exp(-mean);
  let cumulative = probability;
  for (let goals = 1; goals <= threshold; goals++) {
    probability *= mean / goals;
    cumulative += probability;
  }
  return Math.max(0, Math.min(1, 1 - cumulative));
}
function safeError(error) {
  let message = String(error?.message || error).replace(/https?:\/\/\S+/g, '[API URL]');
  for (const name of ['BSD_TOKEN', 'SSTATS_TOKEN', 'EURO365_API_KEY', 'FD_DATA_TOKEN', 'API_FOOTBALL_KEY']) {
    const secret = process.env[name];
    if (secret) message = message.split(secret).join('[REDACTED]');
  }
  return message.slice(0, 220);
}
function moscowDateKey(value) {
  if (value === undefined || value === null || value === '') return null;
  const parsed = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(parsed.getTime())) return null;
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit'
  }).formatToParts(parsed);
  const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}
function dateKey(value) {
  if (value === undefined || value === null || value === '') return null;
  const raw = String(value).trim();
  if (/^\d{4}-\d{2}-\d{2}/.test(raw)) return raw.slice(0, 10);
  const european = raw.match(/^(\d{2})\.(\d{2})\.(\d{4})/);
  if (european) return `${european[3]}-${european[2]}-${european[1]}`;
  const parsed = new Date(raw);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString().slice(0, 10);
}
async function getJson(url, headers = {}, timeoutMs = 5000, options = {}) {
  const response = await fetch(url, { ...options, headers: { ...headers, ...(options.headers || {}) }, signal: AbortSignal.timeout(timeoutMs) });
  const body = await response.text();
  if (!response.ok) throw new Error(`HTTP ${response.status}: ${body.slice(0, 160)}`);
  try { return JSON.parse(body); } catch { throw new Error('API вернул не JSON'); }
}
function teamName(value) {
  if (value && typeof value === 'object') return first(
    pick(value, 'name', 'teamName', 'displayName', 'title', 'shortName', 'short_name', 'label', 'value'),
    '—'
  );
  return first(value, '—');
}
function leagueNameFor(item, leagueNames = new Map()) {
  const raw = first(
    pick(item, 'leagueName', 'competitionName', 'tournamentName', 'divisionName', 'league', 'competition', 'tournament', 'division'),
    pick(item?.event, 'leagueName', 'competitionName', 'tournamentName', 'league', 'competition', 'tournament'),
    pick(item?.league, 'name', 'Name', 'title', 'Title')
  );
  if (raw && typeof raw === 'object') {
    const name = teamName(raw);
    if (name !== '—') return name;
    const id = pick(raw, 'id', 'leagueId', 'competitionId', 'tournamentId');
    if (id !== null && leagueNames.has(String(id))) return leagueNames.get(String(id));
  } else if (raw !== null && !/^\d+$/.test(String(raw))) {
    return String(raw);
  }
  const id = first(
    pick(item, 'leagueId', 'competitionId', 'tournamentId', 'divisionId'),
    pick(item?.league, 'id', 'leagueId'),
    pick(item?.event, 'leagueId', 'competitionId', 'tournamentId'),
    raw !== null && typeof raw !== 'object' && /^\d+$/.test(String(raw)) ? raw : null
  );
  return id !== null && leagueNames.has(String(id)) ? leagueNames.get(String(id)) : (id !== null ? String(id) : '—');
}
function normalizeBsd(event, prediction, leagueNames = new Map()) {
  const nestedEvent = first(prediction?.event, prediction?.match, prediction?.fixture, {}) || {};
  const sourceEvent = { ...(event && typeof event === 'object' ? event : {}), ...(nestedEvent && typeof nestedEvent === 'object' ? nestedEvent : {}) };
  const sourcePrediction = prediction || {};
  const marketRoot = first(sourcePrediction.markets, sourcePrediction.Markets, sourcePrediction.predictions, sourcePrediction.Predictions, sourcePrediction.data?.markets, sourcePrediction.data?.predictions, {}) || {};
  const result = first(pick(marketRoot, 'match_result', 'matchResult', 'result', '1x2', 'matchWinner'), {}) || {};
  const ou = first(pick(marketRoot, 'over_under', 'total_goals', 'totalGoals', 'goals', 'overUnder'), {}) || {};
  const corners = first(pick(marketRoot, 'corners', 'total_corners', 'totalCorners', 'cornerKicks'), {}) || {};
  const expected = first(pick(marketRoot, 'expected_goals', 'expectedGoals', 'xg', 'goalExpectancy'), {}) || {};
  const cards = first(pick(marketRoot, 'yellow_cards', 'yellowCards', 'cards', 'booking_points', 'totalCards'), {}) || {};
  const predicted = first(
    pick(result, 'predicted', 'predicted_result', 'predictedResult', 'prediction', 'winner', 'outcome', 'recommended'),
    pick(sourcePrediction.recommendations || {}, 'favorite', 'predicted', 'winner'),
    pick(sourcePrediction, 'predictedWinner', 'predicted_result', 'predictedResult', 'predicted', 'winner')
  );
  const probHomeRaw = first(
    pick(result, 'prob_home', 'probHome', 'prob_home_win', 'probHomeWin', 'home_win_prob', 'homeWinProb', 'homeProbability', 'homeWinProbability', 'home'),
    deepPick(sourcePrediction, ['prob_home', 'probHome', 'prob_home_win', 'probHomeWin', 'home_win_prob', 'homeWinProb', 'home_win_probability', 'homeWinProbability', 'probability_home', 'probability_home_win'])
  );
  const probDrawRaw = first(
    pick(result, 'prob_draw', 'probDraw', 'draw_prob', 'drawProb', 'drawProbability', 'draw'),
    deepPick(sourcePrediction, ['prob_draw', 'probDraw', 'draw_prob', 'drawProb', 'drawProbability', 'draw_probability', 'probability_draw', 'probability_tie'])
  );
  const probAwayRaw = first(
    pick(result, 'prob_away', 'probAway', 'prob_away_win', 'probAwayWin', 'away_win_prob', 'awayWinProb', 'awayProbability', 'awayWinProbability', 'away'),
    deepPick(sourcePrediction, ['prob_away', 'probAway', 'prob_away_win', 'probAwayWin', 'away_win_prob', 'awayWinProb', 'away_win_probability', 'awayWinProbability', 'probability_away', 'probability_away_win'])
  );
  const probHome = percent(probHomeRaw);
  const probDraw = percent(probDrawRaw);
  const probAway = percent(probAwayRaw);
  const hasResultProbs = [probHomeRaw, probDrawRaw, probAwayRaw].some(value => value !== null && Number.isFinite(Number(value)));
  const probabilityOutcome = hasResultProbs
    ? `П1 ${probHome ?? '—'} / X ${probDraw ?? '—'} / П2 ${probAway ?? '—'}`
    : '—';
  const outcome = predicted
    ? ({ home: 'П1', homewin: 'П1', h: 'П1', '1': 'П1', draw: 'X', tie: 'X', d: 'X', away: 'П2', awaywin: 'П2', a: 'П2', '2': 'П2' })[String(predicted).toLowerCase()] || String(predicted)
    : hasResultProbs
      ? `П1 ${probHome ?? '—'} / X ${probDraw ?? '—'} / П2 ${probAway ?? '—'}`
      : '—';
  const over15 = pick(ou, 'prob_over_15', 'prob_over_1_5', 'over15Probability', 'over1_5');
  const over25 = pick(ou, 'prob_over_25', 'prob_over_2_5', 'over25Probability', 'over2_5');
  const over35 = pick(ou, 'prob_over_35', 'prob_over_3_5', 'over35Probability', 'over3_5');
  const corner85 = pick(corners, 'prob_over_85', 'prob_over_8_5', 'over85Probability', 'over8_5');
  const corner95 = pick(corners, 'prob_over_95', 'prob_over_9_5', 'over95Probability', 'over9_5');
  const corner105 = pick(corners, 'prob_over_105', 'prob_over_10_5', 'over105Probability', 'over10_5');
  const card25 = pick(cards, 'prob_over_25', 'prob_over_2_5', 'over25Probability', 'over2_5');
  const card35 = pick(cards, 'prob_over_35', 'prob_over_3_5', 'over35Probability', 'over3_5');
  const xgHome = first(pick(expected, 'home', 'homeXg', 'xgHome', 'homeExpectedGoals', 'expected_home_goals', 'expectedHomeGoals'), deepPick(sourcePrediction, ['homeXg', 'xgHome', 'homeExpectedGoals']));
  const xgAway = first(pick(expected, 'away', 'awayXg', 'xgAway', 'awayExpectedGoals', 'expected_away_goals', 'expectedAwayGoals'), deepPick(sourcePrediction, ['awayXg', 'xgAway', 'awayExpectedGoals']));
  const eventDate = first(
    pick(sourceEvent, 'eventDate', 'startTime', 'kickoff', 'date', 'dateTime', 'matchDate', 'start'),
    pick(sourcePrediction, 'eventDate', 'startTime', 'kickoff', 'date', 'dateTime', 'matchDate', 'start')
  );
  const homeScore = first(pick(sourceEvent, 'home_score', 'homeScore', 'scoreHome', 'homeGoals', 'goalsHome'), pick(sourceEvent.score || {}, 'home', 'homeScore', 'home_score'));
  const awayScore = first(pick(sourceEvent, 'away_score', 'awayScore', 'scoreAway', 'awayGoals', 'goalsAway'), pick(sourceEvent.score || {}, 'away', 'awayScore', 'away_score'));
  const matchStatus = String(first(pick(sourceEvent, 'status', 'matchStatus', 'match_status', 'state'), '')).toLowerCase();
  const isLive = ['inprogress', 'in_progress', 'live', '1h', '2h', 'ht', 'half_time', 'halftime'].includes(matchStatus);
  const isFinished = ['finished', 'complete', 'completed', 'ft', 'full_time', 'fulltime', 'ended'].includes(matchStatus);
  const homeCorners = first(pick(sourceEvent, 'home_corners', 'homeCorners', 'cornersHome', 'homeCornerKicks'));
  const awayCorners = first(pick(sourceEvent, 'away_corners', 'awayCorners', 'cornersAway', 'awayCornerKicks'));
  const actualResult = homeScore !== null && awayScore !== null
    ? [
        `${isLive ? 'LIVE · ' : isFinished ? 'ФТ · ' : ''}${homeScore}:${awayScore}`,
        Number(homeScore) > Number(awayScore) ? 'П1' : Number(homeScore) < Number(awayScore) ? 'П2' : 'X',
        `голов: ${Number(homeScore) + Number(awayScore)}`,
        homeCorners !== null && awayCorners !== null ? `угл.: ${Number(homeCorners) + Number(awayCorners)}` : null
      ].filter(Boolean).join(' · ')
    : isLive ? 'Матч идёт' : isFinished ? 'Завершён' : '—';
  const home = first(pick(sourceEvent, 'homeTeam', 'home', 'teamHome', 'localTeam', 'homeTeamName'), pick(sourcePrediction, 'homeTeam', 'home', 'teamHome'));
  const away = first(pick(sourceEvent, 'awayTeam', 'away', 'teamAway', 'visitorTeam', 'awayTeamName'), pick(sourcePrediction, 'awayTeam', 'away', 'teamAway'));
  const cornerParts = [
    corner85 !== null ? `8.5: ${percent(corner85)}` : null,
    corner95 !== null ? `9.5: ${percent(corner95)}` : null,
    corner105 !== null ? `10.5: ${percent(corner105)}` : null
  ].filter(Boolean);
  const lambda = xgHome !== null && xgAway !== null ? Number(xgHome) + Number(xgAway) : null;
  const goalParts = [
    over15 !== null ? `ТБ 1.5: ${percent(over15)}` : lambda !== null ? `ТБ 1.5 Poisson: ${percent(poissonOver(lambda, 1))}` : null,
    over25 !== null ? `ТБ 2.5: ${percent(over25)}` : lambda !== null ? `ТБ 2.5 Poisson: ${percent(poissonOver(lambda, 2))}` : null,
    over35 !== null ? `ТБ 3.5: ${percent(over35)}` : lambda !== null ? `ТБ 3.5 Poisson: ${percent(poissonOver(lambda, 3))}` : null
  ].filter(Boolean);
  const underGoalParts = [
    over15 !== null ? `ТМ 1.5: ${percentUnder(over15)}` : lambda !== null ? `ТМ 1.5 Poisson: ${percent(1 - poissonOver(lambda, 1))}` : null,
    over25 !== null ? `ТМ 2.5: ${percentUnder(over25)}` : lambda !== null ? `ТМ 2.5 Poisson: ${percent(1 - poissonOver(lambda, 2))}` : null,
    over35 !== null ? `ТМ 3.5: ${percentUnder(over35)}` : lambda !== null ? `ТМ 3.5 Poisson: ${percent(1 - poissonOver(lambda, 3))}` : null
  ].filter(Boolean);
  const cardParts = [
    card25 !== null ? `ТБ 2.5: ${percent(card25)}` : null,
    card35 !== null ? `ТБ 3.5: ${percent(card35)}` : null
  ].filter(Boolean);
  return {
    source: 'BSD',
    eventId: first(pick(sourceEvent, 'id', 'eventId', 'matchId'), pick(sourcePrediction, 'eventId', 'matchId'), pick(sourcePrediction.event || {}, 'id')),
    time: eventDate,
    league: leagueNameFor(sourceEvent, leagueNames),
    home: teamName(home),
    away: teamName(away),
    actualResult,
    outcome,
    probabilityOutcome,
    totalGoals: goalParts.join(' / ') || (xgHome !== null || xgAway !== null ? `xG ${xgHome ?? '—'}–${xgAway ?? '—'}` : '—'),
    underGoals: underGoalParts.join(' / ') || '—',
    individualTotals: xgHome !== null || xgAway !== null ? `Х ${xgHome ?? '—'} / Г ${xgAway ?? '—'} xG` : '—',
    corners: cornerParts.length ? `ТБ угл. ${cornerParts.join(' / ')}` : '—',
    yellowCards: cardParts.length ? cardParts.join(' / ') : '—'
  };
}
function normalizeSstats(game, glicko = null, leagueNames = new Map()) {
  const detail = glicko?.data || glicko?.result || glicko || {};
  const prediction = first(game.prediction, game.predictions, game.Prediction, detail.prediction, detail.predictions, detail, game) || game;
  const markets = first(prediction.markets, prediction.Markets, prediction.predictions, prediction.Predictions, {}) || {};
  const dateTime = first(pick(game, 'Date', 'DateTime', 'eventDate', 'startTime', 'Kickoff', 'MatchDate', 'gameDate', 'start', 'timestamp', 'date_start'));
  const homeScore = first(pick(game, 'HomeScore', 'homeScore', 'home_score', 'ScoreHome', 'homeGoals', 'goalsHome'), pick(game.Score || game.score || {}, 'home', 'homeScore', 'home_score'));
  const awayScore = first(pick(game, 'AwayScore', 'awayScore', 'away_score', 'ScoreAway', 'awayGoals', 'goalsAway'), pick(game.Score || game.score || {}, 'away', 'awayScore', 'away_score'));
  const matchStatus = String(first(pick(game, 'Status', 'status', 'MatchStatus', 'matchStatus', 'state'), '')).toLowerCase();
  const isLive = ['inprogress', 'in_progress', 'live', '1h', '2h', 'ht', 'half_time', 'halftime'].includes(matchStatus);
  const isFinished = ['finished', 'complete', 'completed', 'ft', 'full_time', 'fulltime', 'ended'].includes(matchStatus);
  const homeCorners = first(pick(game, 'HomeCorners', 'homeCorners', 'home_corners', 'CornersHome'));
  const awayCorners = first(pick(game, 'AwayCorners', 'awayCorners', 'away_corners', 'CornersAway'));
  const actualResult = homeScore !== null && awayScore !== null
    ? [
        `${isLive ? 'LIVE · ' : isFinished ? 'ФТ · ' : ''}${homeScore}:${awayScore}`,
        Number(homeScore) > Number(awayScore) ? 'П1' : Number(homeScore) < Number(awayScore) ? 'П2' : 'X',
        `голов: ${Number(homeScore) + Number(awayScore)}`,
        homeCorners !== null && awayCorners !== null ? `угл.: ${Number(homeCorners) + Number(awayCorners)}` : null
      ].filter(Boolean).join(' · ')
    : isLive ? 'Матч идёт' : isFinished ? 'Завершён' : '—';
  const home = first(pick(game, 'HomeTeamName', 'homeTeamName', 'homeTeam', 'home', 'HomeTeam', 'teamHome'), pick(game.HomeTeam || {}, 'name', 'Name', 'teamName'));
  const away = first(pick(game, 'AwayTeamName', 'awayTeamName', 'awayTeam', 'away', 'AwayTeam', 'teamAway'), pick(game.AwayTeam || {}, 'name', 'Name', 'teamName'));
  const homeProb = first(
    pick(markets.match_result || markets.matchResult || {}, 'prob_home', 'probHome', 'homeProbability', 'homeWinProbability'),
    deepPick(detail, ['prob_home', 'probHome', 'homeProbability', 'homeWinProbability', 'homeWinProb'])
  );
  let drawProb = first(
    pick(markets.match_result || markets.matchResult || {}, 'prob_draw', 'probDraw', 'drawProbability'),
    deepPick(detail, ['prob_draw', 'probDraw', 'drawProbability', 'drawProb'])
  );
  const awayProb = first(
    pick(markets.match_result || markets.matchResult || {}, 'prob_away', 'probAway', 'awayProbability', 'awayWinProbability', 'awayWinProb'),
    deepPick(detail, ['prob_away', 'probAway', 'awayProbability', 'awayWinProbability', 'awayWinProb'])
  );
  if (drawProb === null && homeProb !== null && awayProb !== null) {
    const homeN = Number(homeProb);
    const awayN = Number(awayProb);
    const scale = homeN <= 1 && awayN <= 1 ? 1 : 100;
    const residual = scale - homeN - awayN;
    if (residual >= 0 && residual <= scale) drawProb = residual / scale <= 1 ? residual / scale : residual;
  }
  const winner = first(
    pick(prediction, 'predictedWinner', 'winner', 'prediction', 'recommendedOutcome'),
    deepPick(detail, ['predictedWinner', 'winner', 'recommendedOutcome'])
  );
  const hasProbs = [homeProb, drawProb, awayProb].some(v => v !== null && Number.isFinite(Number(v)));
  const outcome = winner
    ? ({ home: 'П1', homewin: 'П1', h: 'П1', '1': 'П1', draw: 'X', tie: 'X', d: 'X', away: 'П2', awaywin: 'П2', a: 'П2', '2': 'П2' })[String(winner).toLowerCase()] || String(winner)
    : hasProbs ? `П1 ${percent(homeProb) ?? '—'} / X ${percent(drawProb) ?? '—'} / П2 ${percent(awayProb) ?? '—'}` : '—';
  const probabilityOutcome = hasProbs
    ? `П1 ${percent(homeProb) ?? '—'} / X ${percent(drawProb) ?? '—'} / П2 ${percent(awayProb) ?? '—'}`
    : '—';
  const xgHome = first(
    deepPick(detail, ['homeXg', 'xgHome', 'homeExpectedGoals', 'expectedGoalsHome', 'homeXG', 'xGHome']),
    deepPick(game, ['homeXg', 'xgHome', 'homeExpectedGoals', 'expectedGoalsHome'])
  );
  const xgAway = first(
    deepPick(detail, ['awayXg', 'xgAway', 'awayExpectedGoals', 'expectedGoalsAway', 'awayXG', 'xGAway']),
    deepPick(game, ['awayXg', 'xgAway', 'awayExpectedGoals', 'expectedGoalsAway'])
  );
  const over15 = deepPick(detail, ['prob_over_15', 'prob_over_1_5', 'over15Probability', 'over1_5Probability']);
  const over25 = deepPick(detail, ['prob_over_25', 'prob_over_2_5', 'over25Probability', 'over2_5Probability']);
  const over35 = deepPick(detail, ['prob_over_35', 'prob_over_3_5', 'over35Probability', 'over3_5Probability']);
  const corners85 = deepPick(detail, ['prob_corners_over_85', 'cornersOver85Probability', 'prob_over_corners_8_5']);
  const corners95 = deepPick(detail, ['prob_corners_over_95', 'cornersOver95Probability', 'prob_over_corners_9_5']);
  const cards25 = deepPick(detail, ['prob_yellow_cards_over_25', 'yellowCardsOver25Probability', 'prob_cards_over_2_5']);
  const cards35 = deepPick(detail, ['prob_yellow_cards_over_35', 'yellowCardsOver35Probability', 'prob_cards_over_3_5']);
  const lambda = xgHome !== null && xgAway !== null ? Number(xgHome) + Number(xgAway) : null;
  const goalParts = [
    over15 !== null ? `ТБ 1.5: ${percent(over15)}` : lambda !== null ? `ТБ 1.5 Poisson: ${percent(poissonOver(lambda, 1))}` : null,
    over25 !== null ? `ТБ 2.5: ${percent(over25)}` : lambda !== null ? `ТБ 2.5 Poisson: ${percent(poissonOver(lambda, 2))}` : null,
    over35 !== null ? `ТБ 3.5: ${percent(over35)}` : lambda !== null ? `ТБ 3.5 Poisson: ${percent(poissonOver(lambda, 3))}` : null
  ].filter(Boolean);
  const underGoalParts = [
    over15 !== null ? `ТМ 1.5: ${percentUnder(over15)}` : lambda !== null ? `ТМ 1.5 Poisson: ${percent(1 - poissonOver(lambda, 1))}` : null,
    over25 !== null ? `ТМ 2.5: ${percentUnder(over25)}` : lambda !== null ? `ТМ 2.5 Poisson: ${percent(1 - poissonOver(lambda, 2))}` : null,
    over35 !== null ? `ТМ 3.5: ${percentUnder(over35)}` : lambda !== null ? `ТМ 3.5 Poisson: ${percent(1 - poissonOver(lambda, 3))}` : null
  ].filter(Boolean);
  const leagueId = first(pick(game, 'LeagueId', 'leagueId', 'LeagueID'), pick(game.League || {}, 'id', 'Id'));
  let league = leagueNameFor(game, leagueNames);
  if (league === '—' && leagueId !== null && leagueNames.has(String(leagueId))) league = leagueNames.get(String(leagueId));
  const cornerParts = [
    corners85 !== null ? `8.5: ${percent(corners85)}` : null,
    corners95 !== null ? `9.5: ${percent(corners95)}` : null
  ].filter(Boolean);
  const cardParts = [
    cards25 !== null ? `ТБ 2.5: ${percent(cards25)}` : null,
    cards35 !== null ? `ТБ 3.5: ${percent(cards35)}` : null
  ].filter(Boolean);
  return {
    source: 'SStats',
    eventId: first(pick(game, 'Id', 'GameId', 'gameId', 'id')),
    time: dateTime,
    league,
    home: teamName(home),
    away: teamName(away),
    actualResult,
    outcome,
    probabilityOutcome,
    totalGoals: goalParts.join(' / ') || (xgHome !== null || xgAway !== null ? `xG сумма: ${(Number(xgHome || 0) + Number(xgAway || 0)).toFixed(2)}` : '—'),
    underGoals: underGoalParts.join(' / ') || '—',
    individualTotals: xgHome !== null || xgAway !== null ? `Х ${xgHome ?? '—'} / Г ${xgAway ?? '—'} xG` : '—',
    corners: cornerParts.length ? `ТБ угл. ${cornerParts.join(' / ')}` : '—',
    yellowCards: cardParts.length ? cardParts.join(' / ') : '—'
  };
}

function poissonDistribution(lambda, maxGoals = 10) {
  const mean = Math.max(0, Math.min(8, Number(lambda) || 0));
  const values = [Math.exp(-mean)];
  for (let goals = 1; goals <= maxGoals; goals++) values.push(values[goals - 1] * mean / goals);
  const sum = values.reduce((total, value) => total + value, 0);
  return values.map(value => value / sum);
}
function normalizeFootballData(match, history = []) {
  const competitionCode = first(pick(match.competition || {}, 'code'), pick(match, 'competitionCode'));
  const homeTeam = match.homeTeam || {};
  const awayTeam = match.awayTeam || {};
  const homeId = first(pick(homeTeam, 'id'));
  const awayId = first(pick(awayTeam, 'id'));
  const usable = history.filter(item =>
    item.status === 'FINISHED' &&
    item.score?.fullTime?.home !== null && item.score?.fullTime?.home !== undefined &&
    item.score?.fullTime?.away !== null && item.score?.fullTime?.away !== undefined &&
    (!competitionCode || pick(item.competition || {}, 'code') === competitionCode)
  );
  const homePlayedHome = usable.filter(item => String(item.homeTeam?.id) === String(homeId));
  const homePlayedAway = usable.filter(item => String(item.awayTeam?.id) === String(homeId));
  const awayPlayedHome = usable.filter(item => String(item.homeTeam?.id) === String(awayId));
  const awayPlayedAway = usable.filter(item => String(item.awayTeam?.id) === String(awayId));
  const avg = (items, selector) => items.length
    ? items.reduce((total, item) => total + Number(selector(item)), 0) / items.length
    : null;
  const homeAttack = avg(homePlayedHome, item => item.score.fullTime.home);
  const homeDefense = avg(homePlayedHome, item => item.score.fullTime.away);
  const awayAttack = avg(awayPlayedAway, item => item.score.fullTime.away);
  const awayDefense = avg(awayPlayedAway, item => item.score.fullTime.home);
  const fallbackHome = avg([...homePlayedHome, ...homePlayedAway], item =>
    String(item.homeTeam?.id) === String(homeId) ? item.score.fullTime.home : item.score.fullTime.away);
  const fallbackHomeConceded = avg([...homePlayedHome, ...homePlayedAway], item =>
    String(item.homeTeam?.id) === String(homeId) ? item.score.fullTime.away : item.score.fullTime.home);
  const fallbackAway = avg([...awayPlayedHome, ...awayPlayedAway], item =>
    String(item.homeTeam?.id) === String(awayId) ? item.score.fullTime.home : item.score.fullTime.away);
  const fallbackAwayConceded = avg([...awayPlayedHome, ...awayPlayedAway], item =>
    String(item.homeTeam?.id) === String(awayId) ? item.score.fullTime.away : item.score.fullTime.home);
  const lambdaHome = Math.max(0.15, Math.min(4.5, ((homeAttack ?? fallbackHome ?? 1.25) + (awayDefense ?? fallbackAwayConceded ?? 1.25)) / 2));
  const lambdaAway = Math.max(0.15, Math.min(4.5, ((awayAttack ?? fallbackAway ?? 1.0) + (homeDefense ?? fallbackHomeConceded ?? 1.0)) / 2));
  const homeDist = poissonDistribution(lambdaHome);
  const awayDist = poissonDistribution(lambdaAway);
  let pHome = 0, pDraw = 0, pAway = 0;
  for (let h = 0; h < homeDist.length; h++) {
    for (let a = 0; a < awayDist.length; a++) {
      const probability = homeDist[h] * awayDist[a];
      if (h > a) pHome += probability;
      else if (h === a) pDraw += probability;
      else pAway += probability;
    }
  }
  const totalLambda = lambdaHome + lambdaAway;
  const probabilityOutcome = `П1 ${percent(pHome)} / X ${percent(pDraw)} / П2 ${percent(pAway)}`;
  const over15 = poissonOver(totalLambda, 1);
  const over25 = poissonOver(totalLambda, 2);
  const over35 = poissonOver(totalLambda, 3);
  const underParts = [
    `ТМ 1.5: ${percent(1 - over15)}`,
    `ТМ 2.5: ${percent(1 - over25)}`,
    `ТМ 3.5: ${percent(1 - over35)}`
  ];
  const scores = match.score?.fullTime || {};
  const hasScore = scores.home !== null && scores.home !== undefined && scores.away !== null && scores.away !== undefined;
  const status = String(match.status || '').toUpperCase();
  const actualResult = hasScore
    ? `${status === 'FINISHED' ? 'ФТ · ' : ''}${scores.home}:${scores.away} · ${Number(scores.home) > Number(scores.away) ? 'П1' : Number(scores.home) < Number(scores.away) ? 'П2' : 'X'} · голов: ${Number(scores.home) + Number(scores.away)}`
    : status === 'IN_PLAY' || status === 'PAUSED' ? 'Матч идёт' : '—';
  return {
    source: 'FB_DATA',
    eventId: first(pick(match, 'id')),
    time: match.utcDate || null,
    league: first(pick(match.competition || {}, 'name'), '—'),
    home: first(pick(homeTeam, 'shortName', 'name'), '—'),
    away: first(pick(awayTeam, 'shortName', 'name'), '—'),
    actualResult,
    outcome: [['П1', pHome], ['X', pDraw], ['П2', pAway]].sort((a, b) => b[1] - a[1])[0][0],
    probabilityOutcome,
    totalGoals: `ТБ 1.5: ${percent(over15)} / ТБ 2.5: ${percent(over25)} / ТБ 3.5: ${percent(over35)}`,
    underGoals: underParts.join(' / '),
    individualTotals: `Х ${lambdaHome.toFixed(2)} / Г ${lambdaAway.toFixed(2)} xG (модель)`,
    corners: '—',
    yellowCards: '—'
  };
}

function normalizeApiFootball(item) {
  const fixture = item.fixture || {};
  const teams = item.teams || {};
  const league = item.league || {};
  const prediction = item.predictions || {};
  const percentValues = prediction.percent || {};
  const homeProb = percent(percentValues.home);
  const drawProb = percent(percentValues.draw);
  const awayProb = percent(percentValues.away);
  const hasProb = [percentValues.home, percentValues.draw, percentValues.away].some(value => value !== undefined && value !== null && Number.isFinite(Number(String(value).replace('%', ''))));
  const outcomeValues = [['П1', Number(String(percentValues.home ?? '').replace('%', '')) || 0], ['X', Number(String(percentValues.draw ?? '').replace('%', '')) || 0], ['П2', Number(String(percentValues.away ?? '').replace('%', '')) || 0]];
  const winner = prediction.winner?.name || prediction.advice || (hasProb ? outcomeValues.sort((a, b) => b[1] - a[1])[0][0] : '—');
  const status = String(fixture.status?.short || '').toUpperCase();
  const goals = item.goals || {};
  const hasScore = goals.home !== null && goals.home !== undefined && goals.away !== null && goals.away !== undefined;
  const actualResult = hasScore ? (['FT', 'AET', 'PEN'].includes(status) ? 'ФТ · ' : ['1H', '2H', 'HT', 'ET', 'LIVE'].includes(status) ? 'LIVE · ' : '') + goals.home + ':' + goals.away : ['1H', '2H', 'HT', 'ET', 'LIVE'].includes(status) ? 'Матч идёт' : '—';
  return {
    source: 'Api-Football', eventId: fixture.id ?? null, time: fixture.date || null,
    league: league.name || '—', home: teams.home?.name || '—', away: teams.away?.name || '—',
    actualResult, outcome: winner,
    probabilityOutcome: hasProb ? 'П1 ' + (homeProb ?? '—') + ' / X ' + (drawProb ?? '—') + ' / П2 ' + (awayProb ?? '—') : '—',
    totalGoals: prediction.under_over ? 'Тотал: ' + prediction.under_over : '—',
    underGoals: '—',
    individualTotals: prediction.goals ? 'Х ' + (prediction.goals.home ?? '—') + ' / Г ' + (prediction.goals.away ?? '—') : '—',
    corners: '—', yellowCards: '—'
  };
}
async function fetchApiFootball() {
  const token = process.env.API_FOOTBALL_KEY || '';
  if (!token) throw new Error('Не задан API_FOOTBALL_KEY в GitHub Actions Secrets');
  let previous = null;
  try { previous = JSON.parse(fs.readFileSync('data.json', 'utf8')); } catch { /* first run */ }
  const cache = previous?.apiFootballCache;
  if (cache?.date === date && Array.isArray(cache.rows)) {
    apiFootballCache = cache;
    sourceStatus.apifootball = { ok: Boolean(cache.ok), count: cache.rows.length, message: cache.message || '', diagnostic: 'Api-Football: использован дневной кэш; ' + (cache.diagnostic || 'повторных запросов нет') };
    if (!cache.ok) throw new Error(cache.message || 'Api-Football: дневной кэш содержит ошибку');
    return cache.rows;
  }
  const base = 'https://v3.football.api-sports.io';
  const headers = { 'x-apisports-key': token, Accept: 'application/json' };
  const payload = await getJson(base + '/fixtures?date=' + encodeURIComponent(date) + '&timezone=Europe%2FMoscow', headers, 10000);
  if (payload.errors && Object.keys(payload.errors).length) throw new Error('Api-Football: ' + JSON.stringify(payload.errors).slice(0, 180));
  const fixtures = arr(payload).filter(item => item.fixture?.id && ['NS', 'TBD'].includes(String(item.fixture?.status?.short || 'NS').toUpperCase()));
  const maxPredictions = 20;
  const selected = fixtures.slice(0, maxPredictions);
  const rows = [];
  let requestsMade = 1;
  const errorsFound = [];
  for (const fixture of selected) {
    try {
      const result = await getJson(base + '/predictions?fixture=' + encodeURIComponent(fixture.fixture.id), headers, 8000);
      requestsMade++;
      if (result.errors && Object.keys(result.errors).length) { errorsFound.push('fixture ' + fixture.fixture.id + ': ' + JSON.stringify(result.errors).slice(0, 80)); continue; }
      const prediction = arr(result)[0];
      if (prediction) rows.push(normalizeApiFootball(prediction));
    } catch (error) {
      requestsMade++;
      errorsFound.push('fixture ' + fixture.fixture.id + ': ' + safeError(error));
    }
  }
  const diagnostic = 'расписание: ' + fixtures.length + '; прогнозы запрошены для ' + selected.length + ' матчей; запросов в этом запуске: не более ' + requestsMade + (fixtures.length > selected.length ? '; не обработано матчей: ' + (fixtures.length - selected.length) : '') + (errorsFound.length ? '; ошибки: ' + errorsFound.slice(0, 3).join('; ') : '');
  apiFootballCache = { date, rows, ok: true, diagnostic, message: '', requestsMade };
  sourceStatus.apifootball.diagnostic = diagnostic;
  return rows;
}

async function fetchFootballData() {
  const token = process.env.FD_DATA_TOKEN || '';
  if (!token) throw new Error('Не задан FD_DATA_TOKEN в GitHub Actions Secrets');
  const today = new Date().toISOString().slice(0, 10);
  const historyFrom = new Date(Date.now() - 120 * 86400000).toISOString().slice(0, 10);
  const headers = { 'X-Auth-Token': token, Accept: 'application/json' };
  // football-data.org treats dateTo as exclusive, so use tomorrow to include today's fixtures.
  const tomorrow = new Date(Date.parse(`${today}T00:00:00Z`) + 86400000).toISOString().slice(0, 10);
  const todayPayload = await getJson(`https://api.football-data.org/v4/matches?dateFrom=${today}&dateTo=${tomorrow}`, headers, 6000);
  const history = [];
  const historyErrors = [];
  // Football-data.org rejects match queries spanning more than 10 days.
  // Split the 120-day lookback into non-overlapping 10-calendar-day windows.
  const asDate = value => new Date(`${value}T00:00:00Z`);
  const dayKey = value => value.toISOString().slice(0, 10);
  let cursor = asDate(historyFrom);
  const finalDay = asDate(today);
  let requestsMade = 0;
  while (cursor <= finalDay) {
    // dateTo is exclusive: query up to (but not including) the next day.
    const windowEndExclusive = new Date(Math.min(finalDay.getTime() + 86400000, cursor.getTime() + 10 * 86400000));
    const from = dayKey(cursor);
    const to = dayKey(windowEndExclusive);
    let loaded = false;
    let lastError = null;
    // Retry one slow/transient window once; the API occasionally times out on busy date ranges.
    for (let attempt = 0; attempt < 2 && !loaded; attempt += 1) {
      if (requestsMade > 0) {
        // Keep every request, including retries, under the common free-tier rate limit.
        await new Promise(resolve => setTimeout(resolve, 6500));
      }
      requestsMade += 1;
      try {
        const timeoutMs = attempt === 0 ? 9000 : 15000;
        const payload = await getJson(`https://api.football-data.org/v4/matches?dateFrom=${from}&dateTo=${to}&status=FINISHED&limit=500`, headers, timeoutMs);
        history.push(...arr(payload));
        loaded = true;
      } catch (error) {
        lastError = error;
      }
    }
    if (!loaded) historyErrors.push(`${from}—${to}: ${safeError(lastError)} (после повторного запроса)`);
    cursor = windowEndExclusive;
  }
  const matches = arr(todayPayload);
  const todayMatches = matches.filter(match => match.utcDate && dateKey(match.utcDate) === today);
  const historyError = historyErrors.length ? historyErrors.slice(0, 3).join('; ') : '';
  sourceStatus.fbdata.diagnostic = `Football-data.org: расписание ${todayMatches.length}, исторических матчей получено ${history.length} за ${requestsMade} периодов; вероятности рассчитаны по Пуассону на основе результатов за 120 дней${historyError ? `; ошибки истории: ${historyError}; использованы доступные результаты и базовые средние при необходимости` : ''}`;
  return todayMatches.map(match => normalizeFootballData(match, history));
}

async function fetchBSD() {
  const token = process.env.BSD_TOKEN || '';
  if (!token) throw new Error('Не задан BSD_TOKEN в GitHub Actions Secrets');
  const headers = { Authorization: `Token ${token}`, Accept: 'application/json' };
  const params = new URLSearchParams({ date_from: date, date_to: date, limit: '200', offset: '0' });
  const [eventsResult, predictionsResult, leaguesResult] = await Promise.allSettled([
    getJson(`${BSD_BASE}/events/?${params}`, headers, 4500),
    getJson(`${BSD_BASE}/predictions/?${params}`, headers, 4500),
    getJson(`${BSD_BASE}/leagues/?limit=200&offset=0`, headers, 3500)
  ]);
  if (eventsResult.status === 'rejected' && predictionsResult.status === 'rejected') {
    throw new Error(`events: ${safeError(eventsResult.reason)}; predictions: ${safeError(predictionsResult.reason)}`);
  }
  const events = eventsResult.status === 'fulfilled' ? arr(eventsResult.value) : [];
  const predictions = predictionsResult.status === 'fulfilled' ? arr(predictionsResult.value) : [];
  const leagueNames = new Map();
  if (leaguesResult.status === 'fulfilled') {
    for (const league of arr(leaguesResult.value)) {
      const id = pick(league, 'id', 'leagueId', 'competitionId', 'tournamentId');
      const name = pick(league, 'name', 'leagueName', 'competitionName', 'title', 'displayName');
      if (id !== null && name !== null) leagueNames.set(String(id), String(name));
    }
  }
  const predictionByEvent = new Map();
  const predictionByTeams = new Map();
  const nameKey = value => String(teamName(value) || '')
    .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .toLocaleLowerCase('en').replace(/[^a-z0-9]+/g, ' ').trim();
  const eventTeamsKey = event => {
    const home = first(pick(event, 'home_team', 'homeTeam', 'home', 'HomeTeam', 'homeTeamName'), pick(event?.event || {}, 'home_team', 'homeTeam', 'home'));
    const away = first(pick(event, 'away_team', 'awayTeam', 'away', 'AwayTeam', 'awayTeamName'), pick(event?.event || {}, 'away_team', 'awayTeam', 'away'));
    return home && away ? `${nameKey(home)}|${nameKey(away)}` : null;
  };
  for (const prediction of predictions) {
    const nested = first(prediction.event, prediction.match, prediction.fixture, {}) || {};
    const eventId = first(pick(prediction, 'event_id', 'eventId', 'match_id', 'matchId'), pick(nested, 'id', 'eventId', 'matchId'));
    if (eventId !== null) predictionByEvent.set(String(eventId), prediction);
    const teamsKey = eventTeamsKey(nested.home_team || nested.homeTeam ? nested : prediction);
    if (teamsKey) predictionByTeams.set(teamsKey, prediction);
  }
  if (events.length) {
    const matchedPredictions = events.map(event => {
      const id = first(pick(event, 'id', 'event_id', 'eventId', 'matchId'));
      return predictionByEvent.get(String(id)) || predictionByTeams.get(eventTeamsKey(event)) || null;
    });
    const missingUpcoming = [];
    events.forEach((event, index) => {
      const status = String(first(pick(event, 'status', 'matchStatus', 'match_status'), '')).toLowerCase();
      const kickoff = first(pick(event, 'event_date', 'start_time', 'kickoff', 'date', 'dateTime', 'matchDate'));
      const isFuture = kickoff && Date.parse(kickoff) > Date.now();
      const upcoming = !status || ['upcoming', 'scheduled', 'not_started', 'not started', 'prematch', 'pre-match', 'ns'].includes(status);
      if (!matchedPredictions[index] && kickoff && dateKey(kickoff) === date) {
        missingUpcoming.push({ event, index });
      }
    });
    // Query the documented per-event endpoint for missing forecasts too; /predictions/ can be empty while events exist.
    // Bound concurrency to avoid rate spikes and keep the whole refresh within the GitHub Actions job time limit.
    let cursor = 0;
    const workers = Array.from({ length: 18 }, async () => {
      while (cursor < missingUpcoming.length) {
        const item = missingUpcoming[cursor++];
        await fetchEventPrediction(item);
      }
    });
    async function fetchEventPrediction({ event, index }) {
      const id = pick(event, 'id', 'event_id', 'eventId', 'matchId');
      if (id === null) return;
      try {
        const payload = await getJson(`${BSD_BASE}/events/${encodeURIComponent(id)}/prediction/`, headers, 2500);
        const candidate = first(payload?.prediction, payload?.data?.prediction, payload?.data, payload);
        const unwrapped = first(candidate?.prediction, candidate?.data?.prediction, candidate?.data, candidate) || candidate;
        const hasProbabilityFields = pick(unwrapped, 'prob_home', 'prob_home_win', 'home_win_prob', 'home_win_probability', 'prob_draw', 'draw_prob', 'draw_probability', 'prob_away', 'prob_away_win', 'away_win_prob', 'away_win_probability', 'predicted_result', 'expected_home_goals') !== null;
        if (unwrapped && (unwrapped.markets || unwrapped.Markets || unwrapped.predictions || unwrapped.Predictions || unwrapped.recommendations || hasProbabilityFields)) {
          matchedPredictions[index] = unwrapped;
        }
      } catch { /* Some events do not have a prediction yet. */ }
    }
    await Promise.all(workers);
    const matched = matchedPredictions.filter(Boolean).length;
    const rows = events.map((event, index) => normalizeBsd(event, matchedPredictions[index], leagueNames));
    sourceStatus.bsd.diagnostic = `Прогнозы BSD сопоставлены: ${matched} из ${events.length} матчей`;
    if (predictionsResult.status === 'rejected') {
      sourceStatus.bsd.message = `Расписание получено, прогнозы BSD недоступны: ${safeError(predictionsResult.reason)}`;
    } else if (predictions.length && matched === 0) {
      sourceStatus.bsd.message = `Получено ${events.length} матчей и ${predictions.length} прогнозов BSD, но сопоставить прогнозы не удалось`;
    } else if (matched === 0) {
      sourceStatus.bsd.message = `BSD вернул расписание (${events.length} матчей), но не вернул распознаваемые прогнозы; проверьте ответ /predictions/ и права токена`;
    }
    return rows;
  }
  return predictions.map(prediction => {
    const event = first(prediction.event, prediction.match, prediction.fixture, prediction) || prediction;
    return normalizeBsd(event, prediction, leagueNames);
  });
}
async function fetchSStats() {
  const token = process.env.SSTATS_TOKEN || '';
  if (!token) throw new Error('Не задан SSTATS_TOKEN в GitHub Actions Secrets');
  const apiKey = `apikey=${encodeURIComponent(token)}`;
  // Avoid a full-year scan: it can time out on SStats. Try the exact date range,
  // then the documented POST /games/query endpoint with a bounded date condition.
  // Fetch league metadata concurrently to reduce total serverless execution time.
  const leaguesPromise = getJson(`${SSTATS_BASE}/leagues?${apiKey}`, {}, 1800).catch(() => null);
  const querySpecs = [
    // Try the documented date filters separately: API deployments can differ in how From/To are interpreted.
    { label: 'From/To', method: 'GET', query: { From: date, To: date, Order: '-1', Limit: '300', Offset: '0' }, timeoutMs: 4500 },
    { label: 'Date', method: 'GET', query: { Date: date, Order: '-1', Limit: '300', Offset: '0' }, timeoutMs: 4500 },
    { label: 'Today', method: 'GET', query: { Today: 'true', Order: '-1', Limit: '300', Offset: '0' }, timeoutMs: 4500 },
    {
      label: 'Games/query date condition',
      method: 'POST',
      timeoutMs: 5500,
      body: {
        condition: `Date >= '${date}' AND Date < '${new Date(Date.parse(date + 'T00:00:00Z') + 86400000).toISOString().slice(0, 10)}'`,
        // HomeScore/AwayScore are not valid fields in the current SStats query schema; request only documented match fields.
        fields: ['Id', 'Date', 'HomeTeamName', 'AwayTeamName', 'LeagueId', 'Status'],
        format: 'json',
        timezone: 0,
        order: 'Date',
        offset: 0,
        limit: 300
      }
    }
  ];
  const attempts = await Promise.all(querySpecs.map(async spec => {
    try {
      const queryParams = new URLSearchParams({ ...(spec.query || {}), apikey: token });
      const url = SSTATS_BASE + (spec.method === 'POST' ? '/games/query?' : '/games/list?') + queryParams.toString();
      const options = spec.method === 'POST'
        ? { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify(spec.body) }
        : {};
      const payload = await getJson(url, {}, spec.timeoutMs, options);
      const rows = arr(payload);
      const matching = rows.filter(game => {
        const raw = pick(game, 'Date', 'DateTime', 'eventDate', 'StartTime', 'StartDate', 'StartDateTime', 'GameDate', 'GameDateTime', 'UtcDate', 'DateUtc', 'DateLocal', 'Kickoff', 'KickoffTime', 'StartTimeUtc', 'MatchDate', 'gameDate', 'start', 'timestamp', 'date_start');
        return raw !== null && dateKey(raw) === date;
      });
      const envelope = payload && typeof payload === 'object' && !Array.isArray(payload) ? payload : {};
      return {
        label: spec.label,
        payload,
        rows,
        matching,
        count: first(pick(envelope, 'count', 'totalCount', 'TotalCount'), rows.length),
        status: pick(envelope, 'status'),
        message: pick(envelope, 'message'),
        keys: Object.keys(envelope).slice(0, 12)
      };
    } catch (error) {
      return { label: spec.label, error: safeError(error), rows: [], matching: [] };
    }
  }));

  const chosen = attempts.find(item => item.matching && item.matching.length)
    || attempts.find(item => item.rows && item.rows.length)
    || attempts.find(item => item.payload);
  const gamesPayload = chosen ? chosen.payload : null;
  const attemptSummary = attempts.map(item => item.error
    ? item.label + ': timeout/ошибка ' + item.error
    : item.label + ': status=' + (item.status ?? '—') + ', count=' + item.count + ', строк=' + item.rows.length + ', совпало по дате=' + item.matching.length + (item.message ? ', message=' + String(item.message).slice(0, 100) : '') + ', ключи=' + (item.keys.join(',') || 'массив/не объект')).join('; ');
  sourceStatus.sstats.diagnostic = 'SStats: ' + attemptSummary;
  let games = arr(gamesPayload);
  let todayGames = games.filter(game => {
    const raw = pick(game, 'Date', 'DateTime', 'eventDate', 'StartTime', 'StartDate', 'StartDateTime', 'GameDate', 'GameDateTime', 'UtcDate', 'DateUtc', 'DateLocal', 'Kickoff', 'KickoffTime', 'StartTimeUtc', 'MatchDate', 'gameDate', 'start', 'timestamp', 'date_start');
    return raw !== null && dateKey(raw) === date;
  });

  // The main /games/list endpoint can return an empty result for a valid date.
  // Fall back to the documented Flashscore-backed endpoint, which supports Date and TimeZone filters.
  let liveListDiagnostic = '';
  if (!todayGames.length) {
    try {
      const lsUrl = `${SSTATS_BASE}/Ls/List?${new URLSearchParams({ Date: date, TimeZone: '3', Limit: '1000', Offset: '0', apikey: token })}`;
      const lsPayload = await getJson(lsUrl, {}, 7500);
      const lsRows = arr(lsPayload);
      const canonicalRows = lsRows.map(item => {
        const rawDate = first(pick(item, 'Date', 'DateTime', 'StartTime', 'StartDate', 'Kickoff', 'MatchDate', 'timestamp'));
        const home = first(pick(item, 'HomeTeamName', 'homeTeamName', 'HomeTeam', 'homeTeam', 'home', 'teamHome'), pick(item.Home || item.home || {}, 'name', 'Name', 'teamName'));
        const away = first(pick(item, 'AwayTeamName', 'awayTeamName', 'AwayTeam', 'awayTeam', 'away', 'teamAway'), pick(item.Away || item.away || {}, 'name', 'Name', 'teamName'));
        const league = first(pick(item, 'LeagueName', 'leagueName', 'TournamentName', 'tournamentName', 'League', 'Tournament'), pick(item.League || item.league || item.Tournament || item.tournament || {}, 'name', 'Name', 'title'));
        return {
          ...item,
          Id: first(pick(item, 'Id', 'GameId', 'gameId', 'id', 'FlashId', 'flashId')),
          Date: rawDate,
          HomeTeamName: home,
          AwayTeamName: away,
          LeagueName: league,
          LeagueId: first(pick(item, 'LeagueId', 'leagueId', 'TournamentId', 'tournamentId'), pick(item.League || item.league || {}, 'id', 'Id')),
          Status: first(pick(item, 'Status', 'status', 'MatchStatus', 'matchStatus')),
          HomeScore: first(pick(item, 'HomeScore', 'homeScore', 'home_score'), pick(item.Home || item.home || {}, 'score', 'Score')),
          AwayScore: first(pick(item, 'AwayScore', 'awayScore', 'away_score'), pick(item.Away || item.away || {}, 'score', 'Score'))
        };
      });
      const lsToday = canonicalRows.filter(game => game.Date && dateKey(game.Date) === date);
      if (lsToday.length) {
        games = lsToday;
        todayGames = lsToday;
        liveListDiagnostic = `; fallback /Ls/List: найдено ${lsToday.length} матчей из ${lsRows.length} строк`;
      } else {
        liveListDiagnostic = `; fallback /Ls/List: строк ${lsRows.length}, совпало по дате 0, ключи первой строки ${lsRows[0] ? Object.keys(lsRows[0]).slice(0, 18).join(',') : 'нет строк'}`;
      }
    } catch (error) {
      liveListDiagnostic = `; fallback /Ls/List: ${safeError(error)}`;
    }
  }

  const leaguesPayload = await leaguesPromise;
  const leaguesResult = { status: leaguesPayload ? 'fulfilled' : 'rejected', value: leaguesPayload };
  const leagueNames = new Map();
  if (leaguesResult.status === 'fulfilled') {
    for (const league of arr(leaguesResult.value)) {
      const id = pick(league, 'Id', 'LeagueId', 'leagueId');
      const name = pick(league, 'Name', 'LeagueName', 'leagueName', 'Title', 'title');
      if (id !== null && name !== null) leagueNames.set(String(id), String(name));
    }
  }
  if (!todayGames.length) {
    const sample = games[0];
    const sampleKeys = sample && typeof sample === 'object' ? Object.keys(sample).slice(0, 24).join(',') : typeof sample;
    const sampleDate = sample && typeof sample === 'object'
      ? pick(sample, 'Date', 'DateTime', 'eventDate', 'StartTime', 'StartDate', 'StartDateTime', 'GameDate', 'GameDateTime', 'UtcDate', 'DateUtc', 'DateLocal', 'Kickoff', 'KickoffTime', 'StartTimeUtc', 'MatchDate', 'gameDate', 'start', 'timestamp', 'date_start')
      : null;
    sourceStatus.sstats.diagnostic += '; итог: строк после разбора ' + games.length + ', после фильтра даты ' + todayGames.length + ', поля первого матча: ' + (sampleKeys || 'нет') + ', дата первого матча: ' + (sampleDate ?? 'не найдена');
  }
  if (liveListDiagnostic) sourceStatus.sstats.diagnostic += liveListDiagnostic;
  // Glicko/xG is a separate documented endpoint, not part of /games/list.
  // Query it concurrently so the table gets actual model fields instead of empty placeholders.
  const glickoTargets = todayGames.slice(0, 10);
  const glickoPairs = await Promise.all(glickoTargets.map(async game => {
    const id = pick(game, 'Id', 'GameId', 'gameId', 'id');
    if (id === null) return [String(id), null];
    try {
      const result = await getJson(`${SSTATS_BASE}/games/glicko/${encodeURIComponent(id)}?apikey=${encodeURIComponent(token)}`, {}, 2600);
      return [String(id), result];
    } catch (error) {
      return [String(id), null];
    }
  }));
  const glickoById = new Map(glickoPairs);
  const rows = todayGames.map(game => {
    const id = pick(game, 'Id', 'GameId', 'gameId', 'id');
    const detail = id === null ? null : glickoById.get(String(id));
    return normalizeSstats(game, detail, leagueNames);
  });
  const enriched = glickoPairs.filter(([, value]) => value !== null).length;
  sourceStatus.sstats.diagnostic = `${sourceStatus.sstats.diagnostic ? sourceStatus.sstats.diagnostic + '; ' : ''}SStats: получено ${todayGames.length} матчей, Glicko/xG доступны для ${enriched}`;
  return rows;
}
function sourceLabel(name) {
  return ({ bsd: 'BSD', sstats: 'SStats', euro365: 'Euro365', fbdata: 'FB_DATA', apifootball: 'Api-Football', sportmonks: 'Sportmonks' })[name] || name;
}
async function runSource(name, fn) {
  try {
    const rows = await fn();
    const priorMessage = sourceStatus[name].message;
    const diagnostic = name === 'euro365' ? getEuro365Diagnostics() : (sourceStatus[name].diagnostic || '');
    sourceStatus[name] = {
      ok: true,
      count: rows.length,
      message: priorMessage || (rows.length ? '' : [`API ответил, но матчи за ${date} не найдены или формат ответа не распознан`, diagnostic].filter(Boolean).join(' — ')),
      diagnostic
    };
    if (!rows.length) errors.push(`${sourceLabel(name)}: ${sourceStatus[name].message}`);
    else if (priorMessage) errors.push(`${sourceLabel(name)}: ${priorMessage}`);
    return rows;
  } catch (error) {
    const message = safeError(error);
    sourceStatus[name] = { ok: false, count: 0, message };
    errors.push(`${sourceLabel(name)}: ${message}`);
    return [];
  }
}
async function buildData() {
  date = new Date().toISOString().slice(0, 10);
  errors.length = 0;
  sourceStatus.bsd = { ok: false, count: 0, message: '', diagnostic: '' };
  sourceStatus.sstats = { ok: false, count: 0, message: '', diagnostic: '' };
  sourceStatus.euro365 = { ok: false, count: 0, message: '', diagnostic: '' };
  sourceStatus.fbdata = { ok: false, count: 0, message: '', diagnostic: '' };
  sourceStatus.apifootball = { ok: false, count: 0, message: '', diagnostic: '' };
  apiFootballCache = null;
  const [bsd, sstats, euro365, fbdata, apifootball] = await Promise.all([
    runSource('bsd', fetchBSD),
    runSource('sstats', fetchSStats),
    runSource('euro365', () => fetchEuro365(moscowDateKey(new Date()))),
    runSource('fbdata', fetchFootballData),
    runSource('apifootball', fetchApiFootball)
  ]);
  const teamKey = value => String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  const bsdProbabilities = new Map();
  for (const row of bsd) {
    if (row.probabilityOutcome && row.probabilityOutcome !== '—') {
      bsdProbabilities.set(`${teamKey(row.home)}|${teamKey(row.away)}`, row.probabilityOutcome);
    }
  }
  for (const row of sstats) {
    const bsdProbability = bsdProbabilities.get(`${teamKey(row.home)}|${teamKey(row.away)}`);
    if (bsdProbability) row.probabilityOutcome = bsdProbability;
  }
  const predictions = [...bsd, ...sstats, ...euro365, ...fbdata, ...apifootball]
    .filter(row => row.time && (row.source === 'Euro365' ? moscowDateKey(row.time) === moscowDateKey(new Date()) : dateKey(row.time) === date))
    .sort((a, b) => new Date(a.time) - new Date(b.time));
  return { date, generatedAt: new Date().toISOString(), sources: sourceStatus, errors, predictions, apiFootballCache };
}

if (require.main === module) {
  buildData().then(output => {
    fs.writeFileSync('data.json', JSON.stringify(output, null, 2) + '\n');
    console.log(`Generated data.json: ${output.predictions.length} rows; BSD=${output.sources.bsd.count}; SStats=${output.sources.sstats.count}`);
    if (!output.sources.bsd.ok && !output.sources.sstats.ok && !output.sources.euro365.ok && !output.sources.fbdata.ok) {
      console.warn('Both data sources failed; generated JSON contains diagnostic errors.');
    }
  }).catch(error => { console.error(error); process.exitCode = 1; });
}

module.exports = { buildData, normalizeBsd, normalizeSstats, normalizeFootballData, arr, dateKey };
