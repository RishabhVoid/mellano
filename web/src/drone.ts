import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { PLYLoader } from 'three/addons/loaders/PLYLoader.js';

type Step = { available: boolean; reason?: string; count?: number; point_count?: number; measurement?: string };
type Proof = { label: string; available: boolean; missing: string[]; run_id?: string; run_date?: string; updated_at?: string; metrics: string | null; steps: Record<string, Step>; tools: Record<string, boolean>; assets?: Record<string, string | string[]>; dense_preview?: { source_point_count:number; preview_point_count:number; sampling_stride:number; note:string }; sources?: Record<string, { acquired_at: string; vertical_datum?: string }> };
type Values = Record<string, unknown>;
const label = 'Real drone images (OpenDroneMap aukerman) and real USGS LiDAR. Delhi drone data is not public.';
const stages: [string, string][] = [['frames','Selected drone images'],['features','Computed features'],['sparse','COLMAP sparse reconstruction'],['gps_alignment','GPS alignment'],['dense','Fresh dense reconstruction'],['ndsm','Drone nDSM'],['lidar','USGS 3DEP reference'],['validation','Independent LiDAR validation']];
const element = <K extends keyof HTMLElementTagNameMap>(tag: K, text?: string, className?: string): HTMLElementTagNameMap[K] => { const node = document.createElement(tag); if (text) node.textContent = text; if (className) node.className = className; return node; };
const url = (path: string): string => `/api/drone-proof/assets/${path.split('/').map(encodeURIComponent).join('/')}`;
async function json<T>(path: string): Promise<T> { const response = await fetch(path, {cache:'no-store'}); if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json(); }
function value(values: Values, key: string, digits = 3, unit = ''): string { const number = values[key]; return typeof number === 'number' && Number.isFinite(number) ? `${number.toFixed(digits)}${unit}` : 'Unavailable'; }

