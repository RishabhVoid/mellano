import './style.css';
import { Diorama } from './model';
import { ProductFlow } from './product';
import { AreaFlow } from './areaFlow';
import { CanvasShell } from './canvasShell';
import { initDroneProof } from './drone';
import { loadCityTexture } from './cityTexture';
import { showToast } from './toast';
import { maybeStartTour } from './tour';
import type { Available, Building, Catalog, Features, Mask, Run, SceneSpec, Terrain, SurfaceContext, BuildingSummary } from './types';

const el = <T extends HTMLElement = HTMLElement>(id: string): T => { const item = document.getElementById(id); if (!item) throw new Error(`Missing element: ${id}`); return item as T; };
const input = (id: string) => el<HTMLInputElement>(id);
const select = (id: string) => el<HTMLSelectElement>(id);
const view = new Diorama(el('viewport'));
const product = new ProductFlow(view, async id => { select('scenario').value=id;await loadRun(id); },()=>{pause();requestVersion++;runSelectionVersion++;currentRun=undefined;depthCache.clear();});
let proofOpen=false;
const params = new URLSearchParams(location.search);
let catalog: Catalog, currentRun: Run | undefined, frameIndex = 0, playing = false, timelineClock = 0, previousNow = 0;
let remoteModel = false, requestVersion = 0, runSelectionVersion = 0, currentMetrics: Record<string, unknown> | undefined;
let editKind: 'breach' | 'debris' | undefined, editPoints: number[][] = [], editFinished = false;
let sceneSpec: SceneSpec | undefined, sceneTime = 0, scenePlaying = false, recorder: MediaRecorder | undefined;
const depthCache = new Map<string, Float32Array>();
const scenes = import.meta.glob<SceneSpec>('../scenes/*.json', { eager: true, import: 'default' });
const capture = document.createElement('canvas'); capture.width = 1920; capture.height = 1080; const captureCtx = capture.getContext('2d')!;
let playbackLabel = '';
const areas = new AreaFlow(view, {
  getRunContext:()=>product.advisoryContext,
  onAreaSelected: clearSimulation,
  onResponse: clearSimulation,
  onSimulation: () => { if(remoteModel){location.assign('/?simulation=1');return;}clearSimulation();view.setAreaBounds(undefined);product.drawer(false); }
});
const shell = !params.has('scene')&&!params.has('product_run') ? new CanvasShell(view,product,{
  prepareEvent:()=>{clearSimulation();areas.leaveForEvent();},
  reset:clearSimulation,
  openArea:()=>areas.open(),
  closeArea:()=>areas.close(),
  openProof:()=>el('drone-tab').click(),
}):undefined;
function clearSimulation():void {
  pause();scenePlaying=false;requestVersion++;runSelectionVersion++;currentRun=undefined;depthCache.clear();product.deactivate();view.setWater();view.setOutline();view.setReported();view.setFreshness();view.drawEdit([],false);view.controls.enabled=true;
  editKind=undefined;editPoints=[];editFinished=false;input('split-view').checked=false;setSplit(false);metrics();playbackLabel='';
  for(const id of ['water-label','truth-label'])el(id).hidden=true;
  for(const id of ['outline','reported','freshness'])input(id).checked=false;
  input('timeline').disabled=true;el<HTMLButtonElement>('play').disabled=true;el('time-label').textContent='No current hazard measurements';el('stage-label').textContent='— m';el('interpolation').textContent='—';
  select('scenario').value='normal';updateRunButton();
}
function localAsset(url:string,base='/data/terrain.json'):string {
  const resolved=new URL(url,new URL(base,location.origin));
  if(resolved.origin!==location.origin||!resolved.pathname.startsWith('/data/')||resolved.pathname.toLowerCase().includes('.env'))throw new Error('Invalid model asset URL.');
  return resolved.pathname;
}
async function loadAreaModel():Promise<boolean> {
  const id=params.get('area');if(!id)return false;
  if(!/^area_[a-f0-9]{20}$/.test(id))throw new Error('Invalid selected area.');
  const descriptor=await json<{assets:Record<string,string>;metrics_note?:string}>(`/api/areas/${id}/descriptor`);
  const a=descriptor.assets;if(a.terrain_json_url==='/data/terrain.json')return false;
  remoteModel=true;const metadataURL=localAsset(a.terrain_json_url);const metadata=await json<Terrain>(metadataURL);
  const [heights,document,banks]=await Promise.all([binary(localAsset(a.terrain_url||metadata.binary_url,metadataURL)),json<{buildings:Building[]}>(localAsset(a.buildings_url)),a.embankments_url?json<{lines:number[][][]}>(localAsset(a.embankments_url)):Promise.resolve({lines:[]})]);
  await view.load(metadata,heights,document.buildings,banks.lines);
  if(a.context_url){const url=localAsset(a.context_url),context=await json<SurfaceContext>(url);const response=await fetch(localAsset(a.landcover_url||context.landcover_url,url));if(!response.ok)throw new Error('Model surface context unavailable.');view.setSurfaceContext(new Uint8Array(await response.arrayBuffer()),context.roads);el('river-label').textContent=context.water_label;el('river-label').hidden=!context.water_cells;}
  const known=document.buildings.filter(b=>b.height!==null).length;
  void loadCityTexture(metadata,view.renderer.capabilities.maxTextureSize).then(result=>{view.setCityTexture(result.canvas);el('imagery-label').textContent=result.label;el('imagery-label').hidden=false;}).catch(cause=>console.warn('City texture unavailable',cause));
  el('building-count').textContent=document.buildings.length.toLocaleString();el('building-sources').textContent=`OSM ${document.buildings.length.toLocaleString()} · with mapped height ${known.toLocaleString()}`;
  el('height-note').textContent=`${document.buildings.length-known} footprint heights remain unknown. Native DEM resolution: ${metadata.source.terrain.native_resolution_m} m.`;
  el('ground-note').textContent=metadata.source.terrain.attribution??'FABDEM V1-2 · CC BY-NC-SA 4.0 · native 30 m data resampled to 10 m.';
  el('area-caption').textContent=`Selected area · ${metadata.cell_size_m} m grid · ${(metadata.extent_m[0]/1000).toFixed(1)} × ${(metadata.extent_m[1]/1000).toFixed(1)} km`;
  documentBodyRemote();el('loading').hidden=true;await areas.initialize(false);await shell?.initialize(true);
  try {
    const area_km2 = (metadata.extent_m[0] * metadata.extent_m[1]) / 1e6;
    showToast({
      kind: 'area',
      title: 'New area loaded',
      summary: `${document.buildings.length.toLocaleString()} buildings across ${area_km2.toFixed(1)} km². No computed disaster runs for this area; use Simulation to open a cached Delhi scenario.`,
    }, 11000);
  } catch { /* toast is non-critical */ }
  return true;
}
function documentBodyRemote():void {
  document.body.classList.add('remote-model');el('workflow-card').hidden=true;
  const brand=document.querySelector('.product-brand small');if(brand)brand.textContent='SELECTED AREA';
  for(const id of ['scenario','split-view','breach-edit','debris-edit','run']) (el(id) as HTMLInputElement).disabled=true;
  el('data-summary').textContent='Real source terrain and OSM context. No disaster runs, population estimates or current hazard observations are supplied for this area.';
  el('data-status').textContent='MODEL READY';el('run-note').textContent='Simulation playback is available for the cached Delhi model. No Delhi output is applied here.';
}


