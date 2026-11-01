import * as THREE from 'three';
import proj4 from 'proj4';
import type { Diorama } from './model';
import { renderAdvisory, type Recommendation } from './advisory';
import { loadBboxImagery } from './cityTexture';
import './areaFlow.css';

type Bounds = [number, number, number, number];
type Descriptor = { area_id:string; status:string; requested_bbox:Bounds; cached_bbox:Bounds; clip_bounds_local_m:Bounds; metrics_note?:string; subset?:boolean; assets?:{terrain_json_url?:string} };
type AreaJob = { area_id:string; status:string; reason?:string; status_url?:string; descriptor?:Descriptor };
type AreaCatalog = { cached:{label:string;bbox:Bounds}[];worker?:{connected:boolean;note?:string} };
type Report = { kind:'flood'|'quake'|'fire'|'other';description:string;location:string;confirmed_current:boolean };
type Situation = { mode:'simulation'|'response';status:string;label:string;verified_current_observations?:unknown[] };
type Station = { id:string;kind:string;name?:string;position:[number,number];availability?:string };
type Assessment = { situation:Situation;stations?:Station[];current_hazard_data_available?:boolean };
type AreaView = Diorama & {setAreaBounds:(bounds?:Bounds)=>void};

function node<K extends keyof HTMLElementTagNameMap>(tag:K,className='',text=''):HTMLElementTagNameMap[K]{const e=document.createElement(tag);e.className=className;e.textContent=text;return e;}
async function request<T>(path:string,body?:unknown):Promise<T>{
  const result=await fetch(`/api${path}`,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:undefined,body:body?JSON.stringify(body):undefined,cache:'no-store'});
  if(!result.ok){let message=`Request unavailable (${result.status}).`;try{const value=await result.json();if(typeof value.detail==='string')message=value.detail;}catch{/* Retain safe status text. */}throw new Error(message);}
  return result.json() as Promise<T>;
}
function projected(crs:string):string{const match=/^EPSG:(326|327)(\d{2})$/.exec(crs);return match?`+proj=utm +zone=${Number(match[2])} ${match[1]==='327'?'+south ':''}+datum=WGS84 +units=m +no_defs`:crs;}

/** Area views and reported-incident response. It never starts a compute worker. */
export class AreaFlow {
  private root=node('aside','area-flow');
  private launch=node('button','area-launch','Select area ↗');
  private scope=node('div','area-scope');
  private status=node('p','area-status','Choose the area you want to inspect.');
  private body=node('div','area-flow-body');
  private result=node('div','area-result');
  private cached=node('div','area-cached');
  private selection=node('section','area-selection');
  private response=node('section','area-response');
  private lon=node('input');private lat=node('input');private size=node('select');
  private pickButton=node('button','','Choose centre on model');
  private selectButton=node('button','area-primary','Use this area →');
  private assessButton=node('button','area-primary','Check current situation →');
  private recommendButton=node('button','area-primary','Generate priorities');
  private kind=node('select');private description=node('textarea');private confirmed=node('input');
  private reportEnabled=node('input');private reportFields=node('div','area-report-fields');
  private area?:AreaJob;private descriptor?:Descriptor;private assessment?:Assessment;
  private runContext?:{run_id:string;frame_index:number};
  private regions:{bbox:Bounds;label:string}[]=[{bbox:[77,28,78,29],label:'Delhi FABDEM'}];
  private picking=false;private previousControls=true;private busy=false;private initialized=false;private version=0;
  private overlay=new THREE.Group();private stationGroup=new THREE.Group();private stations:Station[]=[];private outline?:THREE.Line;
  private lastExaggeration=0;private redrawTimer?:number;private visualAreaActive=false;private loadedTerrainURL='/data/terrain.json';
  private pendingGroup=new THREE.Group();private pendingPreview?:THREE.Mesh;private pendingLoader?:HTMLDivElement;private pollTimer?:number;private pendingStart=0;private pendingBbox?:Bounds;private packOutlines=new THREE.Group();private packsVisible=false;private imageryVersion=0;
  private onPointer=(event:PointerEvent)=>this.pick(event);
  private onKey=(event:KeyboardEvent)=>{if(event.key==='Escape'){if(this.picking)this.stopPick();else this.close();}};

