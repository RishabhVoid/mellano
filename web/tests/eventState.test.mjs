import assert from 'node:assert/strict';
import test from 'node:test';
import { EventPlayback, currentCachedEvents } from '../src/eventState.ts';

const run=(id,preset='flood',extra={})=>({run_id:`product_${id}`,preset_id:preset,module:preset,manifest_url:`/data/product/runs/product_${id}/manifest.json`,source_current:true,created_at:'2026-11-01',...extra});
const catalog=runs=>({modules:['flood','quake','fire','breach','chain'].map(id=>({id,enabled:true})),presets:['flood','quake','fire','breach','chain'].map(id=>({id,label:id,enabled:true,scenario:{module:id}})),runs});

test('current cache has one newest completed run per enabled preset in preset order',()=>{
  const values=currentCachedEvents(catalog([run('older'),run('latest','flood',{created_at:'2026-11-02'}),run('q','quake')]));
  assert.deepEqual(values.map(item=>item.run_id),['product_latest','product_q']);
  assert.equal(values[0].label,'flood');
});
test('stale, unknown freshness, custom and mismatched-module runs cannot be selected',()=>{
  assert.deepEqual(currentCachedEvents(catalog([run('stale','flood',{source_current:false}),run('unknown','flood',{source_current:undefined}),run('custom',null),run('wrong','flood',{module:'fire'})])),[]);
});
test('untrusted manifest locations and disabled presets are excluded',()=>{
  const value=catalog([run('bad','flood',{manifest_url:'https://example.com/manifest.json'}),run('q','quake')]);value.presets[1].enabled=false;
  assert.deepEqual(currentCachedEvents(value),[]);
});
test('readiness begins loading without fabricated time or results',()=>{
  const state=new EventPlayback();assert.equal(state.snapshot().phase,'idle');state.begin('product_a','fire');
  assert.deepEqual(state.snapshot(),{phase:'loading',run_id:'product_a',module:'fire',time_s:0,duration_s:0,progress:0,speed:0});
});
test('variable frame spacing uses normalized simulation duration, not frame count',()=>{
  const state=new EventPlayback(),ticket=state.begin('product_a','fire');state.load(ticket,[20,25,110,320]);
  for(let i=0;i<60;i++)state.advance(.25);
  assert.equal(state.snapshot().time_s,170);assert.equal(state.snapshot().progress,.5);assert.equal(state.snapshot().speed,10);
});
test('four-frame quake changes by two seconds and ends after six real seconds',()=>{
  const state=new EventPlayback(),ticket=state.begin('product_a','quake');state.load(ticket,[0,20,40,60]);
  for(let i=0;i<8;i++)state.advance(.25);assert.equal(state.snapshot().time_s,20);
  for(let i=0;i<16;i++)state.advance(.25);state.presented(ticket,60);assert.equal(state.snapshot().phase,'complete');
});
test('completion waits until the actual final frame is presented',()=>{
  const state=new EventPlayback(),ticket=state.begin('product_a','flood');state.load(ticket,[0,300]);
  for(let i=0;i<120;i++)state.advance(.25);
  assert.equal(state.snapshot().phase,'running');assert.equal(state.snapshot().progress,1);
  state.presented(ticket,0);assert.equal(state.snapshot().phase,'running');state.presented(ticket,300);assert.equal(state.snapshot().phase,'complete');
});
test('pause holds simulation time and resume continues from it',()=>{
  const state=new EventPlayback(),ticket=state.begin('product_a','fire');state.load(ticket,[0,300]);state.advance(.2);state.pause();state.advance(.2);
  assert.equal(state.snapshot().time_s,2);assert.equal(state.snapshot().phase,'paused');state.resume();state.advance(.2);assert.equal(state.snapshot().time_s,4);
});
test('background-tab gaps and invalid deltas cannot skip an event',()=>{
  const state=new EventPlayback(),ticket=state.begin('product_a','flood');state.load(ticket,[0,300]);state.advance(900);state.advance(NaN);state.advance(-3);
  assert.equal(state.snapshot().time_s,2.5);
});
test('reset invalidates an outstanding manifest and late failure',()=>{
  const state=new EventPlayback(),ticket=state.begin('product_a','fire');state.reset();
  assert.equal(state.load(ticket,[0,300]),false);state.fail(ticket,'stale network failure');assert.equal(state.snapshot().phase,'idle');
});
test('rapid selection cannot commit the older result or clear the new error',()=>{
  const state=new EventPlayback(),old=state.begin('product_a','fire'),latest=state.begin('product_b','quake');
  state.load(latest,[0,60]);state.fail(old,'old failure');state.load(old,[0,999]);assert.equal(state.snapshot().run_id,'product_b');assert.equal(state.snapshot().duration_s,60);
});
test('restart begins at frame zero and errors stop time',()=>{
  const state=new EventPlayback();let ticket=state.begin('product_a','fire');state.load(ticket,[0,30]);state.advance(.2);state.fail(ticket,'Frame unavailable');state.advance(.2);assert.equal(state.snapshot().time_s,.2);
  ticket=state.begin('product_a','fire');state.load(ticket,[0,30]);assert.equal(state.snapshot().time_s,0);assert.equal(state.snapshot().error,undefined);
});
test('malformed timelines are rejected rather than silently inventing duration',()=>{
  for(const times of [[],[0,0],[20,10],[0,NaN],[-1,10]]){const state=new EventPlayback(),ticket=state.begin('product_a','fire');assert.throws(()=>state.load(ticket,times),/invalid simulation timeline/);}
});
test('scene seek pauses without losing normalized rate and snapshots are isolated',()=>{
  const state=new EventPlayback(),ticket=state.begin('product_a','fire');state.load(ticket,[0,300]);state.seek(120);const snapshot=state.snapshot();snapshot.time_s=999;
  assert.equal(state.snapshot().phase,'paused');assert.equal(state.snapshot().time_s,120);assert.equal(state.snapshot().speed,10);
});