function error(message: string): void { el('error').textContent = message; el('error').hidden = false; }
async function json<T>(url: string): Promise<T> { const response = await fetch(url, {cache:'no-store'}); if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`); return response.json(); }
async function binary(url: string): Promise<Float32Array> { const response = await fetch(url); if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`); return new Float32Array(await response.arrayBuffer()); }
async function getCatalog(): Promise<Catalog> { try { return await json<Catalog>('/api/catalog'); } catch { return json<Catalog>('/data/catalog.json'); } }
function pause(): void { product.pause();playing = false; el('play').textContent = '▶ Play'; }
function metrics(values?: Record<string, unknown>): void {
  currentMetrics = values;
  const number = (key: string) => typeof values?.[key] === 'number' && Number.isFinite(values[key]) ? values[key] as number : undefined;
  const overlap = number('iou_vs_sentinel_peak')??number('iou'); el('overlap').textContent = overlap === undefined ? '—' : `${(overlap * 100).toFixed(1)}%`;
  const area = number('flooded_area_km2'); el('flooded-area').textContent = area === undefined ? '—' : `${area.toFixed(2)} km²`;
  el('buildings-wet').textContent = number('buildings_wet')?.toLocaleString() ?? '—';
  el('roads-closed').textContent = number('roads_closed')?.toLocaleString() ?? '—';
  el('metrics-note').textContent = values?.hypothetical ? 'From metrics.json: maximum wetting outside mapped water. Roads closed means modelled exposure, not reported closures. No satellite score.' : values ? 'Loaded from this run’s metrics.json. Missing values remain empty.' : 'No metrics.json is available.';
  if(typeof values?.edit_effect_note==='string')el('metrics-note').textContent+=' '+values.edit_effect_note;
}

