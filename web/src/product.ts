import type { Diorama } from './model';
import { ResponseVisuals } from './responseVisuals';
import { EventPlayback, currentCachedEvents } from './eventState';
import { showToast } from './toast';
import { renderAdvisory, type Recommendation } from './advisory';
import type { CachedEvent, EventState } from './eventState';
export type { CachedEvent, EventState } from './eventState';
import type { Catalog, DisasterModule, ImpactMetrics, ProductCatalog, ProductManifest, ProductRequest, ResponseData, Run } from './types';

const element=<T extends HTMLElement=HTMLElement>(id:string):T=>document.getElementById(id) as T;
const select=(id:string)=>element<HTMLSelectElement>(id),input=(id:string)=>element<HTMLInputElement>(id);
const colors=['#66ad85','#e1cd65','#e19a4c','#d85b53','#74457f'];
const labels:Record<DisasterModule,string[]>={flood:['0–0.3 m','0.3–0.6 m','0.6–1.2 m','1.2–2 m','>2 m'],breach:['0–0.3 m','0.3–0.6 m','0.6–1.2 m','1.2–2 m','>2 m'],chain:['None / shallow','Light','Moderate','Heavy','Collapsed / deep'],quake:['None','Light','Moderate','Heavy','Collapsed'],fire:['Unburnt','At risk','—','Burning','Burnt']};
const format=(value:number|null|undefined,digits=0)=>typeof value==='number'&&Number.isFinite(value)?value.toLocaleString(undefined,{maximumFractionDigits:digits}):'Unavailable';
const api=(url:string)=>url.startsWith('/api/')?url:url.startsWith('/')?`/api${url}`:'';
const requestSignal=(signal?:AbortSignal|null)=>signal?AbortSignal.any([signal,AbortSignal.timeout(15000)]):AbortSignal.timeout(15000);
async function json<T>(url:string,init?:RequestInit):Promise<T>{const response=await fetch(url,{cache:'no-store',...init,signal:requestSignal(init?.signal)});if(!response.ok){let detail='';try{const body=await response.json();detail=typeof body.detail==='string'?body.detail:'';}catch{}throw new Error(detail||`Request unavailable (${response.status}).`);}return response.json() as Promise<T>;}
function dataURL(url:string):string{if(!/^\/data\/[A-Za-z0-9/_.-]+$/.test(url)||url.includes('..'))throw new Error('Run contains an invalid local data URL.');return url;}