  constructor(private view:Diorama,private callbacks:{onSimulation:()=>void;onResponse:()=>void;onAreaSelected?:()=>void;getRunContext?:()=>{run_id:string;frame_index:number}|undefined}){
    this.root.id='area-flow';this.root.setAttribute('aria-label','Area selection and current situation');this.root.hidden=true;
    this.launch.type='button';this.launch.setAttribute('aria-controls','area-flow');this.launch.setAttribute('aria-expanded','false');
    this.scope.id='area-flow-scope';this.scope.hidden=true;
    const header=node('header','area-flow-header');const titles=node('div');titles.append(node('h2','','Area'));
    const close=node('button','area-close','×');close.type='button';close.setAttribute('aria-label','Close area panel');close.onclick=()=>this.close();header.append(titles,close);
    this.selection.append(node('p','area-description','Pick on the model or enter coordinates.'));
    const coordinates=node('div','area-coordinates');this.lon.type=this.lat.type='number';this.lon.step=this.lat.step='0.00001';this.lon.min='-180';this.lon.max='180';this.lat.min='-79.999';this.lat.max='83.999';this.lon.setAttribute('aria-label','Longitude');this.lat.setAttribute('aria-label','Latitude');
    coordinates.append(this.field('Longitude',this.lon),this.field('Latitude',this.lat));
    for(const km of [1,2,5,10])this.size.add(new Option(`${km} × ${km} km`,String(km)));this.size.value='2';this.size.setAttribute('aria-label','Area side length');
    const buttons=node('div','area-button-row');buttons.append(this.pickButton,this.selectButton);this.selection.append(this.cached,coordinates,this.field('Square size',this.size),buttons);
    this.pickButton.type=this.selectButton.type='button';this.pickButton.onclick=()=>this.startPick();this.selectButton.onclick=()=>void this.select();
    const report=node('details','area-report');report.append(node('summary','','Is something happening now?'));
    this.reportEnabled.type='checkbox';const reportLabel=node('label','area-check');reportLabel.append(this.reportEnabled,document.createTextNode('I am reporting a current incident'));report.append(reportLabel);
    for(const [value,label] of [['flood','Flood'],['quake','Earthquake'],['fire','Fire'],['other','Other']])this.kind.add(new Option(label,value));
    this.kind.setAttribute('aria-label','Reported incident type');this.description.maxLength=1200;this.description.rows=3;this.description.placeholder='What happened, where, and when?';this.description.setAttribute('aria-label','Incident description');
    this.confirmed.type='checkbox';const confirmLabel=node('label','area-check');confirmLabel.append(this.confirmed,document.createTextNode('I confirm that I am reporting an incident occurring now.'));
    this.reportFields.append(this.field('Reported incident',this.kind),this.description,confirmLabel,node('p','area-note','Unverified. Response only; no damage or fleet implied.'));this.reportFields.hidden=true;report.append(this.reportFields);
    this.reportEnabled.onchange=()=>{this.reportFields.hidden=!this.reportEnabled.checked;};
    this.assessButton.type='button';this.assessButton.disabled=true;this.assessButton.onclick=()=>void this.assess();
    this.selection.append(report,this.assessButton);
    this.response.hidden=true;
    const footer=node('footer','area-flow-footer');footer.append(this.status);this.status.setAttribute('role','status');this.status.setAttribute('aria-live','polite');
    this.body.append(this.selection,this.response,this.result);this.root.append(header,this.body,footer);document.body.append(this.root,this.scope);
    (document.querySelector('.topbar-actions')??document.body).prepend(this.launch);this.launch.onclick=()=>this.root.hidden?this.open():this.close();
    this.overlay.name='area-selection-outline';this.stationGroup.name='area-real-stations';this.pendingGroup.name='area-pending-preview';this.packOutlines.name='area-pack-outlines';this.packOutlines.visible=false;this.view.scene.add(this.overlay,this.stationGroup,this.pendingGroup,this.packOutlines);
    this.view.renderer.domElement.addEventListener('pointerdown',this.onPointer,true);
    window.addEventListener('keydown',this.onKey);
  }

  private field(text:string,control:HTMLElement):HTMLLabelElement{const label=node('label','area-field');label.append(node('span','',text),control);return label;}
  async initialize(open=true):Promise<void>{
    if(this.initialized||new URLSearchParams(location.search).has('scene')){this.launch.hidden=new URLSearchParams(location.search).has('scene');return;}
    this.initialized=true;
    const terrain=this.view.terrain;const [lon,lat]=proj4(projected(terrain.crs),'EPSG:4326',[terrain.origin[0]+terrain.extent_m[0]/2,terrain.origin[1]-terrain.extent_m[1]/2]);this.lon.value=lon.toFixed(5);this.lat.value=lat.toFixed(5);
    if(open)this.open();
    try{const [catalog,packs]=await Promise.all([request<AreaCatalog>('/areas/catalog'),request<{regions:{bbox:Bounds;label:string}[]}>('/areas/regions')]);this.regions=[...packs.regions,...catalog.cached];this.cached.replaceChildren();for(const item of catalog.cached??[]){const button=node('button','area-cache-button',`Use cached ${item.label} ↗`);button.type='button';button.onclick=()=>void this.select(item.bbox);this.cached.append(button);}this.cached.append(node('p','area-note',catalog.worker?.connected?'A server worker is connected; build status remains explicit.':'No server builder is connected. New areas are saved as requests; no local build or completion-time estimate is invented.'));const requested=new URLSearchParams(location.search).get('area');if(requested&&/^area_[a-f0-9]{20}$/.test(requested)){const descriptor=await request<Descriptor>(`/areas/${requested}/descriptor`);this.loadedTerrainURL=descriptor.assets?.terrain_json_url??'/data/terrain.json';this.applyArea({area_id:requested,status:'ready',descriptor});}}
    catch(error){this.message(error);}
    // Only synchronize this small overlay when vertical exaggeration changes.
    this.redrawTimer=window.setInterval(()=>{if(this.view.exaggeration===this.lastExaggeration)return;this.lastExaggeration=this.view.exaggeration;if(this.visualAreaActive&&this.descriptor)this.drawOutline(this.descriptor.clip_bounds_local_m);if(this.stations.length)this.drawStations(this.stations);if(this.packsVisible)this.drawRegisteredPacks();if(this.pendingBbox)this.drawPendingRect(this.pendingBbox,this.regions.some(region=>this.pendingBbox![0]>=region.bbox[0]&&this.pendingBbox![2]<=region.bbox[2]&&this.pendingBbox![1]>=region.bbox[1]&&this.pendingBbox![3]<=region.bbox[3]));},500);
  }