async function showFrame(index: number): Promise<void> {
  const run = currentRun; if (!run?.frames.length) return;
  const version = ++requestVersion; const safe = Math.max(0, Math.min(run.frames.length - 1, index)); const frame = run.frames[safe];
  let depth = depthCache.get(frame.url); if (!depth) { depth = await binary(frame.url); depthCache.set(frame.url, depth); if (depthCache.size > 8) depthCache.delete(depthCache.keys().next().value!); }
  if (version !== requestVersion || currentRun !== run) return;
  frameIndex = safe; input('timeline').value = String(safe); view.setWater(depth, run); el('water-label').textContent = run.hypothetical ? 'Simulated water · Hypothetical, not a forecast' : run.calibrated ? 'Simulated water' : 'Simulated water · uncalibrated'; el('water-label').hidden = false;
  el('time-label').textContent = frame.time_ist ? new Intl.DateTimeFormat('en-GB', {timeZone:'Asia/Kolkata',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(frame.time_ist)) + ' IST' : 'Simulation time';
  el('stage-label').textContent = `${frame.relative_stage_m !== undefined ? '+' : ''}${frame.level_m.toFixed(2)} m${frame.relative_stage_m !== undefined ? ' relative' : ''}`;
  el('interpolation').textContent = frame.relative_stage_m !== undefined ? 'hypothetical' : frame.stage_kind ?? (frame.interpolated ? 'interpolated' : 'observed stage');
  if(frame.elapsed_s!==undefined)el('time-label').textContent=`+${Math.floor(frame.elapsed_s/60)}:${String(Math.round(frame.elapsed_s%60)).padStart(2,'0')} · simulated`;
  playbackLabel = `${el('time-label').textContent} · ${el('stage-label').textContent} · ${el('interpolation').textContent}`;
}

async function loadRun(id: string): Promise<void> {
  if(product.active)product.deactivate();
  const selectionVersion = ++runSelectionVersion;
  pause(); requestVersion++; currentRun = undefined; view.setWater(); metrics(); el('water-label').hidden = true; playbackLabel = '';
  input('timeline').disabled = true; el<HTMLButtonElement>('play').disabled = true; el('time-label').textContent = 'No replay loaded'; el('stage-label').textContent = '— m'; el('interpolation').textContent = '—';
  const available = catalog.runs[id];
  if (!available?.available || !available.url) { el('run-note').textContent = id==='normal' ? 'Mapped OSM river and water bodies draped on real terrain. This outline has no flood depth or gauge stage.' : id.startsWith('relative_') ? 'This hypothetical scenario has no published playback. A server build is required; local background computation is off.' : 'Waiting for real radar masks and datum calibration for the July 2023 replay.'; updateRunButton(); return; }
  const run = await json<Run>(available.url); if (selectionVersion !== runSelectionVersion || !run.frames.length) return;
  currentRun = run; input('timeline').max = String(run.frames.length - 1); input('timeline').disabled = false; el<HTMLButtonElement>('play').disabled = false;
  el('run-note').textContent = run.hypothetical ? 'Hypothetical, not a forecast. Rise above FABDEM inside mapped OSM river; 60-second ramp. Breach/debris use the same +1 m forcing. No satellite score.' : `Simulated water · ${run.model ?? 'computed replay'}${run.calibrated ? ' · calibrated' : ' · UNCALIBRATED'}`;
  if (run.metrics_url) try { const values = await json<Record<string, unknown>>(run.metrics_url); if (selectionVersion !== runSelectionVersion) return; metrics(values); } catch { if (selectionVersion === runSelectionVersion) metrics(); }
  if (selectionVersion !== runSelectionVersion) return;
  timelineClock = 0; await showFrame(0); updateRunButton();
}

async function loadMask(): Promise<void> {
  const available = catalog.masks[select('mask-choice').value];
  if (!available?.available || !available.url) { input('outline').checked = false; view.outline.visible = false; el('truth-label').hidden = true; view.setOutline(); return; }
  const mask = await json<Mask>(available.url); view.setOutline(mask); el('truth-label').textContent = mask.label; el('truth-label').hidden = !input('outline').checked; view.outline.visible = input('outline').checked; view.dirty = true;
}

async function refreshLayers(next: Catalog): Promise<void> {
  catalog = next;
  product.setCatalog(next);
  for (const option of Array.from(select('mask-choice').options)) option.disabled = !catalog.masks[option.value]?.available;
  const first = Object.entries(catalog.masks).find(([, value]) => value.available)?.[0];
  input('outline').disabled = !first; select('mask-choice').disabled = !first;
  if (first && !catalog.masks[select('mask-choice').value]?.available) select('mask-choice').value = first;
  await loadMask();
  for (const [key, available, setter] of [['reported', catalog.reported, (data?: Features) => view.setReported(data)], ['freshness', catalog.freshness, (data?: Features) => view.setFreshness(data)]] as [string, Available | undefined, (data?: Features) => void][]) {
    input(key).disabled = !available?.available;
    if (available?.available && available.url) setter(await json<Features>(available.url));
    else { input(key).checked = false; setter(); (key === 'reported' ? view.reported : view.freshness).visible = false; }
  }
  const missing = el('missing-data'); missing.replaceChildren();
  for (const item of [...new Set([...(catalog.replay_requirements ?? []),...(catalog.dynamic_requirements ?? [])])]) { const li = document.createElement('li'); li.textContent = item; missing.append(li); }
  el('data-status').textContent = (catalog.replay_requirements?.length ?? 0) ? 'FLOOD INPUTS PENDING' : 'INPUTS AVAILABLE';
  el('data-summary').textContent = 'Terrain and buildings are cached real-source files. Local startup runs no downloads, simulations or export watchers. New data is published explicitly by the server.';
  updateRunButton();
}

el('drone-tab').addEventListener('click',()=>{proofOpen=true;el('drone-close').hidden=false;document.body.classList.add('proof-open');el('drone-proof').hidden=false;el('drone-tab').classList.add('selected');el('diorama-tab').classList.remove('selected');pause();void initDroneProof(el('drone-proof')).then(()=>{if(!document.getElementById('canvas-proof-back')){const back=document.createElement('button');back.id='canvas-proof-back';back.textContent='← Back to model';back.onclick=()=>el('diorama-tab').click();el('drone-proof').prepend(back);}});});
el('drone-close').addEventListener('click',()=>el('diorama-tab').click());
el('diorama-tab').addEventListener('click',()=>{proofOpen=false;el('drone-close').hidden=true;document.body.classList.remove('proof-open');el('drone-proof').hidden=true;el('drone-tab').classList.remove('selected');el('diorama-tab').classList.add('selected');view.resize();});

function updateRunButton(): void { el<HTMLButtonElement>('run').disabled = !editFinished || !view.ready || !!catalog?.relative_requirements?.length; }
function selectEdit(kind: 'breach' | 'debris'): void { input('split-view').checked=false; setSplit(false); editKind = kind; editPoints = []; editFinished = false; view.drawEdit([], false); view.controls.enabled = false; el('breach-edit').classList.toggle('selected', kind === 'breach'); el('debris-edit').classList.toggle('selected', kind === 'debris'); el('edit-help').textContent = kind === 'breach' ? 'Click the model to draw a line across a mapped embankment. Finish shape to restore orbit.' : 'Click the model to draw a debris polygon. Finish shape to close it and restore orbit.'; el<HTMLButtonElement>('clear-edit').disabled = false; el<HTMLButtonElement>('finish-edit').disabled = true; updateRunButton(); }
view.renderer.domElement.addEventListener('pointerdown', (event) => { if (!editKind || editFinished || event.button !== 0 || !view.ready) return; const point = view.pick(event); if (!point) return; editPoints.push(point); view.drawEdit(editPoints, false); el<HTMLButtonElement>('finish-edit').disabled = editPoints.length < (editKind === 'breach' ? 2 : 3); });
el('breach-edit').addEventListener('click', () => { input('embankments').checked = true; view.embankments.visible = true; selectEdit('breach'); });
el('debris-edit').addEventListener('click', () => selectEdit('debris'));
el('finish-edit').addEventListener('click', () => { editFinished = true; view.controls.enabled = true; view.drawEdit(editPoints, editKind === 'debris'); el('edit-help').textContent = 'Shape ready. Run uses a hypothetical +1 m rise above the mapped-river reference.'; updateRunButton(); });
el('clear-edit').addEventListener('click', () => { editKind = undefined; editPoints = []; editFinished = false; view.drawEdit([], false); view.controls.enabled = true; el('breach-edit').classList.remove('selected'); el('debris-edit').classList.remove('selected'); el<HTMLButtonElement>('finish-edit').disabled = true; updateRunButton(); });

el('run').addEventListener('click', async () => {
  try {
    el<HTMLButtonElement>('run').disabled = true; el('edit-help').textContent = 'Computing scenario…';
    const response = await fetch('/api/run', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({base_run:'mapped_river',river_plus_m:1,edits:[{type:editKind,coordinates:editPoints,height_m:Number(input('debris-height').value)}]})});
    const result = await response.json(); if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : JSON.stringify(result.detail));
    const poll = async (): Promise<void> => { const status = await json<{status:string;error?:string}>(`/api/runs/${result.run_id}/status`); if (['failed','blocked','needs_input','unavailable'].includes(status.status)) throw new Error(status.error ?? `Scenario ${status.status}; check the local job log.`); if (status.status !== 'complete') { await new Promise(resolve => setTimeout(resolve, 1000)); return poll(); } };
    await poll(); await refreshLayers(await getCatalog()); const option = new Option('Custom surface edit', result.run_id); select('scenario').add(option); select('scenario').value = result.run_id; await loadRun(result.run_id); el('edit-help').textContent = 'Computed scenario loaded.';
  } catch (cause) { error(cause instanceof Error ? cause.message : String(cause)); } finally { updateRunButton(); }
});