export class ProductFlow {
  private ready=false;
  private busy=false;
  private catalog?:Catalog;
  private productCatalog?:ProductCatalog;
  private point?:[number,number];
  private buildingIndex?:number;
  private picking=false;
  private previousOrbit=true;
  private previousBankVisible=false;
  private hover?:{point:[number,number];index?:number};
  private requestVersion=0;
  private admissionVersion=0;
  private manifestVersion=0;
  private manifestAbort?:AbortController;
  private frameAbort?:AbortController;
  private refreshPromise?:Promise<void>;
  private catalogError?:string;
  private resolveReady!:()=>void;
  private whenReady=new Promise<void>(resolve=>{this.resolveReady=resolve;});
  private eventPlayback=new EventPlayback();
  private eventMode=false;
  private eventTicket=0;
  private eventFrame?:{ticket:number;index:number};
  private routePreview={start:0,end:0,time:0,paused:false};
  private responseSnapshot?:ResponseData;
  private responsePlanTime?:number;
  private frameCache=new Map<number,{depth?:Float32Array;severity:Uint8Array;states:Uint8Array;metrics:ImpactMetrics;response:ResponseData}>();
  private visuals:ResponseVisuals;
  private previousHover=0;
  private frameIndex=-1;
  private clock=0;
  private playing=false;
  manifest?:ProductManifest;
  metrics?:ImpactMetrics;
  response?:ResponseData;
  stage=1;
  get active():boolean{return !!this.manifest;}
  get advisoryContext():{run_id:string;frame_index:number}|undefined{return this.manifest&&this.frameIndex>=0?{run_id:this.manifest.run_id,frame_index:this.frameIndex}:undefined;}
  get scenarioLabel():string{const run=this.manifest;if(!run)return '';const details:string[]=[];if(['quake','chain'].includes(run.module)&&run.scenario?.magnitude!==undefined)details.push(`M${run.scenario.magnitude}`);if(['flood','breach','chain'].includes(run.module)&&run.scenario?.river_plus_m!==undefined)details.push(`+${run.scenario.river_plus_m} m`);return [run.labels.title??run.module,...details].join(' · ');}
  get playbackLabel():string{return this.manifest?`${this.scenarioLabel} · t+${Math.round(this.clock)} s · Hypothetical, not a forecast`:'';}
  constructor(private view:Diorama,private loadLegacy:(id:string)=>Promise<void>,private stopLegacy:()=>void){
    this.visuals=new ResponseVisuals(view,id=>this.followResponseUnit(id));
    element('advanced-toggle').addEventListener('click',()=>this.drawer(Boolean(element('panel').hidden)));
    element('collapse-panel').addEventListener('click',()=>this.drawer(false));
    element('product-fit').addEventListener('click',()=>{if(view.ready)view.fit();});
    select('disaster-module').addEventListener('change',()=>{select('disaster-preset').value='custom';this.point=undefined;this.buildingIndex=undefined;this.cancelPick();this.inputs();});
    select('disaster-preset').addEventListener('change',()=>this.presetChanged());
    for(const id of ['river-rise','quake-magnitude','wind-speed','wind-direction'])input(id).addEventListener('input',()=>this.inputs());
    element('product-run').addEventListener('click',()=>void this.run());
    element('product-next').addEventListener('click',()=>void this.inspectStage(Math.min(3,this.stage+1)));
    element('pick-location').addEventListener('click',()=>this.startPick());element('cancel-selection').addEventListener('click',()=>this.cancelPick());
    for(const button of document.querySelectorAll<HTMLButtonElement>('[data-product-step]'))button.addEventListener('click',()=>void this.inspectStage(Number(button.dataset.productStep)));
    view.renderer.domElement.addEventListener('pointermove',event=>this.hoverPick(event));view.renderer.domElement.addEventListener('pointerdown',event=>{if(!this.picking||event.button!==0)return;event.stopImmediatePropagation();this.hoverPick(event,true);if(this.hover){select('disaster-preset').value='custom';this.point=this.hover.point;this.buildingIndex=this.hover.index;this.cancelPick();this.view.drawEdit([this.point],false);this.inputs();}},true);
    window.addEventListener('keydown',event=>{if(event.key==='Escape')this.cancelPick();});
    element('briefing-button').addEventListener('click',()=>void this.briefing());this.inputs();
  }
  drawer(open:boolean):void{element('panel').hidden=!open;element('workflow-card').hidden=open;element('advanced-toggle').setAttribute('aria-expanded',String(open));element('expand-panel').hidden=true;document.body.classList.toggle('advanced-open',open);this.view.resize();}
  setReady(catalog:Catalog):void{this.ready=true;this.setCatalog(catalog);this.resolveReady();void this.refresh();}
  setCatalog(catalog:Catalog):void{this.catalog=catalog;this.inputs();}
  async refresh():Promise<void>{
    if(this.refreshPromise)return this.refreshPromise;
    this.refreshPromise=this.refreshCatalog();
    try{await this.refreshPromise;}finally{this.refreshPromise=undefined;}
  }
  private async refreshCatalog():Promise<void>{
    try{
      let catalog:ProductCatalog;
      try{catalog=await json<ProductCatalog>('/api/product/catalog');}
      catch{catalog=await json<ProductCatalog>('/data/product/catalog.json');}
      if(!Array.isArray(catalog.presets)||!Array.isArray(catalog.modules)||!catalog.building_index?.sha256)throw new Error('Cached event catalog is unavailable.');
      this.productCatalog=catalog;this.catalogError=undefined;
      for(const option of select('disaster-module').options){const module=this.productCatalog.modules.find(item=>item.id===option.value);option.disabled=!module?.enabled;option.textContent=module?.label??option.textContent;option.title=module?.missing?.join('; ')??'Module unavailable';}
      const previous=select('disaster-preset').value;const options=[new Option('Custom scenario','custom')];
      for(const preset of this.productCatalog.presets){const option=new Option(preset.id==='replay2023'&&!preset.enabled?'2023 replay · radar/calibration pending':preset.label,preset.id);option.disabled=!preset.enabled;option.title=preset.reason??'';options.push(option);}
      if(!options.some(option=>option.value==='replay2023')){const replay=new Option(this.catalog?.runs.replay2023?.available?'July 2023 replay':'July 2023 replay · data pending','replay2023');replay.disabled=!this.catalog?.runs.replay2023?.available;replay.title=this.catalog?.replay_requirements?.join('; ')??'';options.push(replay);}
      select('disaster-preset').replaceChildren(...options);select('disaster-preset').value=options.some(option=>option.value===previous)?previous:'custom';
      void this.visuals.initialize();this.inputs();
    }catch(cause){this.catalogError=cause instanceof Error?cause.message:'Cached events are unavailable.';this.inputs();}
  }
  private presetChanged():void{
    this.cancelPick();this.point=undefined;this.buildingIndex=undefined;
    const id=select('disaster-preset').value,preset=this.productCatalog?.presets.find(item=>item.id===id);
    if(preset){const p=preset.scenario;select('disaster-module').value=p.module;if(p.river_plus_m!==undefined)input('river-rise').value=String(p.river_plus_m);if(p.magnitude!==undefined)input('quake-magnitude').value=String(p.magnitude);if(p.wind_speed_m_s!==undefined)input('wind-speed').value=String(p.wind_speed_m_s);if(p.wind_direction_deg!==undefined)select('wind-direction').value=String(p.wind_direction_deg);this.point=p.point;this.buildingIndex=p.building_index;}
    else{select('disaster-module').value=id==='breach_near_ito'?'breach':'flood';if(id==='river_plus_3m')input('river-rise').value='3';}
    this.inputs();
  }
  private inputs():void{
    const module=select('disaster-module').value as DisasterModule,preset=select('disaster-preset').value;
    element('flood-inputs').hidden=module==='fire'||module==='quake'||preset==='replay2023';element('quake-inputs').hidden=module!=='quake'&&module!=='chain';element('fire-inputs').hidden=module!=='fire';
    element('river-rise-value').textContent=`+${input('river-rise').value} m`;element('quake-magnitude-value').textContent=`M${Number(input('quake-magnitude').value).toFixed(1)}`;element('wind-speed-value').textContent=`${input('wind-speed').value} m/s`;
    const moduleDefinition=this.productCatalog?.modules.find(item=>item.id===module);if(!this.active)element('product-assumptions-text').textContent=moduleDefinition?.labels?.limitations?.join(' ')??'No product model is loaded.';
    const needsPoint=module!=='flood';element('pick-location').hidden=!needsPoint;element('pick-location').textContent=module==='breach'?'Choose an embankment point ↗':module==='fire'?'Choose the ignition building ↗':'Choose the epicentre ↗';
    element('selection-note').hidden=!needsPoint;element('selection-note').textContent=this.point?`Selected ${this.point.map(v=>Math.round(v).toLocaleString()).join(', ')} m · local grid`:this.buildingIndex!==undefined?'Preset building selected.':'Use the preset location, or choose on the model.';
    const presetConfig=this.productCatalog?.presets.find(item=>item.id===preset);const enabled=preset==='replay2023'?!!this.catalog?.runs.replay2023?.available:this.productCatalog?!!this.productCatalog.modules.find(item=>item.id===module)?.enabled:!!this.catalog?.runs[this.legacyID()]?.available;
    const locationReady=!needsPoint||!!this.point||this.buildingIndex!==undefined||!!presetConfig?.enabled||(!this.productCatalog&&module==='breach');
    element<HTMLButtonElement>('product-run').disabled=!this.ready||this.busy||!enabled||!locationReady||this.picking;
    if(!this.busy)element('product-status').textContent=!this.ready?'Loading the real terrain…':!enabled?'Required data or this module is unavailable. No placeholder results are shown.':!locationReady?'Choose a location on the model to continue.':this.active?'Computed results loaded. Scrub the timeline or run another scenario.':this.productCatalog?'Open a cached scenario. New calculations require a server.':'Existing computed flood runs are ready. Impact service is connecting.';
    element('product-run').hidden=this.stage!==1;element('product-next').hidden=!this.active||this.stage===3;element('product-next').textContent=this.stage===1?'See impact →':'Plan response →';
  }
  private legacyID():string{return select('disaster-module').value==='breach'?'relative_breach_near_ito':select('disaster-preset').value==='replay2023'?'replay2023':`relative_river_plus_${input('river-rise').value}m`;}
  private async run():Promise<void>{
    if(this.busy)return;const admission=++this.admissionVersion;this.busy=true;this.cancelPick();this.inputs();element('product-status').textContent='Preparing the scenario…';
    try{
      await this.refresh();if(admission!==this.admissionVersion)return;
      if(!this.productCatalog||select('disaster-preset').value==='replay2023'){this.deactivate();await this.loadLegacy(this.legacyID());element('product-caveat').textContent=this.legacyID()==='replay2023'?'Simulated July 2023 replay':'Hypothetical, not a forecast';element('product-status').textContent='Computed playback loaded. Impact requires a product run.';return;}
      const request:ProductRequest={module:select('disaster-module').value as DisasterModule,seed:0};const preset=select('disaster-preset').value;if(preset!=='custom')request.preset_id=preset;
      if(request.module==='breach')request.bank_source=this.productCatalog.presets.find(item=>item.id===preset)?.scenario.bank_source??'mapped_embankment';
      if(['flood','breach','chain'].includes(request.module))request.river_plus_m=Number(input('river-rise').value);
      if(['quake','chain'].includes(request.module))request.magnitude=Number(input('quake-magnitude').value);
      if(request.module==='fire'){request.wind_direction_deg=Number(select('wind-direction').value);request.wind_speed_m_s=Number(input('wind-speed').value);if(this.buildingIndex!==undefined)request.building_index=this.buildingIndex;}
      if(this.point)request.point=this.point;
      const result=await json<{run_id:string;status_url:string;manifest_url:string}>('/api/product/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)});
      for(let attempt=0;attempt<1800;attempt++){
        const status=await json<{status:string;detail?:string;error?:string;message?:string}>(api(result.status_url));
        if(admission!==this.admissionVersion)return;
        if(['complete','completed','ready','succeeded'].includes(status.status)){await this.loadManifest(result.manifest_url);break;}
        if(['failed','blocked','needs_input','unavailable','cancelled'].includes(status.status))throw new Error(status.error??status.detail??status.message??`Run ${status.status}.`);
        element('product-status').textContent=status.detail??status.message??'Computing the deterministic scenario and routes…';await new Promise(resolve=>setTimeout(resolve,2000));
        if(attempt===1799)throw new Error('Run is still active. Its files are preserved; check Advanced status later.');
      }
    }catch(cause){element('product-status').textContent=cause instanceof Error?cause.message:'Scenario unavailable.';}
    finally{this.busy=false;const message=element('product-status').textContent;this.inputs();element('product-status').textContent=message;}
  }
  /** Wait for the real model and refresh the GET-only list of completed, current events. */
  async prepareEvents():Promise<CachedEvent[]>{
    await this.whenReady;await this.refresh();
    const events=this.getCachedEvents();
    if(!events.length)throw new Error(this.catalogError??'No current computed events are available.');
    return events;
  }
  getCachedEvents():CachedEvent[]{return currentCachedEvents(this.productCatalog);}
  getEventState():EventState{
    const state=this.eventPlayback.snapshot();
    if(this.eventMode&&this.stage===3&&this.getResponseData()?.units.length){const preview=this.routePreview;return {...state,route_preview_s:preview.time-preview.start,route_preview_duration_s:preview.end-preview.start,route_preview_playing:!preview.paused&&['running','paused','complete'].includes(state.phase)&&preview.time<preview.end};}
    return state;
  }
  async startEvent(runId:string):Promise<void>{
    const event=this.getCachedEvents().find(item=>item.run_id===runId);
    this.deactivate();this.stopLegacy();
    if(!event){const ticket=this.eventPlayback.begin(runId);const message='This event is not in the current completed cache. Refresh the event list.';this.eventPlayback.fail(ticket,message);element('product-status').textContent=message;throw new Error(message);}
    const ticket=this.eventPlayback.begin(event.run_id,event.module);this.eventTicket=ticket;this.eventMode=true;
    element('product-status').textContent=`Opening ${event.label}…`;
    try{
      if(!this.ready)throw new Error('The real terrain is still loading.');
      if(!await this.openManifest(event.manifest_url,event.run_id)||!this.eventPlayback.current(ticket))return;
      this.eventPlayback.load(ticket,this.manifest!.frames.map(frame=>frame.time_s));
      this.clock=this.manifest!.frames[0].time_s;this.setStage(2);this.playing=this.getEventState().phase==='running';
      element('play').textContent=this.playing?'Ⅱ Pause':'▶ Play';
      element('product-status').textContent='Event running · simulated time · drag to explore.';
    }catch(cause){if(!this.eventPlayback.current(ticket))return;this.playing=false;const message=cause instanceof Error?cause.message:'This computed event could not be opened.';this.eventPlayback.fail(ticket,message);element('product-status').textContent=message;throw new Error(message);}
  }
  pauseEvent():void{this.pause();}
  resumeEvent():void{if(!this.eventMode)return;this.routePreview.paused=false;this.eventPlayback.resume();this.playing=this.getEventState().phase==='running';element('play').textContent=this.playing?'Ⅱ Pause':'▶ Play';}
  async restartEvent():Promise<void>{const id=this.getEventState().run_id;if(id)await this.startEvent(id);}
  resetEvent():void{this.deactivate();this.stopLegacy();input('timeline').value='0';input('timeline').disabled=true;element<HTMLButtonElement>('play').disabled=true;element('time-label').textContent='No event selected';element('stage-label').textContent='Real terrain';element('interpolation').textContent='';}
  setEventView(view:'impact'|'response'):void{this.setStage(view==='response'?3:2);}
  getResponseData():ResponseData|undefined{return this.responseSnapshot??this.response;}
  getResponsePlanTime():number|undefined{return this.responsePlanTime;}
  getResponseUnits(){return this.visuals.getUnits();}
  getResponseFollowState(){return this.visuals.getFollowState();}
  followResponseUnit(id?:string):void{
    if(!id){this.visuals.followUnit();return;}
    if(!this.eventMode||this.stage!==3||!this.visuals.getUnits().some(unit=>unit.id===id))return;
    this.resetRoutePreview(this.responsePlanTime??this.clock);this.routePreview.paused=false;
    this.visuals.tick(this.routePreview.time,0);this.visuals.followUnit(id);
  }
  replayResponse():void{const id=this.visuals.getFollowState().unitId??this.preferredResponseUnit();if(id)this.followResponseUnit(id);}
  private preferredResponseUnit():string|undefined{
    const units=this.visuals.getUnits().filter(unit=>unit.duration_s>0);
    return (units.find(unit=>unit.type==='rescue_boat')??units.find(unit=>unit.type==='ambulance')??units[0])?.id;
  }
  private pinResponse(time:number):void{
    this.responseSnapshot=this.response;this.responsePlanTime=time;this.visuals.set(this.responseSnapshot);this.resetRoutePreview(time);this.routePreview.paused=false;
  }

  getEventFocus():{point:[number,number];radius_m:number}|undefined{
    const run=this.manifest;if(!run)return;
    const ignition=run.scenario?.building_index;
    const point=run.module==='fire'&&ignition!==undefined?this.view.getBuildingPosition(ignition):run.scenario?.point;
    if(point)return {point:[...point],radius_m:run.module==='fire'?400:run.module==='breach'?2200:4000};
    const hotspot=this.response?.hotspots.find(item=>item.severity>=3)??this.response?.hotspots[0];
    if(hotspot)return {point:[...hotspot.position],radius_m:run.module==='fire'?400:run.module==='breach'?2200:4500};
    return {point:[this.view.terrain.extent_m[0]/2,this.view.terrain.extent_m[1]/2],radius_m:Math.max(...this.view.terrain.extent_m)*.35};
  }
  async loadManifest(url:string):Promise<void>{
    this.eventMode=false;this.eventPlayback.reset();this.eventFrame=undefined;
    await this.openManifest(url);
  }
  private async openManifest(url:string,expectedRunId?:string):Promise<boolean>{
    this.playing=false;const version=++this.manifestVersion;this.requestVersion++;this.manifestAbort?.abort();this.frameAbort?.abort();
    const controller=new AbortController();this.manifestAbort=controller;
    const manifest=await json<ProductManifest>(dataURL(url),{signal:controller.signal});if(version!==this.manifestVersion)return false;
    if(manifest.schema_version!==1||manifest.building_states?.stride!==2||!Number.isInteger(manifest.grid?.width)||manifest.grid.width<=0||!Number.isInteger(manifest.grid.height)||manifest.grid.height<=0||!Number.isFinite(manifest.grid.cell_size_m)||manifest.grid.cell_size_m<=0)throw new Error('Unsupported computed run format.');
    if(expectedRunId&&manifest.run_id!==expectedRunId)throw new Error('Cached event identity does not match its computed manifest.');
    if(!manifest.frames?.length||manifest.frames.some((frame,index)=>!Number.isFinite(frame.time_s)||frame.time_s<0||index>0&&frame.time_s<=manifest.frames[index-1].time_s))throw new Error('Computed run has an invalid simulation timeline.');
    if(this.productCatalog&&manifest.building_index.sha256!==this.productCatalog.building_index.sha256)throw new Error('Building order changed. Recompute this run before displaying exposure.');
    this.stopLegacy();this.playing=false;this.requestVersion++;this.frameCache.clear();this.manifest=manifest;this.frameIndex=-1;this.clock=manifest.frames[0].time_s;
    this.view.setFireMode(manifest.module==='fire'||manifest.module==='chain');
    this.view.setQuakeMode(manifest.module==='quake'||manifest.module==='chain');
    select('disaster-module').value=manifest.module;select('disaster-preset').value=Array.from(select('disaster-preset').options).some(option=>option.value===manifest.preset_id)?manifest.preset_id!:'custom';
    const scenario=manifest.scenario;this.point=scenario?.point;this.buildingIndex=scenario?.building_index;
    if(scenario?.river_plus_m!==undefined)input('river-rise').value=String(scenario.river_plus_m);if(scenario?.magnitude!==undefined)input('quake-magnitude').value=String(scenario.magnitude);if(scenario?.wind_direction_deg!==undefined)select('wind-direction').value=String(scenario.wind_direction_deg);if(scenario?.wind_speed_m_s!==undefined)input('wind-speed').value=String(scenario.wind_speed_m_s);
    input('timeline').max=String(manifest.frames.length-1);input('timeline').disabled=false;element<HTMLButtonElement>('play').disabled=false;
    for(const button of document.querySelectorAll<HTMLButtonElement>('[data-product-step]'))button.disabled=false;
    element('product-assumptions-text').textContent=[...(manifest.labels?.limitations??[]),...(manifest.limitations??[])].join(' ');element('product-caveat').textContent='Hypothetical, not a forecast';
    if(!await this.showFrame(0)||version!==this.manifestVersion)return false;
    this.setStage(1);element('product-status').textContent='Computed run ready. Play the scenario, then explore its impact.';
    try {
      showToast({
        kind: manifest.module,
        title: manifest.labels?.title ?? manifest.label ?? this.scenarioLabel,
        runId: /^product_[0-9a-f]{16}$/.test(manifest.run_id) ? manifest.run_id : undefined,
        frameIndex: 0,
        metrics: this.metrics,
        response: this.response ? { metrics: this.response.metrics } : undefined,
      });
    } catch { /* toast is non-critical */ }
    return true;
  }
  async loadPresetScene(preset:string):Promise<void>{
    await this.refresh();const cached=this.productCatalog?.runs?.filter(run=>run.preset_id===preset&&run.source_current!==false).sort((a,b)=>(a.created_at??'').localeCompare(b.created_at??'')).at(-1);
    if(!cached)throw new Error(`This scene needs a completed ${preset} product run. Use Run first; no placeholder recording will be created.`);
    await this.loadManifest(cached.manifest_url);this.setStage(3);
  }
  deactivate():void{this.cancelPick();this.point=undefined;this.buildingIndex=undefined;this.view.drawEdit([],false);this.admissionVersion++;this.manifestVersion++;this.requestVersion++;this.manifestAbort?.abort();this.frameAbort?.abort();this.eventMode=false;this.eventPlayback.reset();this.eventFrame=undefined;this.routePreview={start:0,end:0,time:0,paused:false};this.responseSnapshot=undefined;this.responsePlanTime=undefined;this.busy=false;this.manifest=undefined;this.playing=false;this.metrics=undefined;this.response=undefined;this.frameCache.clear();this.clock=0;this.frameIndex=-1;this.view.setWater();element('water-label').hidden=true;this.view.setSeverity();this.view.setBuildingStates();this.view.setImpactVisible(false);this.view.setFireMode(false);this.view.setQuakeMode(false);this.visuals.setVisible(false);this.visuals.set();this.setStage(1);for(const button of document.querySelectorAll<HTMLButtonElement>('[data-product-step]'))button.disabled=button.dataset.productStep!=='1';}
  private async showFrame(index:number):Promise<boolean>{
    const run=this.manifest;if(!run)return false;index=Math.max(0,Math.min(run.frames.length-1,index));const frame=run.frames[index],version=++this.requestVersion;
    this.frameAbort?.abort();const controller=new AbortController();this.frameAbort=controller;
    let cached=this.frameCache.get(index);
    if(!cached){
      const binary=async(url:string)=>{const response=await fetch(dataURL(url),{signal:requestSignal(controller.signal)});if(!response.ok)throw new Error('Computed frame file is unavailable.');return response.arrayBuffer();};
      const [depth,severity,states,metrics,response]=await Promise.all([frame.depth_url?binary(frame.depth_url):undefined,binary(frame.severity_url),binary(frame.building_states_url),json<ImpactMetrics>(dataURL(frame.metrics_url),{signal:controller.signal}),json<ResponseData>(dataURL(frame.response_url),{signal:controller.signal})]);
      if(version!==this.requestVersion||run!==this.manifest)return false;
      if(severity.byteLength!==run.grid.width*run.grid.height||states.byteLength!==run.building_states.count*2||depth&&depth.byteLength!==run.grid.width*run.grid.height*4)throw new Error('Computed frame dimensions do not match its manifest.');
      cached={depth:depth?new Float32Array(depth):undefined,severity:new Uint8Array(severity),states:new Uint8Array(states),metrics,response};this.frameCache.set(index,cached);while(this.frameCache.size>5)this.frameCache.delete(this.frameCache.keys().next().value!);
    }
    if(version!==this.requestVersion||run!==this.manifest)return false;
    this.frameIndex=index;this.metrics=cached.metrics;this.response=cached.response;input('timeline').value=String(index);
    const waterRun:Run={run_id:run.run_id,calibrated:false,hypothetical:true,frames:[],width:run.grid.width,height:run.grid.height,cell_size_m:run.grid.cell_size_m,render_stride:run.grid.stride,metrics_url:run.metrics_url};
    this.view.setWater(cached.depth,cached.depth?waterRun:undefined);this.view.setSeverity(run.module==='flood'||run.module==='breach'?undefined:cached.severity,run.grid.width,run.grid.height,colors);this.view.setBuildingStates(cached.states,colors);await this.visuals.initialize();if(version!==this.requestVersion||run!==this.manifest)return false;if(this.eventMode&&this.stage===3){
      // A response plan is one saved observation: later hazard frames must not teleport its vehicles.
      if(!this.responseSnapshot?.units.length){this.pinResponse(frame.time_s);const id=this.preferredResponseUnit();if(id)this.followResponseUnit(id);}
    }else{this.visuals.set(cached.response);this.resetRoutePreview(frame.time_s);}
    this.visuals.refreshSurface();this.view.setImpactVisible(this.stage>1);
    element('time-label').textContent=`t + ${Math.floor(frame.time_s/60)}:${String(Math.floor(frame.time_s%60)).padStart(2,'0')}`;element('stage-label').textContent=this.scenarioLabel;element('interpolation').textContent='Simulated time';
    element('water-label').textContent=`Simulated ${run.module==='quake'?'earthquake damage':run.module==='fire'?'fire':run.module==='chain'?'earthquake → flood':'water'} · Hypothetical, not a forecast`;element('water-label').hidden=false;
    this.cards();this.legend();this.visuals.tick(this.eventMode&&this.stage===3?this.routePreview.time:this.clock,0);this.view.dirty=true;return true;
  }
  async scrub(index:number):Promise<void>{this.pause();if(!this.manifest)return;index=Math.max(0,Math.min(this.manifest.frames.length-1,Math.round(index)));this.clock=this.manifest.frames[index].time_s;if(this.eventMode)this.eventPlayback.seek(this.clock);await this.showFrame(index);}
  togglePlay():void{if(!this.manifest)return;if(this.eventMode){const phase=this.getEventState().phase;if(phase==='running')this.pauseEvent();else if(phase==='complete')void this.restartEvent().catch(()=>{});else this.resumeEvent();return;}this.playing=!this.playing;if(this.frameIndex===this.manifest.frames.length-1)this.clock=0;element('play').textContent=this.playing?'Ⅱ Pause':'▶ Play';}
  pause():void{if(this.picking)this.cancelPick();if(this.eventMode){this.eventPlayback.pause();this.routePreview.paused=true;}this.playing=false;element('play').textContent='▶ Play';}
  async tick(delta:number):Promise<void>{
    if(!this.manifest)return;
    if(this.eventMode){
      const ticket=this.eventTicket;
      if(!this.eventFrame&&this.getEventState().phase==='running'){
        this.clock=this.eventPlayback.advance(delta);const frames=this.manifest.frames;
        let index=frames.findIndex(frame=>frame.time_s>this.clock)-1;if(index<0)index=this.clock>=frames.at(-1)!.time_s?frames.length-1:0;
        if(index!==this.frameIndex){
          const pending={ticket,index};this.eventFrame=pending;
          // Never wait for disk/network in the rendering loop: orbiting remains independent.
          void this.showFrame(index).then(shown=>{if(shown&&this.eventPlayback.current(ticket)){this.eventPlayback.presented(ticket,frames[index].time_s);if(this.getEventState().phase==='complete'){this.playing=false;element('play').textContent='▶ Play';element('product-status').textContent='Event complete · explore the computed impact or planned response.';}}})
            .catch(cause=>{if(!this.eventPlayback.current(ticket))return;this.playing=false;const message=cause instanceof Error?cause.message:'Computed frame unavailable.';this.eventPlayback.fail(ticket,message);element('product-status').textContent=message;})
            .finally(()=>{if(this.eventFrame===pending)this.eventFrame=undefined;});
        }else this.eventPlayback.presented(ticket,frames[index].time_s);
        if(this.getEventState().phase==='complete'){this.playing=false;element('play').textContent='▶ Play';}
      }
      this.tickEventVisuals(delta);return;
    }
    if(this.playing){const frames=this.manifest.frames,last=frames.at(-1)!;const frameStep=frames.length>1?frames[1].time_s-frames[0].time_s:1;this.clock=Math.min(last.time_s,this.clock+delta*Number(select('speed').value)*Math.max(1,frameStep));let index=frames.findIndex(frame=>frame.time_s>this.clock)-1;if(index<0)index=this.clock>=last.time_s?frames.length-1:0;if(index!==this.frameIndex)await this.showFrame(index);if(this.clock>=last.time_s)this.pause();}
    this.visuals.tick(this.clock);
  }
  private resetRoutePreview(time:number):void{
    const end=Math.max(time,...this.visuals.getUnits().map(unit=>unit.departure_time_s+unit.duration_s).filter(Number.isFinite));
    this.routePreview={start:time,end,time,paused:this.getEventState().phase==='paused'};
  }
  private tickEventVisuals(delta:number):void{
    const state=this.getEventState(),preview=this.routePreview;
    if(this.stage===3&&this.getResponseData()?.units.length){
      if(!preview.paused&&['running','paused','complete'].includes(state.phase)&&Number.isFinite(delta)&&delta>0)preview.time=Math.min(preview.end,preview.time+Math.min(delta,.25)*Math.max(1,(preview.end-preview.start)/25));
      this.visuals.tick(preview.time,delta);
    }else this.visuals.tick(this.clock,delta);
  }
  async seekFraction(fraction:number):Promise<void>{if(!this.manifest)return;const frames=this.manifest.frames;this.clock=frames[0].time_s+(frames.at(-1)!.time_s-frames[0].time_s)*Math.max(0,Math.min(1,fraction));if(this.eventMode)this.eventPlayback.seek(this.clock);let index=frames.findIndex(frame=>frame.time_s>this.clock)-1;if(index<0)index=this.clock>=frames.at(-1)!.time_s?frames.length-1:0;if(index!==this.frameIndex)await this.showFrame(index);this.visuals.tick(this.clock);}
  private async inspectStage(stage:number):Promise<void>{
    if(stage>1&&this.manifest&&this.clock===this.manifest.frames[0].time_s)await this.scrub(this.manifest.frames.length-1);this.setStage(stage);
  }
  setStage(stage:number):void{
    if(stage!==1&&!this.active)return;if(this.picking)this.cancelPick();
    const enteringResponse=this.eventMode&&stage===3&&this.stage!==3;
    if(this.eventMode&&stage!==3&&this.stage===3){this.responseSnapshot=undefined;this.responsePlanTime=undefined;this.visuals.followUnit();this.visuals.set(this.response);}
    if(enteringResponse)this.pinResponse(this.manifest?.frames[this.frameIndex]?.time_s??this.clock);
    this.stage=stage;
    for(const button of document.querySelectorAll<HTMLButtonElement>('[data-product-step]')){const active=Number(button.dataset.productStep)===stage;button.classList.toggle('active',active);if(active)button.setAttribute('aria-current','step');else button.removeAttribute('aria-current');}
    element('disaster-controls').hidden=stage!==1;element('impact-controls').hidden=stage!==2;element('response-controls').hidden=stage!==3;this.view.setImpactVisible(stage>1);this.visuals.setVisible(stage===3);if(enteringResponse){const id=this.preferredResponseUnit();if(id)this.followResponseUnit(id);}this.legend();this.inputs();if(stage===3){element('briefing-button').hidden=false;void this.briefingStatus();}
  }
  private bandLabels():(string|null)[]{return this.manifest?.labels?.bands??labels[this.manifest?.module??'flood'];}
  private cards():void{
    const metric=this.metrics;if(!metric)return;
    const row=(name:string,value:string)=>{const div=document.createElement('div'),dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=name;dd.textContent=value;div.append(dt,dd);return div;};
    element('impact-card').replaceChildren(row('Affected land',`${format(metric.affected_area_km2,2)} km²`),row('Roads closed',format(metric.roads_closed)),row('People affected',format(metric.people_affected)),row('Unknown building severity',format(metric.buildings_unknown)));
    const partialPopulation=metric.population.available&&metric.people_affected===null;
    if(partialPopulation&&typeof metric.people_in_known_cells==='number'&&Number.isFinite(metric.people_in_known_cells))element('impact-card').append(row('People in covered cells',`${format(metric.people_in_known_cells)} · partial`));
    if(partialPopulation&&typeof metric.affected_population_coverage==='number'&&Number.isFinite(metric.affected_population_coverage))element('impact-card').append(row('Affected-area coverage',`${format(metric.affected_population_coverage*100,1)}%`));
    const populationSource=`${metric.population.source??'WorldPop'} ${metric.population.year}. `;
    const overlap=metric.iou_vs_sentinel_peak;element('overlap').textContent=typeof overlap==='number'?`${(overlap*100).toFixed(1)}%`:'—';element('overlap').title=metric.sentinel_comparison?.caveat??'Real Sentinel-1 comparison unavailable';
    element('population-note').textContent=populationSource+(metric.population.available?(partialPopulation?'Coverage is incomplete. The partial subtotal includes covered cells only; the full total remains unavailable.':'Modelled residential population, not a live count.'):metric.population.status==='downloading'?'Population estimates were still downloading when this run was computed. Run again after the download finishes.':'Population data is unavailable for this run. No estimate is substituted.');
    element('impact-bands').replaceChildren(...this.bandLabels().flatMap((label,i)=>{if(!label||label==='—')return [];const div=document.createElement('div');div.className='impact-band';const swatch=document.createElement('i');swatch.style.backgroundColor=colors[i];const name=document.createElement('span');name.textContent=label;const count=document.createElement('b');count.textContent=`${format(metric.buildings_per_band[i])} buildings`;div.append(swatch,name,count);return[div];}));
    if(typeof metric.iou_vs_sentinel_peak==='number')element('impact-card').append(row('SAR geometric overlap',`${(metric.iou_vs_sentinel_peak*100).toFixed(1)}%`));
    const response=this.response;element('response-card').replaceChildren(row('Units dispatched',format(response?.metrics.units_dispatched)),row('Average modelled ETA',response?.metrics.average_eta_s==null?'Unavailable':`${format(response.metrics.average_eta_s/60,1)} min`),row('Hotspots unreached',format(response?.metrics.hotspots_unreached)),row('Stations cut off',format(response?.metrics.stations_cut_off)));
    element('response-note').textContent=[response?.available?'Modelled units · operational availability unknown. Schools are potential relief sites. Vehicle display scale increases at overview distance.':'Response unavailable.',...(response?.missing??[]),...(response?.limitations??[])].join(' ');
    element('impact-summary').textContent=this.manifest?.module==='chain'?'Combined severity: the higher of earthquake damage and flood depth band. Purple can mean collapse on dry land or deep water; it is not a water-depth-only class.':this.manifest?.module==='quake'?'Hypothetical damage from the selected epicentre, magnitude and available heights.':this.manifest?.module==='fire'?'Hypothetical spread between real footprints. Wind is a chosen scenario input.':'Computed water depth and nearby building exposure. This does not estimate indoor depth.';
  }
  private legend():void{
    const host=element('product-legend');if(this.stage===1||!this.manifest){host.replaceChildren();for(const [color,text] of [['#679dac','Yamuna & water bodies'],['#e9e5d9','Buildings'],['#777b77','Roads & bridges']]){const span=document.createElement('span'),i=document.createElement('i');i.style.backgroundColor=color;span.append(i,document.createTextNode(text));host.append(span);}return;}
    host.replaceChildren(...this.bandLabels().flatMap((label,i)=>{if(!label||label==='—')return[];const span=document.createElement('span'),swatch=document.createElement('i');swatch.style.backgroundColor=colors[i];span.append(swatch,document.createTextNode(`${label} · ${format(this.metrics?.buildings_per_band[i])} buildings`));return[span];}));
    if(this.stage===3){const span=document.createElement('span');span.textContent='◇ Mapped stations · lines: modelled routes';host.append(span);}
  }
  private startPick():void{if(!this.view.ready)return;this.picking=true;this.previousOrbit=this.view.controls.enabled;this.previousBankVisible=this.view.embankments.visible;this.view.controls.enabled=false;if(select('disaster-module').value==='breach')this.view.embankments.visible=true;element('cancel-selection').hidden=false;this.view.renderer.domElement.style.cursor='crosshair';this.inputs();}
  private cancelPick():void{if(this.picking){this.view.controls.enabled=this.previousOrbit;this.view.embankments.visible=this.previousBankVisible;this.view.drawEdit(this.point?[this.point]:[],false);}this.picking=false;this.hover=undefined;element('cancel-selection').hidden=true;element('selection-cursor').hidden=true;this.view.renderer.domElement.style.cursor='';this.view.dirty=true;this.inputs();}
  private hoverPick(event:PointerEvent,force=false):void{
    if(!this.picking||!force&&performance.now()-this.previousHover<70)return;this.previousHover=performance.now();const module=select('disaster-module').value;this.hover=undefined;const point=this.view.pick(event);
    if(module==='fire'){const building=this.view.pickBuilding(event);if(building)this.hover={point:building.point,index:building.index};}
    else if(point){const candidate=module==='breach'?this.view.nearestEmbankment(point):point;if(candidate)this.hover={point:candidate};}
    const label=element('selection-cursor');label.hidden=false;label.style.left=`${event.clientX+16}px`;label.style.top=`${event.clientY+16}px`;label.textContent=this.hover?'Click to select':module==='breach'?'Move onto a mapped embankment':module==='fire'?'Move onto a building roof':'Move onto the model';if(this.hover)this.view.drawEdit([this.hover.point],false);
  }
  drawCapture(context:CanvasRenderingContext2D,width:number,height:number):void {
    if(!this.manifest||this.stage<2)return;context.save();const bandCount=this.bandLabels().filter(label=>label&&label!=='—').length;context.fillStyle='#111a20e8';context.fillRect(width-400,24,376,bandCount*28+50);context.font='15px sans-serif';context.fillStyle='#eee9df';context.fillText(this.manifest.module==='chain'?'Combined damage / depth severity':'Buildings by computed severity',width-380,48);context.font='18px sans-serif';
    let row=0;for(const [i,label] of this.bandLabels().entries()){if(!label||label==='—')continue;const y=72+row++*28;context.fillStyle=colors[i];context.fillRect(width-380,y-14,12,12);context.fillStyle='#eee9df';context.fillText(`${label} · ${format(this.metrics?.buildings_per_band[i])} buildings`,width-358,y);}
    if(this.stage===3)this.visuals.drawCapture(context,width,height);context.restore();
  }
  private async briefingStatus():Promise<void>{try{const status=await json<{ready?:boolean;inference_ready?:boolean;reason?:string;message?:string}>('/api/gemini/status');element('briefing-status').textContent=status.ready||status.inference_ready?'Briefing uses this frame’s computed results.':status.reason??status.message??'Briefing availability is checked when requested.';}catch{element('briefing-status').textContent='Briefing service unavailable. Computed results remain available.';}}
  private async briefing():Promise<void>{
    const run=this.manifest,index=this.frameIndex;if(!run)return;
    const button=element<HTMLButtonElement>('briefing-button');button.disabled=true;element('briefing-status').textContent='Preparing advice from this frame’s computed files…';
    try{
      const result=await json<Recommendation>(`/api/product/runs/${encodeURIComponent(run.run_id)}/advisory`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({frame_index:index})});
      if(this.manifest!==run||this.frameIndex!==index)return;
      renderAdvisory(element('briefing-text'),result);element('briefing-text').hidden=false;element('briefing-status').textContent=result.label;
    }catch(cause){if(this.manifest===run&&this.frameIndex===index)element('briefing-status').textContent=cause instanceof Error?cause.message:'Advisory unavailable.';}
    finally{button.disabled=false;}
  }
}
