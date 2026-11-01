'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const m=require('../goals-model.js');

test('league aliases resolve canonical and common feed names',()=>{
 const c={leagueAliases:{'england - premier league':'E0','premier league':'E0'},leagueNames:{E0:'England - Premier League'}};
 assert.equal(m.resolveLeague('England - Premier League',c),'E0');
 assert.equal(m.resolveLeague('Premier League',c),'E0');
 assert.equal(m.resolveLeague('Unknown League',c),null);
});
test('team matching is normalized but refuses ambiguous matches',()=>{
 assert.equal(m.findTeam({Milan:{name:'Milan'},Inter:{name:'Inter'}},'AC Milan').name,'Milan');
 assert.equal(m.findTeam({'Den Haag':{name:'Den Haag'},Zwolle:{name:'Zwolle'}},'ADO Den Haag').name,'Den Haag');
 assert.equal(m.findTeam({'Sheffield Weds':{name:'Sheffield Weds'}},'Sheffield Wednesday').name,'Sheffield Weds');
 assert.equal(m.findTeam({A:{name:'United FC'},B:{name:'United'}},'United'),null);
});
test('Poisson probabilities sum to one and outcome markets are coherent',()=>{
 const d=m.scoreDistribution(1.55,1.1);
 assert.ok(Math.abs(d.home+d.draw+d.away-1)<1e-10);
 assert.ok(d.btts>=0 && d.btts<=1);
 assert.ok(Math.abs(d.home+d.draw-(1-d.away))<1e-10);
});
test('totals and Asian handicap include pushes correctly',()=>{
 const d=m.scoreDistribution(1.4,1.2);
 for(const line of [0.5,1.5,2.5,3.5]) {
   const over=m.jointTotalOver(d,line);
   assert.ok(over>=0 && over<=1);
 }
 const level=m.asianOutcome(d,0);
 assert.ok(Math.abs(level.win+level.push+level.lose-1)<1e-10);
 assert.ok(level.push>0);
 const half=m.asianOutcome(d,-0.5);
 assert.equal(half.push,0);
});
test('features map team and league stats into model feature names',()=>{
 const f=m.buildFeatures({stats:{goal_team_for_avg5:1.8}},{stats:{goal_team_for_avg5:1.1}},{league_goals_avg:2.7});
 assert.equal(f.home_goal_team_for_avg5,1.8);
 assert.equal(f.away_goal_team_for_avg5,1.1);
 assert.equal(f.league_goals_avg,2.7);
 assert.equal(f.home_advantage,1);
});