for (const button of document.querySelectorAll<HTMLButtonElement>('[data-camera]')) button.addEventListener('click', () => { if (!view.ready) return; document.querySelectorAll('[data-camera]').forEach(b => b.classList.remove('selected')); button.classList.add('selected'); view.preset(button.dataset.camera!); });
input('exaggeration').addEventListener('input', () => { el('exaggeration-value').textContent = `${input('exaggeration').value}×`; if (view.ready) view.setExaggeration(Number(input('exaggeration').value), input('exaggerate-buildings').checked); });
input('exaggerate-buildings').addEventListener('change', () => view.setExaggeration(Number(input('exaggeration').value), input('exaggerate-buildings').checked));
input('building-exaggeration').addEventListener('input', () => { el('building-exaggeration-value').textContent = `${input('building-exaggeration').value}×`; if (view.ready) view.setBuildingExaggeration(Number(input('building-exaggeration').value)); });
view.buildingHeightScale = Number(input('building-exaggeration').value) || 5;
view.exaggerateBuildings = input('exaggerate-buildings').checked;
input('ao').addEventListener('change', () => { view.ao = input('ao').checked; view.dirty = true; });
function setSplit(on:boolean):void { view.setSplit(on); el('split-labels').hidden=!on; document.body.classList.toggle('split-view',on); }
input('split-view').addEventListener('change',()=>setSplit(input('split-view').checked));
el('fit-camera').addEventListener('click', () => { if(!view.ready)return;document.querySelectorAll('[data-camera]').forEach(button=>button.classList.remove('selected'));view.fit(); });
for (const [key,property] of [['river','riverVisible'],['green','greenVisible']] as const) input(key).addEventListener('change',()=>{view[property]=input(key).checked;view.updateSurface();if(key==='river')el('river-label').hidden=!input(key).checked;});
for (const key of ['buildings', 'embankments', 'reported', 'freshness', 'roads'] as const) input(key).addEventListener('change', () => { view[key].visible = input(key).checked; view.dirty = true; });
input('outline').addEventListener('change', () => { view.outline.visible = input('outline').checked; el('truth-label').hidden = !input('outline').checked; view.dirty = true; });
select('mask-choice').addEventListener('change', () => void loadMask().catch(cause => error(String(cause))));
select('scenario').addEventListener('change', () => void loadRun(select('scenario').value).catch(cause => error(String(cause))));
input('timeline').addEventListener('input', () => { if(product.active){void product.scrub(Number(input('timeline').value)).catch(cause=>error(String(cause)));return;}pause(); timelineClock = Number(input('timeline').value); void showFrame(Math.round(timelineClock)).catch(cause => error(String(cause))); });
el('play').addEventListener('click', () => { if(product.active){product.togglePlay();return;}if (!currentRun) return; playing = !playing; if (frameIndex === currentRun.frames.length-1) timelineClock = 0; el('play').textContent = playing ? 'Ⅱ Pause' : '▶ Play'; });
el('collapse-panel').addEventListener('click', () => { el('panel').hidden = true; el('expand-panel').hidden = true; view.resize(); });
el('expand-panel').addEventListener('click', () => { el('panel').hidden = false; el('expand-panel').hidden = true; view.resize(); });

