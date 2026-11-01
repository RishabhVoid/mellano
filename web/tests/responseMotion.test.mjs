import assert from 'node:assert/strict';
import test from 'node:test';
import {drapeRoute,remainingRouteSeconds,sampleRoute,validRoute,vehicleDisplayScale,smoothHeading} from '../src/responseMotion.ts';
const unit={id:'boat',type:'rescue_boat',departure_time_s:20,eta_s:100,path:[[0,0],[100,0],[100,100]],path_times_s:[0,50,100]};
test('saved path interpolation respects departure, timing, turns and final endpoint',()=>{
  assert.deepEqual(sampleRoute(unit,0).position,[0,0]);assert.equal(sampleRoute(unit,0).waiting,true);
  assert.deepEqual(sampleRoute(unit,45).position,[50,0]);assert.equal(sampleRoute(unit,45).heading,Math.PI/2);
  assert.deepEqual(sampleRoute(unit,95).position,[100,50]);assert.equal(sampleRoute(unit,95).heading,0);
  const end=sampleRoute(unit,999);assert.deepEqual(end.position,[100,100]);assert.equal(end.arrived,true);assert.equal(end.progress,1);
});
test('duplicate stationary points do not create a false heading at departure or NaN',()=>{
  const value={...unit,path:[[0,0],[0,0],[10,0]],path_times_s:[0,0,10]};const sample=sampleRoute(value,15);assert.deepEqual(sample.position,[0,0]);assert.equal(sample.heading,Math.PI/2);assert.equal(Number.isFinite(sample.heading),true);
});
test('invalid route data is rejected instead of inventing connections',()=>{
  assert.equal(validRoute(unit),true);
  for(const change of [{path_times_s:[0,100]},{path_times_s:[0,10,5]},{path:[[0,0],[NaN,0],[100,100]]},{departure_time_s:NaN}])assert.equal(validRoute({...unit,...change}),false);
});
test('screen-space visibility is bounded, independent of viewport height, and natural near the vehicle',()=>{
  const scale=vehicleDisplayScale(5.5,10,36,735),pixels=5.5*.001*scale/(2*10*Math.tan(Math.PI/10)/735);assert.ok(Math.abs(pixels-22)<1e-8);
  assert.equal(vehicleDisplayScale(5.5,.02,36,735),1);assert.equal(vehicleDisplayScale(5.5,1000,36,735),60);
  assert.ok(vehicleDisplayScale(5.5,10,36,1470)<scale);
});
test('camera turn interpolation uses the short arc across ±pi',()=>{
  const result=smoothHeading(Math.PI-.1,-Math.PI+.1,.5);assert.ok(Math.abs(result-Math.PI)<1e-8);
});

test('boat ETA counts launch access once, using saved departure plus actual path end',()=>{
  const boat={...unit,departure_time_s:120,eta_s:360,path:[[0,0],[100,0]],path_times_s:[0,240]};assert.equal(remainingRouteSeconds(boat,180),180);assert.equal(remainingRouteSeconds(boat,400),0);assert.equal(boat.eta_s,360);
});
test('one-point routes remain stationary at their real destination',()=>{
  const stationary={...unit,path:[[5,6]],path_times_s:[0]};assert.equal(validRoute(stationary),true);assert.deepEqual(sampleRoute(stationary,10).position,[5,6]);assert.equal(sampleRoute(stationary,10).arrived,false);assert.equal(sampleRoute(stationary,20).arrived,true);
});
test('surface draping retains turns, adds at most20m gaps and never shortcuts an over-budget route',()=>{
  const path=[[0,0],[60,0],[60,80]],points=drapeRoute(path);assert.equal(points.length,8);for(let i=1;i<points.length;i++)assert.ok(Math.hypot(points[i][0]-points[i-1][0],points[i][1]-points[i-1][1])<=20);assert.ok(points.some(point=>point[0]===60&&point[1]===0));
  const bounded=drapeRoute([[0,0],[0,100000],[100000,100000]],20,10);assert.equal(bounded.length,10);assert.ok(bounded.some(point=>point[0]===0&&point[1]===100000));assert.equal(drapeRoute([[0,0],[1,0],[1,1]],20,2),undefined);
});