  open():void{
    this.runContext=this.callbacks.getRunContext?.();
    if(this.view.ready){
      this.version++;this.setBusy(false);this.callbacks.onAreaSelected?.();this.assessment=undefined;this.response.hidden=true;this.selection.hidden=false;this.result.replaceChildren();this.clearStations();document.body.classList.remove('area-flow-response');
      if(this.area?.status==='ready'&&this.descriptor){this.visualAreaActive=true;this.setBounds(this.descriptor.clip_bounds_local_m);this.drawOutline(this.descriptor.clip_bounds_local_m);this.scope.hidden=true;this.result.append(node('p','area-note',this.descriptor.metrics_note??'A selected view does not recompute whole-model metrics.'));this.simulationButton();this.message('Real selected area restored. Reassess the current situation or explore a simulation.');}
    }
    this.packsVisible=true;this.drawRegisteredPacks();
    this.root.hidden=false;this.launch.setAttribute('aria-expanded','true');document.body.classList.add('area-flow-open');this.view.resize();
  }
  leaveForEvent():void{this.version++;this.close();this.clearStations();this.clearOutline();this.clearPending();this.visualAreaActive=false;this.setBounds(undefined);this.scope.hidden=true;this.packsVisible=false;this.packOutlines.visible=false;document.body.classList.remove('area-flow-response');}
  close():void{this.stopPick();this.root.hidden=true;this.launch.setAttribute('aria-expanded','false');this.packsVisible=false;this.packOutlines.visible=false;this.view.dirty=true;document.body.classList.remove('area-flow-open');this.view.resize();}
  private message(value:unknown):void{this.status.textContent=value instanceof Error?value.message:String(value);}
  private setBusy(value:boolean):void{this.busy=value;this.selectButton.disabled=value;this.pickButton.disabled=value;this.assessButton.disabled=value||this.area?.status!=='ready';this.recommendButton.disabled=value;}
  private startPick():void{if(this.busy||!this.view.ready)return;this.picking=true;this.previousControls=this.view.controls.enabled;this.view.controls.enabled=false;this.pickButton.textContent='Click the model · Esc cancels';this.message('Click the centre of the square. This chooses an area, not a disaster location.');}
  private stopPick():void{if(this.picking)this.view.controls.enabled=this.previousControls;this.picking=false;this.pickButton.textContent='Choose centre on model';}
  private pick(event:PointerEvent):void{
    if(!this.picking||event.button!==0)return;event.preventDefault();event.stopImmediatePropagation();const point=this.view.pick(event);if(!point){this.message('Choose a point on the real terrain block.');return;}
    const t=this.view.terrain;const [lon,lat]=proj4(projected(t.crs),'EPSG:4326',[t.origin[0]+point[0],t.origin[1]-point[1]]);this.lon.value=lon.toFixed(6);this.lat.value=lat.toFixed(6);this.stopPick();void this.select();
  }