async function seek(seconds: number): Promise<void> {
  if (!sceneSpec || !view.ready) return;
  sceneTime = Math.max(0, Math.min(sceneSpec.duration_s, seconds));
  const keys = sceneSpec.camera; const next = keys.findIndex(k => k.time >= sceneTime); const b = keys[next < 0 ? keys.length-1 : next]; const a = keys[Math.max(0, (next < 0 ? keys.length-1 : next)-1)];
  const raw = a === b ? 0 : (sceneTime-a.time)/(b.time-a.time); const t = raw*raw*(3-2*raw); const span = Math.max(...view.terrain.extent_m)*0.001;
  const mix = (x:number[], y:number[]) => x.map((v,i)=>(v+(y[i]-v)*t)*span);
  view.cameraAt(mix(a.position,b.position),mix(a.target,b.target));
  if(product.active){const range=sceneSpec.timeline;await product.seekFraction(range[0]+(range[1]-range[0])*sceneTime/sceneSpec.duration_s);}
  if (currentRun) { const range = sceneSpec.timeline; const f = range[0]+(range[1]-range[0])*sceneTime/sceneSpec.duration_s; const index = Math.round(f*(currentRun.frames.length-1)); if (index !== frameIndex || !view.water.children.length) await showFrame(index); }
  el('caption').textContent = sceneSpec.captions.find(c => sceneTime >= c.start && sceneTime < c.end)?.text ?? '';
  view.render(); composeCapture();
}

