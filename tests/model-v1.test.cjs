const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const root = process.cwd();
const source = fs.readFileSync(root + '/model-v1.js', 'utf8');
const html = fs.readFileSync(root + '/model-v1.html', 'utf8');
const policy = JSON.parse(fs.readFileSync(root + '/data/corners_cards_distribution_v1.json', 'utf8'));
const models = JSON.parse(fs.readFileSync(root + '/data/corners_cards_models.json', 'utf8'));
const context = { document: { addEventListener() {} }, console, Math, Number, Date, Map, Set };
vm.createContext(context);
vm.runInContext(source + ';globalThis.__modelV1Test = { countOver, formatTotals, makeModelRows, configure: (models, config) => { productionModels = models; distributionConfig = config; } };', context, { filename: 'model-v1.js' });
test('page contains controls and loads v1 script', () => {
 for (const id of ['market-type','market-source','market-search','markets-body','markets-error','markets-empty','markets-count','markets-date','refresh-btn']) assert.ok(html.includes('id="'+id+'"'));
 assert.ok(html.includes('<script src="model-v1.js"></script>'));
 assert.ok(html.includes('<option value="all">Внутренняя модель v1</option>'));
});
test('hybrid policy covers the validated lines with the selected distributions', () => {
 assert.deepEqual(Object.keys(policy.markets.corners.lines), ['7.5','8.5','9.5','10.5','11.5','12.5']);
 assert.deepEqual(Object.keys(policy.markets.yellowCards.lines), ['0.5','1.5','2.5','3.5','4.5','5.5']);
 assert.deepEqual(Object.values(policy.markets.corners.lines), ['negativeBinomial','negativeBinomial','negativeBinomial','poisson','poisson','poisson']);
 assert.deepEqual(Object.values(policy.markets.yellowCards.lines), ['negativeBinomial','poisson','poisson','poisson','negativeBinomial','negativeBinomial']);
 for (const market of Object.values(policy.markets)) assert.ok(Number.isFinite(market.alpha) && market.alpha > 0);
});
test('Poisson and NB2 probabilities stay bounded and decline at higher lines', () => {
 for (const distribution of ['poisson','negativeBinomial']) {
  const values=[7.5,8.5,9.5,10.5,11.5,12.5].map(line=>context.__modelV1Test.countOver(10,line,distribution,policy.markets.corners.alpha));
  for (const p of values) assert.ok(p>=0 && p<=1);
  for(let i=1;i<values.length;i++) assert.ok(values[i]<=values[i-1]);
 }
});
test('NB2 and Poisson produce distinct probabilities when dispersion is positive', () => {
 const a=context.__modelV1Test.countOver(10,7.5,'poisson',policy.markets.corners.alpha);
 const b=context.__modelV1Test.countOver(10,7.5,'negativeBinomial',policy.markets.corners.alpha);
 assert.ok(Math.abs(a-b)>1e-5);
});
test('formatted output labels each line with its selected distribution', () => {
 context.__modelV1Test.configure({lines:[7.5,8.5,9.5,10.5,11.5,12.5]},policy);
 const s=context.__modelV1Test.formatTotals(10,'corners');
 assert.ok(s.includes('(NB2)')); assert.ok(s.includes('(Poisson)'));
 assert.equal((s.match(/\(NB2\)/g)||[]).length,3);
 assert.equal((s.match(/\(Poisson\)/g)||[]).length,3);
});

test('feature model produces a hybrid v1 forecast for a supported fixture', () => {
 const teams = Object.values(models.teams.E0);
 assert.ok(teams.length >= 2);
 context.__modelV1Test.configure(models, policy);
 const rows = context.__modelV1Test.makeModelRows([{
  time: '2099-01-01T15:00:00Z',
  league: models.leagueNames.E0,
  home: teams[0].name,
  away: teams[1].name
 }]);
 assert.equal(rows.length, 1);
 assert.match(rows[0].corners, /NB2/);
 assert.match(rows[0].corners, /Poisson/);
 assert.match(rows[0].yellowCards, /NB2/);
 assert.match(rows[0].yellowCards, /Poisson/);
});
