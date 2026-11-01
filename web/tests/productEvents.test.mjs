import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { stripTypeScriptTypes } from 'node:module';
// Exercise actual ProductFlow without WebGL or a browser. Only scene rendering is stubbed.
const source=await readFile(new URL('../src/product.ts',import.meta.url),'utf8');
const compiled=stripTypeScriptTypes(source,{mode:'transform',sourceMap:false})
  .replace(/import \{ ResponseVisuals \} from ['"]\.\/responseVisuals['"];?/,`class ResponseVisuals { visible=false; follow={phase:'off',progress:0}; initialize(){return Promise.resolve();} setVisible(value){this.visible=value;if(!value)this.followUnit();} set(value){this.data=value;this.followUnit();} tick(value){this.time=value;} getUnits(){return (this.data?.units??[]).map(u=>({...u,label:u.type,duration_s:u.path_times_s.at(-1),progress:0,modelAvailable:true}));} followUnit(id){this.follow=id?{unitId:id,phase:'following',progress:0}:{phase:'off',progress:0};} getFollowState(){return this.follow;} refreshSurface(){} }`)
  .replace(/import \{ showToast \} from ['"]\.\/toast['"];?/,`const showToast=()=>({});`)
  .replace(/import \{ renderAdvisory[^}]*\} from ['"]\.\/advisory['"];?/,`const renderAdvisory=()=>document.createElement('div');`)
  .replace(/from (['"])\.\/eventState\1/g,`from ${JSON.stringify(new URL('../src/eventState.ts',import.meta.url).href)}`);
const {ProductFlow}=await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const modules=['flood','breach','quake','fire','chain'];
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
function fixture(){
  const elements=new Map();
  const node=()=>({textContent:'',hidden:false,disabled:false,value:'',options:[],style:{},dataset:{},children:[],classList:{toggle(){}},addEventListener(){},setAttribute(){},removeAttribute(){},append(...children){this.children.push(...children);},replaceChildren(...children){this.children=children;this.options=children;}});
  const get=id=>{if(!elements.has(id))elements.set(id,node());return elements.get(id);};
  globalThis.document={getElementById:get,querySelectorAll:()=>[],createElement:node,createTextNode:text=>({textContent:text}),body:{classList:{toggle(){}}}};
  globalThis.window={addEventListener(){}};
  globalThis.Option=class {constructor(label,value){this.textContent=label;this.value=value;}};
  get('disaster-module').options=modules.map(value=>({value}));get('disaster-module').value='flood';get('disaster-preset').value='custom';get('river-rise').value='3';get('quake-magnitude').value='6';get('wind-speed').value='3';get('speed').value='1';
  const view={ready:true,renderer:{domElement:{addEventListener(){},style:{}}},controls:{enabled:true},embankments:{visible:false},terrain:{extent_m:[100,100]},drawEdit(){},resize(){},setWater(value){this.depth=value;},setSeverity(value){this.severity=value;},setBuildingStates(value){this.states=value;},setImpactVisible(value){this.impact=value;},setFireMode(value){this.fire=value;},setQuakeMode(value){this.quake=value;},getBuildingPosition:()=>[25,35]};
  const catalog={building_index:{count:2,sha256:'fixture-order',url:'/data/buildings.json'},context_url:'/data/response_context.json',modules:modules.map(id=>({id,label:id,enabled:true,missing:[]})),presets:modules.map(id=>({id,label:id,enabled:true,scenario:{module:id}})),runs:modules.map(module=>({run_id:`product_${module}`,preset_id:module,module,manifest_url:`/data/product/runs/product_${module}/manifest.json`,source_current:true,created_at:'2026-11-01'}))};
  const manifest=module=>({schema_version:1,run_id:`product_${module}`,preset_id:module,module,label:'Hypothetical, not a forecast',hypothetical:true,scenario:{module,building_index:1},grid:{width:2,height:2,cell_size_m:40,stride:4},building_index:catalog.building_index,building_states:{count:2,stride:2},frames:[0,20,40,60].map((time_s,i)=>({time_s,...(['flood','breach'].includes(module)?{depth_url:`/data/${module}/${i}/depth.bin`}:{}),severity_url:`/data/${module}/${i}/severity.bin`,building_states_url:`/data/${module}/${i}/states.bin`,metrics_url:`/data/${module}/${i}/metrics.json`,response_url:`/data/${module}/${i}/response.json`})),labels:{bands:['None','Light','Moderate','Heavy','Collapsed']},limitations:[]});
  const response={available:true,stations:[],hotspots:[],units:[{id:'unit',type:'ambulance',departure_time_s:60,eta_s:600,path_times_s:[0,600],path:[[0,0],[10,10]]}],metrics:{units_dispatched:1,average_eta_s:600,hotspots_unreached:0,stations_cut_off:0},missing:[],limitations:[]};
  const metrics={affected_area_km2:1,roads_closed:1,people_affected:null,buildings_unknown:0,buildings_per_band:[1,0,0,0,1],population:{available:false,year:2020}};
  const requests=[];
  const defaultFetch=async(url,options={})=>{requests.push({url,method:options.method??'GET'});if(url.includes('catalog'))return Response.json(catalog);if(url.endsWith('/manifest.json'))return Response.json(manifest(url.split('/').at(-2).replace('product_','')));if(url.endsWith('depth.bin'))return new Response(Float32Array.of(0,1,2,3));if(url.endsWith('severity.bin'))return new Response(Uint8Array.of(0,1,3,4));if(url.endsWith('states.bin'))return new Response(Uint8Array.of(0,0,4,2));if(url.endsWith('metrics.json'))return Response.json(metrics);if(url.endsWith('response.json'))return Response.json(response);if(url==='/api/gemini/status')return Response.json({ready:true});throw new Error('Unexpected test request');};
  globalThis.fetch=defaultFetch;
  const product=new ProductFlow(view,async()=>{},()=>{});product.setReady({runs:{},masks:{},terrain:'fixture',stage:null});
  return {product,view,elements,requests,defaultFetch,manifest};
}

test('cached start is GET-only, runs frame zero, shows computed effects and leaves orbit free',async()=>{
  const {product,view,requests}=fixture();const events=await product.prepareEvents();assert.equal(events.length,5);
  await product.startEvent('product_fire');assert.equal(product.getEventState().phase,'running');assert.equal(product.getEventState().time_s,0);assert.equal(product.stage,2);assert.equal(view.impact,true);assert.equal(view.controls.enabled,true);assert.ok(view.states instanceof Uint8Array);assert.ok(requests.every(request=>request.method==='GET'));
  assert.deepEqual(product.getEventFocus(),{point:[25,35],radius_m:400});
});
test('a delayed earlier selection cannot overwrite a newer event',async()=>{
  const {product,defaultFetch}=fixture();await product.prepareEvents();const old=deferred();globalThis.fetch=(url,options)=>url.includes('product_fire/manifest.json')?old.promise:defaultFetch(url,options);
  const first=product.startEvent('product_fire');await product.startEvent('product_quake');old.resolve(await defaultFetch('/data/product/runs/product_fire/manifest.json'));await first;
  assert.equal(product.getEventState().run_id,'product_quake');assert.equal(product.manifest.run_id,'product_quake');assert.equal(product.stage,2);
});
test('reset during a delayed manifest prevents resurrection of water or effects',async()=>{
  const {product,view,defaultFetch}=fixture();await product.prepareEvents();const pending=deferred();globalThis.fetch=(url,options)=>url.includes('product_fire/manifest.json')?pending.promise:defaultFetch(url,options);
  const opened=product.startEvent('product_fire');product.resetEvent();pending.resolve(await defaultFetch('/data/product/runs/product_fire/manifest.json'));await opened;
  assert.equal(product.getEventState().phase,'idle');assert.equal(product.manifest,undefined);assert.equal(view.states,undefined);assert.equal(view.depth,undefined);assert.equal(view.impact,false);
});
test('event tick returns while a frame is pending; reset ignores that frame',async()=>{
  const {product,view,defaultFetch}=fixture();await product.prepareEvents();await product.startEvent('product_quake');const pending=deferred();globalThis.fetch=(url,options)=>url==='/data/quake/1/severity.bin'?pending.promise:defaultFetch(url,options);
  for(let i=0;i<8;i++)await product.tick(.25);
  assert.equal(product.getEventState().time_s,20);await product.tick(.25);assert.equal(product.getEventState().time_s,20);
  product.resetEvent();pending.resolve(new Response(Uint8Array.of(4,4,4,4)));await new Promise(resolve=>setImmediate(resolve));
  assert.equal(product.getEventState().phase,'idle');assert.equal(view.states,undefined);assert.equal(view.impact,false);
});
test('missing frame is a visible error state and never launches compute',async()=>{
  const {product,defaultFetch,requests,elements}=fixture();await product.prepareEvents();globalThis.fetch=(url,options)=>url==='/data/fire/0/states.bin'?Promise.resolve(new Response('',{status:404})):defaultFetch(url,options);
  await assert.rejects(product.startEvent('product_fire'),/frame file is unavailable/);assert.equal(product.getEventState().phase,'error');assert.match(elements.get('product-status').textContent,/unavailable/);assert.ok(requests.every(request=>request.method==='GET'));
});
test('response preview advances saved routes after hazard completion without changing metrics or hazard time',async()=>{
  const {product}=fixture();await product.prepareEvents();await product.startEvent('product_quake');
  for(let i=0;i<32;i++){await product.tick(.25);await new Promise(resolve=>setImmediate(resolve));}
  assert.equal(product.getEventState().phase,'complete');product.setEventView('response');const metrics=product.metrics,time=product.getEventState().time_s;
  await product.tick(.25);assert.ok(product.getEventState().route_preview_s>0);assert.equal(product.getEventState().time_s,time);assert.equal(product.metrics,metrics);
  product.pauseEvent();const preview=product.getEventState().route_preview_s;await product.tick(.25);assert.equal(product.getEventState().route_preview_s,preview);product.resumeEvent();await product.tick(.25);assert.ok(product.getEventState().route_preview_s>preview);
});

// Flood colour must come from the saved depth mesh, not the terrain severity heatmap.
test('flood and breach retain depth water and building exposure without terrain heatmap',async()=>{
  const {product,view}=fixture();await product.prepareEvents();
  for(const module of ['flood','breach']){
    await product.startEvent('product_fire');assert.ok(view.severity instanceof Uint8Array);
    await product.startEvent(`product_${module}`);assert.equal(view.severity,undefined);assert.deepEqual([...view.depth],[0,1,2,3]);assert.ok(view.states instanceof Uint8Array);assert.equal(view.impact,true);
  }
});


test('a selected response plan and follow survive later hazard frames without teleporting',async()=>{
  const {product}=fixture();await product.prepareEvents();await product.startEvent('product_quake');
  product.setEventView('response');const plan=product.getResponseData(),planTime=product.getResponsePlanTime();
  assert.equal(product.getResponseFollowState().unitId,'unit');
  for(let i=0;i<12;i++){await product.tick(.25);await new Promise(resolve=>setImmediate(resolve));}
  assert.ok(product.getEventState().time_s>=20);assert.notEqual(product.response,plan);
  assert.equal(product.getResponseData(),plan);assert.equal(product.getResponsePlanTime(),planTime);
  assert.ok(product.getEventState().route_preview_s>60);assert.equal(product.getResponseFollowState().unitId,'unit');
  const hazard=product.getEventState().time_s;product.followResponseUnit('unit');
  assert.equal(product.getEventState().route_preview_s,0);assert.equal(product.getEventState().time_s,hazard);
  product.setEventView('impact');assert.equal(product.getResponseFollowState().phase,'off');assert.equal(product.getResponsePlanTime(),undefined);
  assert.equal(product.getResponseData(),product.response);
});
test('response opened before units exist adopts the first real route then pins it',async()=>{
  const {product,defaultFetch}=fixture();await product.prepareEvents();
  globalThis.fetch=async(url,options)=>{if(url==='/data/quake/0/response.json'){const r=await (await defaultFetch(url,options)).json();return Response.json({...r,units:[]});}return defaultFetch(url,options);};
  await product.startEvent('product_quake');product.setEventView('response');assert.equal(product.getResponseUnits().length,0);
  for(let i=0;i<12;i++){await product.tick(.25);await new Promise(resolve=>setImmediate(resolve));}
  assert.equal(product.getResponseUnits().length,1);assert.equal(product.getResponsePlanTime(),20);assert.equal(product.getResponseFollowState().unitId,'unit');
  product.resetEvent();assert.equal(product.getResponseData(),undefined);assert.equal(product.getResponseFollowState().phase,'off');
});
test('route replay can complete with hazard paused, without changing the saved plan',async()=>{
  const {product}=fixture();await product.prepareEvents();await product.startEvent('product_quake');product.pauseEvent();
  product.setEventView('response');const hazard=product.getEventState().time_s,plan=product.getResponseData();
  for(let i=0;i<110;i++)await product.tick(.25);
  assert.equal(product.getEventState().time_s,hazard);assert.equal(product.getResponseData(),plan);
  assert.equal(product.getEventState().route_preview_playing,false);
  assert.equal(product.getEventState().route_preview_s,product.getEventState().route_preview_duration_s);
  product.replayResponse();assert.equal(product.getEventState().route_preview_s,0);assert.equal(product.getEventState().route_preview_playing,true);
  product.pauseEvent();await product.tick(.25);assert.equal(product.getEventState().route_preview_s,0);
});


test('boat launch access is counted once in the preview end time',async()=>{
  const {product,defaultFetch}=fixture();await product.prepareEvents();
  globalThis.fetch=async(url,options)=>{if(url.endsWith('response.json')){const r=await (await defaultFetch(url,options)).json();r.units[0]={...r.units[0],type:'rescue_boat',departure_time_s:120,eta_s:720,path_times_s:[0,600]};return Response.json(r);}return defaultFetch(url,options);};
  await product.startEvent('product_flood');product.pauseEvent();product.setEventView('response');
  assert.equal(product.getEventState().route_preview_duration_s,720);
  for(let i=0;i<101;i++)await product.tick(.25);
  assert.equal(product.getEventState().route_preview_s,720);assert.equal(product.getEventState().route_preview_playing,false);
});