function composeCapture(): void {
  captureCtx.fillStyle = '#1c1d20'; captureCtx.fillRect(0,0,1920,1080);
  const source = view.renderer.domElement; const factor = Math.min(1920/source.width,1080/source.height); const width = source.width*factor, height=source.height*factor;
  captureCtx.drawImage(source,(1920-width)/2,(1080-height)/2,width,height);
  captureCtx.fillStyle='#e6e1d4'; captureCtx.font='34px sans-serif'; captureLabel('otec · Delhi / Yamuna',50,60);
  if(view.split){captureCtx.strokeStyle='#8d939466';captureCtx.beginPath();captureCtx.moveTo(960,0);captureCtx.lineTo(960,1080);captureCtx.stroke();captureCtx.font='20px sans-serif';captureLabel('Mapped river · baseline',70,130);captureLabel('Simulated scenario',1010,130);}
  captureCtx.font='20px monospace'; const labels = [!el('river-label').hidden ? el('river-label').textContent! : '', !el('water-label').hidden ? el('water-label').textContent! : '', !el('truth-label').hidden ? el('truth-label').textContent! : '', !el('imagery-label').hidden ? el('imagery-label').textContent! : '', product.active?product.playbackLabel:playbackLabel].filter(Boolean);
  labels.forEach((label,i) => captureLabel(label,50,1050-(labels.length-1-i)*34));
  if(product.active&&product.metrics){const m=product.metrics;captureCtx.font='20px monospace';captureLabel(`Affected land ${m.affected_area_km2.toFixed(2)} km² · roads closed ${m.roads_closed??'unavailable'}`,50,100);if(product.stage===3&&product.response){const r=product.response.metrics;captureLabel(`Modelled response · ${r.units_dispatched} units · ${r.hotspots_unreached} hotspots unreached`,50,134);}}
  product.drawCapture(captureCtx,1920,1080);
  captureCtx.textAlign='center'; captureCtx.font='26px sans-serif'; captureLabel(el('caption').textContent??'',960,920); captureCtx.textAlign='left';
}

function captureLabel(text:string,x:number,y:number):void {
  if(!text)return;captureCtx.save();const height=Number.parseFloat(captureCtx.font)||20;const width=Math.min(1820,captureCtx.measureText(text).width);const left=captureCtx.textAlign==='center'?x-width/2:captureCtx.textAlign==='right'?x-width:x;
  captureCtx.fillStyle='#111a20e8';captureCtx.fillRect(left-12,y-height-6,width+24,height+16);
  captureCtx.fillStyle='#f2eee4';captureCtx.shadowColor='#000';captureCtx.shadowBlur=3;captureCtx.shadowOffsetY=1;captureCtx.fillText(text,x,y,1820);captureCtx.restore();
}

function startRecording(): void {
  if (!sceneSpec) throw new Error('Recording requires ?scene=<name>.');
  const mimeType = ['video/webm;codecs=vp9','video/webm;codecs=vp8','video/webm'].find(type=>MediaRecorder.isTypeSupported(type));
  if (!mimeType) throw new Error('WebM recording is unavailable in this browser.');
  const stream = capture.captureStream(30); const chunks: Blob[] = [];
  recorder = new MediaRecorder(stream,{mimeType,videoBitsPerSecond:10000000}); recorder.ondataavailable=e=>{if(e.data.size)chunks.push(e.data);};
  recorder.onstop=()=>{const url=URL.createObjectURL(new Blob(chunks,{type:mimeType}));const a=document.createElement('a');a.href=url;a.download=`${params.get('scene')}.webm`;a.click();setTimeout(()=>URL.revokeObjectURL(url),60000);stream.getTracks().forEach(track=>track.stop());};
  recorder.start();
}

async function startScene(name: string): Promise<void> {
  const specification = scenes[`../scenes/${name}.json`]; if (!specification) throw new Error(`Scene ${name} does not exist.`);
  if (!specification.product && specification.scenario !== 'terrain' && !catalog.runs[specification.scenario]?.available) throw new Error(`Scene ${name} needs a computed ${specification.scenario} run. No placeholder flood recording will be created.`);
  if (specification.layers.outline && !Object.values(catalog.masks).some(mask => mask.available)) throw new Error('This scene requires a real Sentinel-1 outline; no placeholder will be recorded.');
  sceneSpec = specification; document.body.classList.add('scene-mode'); pause(); view.controls.enabled = false; view.controls.enableDamping = false;
  if(specification.product){await product.loadPresetScene(specification.product.preset_id);product.setStage(specification.product.stage??3);}
  else if (specification.scenario !== 'terrain') { select('scenario').value = specification.scenario; await loadRun(specification.scenario); }
  for (const [layer,on] of Object.entries(specification.layers)) { const control=input(layer); control.checked=on&&!control.disabled; control.dispatchEvent(new Event('change')); }
  input('split-view').checked=!!specification.split;setSplit(!!specification.split);
  if(params.get('record')==='1')view.captureSize(1920,1080);
  await seek(0); if (params.get('record')==='1') startRecording(); scenePlaying = params.get('manual') !== '1';
}