export async function initDroneProof(host: HTMLElement): Promise<void> {
  if (host.dataset.initialized) return;
  host.dataset.initialized = 'true'; host.classList.add('drone-proof-content');
  const heading = element('header'); heading.append(element('h1','Drone proof'),element('p',label));
  const state = element('p','Checking actual run files…','drone-state'); state.setAttribute('role','status');
  const refresh = element('button','Refresh proof'); refresh.type = 'button'; heading.append(refresh);
  const sources = element('p',undefined,'drone-sources');
  const missing = element('ul',undefined,'drone-missing');
  const tools = element('p'); const steps = element('ol',undefined,'drone-steps');
  const media = element('section',undefined,'drone-media');
  const preview = element('section',undefined,'drone-preview');
  const metrics = element('section',undefined,'drone-metrics');
  host.append(heading,state,sources,missing,tools,steps,media,preview,metrics);
  let revision = '', busy = false, disposePreview: (() => void) | undefined;
  function emptyPreview(): void { disposePreview?.(); disposePreview = undefined; preview.replaceChildren(); }
  function previewFailure(error: unknown): void { emptyPreview(); preview.append(element('p',`Actual asset could not be loaded: ${String(error)}`)); }

  async function cloud(path: string, name: string): Promise<void> {
    emptyPreview(); preview.append(element('h2',name),element('p','Loading computed point cloud…'));
    const geometry = await new PLYLoader().loadAsync(url(path));
    emptyPreview(); preview.append(element('h2',name),element('p','Orbit, pan and zoom the computed points. Empty space has no observations.'));
    geometry.computeBoundingBox(); const center = geometry.boundingBox!.getCenter(new THREE.Vector3()); geometry.translate(-center.x,-center.y,-center.z);
    geometry.computeBoundingSphere(); const radius = Math.max(geometry.boundingSphere!.radius,1);
    const canvas = element('canvas'); canvas.style.width = '100%'; canvas.style.height = '400px'; preview.append(canvas);
    const renderer = new THREE.WebGLRenderer({canvas,antialias:true}); renderer.setPixelRatio(Math.min(devicePixelRatio,2));
    const scene = new THREE.Scene(); scene.background = new THREE.Color('#202227');
    const camera = new THREE.PerspectiveCamera(45,1,radius/10000,radius*20); camera.up.set(0,0,1); camera.position.set(radius*1.3,-radius*1.7,radius*1.3);
    const controls = new OrbitControls(camera,canvas); controls.enableDamping = true;
    const material = new THREE.PointsMaterial({size:radius/500,vertexColors:geometry.hasAttribute('color'),color:geometry.hasAttribute('color') ? 0xffffff : 0xb9c9cf});
    scene.add(new THREE.Points(geometry,material));
    const resize = new ResizeObserver(() => { const width = Math.max(canvas.clientWidth,1); renderer.setSize(width,400,false); camera.aspect = width/400; camera.updateProjectionMatrix(); }); resize.observe(canvas);
    let frame = 0; const draw = (): void => { frame = requestAnimationFrame(draw); if (!host.hidden) { controls.update(); renderer.render(scene,camera); } }; draw();
    disposePreview = () => { cancelAnimationFrame(frame); resize.disconnect(); controls.dispose(); material.dispose(); geometry.dispose(); renderer.dispose(); };
  }

  async function grid(path: string, name: string): Promise<void> {
    emptyPreview(); preview.append(element('h2',name),element('p','Loading observed height grid…'));
    const meta = await json<{ width:number; height:number; min:number; max:number; binary:string; observed_cells:number }>(url(path));
    const response = await fetch(url(meta.binary)); if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const heights = new Float32Array(await response.arrayBuffer()); if (heights.length !== meta.width*meta.height) throw new Error('Grid size does not match metadata');
    emptyPreview(); preview.append(element('h2',name),element('p',`${meta.observed_cells.toLocaleString()} observed cells · ${meta.min.toFixed(2)}–${meta.max.toFixed(2)} m · white hatch: no observation`));
    const canvas = element('canvas'); canvas.width = meta.width; canvas.height = meta.height; canvas.style.maxWidth = '100%'; canvas.style.maxHeight = '550px'; canvas.style.objectFit = 'contain';
    const context = canvas.getContext('2d')!; const pixels = context.createImageData(meta.width,meta.height);
    for (let i=0;i<heights.length;i++) { const j=i*4; if (!Number.isFinite(heights[i])) { const hatch = (Math.floor(i/meta.width)+(i%meta.width))%10<2 ? 120 : 40; pixels.data.set([hatch,hatch,hatch,255],j); continue; } const t=Math.max(0,Math.min(1,(heights[i]-meta.min)/Math.max(meta.max-meta.min,1e-6))); pixels.data.set([50+Math.round(190*t),100+Math.round(110*t),155-Math.round(100*t),255],j); }
    context.putImageData(pixels,0,0); preview.append(canvas);
  }

  function showMetrics(values?: Values): void {
    metrics.replaceChildren(element('h2','Independent validation'));
    if (!values) { metrics.append(element('p','No validated runs/drone_proof/metrics.json for the current run. Scale, rotation, NMAD, tilt and building comparison remain unavailable.')); return; }
    metrics.append(element('p','Computed values read from runs/drone_proof/metrics.json. LiDAR fitting controls and held-out evaluation regions are separate.'));
    const table = element('table'); const body = element('tbody');
    const fields: [string,string,number,string][] = [['Held-out XY RMSE','held_out_xy_rmse_m',3,' m'],['Held-out vertical RMSE','held_out_z_rmse_m',3,' m'],['Held-out vertical NMAD','held_out_z_nmad_m',3,' m'],['Surface NMAD','surface_nmad_m',3,' m'],['Surface bias','surface_bias_m',3,' m'],['Scale error','scale_error_percent',4,'%'],['Rotation error','rotation_error_deg',4,'°'],['Tilt','tilt_deg',4,'°'],['Fitted vertical offset','fitted_vertical_offset_m',3,' m'],['Held-out surface cells','surface_held_out_cells',0,''],['Source-date separation','temporal_gap_days',1,' days']];
    for (const [title,key,digits,unit] of fields) { const row=element('tr');row.append(element('th',title),element('td',value(values,key,digits,unit)));body.append(row); }
    for (const [title,key,unit] of [['Fitted XY shift','fitted_xy_shift_m',' m'],['Tilt X/Y','tilt_m_per_km',' m/km']]) { const components=values[key]; const row=element('tr'); row.append(element('th',title),element('td',Array.isArray(components) && components.every(v=>typeof v==='number' && Number.isFinite(v)) ? components.map(v=>Number(v).toFixed(3)).join(' / ')+unit : 'Unavailable'));body.append(row); }
    table.append(body); metrics.append(table,element('p',String(values.correction_policy ?? '')),element('p',String(values.vertical_offset_interpretation ?? '')));
    if (Array.isArray(values.limitations)) { const notes=element('ul'); for (const note of values.limitations) notes.append(element('li',String(note))); metrics.append(notes); }
    if (Array.isArray(values.per_building)) { const buildings=element('table'); const header=element('tr'); for(const text of ['Building','Ours median','LiDAR median','Paired cells','Coverage']) header.append(element('th',text)); buildings.append(header); for (const record of values.per_building as Values[]) { const row=element('tr'); row.append(element('td',String(record.id)),element('td',value(record,'ours_median_m',2,' m')),element('td',value(record,'lidar_median_m',2,' m')),element('td',value(record,'paired_cells',0)),element('td',value(record,'coverage_fraction',3))); buildings.append(row); } metrics.append(element('h3','Real held-out buildings · nDSM'),buildings); }
  }

  async function load(): Promise<void> {
    if (busy) return; busy = true; refresh.disabled = true;
    try {
      const proof = await json<Proof>('/api/drone-proof/status'); const next = JSON.stringify(proof);
      if (next === revision) return; revision = next; emptyPreview();
      state.textContent = proof.available ? `Fresh run with independent comparison · ${proof.run_id}` : proof.run_id ? `Fresh run incomplete · ${proof.run_id}` : 'Missing source data · no drone reconstruction has been run';
      sources.textContent = proof.run_date ? `Reconstruction started ${new Date(proof.run_date).toLocaleString('en-IN',{timeZone:'Asia/Kolkata',timeZoneName:'short'})}. Images acquired ${proof.sources?.image_collection?.acquired_at ?? proof.sources?.video?.acquired_at ?? 'date in source metadata'}. LiDAR acquired ${proof.sources?.lidar?.acquired_at ?? 'unavailable'}.` : 'Original OpenDroneMap images and matching public USGS LiDAR are being prepared. Only newly computed stages appear here.';
      missing.replaceChildren(); for (const reason of proof.missing ?? []) missing.append(element('li',reason));
      const unavailable = Object.entries(proof.tools ?? {}).filter(([,present])=>!present).map(([name])=>name); tools.textContent = unavailable.length ? `Local reconstruction tools missing: ${unavailable.join(', ')}.` : 'Local reconstruction executables detected; input and accuracy checks still apply.';
      steps.replaceChildren(); for (const [key,title] of stages) { const step=proof.steps[key]; const item=element('li'); item.dataset.available=String(Boolean(step?.available)); const count=step?.count ?? step?.point_count; item.append(element('strong',title),element('span',step?.available ? ` · computed${typeof count==='number' ? ` · ${count.toLocaleString()}` : ''}` : ` · ${step?.reason ?? 'Not computed'}`));steps.append(item); }
      media.replaceChildren(element('h2','Real run assets')); const assets=proof.assets ?? {};
      if (proof.dense_preview) media.append(element('p',`Dense preview: ${proof.dense_preview.preview_point_count.toLocaleString()} actual points sampled from ${proof.dense_preview.source_point_count.toLocaleString()}. ${proof.dense_preview.note}`));
      if (typeof assets.video==='string') { const video=element('video');video.src=url(assets.video);video.controls=true;video.preload='metadata';video.style.maxWidth='100%';media.append(video); }
      if (Array.isArray(assets.frames)) { const gallery=element('div',undefined,'drone-frame-gallery'); for(const path of assets.frames) { const image=element('img');image.src=url(path);image.alt=`Selected real drone image ${path.split('/').pop()}`;image.loading='lazy';image.width=180;image.style.maxWidth='100%';gallery.append(image); } media.append(gallery); }
      for (const [key,title] of [['sparse','Inspect sparse cloud'],['dense','Inspect fresh dense cloud'],['fresh_ndsm_grid','Inspect drone nDSM'],['lidar_ndsm_grid','Inspect LiDAR nDSM']]) { const path=assets[key]; if(typeof path!=='string')continue;const button=element('button',title);button.type='button';button.addEventListener('click',()=>void (key.endsWith('_grid') ? grid(path,title) : cloud(path,title)).catch(previewFailure));media.append(button); }
      if (!Object.keys(assets).length) media.append(element('p','No real stills, clips, sparse cloud, dense cloud or height grid exist for this proof yet.'));
      showMetrics(); if(proof.available && proof.metrics) { const values=await json<Values>('/api/drone-proof/metrics'); if(values.run_id!==proof.run_id)throw new Error('Metrics do not belong to the current run');showMetrics(values); }
    } catch (error) { state.textContent=`Proof unavailable: ${String(error)}. Start the local API to read actual run files.`;showMetrics();revision=''; }
    finally { busy=false;refresh.disabled=false; }
  }
  refresh.addEventListener('click',()=>void load()); await load();
  window.setInterval(()=>{if(host.isConnected && !host.hidden)void load();},10000);
}