  private async select(bbox?:Bounds):Promise<void>{
    if(this.busy)return;this.stopPick();const lon=Number(this.lon.value),lat=Number(this.lat.value);if(!bbox&&(!this.lon.value||!this.lat.value||!Number.isFinite(lon)||!Number.isFinite(lat)||lon < -180||lon > 180||lat<=-80||lat>=84)){this.message('Enter valid longitude and latitude within −80° to 84°.');return;}
    const centre=bbox?[(bbox[0]+bbox[2])/2,(bbox[1]+bbox[3])/2]:[lon,lat];
    const inPack=this.regions.some(region=>centre[0]>=region.bbox[0]&&centre[0]<=region.bbox[2]&&centre[1]>=region.bbox[1]&&centre[1]<=region.bbox[3]);
    // Compute the WGS84 bbox so we can draw a visible rectangle + preview even for out-of-pack picks.
    const previewBbox:Bounds=bbox??this.deriveBbox([lon,lat],Number(this.size.value)||2);
    // IMMEDIATE visual: glow rectangle, camera fly, loading badge — before any network call.
    this.clearPending();this.clearOutline();this.visualAreaActive=false;
    this.drawPendingRect(previewBbox,inPack);
    this.updateLoader(inPack?'Fetching terrain and buildings':'Outside Delhi pack — preview only','Loading satellite tiles');
    void this.attachImageryPreview(previewBbox);
    if(!inPack){
      this.message('Outside Delhi FABDEM pack. Preview only, no build.');
      return;
    }
    const version=++this.version;this.area=undefined;this.setBusy(true);this.message('Checking cache');this.result.replaceChildren();this.response.hidden=true;this.selection.hidden=false;document.body.classList.remove('area-flow-response');
    try{const job=await request<AreaJob>('/areas/select',bbox?{bbox}:{center:[lon,lat],size_km:Number(this.size.value)});if(version!==this.version)return;this.applyArea(job);}
    catch(error){if(version===this.version)this.message(error);}finally{if(version===this.version)this.setBusy(false);}
  }
  private applyArea(job:AreaJob):void{
    this.callbacks.onAreaSelected?.();
    this.area=job;this.descriptor=job.descriptor;this.assessment=undefined;this.clearStations();this.result.replaceChildren();
    if(job.status==='ready'&&job.descriptor){this.clearPending();const terrainURL=job.descriptor.assets?.terrain_json_url;if(terrainURL&&terrainURL!==this.loadedTerrainURL){window.location.assign(`/?area=${encodeURIComponent(job.area_id)}`);return;}this.lon.value=((job.descriptor.requested_bbox[0]+job.descriptor.requested_bbox[2])/2).toFixed(6);this.lat.value=((job.descriptor.requested_bbox[1]+job.descriptor.requested_bbox[3])/2).toFixed(6);this.visualAreaActive=true;this.setBounds(job.descriptor.clip_bounds_local_m);this.drawOutline(job.descriptor.clip_bounds_local_m);this.message('Real cached model ready. Check the situation or explore a simulation.');this.result.append(node('p','area-note',job.descriptor.metrics_note??'A selected view does not recompute whole-model metrics.'));this.simulationButton();this.scope.hidden=true;}
    else{this.visualAreaActive=false;this.clearOutline();this.setBounds(undefined);this.message(job.reason??'Build required. No worker running.');this.result.append(node('h3','','Build pending'),node('p','area-description','No ready model. Server build takes time.'));this.scope.textContent='Pending · showing previous model';this.scope.hidden=false;if(job.status_url&&/^\/areas\/jobs\/build_[a-f0-9]+$/.test(job.status_url)){const check=node('button','','Check now');check.type='button';check.onclick=()=>void this.refreshArea();this.result.append(check);if(!this.pollTimer)this.startPolling();}}
    this.assessButton.disabled=job.status!=='ready';this.view.dirty=true;
  }
  private setBounds(bounds?:Bounds):void{const method=(this.view as AreaView).setAreaBounds;if(typeof method==='function')method.call(this.view,bounds);else if(bounds)this.message('Area metadata is ready; terrain clipping is not available in this viewer build.');}
  private async refreshArea():Promise<void>{if(!this.area?.status_url||this.busy)return;const version=this.version;this.setBusy(true);try{const job=await request<AreaJob>(this.area.status_url);if(version===this.version)this.applyArea(job);}catch(error){if(version===this.version)this.message(error);}finally{if(version===this.version)this.setBusy(false);}}
  private report():Report|undefined{if(!this.reportEnabled.checked)return undefined;if(!this.description.value.trim()||!this.confirmed.checked)throw new Error('Describe the incident and confirm that your report is current.');return {kind:this.kind.value as Report['kind'],description:this.description.value.trim(),location:`${this.lat.value}°, ${this.lon.value}°`,confirmed_current:true};}
  private async assess():Promise<void>{
    if(this.busy||!this.area||this.area.status!=='ready')return;let report:Report|undefined;try{report=this.report();}catch(error){this.message(error);return;}
    this.callbacks.onAreaSelected?.();if(this.descriptor){this.visualAreaActive=true;this.setBounds(this.descriptor.clip_bounds_local_m);this.drawOutline(this.descriptor.clip_bounds_local_m);}this.scope.hidden=true;this.setBusy(true);this.message('Separating reported incidents from verified observations…');const version=this.version;
    try{const result=await request<Assessment>('/situation/assess',{area_id:this.area.area_id,report,...this.runContext});if(version!==this.version)return;this.assessment=result;this.showSituation(result);}
    catch(error){if(version===this.version)this.message(error);}finally{if(version===this.version)this.setBusy(false);}
  }
  private showSituation(result:Assessment):void{
    this.result.replaceChildren();this.response.replaceChildren();this.response.hidden=false;const actual=result.situation.mode==='response';
    this.response.append(node('h3','',actual?'Response':'No incident'),node('p','area-evidence-label',result.situation.label));
    if(actual){this.callbacks.onResponse();document.body.classList.add('area-flow-response');this.selection.hidden=true;this.drawStations(result.stations??[]);const list=node('details','area-station-list');list.append(node('summary','',`${this.stations.length.toLocaleString()} facilities`));const ul=node('ul');for(const station of this.stations.slice(0,30)){const row=node('li','',`${station.name||station.kind.replaceAll('_',' ')} · ${station.kind.replaceAll('_',' ')}`);ul.append(row);}list.append(ul,node('p','area-note','OSM locations. Availability unknown.'));this.response.append(list);}
    else{this.clearStations();document.body.classList.remove('area-flow-response');}
    this.recommendButton.type='button';this.recommendButton.onclick=()=>void this.recommend();this.response.append(this.recommendButton);this.simulationButton(this.response);
    const choose=node('button','','Change');choose.type='button';choose.onclick=()=>{this.selection.hidden=false;this.response.hidden=true;this.result.replaceChildren();};this.response.append(choose);this.message(actual?'Unverified. Human approval required.':'No incident inferred.');
  }
  private simulationButton(target:HTMLElement=this.result):void{const button=node('button','area-secondary','Simulate');button.type='button';button.onclick=()=>{this.visualAreaActive=false;this.clearStations();this.clearOutline();this.setBounds(undefined);document.body.classList.remove('area-flow-response');this.scope.textContent='Full Delhi AOI';this.scope.hidden=false;this.close();this.callbacks.onSimulation();};target.append(button);}
  private async recommend():Promise<void>{if(this.busy||!this.area)return;let report:Report|undefined;try{report=this.report();}catch(error){this.message(error);return;}const version=this.version;this.setBusy(true);this.message('Preparing evidence-grounded priorities…');try{const value=await request<Recommendation|{recommendations:Recommendation}>('/situation/recommend',{area_id:this.area.area_id,report,...this.runContext});if(version!==this.version)return;const result='recommendations' in value?value.recommendations:value;this.renderRecommendations(result);this.message(result.engine==='gemini'?'Gemini authored an evidence-grounded advisory. Human review is required.':'Gemini is unavailable; this is a deterministic evidence checklist.');}catch(error){if(version===this.version)this.message(error);}finally{if(version===this.version)this.setBusy(false);}}
  private renderRecommendations(value:Recommendation):void{
    renderAdvisory(this.result,value);
  }
  private clearOutline():void{if(this.outline){this.overlay.remove(this.outline);this.outline.geometry.dispose();(this.outline.material as THREE.Material).dispose();this.outline=undefined;}}
  private drawOutline(bounds:Bounds):void{this.clearOutline();const [x0,z0,x1,z1]=bounds;const corners:[[number,number],[number,number],[number,number],[number,number],[number,number]]=[[x0,z0],[x1,z0],[x1,z1],[x0,z1],[x0,z0]];const points:THREE.Vector3[]=[];for(let e=1;e<corners.length;e++){const a=corners[e-1],b=corners[e];const count=Math.max(1,Math.ceil(Math.hypot(a[0]-b[0],a[1]-b[1])/50));for(let i=0;i<=count;i++){const t=i/count,x=a[0]+(b[0]-a[0])*t,z=a[1]+(b[1]-a[1])*t;const p=this.view.point(x,z,this.view.renderedGround(x,z));p.y+=.012;points.push(p);}}this.outline=new THREE.Line(new THREE.BufferGeometry().setFromPoints(points),new THREE.LineBasicMaterial({color:'#c9b682',depthTest:false,transparent:true,opacity:.9}));this.outline.renderOrder=12;this.overlay.add(this.outline);this.view.dirty=true;}
  private clearStations():void{this.stations=[];this.stationGroup.traverse(object=>{if(object instanceof THREE.Mesh){object.geometry.dispose();const materials=Array.isArray(object.material)?object.material:[object.material];materials.forEach(material=>material.dispose());}});this.stationGroup.clear();this.view.dirty=true;}
  private drawStations(values:Station[]):void{this.clearStations();const bounds=this.descriptor?.clip_bounds_local_m??[0,0,...this.view.terrain.extent_m];this.stations=values.filter(s=>Array.isArray(s.position)&&s.position.length===2&&s.position.every(Number.isFinite)&&s.position[0]>=bounds[0]&&s.position[0]<=bounds[2]&&s.position[1]>=bounds[1]&&s.position[1]<=bounds[3]);if(!this.stations.length)return;const mesh=new THREE.InstancedMesh(new THREE.SphereGeometry(.022,8,6),new THREE.MeshBasicMaterial({color:'#d0d8cf',depthTest:false}),this.stations.length);const matrix=new THREE.Matrix4();const colors:Record<string,string>={hospital:'#81b7ae',clinic:'#81b7ae',fire_station:'#ce987d',police:'#92aacc',school:'#cbb783'};this.stations.forEach((station,index)=>{const p=this.view.point(...station.position);p.y+=.04;mesh.setMatrixAt(index,matrix.makeTranslation(p.x,p.y,p.z));mesh.setColorAt(index,new THREE.Color(colors[station.kind]??'#c5c4b8'));});mesh.renderOrder=13;mesh.instanceMatrix.needsUpdate=true;if(mesh.instanceColor)mesh.instanceColor.needsUpdate=true;this.stationGroup.add(mesh);this.view.dirty=true;}
  private clearPending():void{
    this.imageryVersion++;
    this.pendingGroup.traverse(object=>{if(object instanceof THREE.Mesh){object.geometry.dispose();const materials=Array.isArray(object.material)?object.material:[object.material];materials.forEach(material=>{(material as THREE.MeshBasicMaterial).map?.dispose();material.dispose();});} if(object instanceof THREE.Sprite){(object.material.map as THREE.Texture|undefined)?.dispose();object.material.dispose();}});
    this.pendingGroup.clear();this.pendingPreview=undefined;this.pendingBbox=undefined;
    if(this.pollTimer){clearInterval(this.pollTimer);this.pollTimer=undefined;}
    if(this.pendingLoader){this.pendingLoader.remove();this.pendingLoader=undefined;}
    this.view.dirty=true;
  }
  private ensureLoader():HTMLDivElement{
    if(this.pendingLoader)return this.pendingLoader;
    const loader=document.createElement('div');loader.id='area-loading';
    const spinner=document.createElement('span');spinner.className='area-loading-spinner';
    const text=document.createElement('div');text.className='area-loading-text';text.textContent='Fetching real terrain and buildings · this may take 2–10 minutes';
    const sub=document.createElement('div');sub.className='area-loading-sub';sub.textContent='Satellite preview loading…';
    loader.append(spinner,text,sub);document.body.append(loader);
    this.pendingLoader=loader;return loader;
  }
  private updateLoader(label:string,sub?:string):void{
    const loader=this.ensureLoader();
    const text=loader.querySelector('.area-loading-text') as HTMLElement|null;if(text)text.textContent=label;
    const subEl=loader.querySelector('.area-loading-sub') as HTMLElement|null;if(subEl){subEl.textContent=sub??'';subEl.hidden=!sub;}
  }
  /** Compute a WGS84 bbox from a size_km around a centre, mirroring server math. */
  private deriveBbox(centre:[number,number],size_km:number):Bounds{
    const [lon,lat]=centre;const halfKm=size_km/2;
    const latDelta=halfKm/111.32;
    const lonDelta=halfKm/(111.32*Math.max(0.01,Math.cos(lat*Math.PI/180)));
    return [lon-lonDelta,lat-latDelta,lon+lonDelta,lat+latDelta];
  }
  /** Drop a glowing yellow rectangle onto the terrain for the requested bbox. */
  private drawPendingRect(bbox:Bounds,inPack:boolean):void{
    const terrain=this.view.terrain;if(!terrain)return;
    // Remove any previous rectangle/label; keep the satellite preview mesh.
    for(const child of [...this.pendingGroup.children]){
      if(child===this.pendingPreview)continue;
      if(child instanceof THREE.Line){child.geometry.dispose();(child.material as THREE.Material).dispose();this.pendingGroup.remove(child);}
      else if(child instanceof THREE.Sprite){(child.material.map as THREE.Texture|undefined)?.dispose();child.material.dispose();this.pendingGroup.remove(child);}
    }
    const [west,south,east,north]=bbox;
    const toLocal=proj4('EPSG:4326',projected(terrain.crs));
    const corners=[[west,north],[east,north],[east,south],[west,south]].map(([lon,lat])=>{
      const [x,y]=toLocal.forward([lon,lat]) as [number,number];
      return [x-terrain.origin[0],terrain.origin[1]-y] as [number,number];
    });
    const extent=terrain.extent_m;
    const clamp=(p:[number,number]):[number,number]=>[Math.max(-extent[0]*0.1,Math.min(extent[0]*1.1,p[0])),Math.max(-extent[1]*0.1,Math.min(extent[1]*1.1,p[1]))];
    const edgePts:THREE.Vector3[]=[];
    for(let i=0;i<=corners.length;i++){
      const [x,z]=clamp(corners[i%corners.length]);
      const [nx,nz]=clamp(corners[(i+1)%corners.length]);
      const steps=Math.max(4,Math.ceil(Math.hypot(nx-x,nz-z)/50));
      for(let s=0;s<=steps;s++){
        const t=s/steps,px=x+(nx-x)*t,pz=z+(nz-z)*t;
        const inside=px>=0&&px<=extent[0]&&pz>=0&&pz<=extent[1];
        const elevation=inside?this.view.renderedGround(px,pz):terrain.min_height_m;
        const p=this.view.point(px,pz,elevation);p.y+=.024;edgePts.push(p);
      }
    }
    const color=inPack?'#c7a86b':'#e07040';
    const line=new THREE.Line(new THREE.BufferGeometry().setFromPoints(edgePts),new THREE.LineBasicMaterial({color,depthTest:false,transparent:true,opacity:.95,linewidth:3}));
    line.renderOrder=14;this.pendingGroup.add(line);
    // Build floating label sprite in the centre of the bbox (clamped to terrain for visibility).
    const cx=(corners[0][0]+corners[2][0])/2, cz=(corners[0][1]+corners[2][1])/2;
    const [clampedX,clampedZ]=clamp([cx,cz]);
    const inside=clampedX===cx&&clampedZ===cz;
    const base=inside?this.view.point(clampedX,clampedZ,this.view.renderedGround(clampedX,clampedZ)):this.view.point(clampedX,clampedZ,terrain.min_height_m);
    base.y+=.18;
    const canvas=document.createElement('canvas');canvas.width=1024;canvas.height=180;const ctx=canvas.getContext('2d')!;
    const label=inPack?'New area':'Preview only';
    const sub=inPack?'Fetching real data':`${Math.round(this.distanceKm(bbox,[77.2,28.6]))} km from Delhi`;
    ctx.fillStyle='#1a1612ed';ctx.fillRect(0,0,canvas.width,canvas.height);
    ctx.fillStyle=color;ctx.fillRect(0,0,8,canvas.height);
    ctx.fillStyle='#f5efdd';ctx.font='bold 44px system-ui, sans-serif';ctx.fillText(label.slice(0,50),28,76);
    ctx.fillStyle='#c7cbd1';ctx.font='28px system-ui, sans-serif';ctx.fillText(sub.slice(0,70),28,130);
    const texture=new THREE.CanvasTexture(canvas);texture.colorSpace=THREE.SRGBColorSpace;
    const sprite=new THREE.Sprite(new THREE.SpriteMaterial({map:texture,depthTest:false,transparent:true}));
    sprite.position.copy(base);sprite.scale.set(1.6,.28,1);sprite.renderOrder=15;this.pendingGroup.add(sprite);
    // Fly camera to the clamped bbox centre so the user sees motion.
    const radius=inside?Math.max(400,this.distanceKm(bbox,[west,south])*500):extent[0]/4;
    this.view.focusPoint([clampedX,clampedZ],radius);
    this.view.dirty=true;
  }
  /** Rough great-circle distance in km. */
  private distanceKm(bbox:Bounds,ref:[number,number]):number{
    const cx=(bbox[0]+bbox[2])/2, cy=(bbox[1]+bbox[3])/2;
    const dx=(cx-ref[0])*111.32*Math.cos(ref[1]*Math.PI/180);
    const dy=(cy-ref[1])*111.32;
    return Math.hypot(dx,dy);
  }
  /** Overlay registered pack bboxes as faint rectangles whenever the panel opens. */
  private drawRegisteredPacks():void{
    this.packOutlines.clear();if(!this.view.terrain||!this.regions.length)return;
    const terrain=this.view.terrain;const toLocal=proj4('EPSG:4326',projected(terrain.crs));
    for(const region of this.regions){
      const [west,south,east,north]=region.bbox;
      const corners=[[west,north],[east,north],[east,south],[west,south]].map(([lon,lat])=>{
        const [x,y]=toLocal.forward([lon,lat]) as [number,number];
        return [x-terrain.origin[0],terrain.origin[1]-y] as [number,number];
      });
      const edgePts:THREE.Vector3[]=[];
      for(let i=0;i<=corners.length;i++){
        const [x,z]=corners[i%corners.length];
        const [nx,nz]=corners[(i+1)%corners.length];
        const steps=Math.max(2,Math.ceil(Math.hypot(nx-x,nz-z)/80));
        for(let s=0;s<=steps;s++){
          const t=s/steps,px=x+(nx-x)*t,pz=z+(nz-z)*t;
          const inside=px>=0&&px<=terrain.extent_m[0]&&pz>=0&&pz<=terrain.extent_m[1];
          const elevation=inside?this.view.renderedGround(px,pz):terrain.min_height_m;
          const p=this.view.point(px,pz,elevation);p.y+=.008;edgePts.push(p);
        }
      }
      const line=new THREE.Line(new THREE.BufferGeometry().setFromPoints(edgePts),new THREE.LineDashedMaterial({color:'#c7a86b',dashSize:.08,gapSize:.06,depthTest:false,transparent:true,opacity:.55}));
      line.computeLineDistances();line.renderOrder=11;this.packOutlines.add(line);
    }
    this.packOutlines.visible=this.packsVisible;this.view.dirty=true;
  }
  /** Attach a satellite imagery preview as a flat decal over the bbox. */
  private async attachImageryPreview(bbox:Bounds):Promise<void>{
    const version=++this.imageryVersion;this.pendingBbox=bbox;
    const terrain=this.view.terrain;if(!terrain){this.updateLoader('Terrain still loading…');return;}
    try{
      const span=Math.max(bbox[2]-bbox[0],bbox[3]-bbox[1]);
      const zoom=span>0.05?15:span>0.015?16:17;
      const canvas=await loadBboxImagery(bbox,zoom);
      if(version!==this.imageryVersion)return;
      if(!canvas){this.updateLoader('Fetching terrain and buildings','Satellite tiles unavailable.');return;}
      const toLocal=proj4('EPSG:4326',projected(terrain.crs));
      const [west,south,east,north]=bbox;
      const corners=[[west,north],[east,north],[east,south],[west,south]].map(([lon,lat])=>{
        const [x,y]=toLocal.forward([lon,lat]) as [number,number];
        return [x-terrain.origin[0],terrain.origin[1]-y] as [number,number];
      });
      const extent=terrain.extent_m;
      const clamp=(p:[number,number]):[number,number]=>[Math.max(-extent[0]*0.1,Math.min(extent[0]*1.1,p[0])),Math.max(-extent[1]*0.1,Math.min(extent[1]*1.1,p[1]))];
      const cNW=clamp(corners[0] as [number,number]);const cSE=clamp(corners[2] as [number,number]);
      const width_m=Math.abs(cSE[0]-cNW[0]), depth_m=Math.abs(cSE[1]-cNW[1]);
      if(width_m<=0||depth_m<=0){return;}
      const geometry=new THREE.PlaneGeometry(width_m,depth_m);
      const texture=new THREE.CanvasTexture(canvas);texture.colorSpace=THREE.SRGBColorSpace;texture.anisotropy=4;
      const material=new THREE.MeshBasicMaterial({map:texture,transparent:true,opacity:.95,depthTest:true,depthWrite:false,side:THREE.DoubleSide,polygonOffset:true,polygonOffsetFactor:-6,polygonOffsetUnits:-6,toneMapped:false});
      const mesh=new THREE.Mesh(geometry,material);
      mesh.rotation.x=-Math.PI/2;
      const centerLocalX=(cNW[0]+cSE[0])/2, centerLocalZ=(cNW[1]+cSE[1])/2;
      const inside=centerLocalX>=0&&centerLocalX<=extent[0]&&centerLocalZ>=0&&centerLocalZ<=extent[1];
      const elevation=inside?this.view.renderedGround(centerLocalX,centerLocalZ):terrain.min_height_m;
      const p=this.view.point(centerLocalX,centerLocalZ,elevation);
      mesh.position.copy(p);mesh.position.y+=.004;
      mesh.scale.set(0.001,0.001,0.001);
      mesh.renderOrder=10;
      this.pendingGroup.add(mesh);this.pendingPreview=mesh;
      this.updateLoader('Satellite preview loaded','Esri · display only');
      this.view.dirty=true;
    }catch(cause){
      if(version!==this.imageryVersion)return;
      this.updateLoader('Fetching terrain and buildings','Preview failed: '+String(cause));
    }
  }
  /** Start/stop polling the build status. */
  private startPolling():void{
    if(this.pollTimer)clearInterval(this.pollTimer);
    this.pendingStart=performance.now();
    this.pollTimer=window.setInterval(()=>{
      const elapsed=Math.round((performance.now()-this.pendingStart)/1000);
      if(this.pendingLoader){
        const sub=this.pendingLoader.querySelector('.area-loading-sub') as HTMLElement|null;
        if(sub && !sub.textContent?.includes('Satellite preview loaded')) {
          sub.hidden=false; sub.textContent=`Status: polling · ${elapsed}s elapsed`;
        }
      }
      if(!this.area?.status_url)return;
      void this.refreshArea();
    },5000);
  }
  dispose():void{this.stopPick();if(this.redrawTimer)clearInterval(this.redrawTimer);if(this.pollTimer)clearInterval(this.pollTimer);this.view.renderer.domElement.removeEventListener('pointerdown',this.onPointer,true);window.removeEventListener('keydown',this.onKey);this.clearOutline();this.clearStations();this.clearPending();this.view.scene.remove(this.overlay,this.stationGroup,this.pendingGroup,this.packOutlines);this.root.remove();this.launch.remove();this.scope.remove();document.body.classList.remove('area-flow-open','area-flow-response');}
}