async function tick(now: number): Promise<void> {
  const delta = previousNow ? Math.min((now-previousNow)/1000,0.1) : 0; previousNow=now;
  try {
    if (scenePlaying && sceneSpec) { await seek(sceneTime+delta); if(sceneTime>=sceneSpec.duration_s){scenePlaying=false;if(recorder?.state==='recording')recorder.stop();} }
    else { if(playing&&currentRun){timelineClock+=delta*Number(select('speed').value);const target=Math.min(currentRun.frames.length-1,Math.floor(timelineClock));if(target!==frameIndex)await showFrame(target);if(target===currentRun.frames.length-1)pause();} if(!proofOpen){if(product.active)await product.tick(delta);view.tick(now);shell?.update(now);} }
  } catch(cause){pause();scenePlaying=false;error(String(cause));}
  requestAnimationFrame(tick);
}

async function boot(): Promise<void> {
  if(await loadAreaModel())return;
  catalog = await getCatalog(); if (!catalog.terrain) throw new Error(catalog.reason ?? 'Real terrain is not available. Run make data grid export.');
  const metadata = await json<Terrain>(catalog.terrain);
  const [heights, buildings, banks] = await Promise.all([binary(metadata.binary_url),json<{buildings:Building[];summary?:BuildingSummary}>('/data/buildings.json'),json<{lines:number[][][]}>('/data/embankments.json')]);
  el('loading').querySelector('small')!.textContent = 'Preparing real terrain and distance-based building detail…';
  await view.load(metadata,heights,buildings.buildings,banks.lines);
  el('area-caption').textContent = `${metadata.cell_size_m} m grid · ${(metadata.extent_m[0]/1000).toFixed(1)} × ${(metadata.extent_m[1]/1000).toFixed(1)} km`;
  const context=await json<SurfaceContext>('/data/context.json');
  const landcoverResponse=await fetch(context.landcover_url);if(!landcoverResponse.ok)throw new Error('Mapped surface data is unavailable.');
  view.setSurfaceContext(new Uint8Array(await landcoverResponse.arrayBuffer()),context.roads);
  input('river').disabled=!context.water_cells;input('green').disabled=!context.green_cells;input('roads').disabled=!context.roads.length;
  el('river-label').textContent=context.water_label;el('river-label').hidden=!context.water_cells;
  let cityTextureState:{source:string;label:string}={source:'loading',label:'City texture loading'};
  void loadCityTexture(metadata,view.renderer.capabilities.maxTextureSize).then(result=>{view.setCityTexture(result.canvas);cityTextureState={source:result.source,label:result.label};el('imagery-label').textContent=result.label;el('imagery-label').hidden=false;}).catch(cause=>console.warn('City texture unavailable',cause));
  Object.assign(window,{otecCityTexture:()=>cityTextureState});
  const summary=buildings.summary;
  el('building-count').textContent=(summary?.total??metadata.source.osm.buildings).toLocaleString();
  el('building-sources').textContent=`OSM ${(summary?.counts.OSM??metadata.source.osm.buildings).toLocaleString()} / Open Buildings ${summary?.google_available?(summary.counts['Open Buildings']??0).toLocaleString():'pending'}`;
  if(summary?.height_known_by_footprint_source)el('building-sources').textContent+=` · with height: OSM ${summary.height_known_by_footprint_source.OSM.toLocaleString()} / Open Buildings ${summary.height_known_by_footprint_source['Open Buildings'].toLocaleString()}`;
  el('height-note').textContent = `${metadata.source.osm.height_known.toLocaleString()} buildings have mapped or levels-derived height; ${metadata.source.osm.height_missing.toLocaleString()} are shown as footprints only. Native DEM resolution: ${metadata.source.terrain.native_resolution_m} m.`;
  if(summary)el('height-note').textContent=`${summary.height_2023_count.toLocaleString()} footprints use median Google 2023 heights; ${summary.height_missing.toLocaleString()} heights remain unknown. ${summary.height_available?'':'2023 height export pending. '}Google additions require confidence ≥ 0.75 and no OSM footprint within 2 m. Native DEM resolution: ${metadata.source.terrain.native_resolution_m} m.`;
  el('ground-note').textContent=[metadata.source.terrain.attribution,metadata.source.terrain.caveat,metadata.source.terrain.copernicus_attribution].filter(Boolean).join(' ');
  await refreshLayers(catalog); await loadRun(select('scenario').value); el('loading').hidden=true; product.setReady(catalog);
  if(!params.get('scene')&&!params.get('product_run')){await areas.initialize(false);await shell?.initialize();}
  if(params.get('product_run')&&!params.get('scene')){const id=params.get('product_run')!;if(!/^[a-zA-Z0-9_-]+$/.test(id))throw new Error('Invalid product run id.');await product.refresh();await product.loadManifest(`/data/product/runs/${id}/manifest.json`);if(params.has('frame'))await product.scrub(Number(params.get('frame'))||0);product.setStage(Math.max(1,Math.min(3,Number(params.get('step'))||1)));}
  if(params.get('scene'))await startScene(params.get('scene')!);
  if(!sceneSpec&&['top','oblique','river','ito'].includes(params.get('camera')??''))view.preset(params.get('camera')!,false);
  if(params.has('check')){
    const result=await benchmark(params.get('check')==='near');
    const report={checked_at:new Date().toISOString(),mode:params.get('check'),...result,memory:view.estimateMemoryBytes(),imagery:{enabled:false,mode:'matte',reason:'Satellite textures removed by request'}};
    Object.assign(window,{otecLastCheck:report});
    const link=document.createElement('a');link.href=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}));link.download='otec-browser-check-20261101.json';link.click();setTimeout(()=>URL.revokeObjectURL(link.href),60000);
  }

}

async function benchmark(near=false): Promise<Record<string, number>> {
  if (!view.ready) throw new Error('Wait for the real model to load.');
  const originalPosition=view.camera.position.clone(), originalTarget=view.controls.target.clone(), originalRatio=view.renderer.getPixelRatio();
  const enabled=view.controls.enabled; view.controls.enabled=false; pause();
  view.renderer.setPixelRatio(1); view.renderer.setSize(1920,1080,false); view.composer.setPixelRatio(1); view.composer.setSize(1920,1080);
  view.camera.clearViewOffset(); view.camera.aspect=1920/1080; view.camera.updateProjectionMatrix();
  const span=near ? .35 :Math.max(...view.terrain.extent_m)*0.001; const center=near?originalTarget:originalTarget.clone().set(0,0,0); const intervals:number[]=[];let previous=0;
  for(let frame=0;frame<180;frame++) { const now=await new Promise<number>(resolve=>requestAnimationFrame(resolve)); if(previous)intervals.push(now-previous);previous=now;const angle=frame/180*Math.PI*0.5;view.cameraAt([center.x+Math.cos(angle)*span,center.y+span*.6,center.z+Math.sin(angle)*span+span*.5],center.toArray());view.render(); }
  view.camera.position.copy(originalPosition);view.controls.target.copy(originalTarget);view.controls.enabled=enabled;view.renderer.setPixelRatio(originalRatio);view.composer.setPixelRatio(originalRatio);view.resize();view.dirty=true;
  intervals.sort((a,b)=>a-b);const mean=intervals.reduce((a,b)=>a+b,0)/intervals.length;
  return {width:1920,height:1080,frames:intervals.length,fps:1000/mean,median_frame_ms:intervals[Math.floor(intervals.length/2)],p95_frame_ms:intervals[Math.floor(intervals.length*.95)]};
}

Object.assign(window,{otec:{seek,benchmark,render:()=>{view.render();composeCapture();},get ready(){return view.ready;},getState:()=>({event:product.getEventState(),product:{active:product.active,stage:product.stage,run:product.manifest?.run_id,metrics:product.metrics},ready:view.ready,sceneTime,frameIndex,split:view.split,run:currentRun?.run_id??null,metrics:currentMetrics??null,lod:view.getLodStats(),drawCalls:view.renderer.info.render.calls,triangles:view.renderer.info.render.triangles,meanRenderMs:view.frameTimes.reduce((a,b)=>a+b,0)/(view.frameTimes.length||1),imagery:{enabled:false,mode:'matte',reason:'Satellite textures removed by request'},gpuEstimate:view.estimateMemoryBytes(),canvas:[view.renderer.domElement.width,view.renderer.domElement.height],exaggeration:view.exaggeration,camera:view.camera.position.toArray(),target:view.controls.target.toArray(),layers:{river:view.riverVisible,green:view.greenVisible,roads:view.roads.visible,buildings:view.buildings.visible}}),captureCanvas:capture}});
requestAnimationFrame(tick);boot().then(()=>{if(!params.has('scene')&&!params.has('product_run'))maybeStartTour();}).catch(cause=>{el('loading').hidden=true;error(cause instanceof Error?cause.message:String(cause));});
