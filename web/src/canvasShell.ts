import type { Diorama } from './model';
import type { ProductFlow, CachedEvent } from './product';
import './canvasShell.css';

const node=<K extends keyof HTMLElementTagNameMap>(tag:K,className='',text=''):HTMLElementTagNameMap[K]=>{const e=document.createElement(tag);e.className=className;e.textContent=text;return e;};
const fmt=(value:number|null|undefined,digits=0)=>typeof value==='number'&&Number.isFinite(value)?value.toLocaleString(undefined,{maximumFractionDigits:digits}):'Unknown';
const simulatedClock=(time:number)=>{const seconds=Math.max(0,Math.round(time));return `${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`;};
const icon=(name:string):SVGSVGElement=>{
  const paths:Record<string,string>={flood:'M3 7c3-4 5 4 9 0s6 0 9 0M3 12c3-4 5 4 9 0s6 0 9 0M3 17c3-4 5 4 9 0s6 0 9 0',quake:'M12 2 8 9l7 2-7 5 4 6M3 19h4M17 5h4',fire:'M13 3c1 5 6 7 6 12a7 7 0 0 1-14 0c0-4 3-7 5-9 0 4 1 5 2 6 2-3 2-6 1-9Z',breach:'M3 19h5l2-7 3 3 3-10h5M2 10h5M17 16h5',chain:'m3 6 4-4 4 4M7 2v12M13 9c3-4 5 4 9 0M13 15c3-4 5 4 9 0M13 21c3-4 5 4 9 0',layers:'m12 3 10 5-10 5L2 8l10-5Zm-9 10 9 5 9-5M3 18l9 5 9-5',area:'M4 8V4h4M16 4h4v4M20 16v4h-4M8 20H4v-4',info:'M12 10v7M12 6h.01M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',fit:'M8 3H3v5M16 3h5v5M21 16v5h-5M8 21H3v-5',top:'m12 3 9 9-9 9-9-9 9-9ZM12 3v18M3 12h18',plus:'M12 5v14M5 12h14',minus:'M5 12h14'};
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('fill','none');svg.setAttribute('stroke','currentColor');svg.setAttribute('stroke-linecap','round');svg.setAttribute('stroke-linejoin','round');svg.setAttribute('aria-hidden','true');svg.classList.add('canvas-icon');const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',paths[name]??paths.info);svg.append(path);return svg;
};
const button=(label:string,fn:()=>void,iconName?:string)=>{const b=node('button');b.type='button';b.setAttribute('aria-label',label);b.title=label;if(iconName)b.append(icon(iconName));b.append(node('span','',label));b.onclick=fn;return b;};
const titles:Record<string,string>={flood:'Flood',quake:'Earthquake',fire:'Fire',breach:'Bank cut',chain:'Quake → flood'};

/** Small canvas tools. Only published cached events are exposed. */
export class CanvasShell {
  private root=node('div');private tools=node('nav');private inspector=node('aside');private popover=node('aside');
  private status=node('div');private statusText=node('span','canvas-status-text','Loading the real model…');
  private pauseButton=button('Pause',()=>this.togglePause());private restartButton=button('Restart',()=>void this.restart());private resetButton=button('Reset',()=>this.reset());
  private heading=node('h2');private subtitle=node('p','canvas-muted');private facts=node('dl','canvas-facts');private legend=node('div','canvas-legend');private note=node('p','canvas-note');
  private brief=node('div','canvas-brief');private viewButtons=node('div','canvas-segmented');private impactButton=button('Impact',()=>this.setMode('impact'));private responseButton=button('Response',()=>this.setMode('response'));
  private focusButton=button('Locate event',()=>this.focus());private aiButton=button('Explain with Gemini',()=>void this.explain());
  private responseControls=node('div','canvas-response-follow');private responsePlan=node('p','canvas-response-plan');private responseSelect=node('select');private responseState=node('p','canvas-response-state');private responseProgress=node('progress');private responseModel=node('p','canvas-response-model');
  private followButton=button('Follow selected',()=>this.toggleFollow());private replayButton=button('Replay route',()=>this.replayRoute());private responseOptions='';
  private eventButtons=new Map<string,HTMLButtonElement>();private entries:CachedEvent[]=[];private ready=false;private remote=false;private sequence=0;private mode:'impact'|'response'='impact';private previous='';private lastUpdate=0;private popoverKind='';private busyBrief=false;private briefVersion=0;
  constructor(private view:Diorama,private product:ProductFlow,private actions:{prepareEvent:()=>void;reset:()=>void;openArea:()=>void;closeArea:()=>void;openProof:()=>void}){
    document.body.classList.add('canvas-workspace');this.root.id='canvas-workspace';
    const brand=node('div','canvas-island');brand.id='canvas-brand';brand.append(node('strong','','otec'),node('small','canvas-muted','Delhi · Yamuna'));
    this.tools.id='canvas-tools';this.tools.className='canvas-island';this.tools.setAttribute('aria-label','Start a simulated event');this.tools.append(node('span','canvas-muted','Preparing events…'));
    const toolMenu=node('div','canvas-island');toolMenu.id='canvas-actions';toolMenu.append(button('Area',()=>{this.closePopover();this.actions.openArea();},'area'),button('Layers',()=>this.showPopover('layers'),'layers'),button('Info',()=>this.showPopover('info'),'info'));
    this.inspector.id='canvas-inspector';this.inspector.className='canvas-island';this.inspector.hidden=true;this.inspector.setAttribute('aria-label','Simulated event impact');
    this.responseControls.hidden=true;this.responseSelect.id='canvas-response-unit';const unitLabel=node('label','','Follow vehicle');unitLabel.htmlFor=this.responseSelect.id;this.responseSelect.onchange=()=>{if(this.responseSelect.value)this.product.followResponseUnit(this.responseSelect.value);this.update(performance.now(),true);};this.responseProgress.max=1;this.responseProgress.value=0;this.responseProgress.setAttribute('aria-label','Selected vehicle route progress');const followActions=node('div','canvas-response-actions');followActions.append(this.followButton,this.replayButton);this.responseControls.append(this.responsePlan,unitLabel,this.responseSelect,this.responseState,this.responseProgress,followActions,this.responseModel);
    this.impactButton.disabled=true;this.responseButton.disabled=true;this.viewButtons.append(this.impactButton,this.responseButton);this.inspector.append(node('span','canvas-eyebrow','SIMULATED EVENT'),this.heading,this.subtitle,this.viewButtons,this.responseControls,this.facts,this.legend,this.note,this.focusButton,this.aiButton,this.brief);
    this.status.id='canvas-event-status';this.status.className='canvas-island';this.status.setAttribute('role','status');this.status.setAttribute('aria-live','polite');this.status.append(this.statusText,this.pauseButton,this.restartButton,this.resetButton);
    for(const item of [this.pauseButton,this.restartButton,this.resetButton])item.hidden=true;
    const nav=node('nav','canvas-island');nav.id='canvas-nav';nav.setAttribute('aria-label','Canvas navigation');nav.append(button('Zoom out',()=>this.zoom(1.3),'minus'),button('Fit',()=>this.fit(),'fit'),button('Top',()=>{this.stopFollow();if(this.view.ready)this.view.preset('top');},'top'),button('Zoom in',()=>this.zoom(1/1.3),'plus'));
    const source=node('div');source.id='canvas-source';source.append(button('Sources & limits',()=>this.showPopover('info')),node('span','canvas-muted','Drag to orbit · scroll to zoom'));
    this.popover.id='canvas-popover';this.popover.className='canvas-island';this.popover.hidden=true;
    this.root.append(brand,this.tools,toolMenu,this.inspector,this.status,nav,source,this.popover);document.body.append(this.root);
    window.addEventListener('keydown',e=>{if(e.key==='Escape'){this.stopFollow();this.closePopover();}if(e.defaultPrevented||e.target instanceof Element&&e.target.closest('button,a,input,textarea,select,[role=button],[contenteditable=true]')||e.metaKey||e.ctrlKey||e.altKey)return;if(e.code==='Space'&&this.product.active){e.preventDefault();this.togglePause();}if(e.key.toLowerCase()==='f')this.fit();});
  }
  async initialize(remote=false):Promise<void>{
    this.remote=remote;
    if(remote){this.tools.replaceChildren(button('Open Delhi events',()=>location.assign('/')));this.statusText.textContent='Real selected model · simulation events are available for Delhi.';this.ready=true;return;}
    try{this.entries=await this.product.prepareEvents();this.tools.replaceChildren();this.eventButtons.clear();
      for(const entry of this.entries){const b=button(titles[entry.module]??entry.label,()=>void this.start(entry.run_id),entry.module);b.className='canvas-event';b.title=entry.label;b.dataset.runId=entry.run_id;b.dataset.module=entry.module;b.setAttribute('aria-pressed','false');this.eventButtons.set(entry.run_id,b);this.tools.append(b);}
      if(!this.entries.length)throw new Error('No complete event is available for this model.');this.ready=true;this.statusText.textContent='Choose an event above. The canvas stays yours.';this.view.resize();this.view.fit(false);
    }catch(error){this.tools.replaceChildren(button('Retry events',()=>void this.initialize()));this.statusText.textContent=error instanceof Error?error.message:'Events unavailable.';}
  }
  private async start(id:string):Promise<void>{
    if(!this.ready||this.remote)return;const sequence=++this.sequence;this.briefVersion++;this.busyBrief=false;this.brief.replaceChildren();this.aiButton.disabled=false;this.actions.prepareEvent();this.closePopover();this.mode='impact';this.inspector.hidden=false;this.view.resize();this.statusText.textContent='Starting event…';
    try{await this.product.startEvent(id);if(sequence!==this.sequence||this.product.getEventState().run_id!==id||this.product.getEventState().phase==='idle')return;this.focus();this.previous='';this.update(performance.now(),true);}catch(error){if(sequence===this.sequence)this.statusText.textContent=error instanceof Error?error.message:'Event unavailable. Choose another event or reset.';}
  }
  private async restart():Promise<void>{const state=this.product.getEventState();if(state.run_id)await this.start(state.run_id);}
  private reset():void{this.sequence++;this.briefVersion++;this.actions.reset();this.product.resetEvent();this.inspector.hidden=true;this.brief.replaceChildren();this.mode='impact';this.previous='';this.view.resize();this.update(performance.now(),true);}
  private togglePause():void{const state=this.product.getEventState();if(state.phase==='running'||state.route_preview_playing)this.product.pauseEvent();else if(state.phase==='paused'||(state.route_preview_duration_s??0)>0)this.product.resumeEvent();this.update(performance.now(),true);}
  private setMode(mode:'impact'|'response'):void{if(!this.product.active||['loading','idle','error'].includes(this.product.getEventState().phase))return;this.mode=mode;this.product.setEventView(mode);this.previous='';this.update(performance.now(),true);}
  private stopFollow():void{this.product.followResponseUnit();this.update(performance.now(),true);}
  private fit():void{this.stopFollow();if(this.view.ready)this.view.fit();}
  private focus():void{this.stopFollow();const focus=this.product.getEventFocus();if(focus)this.view.focusPoint(focus.point,focus.radius_m);else if(this.view.ready)this.view.fit();}
  private zoom(factor:number):void{this.stopFollow();if(!this.view.ready)return;const target=this.view.controls.target,position=this.view.camera.position.clone().sub(target).multiplyScalar(factor).add(target);this.view.cameraAt(position.toArray(),target.toArray());}
  private toggleFollow():void{const follow=this.product.getResponseFollowState();if(follow.phase==='following')this.product.followResponseUnit();else if(this.responseSelect.value)this.product.followResponseUnit(this.responseSelect.value);this.update(performance.now(),true);}
  private replayRoute():void{if(this.responseSelect.value)this.product.followResponseUnit(this.responseSelect.value);else this.product.replayResponse();this.update(performance.now(),true);}
  private updateResponseControls():void{
    this.responseControls.hidden=this.mode!=='response';if(this.responseControls.hidden)return;
    const units=this.product.getResponseUnits(),follow=this.product.getResponseFollowState(),planTime=this.product.getResponsePlanTime();
    this.responsePlan.textContent=planTime===undefined?'Plan snapshot unavailable':`Plan at ${simulatedClock(planTime)} simulated`;
    const optionKey=JSON.stringify(units.map(unit=>[unit.id,unit.label]));
    if(optionKey!==this.responseOptions){const selected=this.responseSelect.value;this.responseOptions=optionKey;const existing=new Map(Array.from(this.responseSelect.options,option=>[option.value,option]));for(const option of Array.from(this.responseSelect.options))if(!units.some(unit=>unit.id===option.value))option.remove();for(const unit of units){let option=existing.get(unit.id);if(!option){option=new Option(unit.label,unit.id);this.responseSelect.add(option);}else if(option.textContent!==unit.label)option.textContent=unit.label;}if(units.some(unit=>unit.id===selected))this.responseSelect.value=selected;}
    if(follow.unitId&&units.some(unit=>unit.id===follow.unitId))this.responseSelect.value=follow.unitId;
    const selected=units.find(unit=>unit.id===this.responseSelect.value)??units[0];if(selected)this.responseSelect.value=selected.id;
    this.responseSelect.disabled=!units.length;this.followButton.disabled=!selected;this.replayButton.disabled=!selected;this.responseProgress.hidden=!selected;this.responseModel.hidden=!selected;
    if(!selected){this.responseState.textContent='No reachable modelled route in this plan.';this.followButton.textContent='Follow selected';this.followButton.setAttribute('aria-label','Follow selected');return;}
    const progress=Math.max(0,Math.min(1,follow.unitId===selected.id&&follow.phase!=='off'?follow.progress:selected.progress));this.responseProgress.value=progress;this.responseProgress.setAttribute('aria-valuetext',`${Math.round(progress*100)}% of the modelled route`);
    const phase=follow.unitId===selected.id?follow.phase:'off';this.responseState.textContent=phase==='arrived'||progress>=1?`Route complete · ${selected.label}`:phase==='following'?`Following ${selected.label} · ${Math.round(progress*100)}%`:`Camera free · route ${Math.round(progress*100)}%`;
    this.followButton.textContent=phase==='following'?'Stop following':phase==='arrived'||progress>=1?'Follow again':'Follow selected';this.followButton.setAttribute('aria-label',this.followButton.textContent);this.followButton.title=this.followButton.textContent;
    this.responseModel.textContent=selected.modelAvailable?'Illustrative Kenney CC0 model · enlarged in overview.':'3D vehicle model unavailable · computed path remains visible.';
  }
  private closePopover():void{this.popover.hidden=true;this.popoverKind='';}
  private showPopover(kind:'layers'|'info'):void{
    if(!this.popover.hidden&&this.popoverKind===kind){this.closePopover();return;}this.actions.closeArea();this.popoverKind=kind;this.popover.dataset.view=kind;this.popover.hidden=false;this.popover.replaceChildren();
    const head=node('div','canvas-panel-heading');head.append(node('h2','',kind==='layers'?'Layers':'About this model'),button('Close',()=>this.closePopover()));this.popover.append(head);
    if(kind==='layers'){
      for(const [id,label] of [['buildings','Buildings'],['river','River'],['roads','Roads & bridges'],['green','Green areas']]){const original=document.getElementById(id) as HTMLInputElement;if(original.disabled)continue;const row=node('label','canvas-toggle');const check=node('input');check.type='checkbox';check.checked=original.checked;check.onchange=()=>{original.checked=check.checked;original.dispatchEvent(new Event('change'));};row.append(check,document.createTextNode(label));this.popover.append(row);}
      const heightLabel=node('label','','Building height');heightLabel.htmlFor='canvas-building-height';const heightValue=node('output','canvas-count',`${this.view.buildingHeightScale}×`);heightLabel.append(heightValue);const height=node('input');height.id='canvas-building-height';height.type='range';height.min='1';height.max='10';height.step='1';height.value=String(this.view.buildingHeightScale);height.oninput=()=>{const factor=Number(height.value);this.view.exaggerateBuildings=true;this.view.setBuildingExaggeration(factor);heightValue.textContent=`${factor}×`;const original=document.getElementById('building-exaggeration') as HTMLInputElement;original.value=height.value;document.getElementById('building-exaggeration-value')!.textContent=`${factor}×`;(document.getElementById('exaggerate-buildings') as HTMLInputElement).checked=true;};this.popover.append(heightLabel,height);
      this.popover.append(node('p','canvas-muted','Height scale is display only. Buildings gain detail as you move closer.'));
    }else{
      this.popover.append(node('p','','Real FABDEM ground, OpenStreetMap features and Google building footprints. Heights and source dates have limits.'),node('p','canvas-note','Events use computed local simulation states on this interactive 3D model. They are hypothetical, not forecasts. Current incidents require separate reports and verification.'));
      for(const id of ['building-sources','height-note','ground-note']){const text=document.getElementById(id)?.textContent;if(text)this.popover.append(node('p','canvas-note',text));}
      for(const [label,url] of [['FABDEM · CC BY-NC-SA 4.0','https://doi.org/10.5523/bris.s5hqmjcdj8yo2ibzi9b4ew3sn'],['© OpenStreetMap contributors','https://www.openstreetmap.org/copyright'],['Google Open Buildings','https://sites.research.google/gr/open-buildings/']]){const a=node('a','',label);a.href=url;a.target='_blank';a.rel='noreferrer';this.popover.append(a);}
      this.popover.append(node('p','canvas-note','July 2023 replay is unavailable: radar masks and calibration are missing.'),button('Drone proof & validation',()=>{this.closePopover();this.actions.openProof();}));
    }
  }
  private async explain():Promise<void>{
    if(this.busyBrief||!this.product.manifest)return;const version=++this.briefVersion;const run=this.product.manifest;this.busyBrief=true;this.aiButton.disabled=true;this.brief.replaceChildren(node('p','canvas-muted','Reading this event’s computed evidence…'));
    const time=(this.mode==='response'?this.product.getResponsePlanTime():undefined)??this.product.getEventState().time_s;let frame=0;run.frames.forEach((f,i)=>{if(f.time_s<=time)frame=i;});
    try{const response=await fetch(`/api/product/runs/${encodeURIComponent(run.run_id)}/briefing`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({frame_index:frame})});const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Gemini is unavailable.');if(version!==this.briefVersion)return;this.brief.replaceChildren(node('p','canvas-note',`At ${Math.floor(run.frames[frame].time_s/60)}:${String(Math.round(run.frames[frame].time_s%60)).padStart(2,'0')} simulated · ${data.label??'AI-generated · human review required'}`),node('p','canvas-brief-text',data.text??data.briefing??'No explanation returned.'));}
    catch(error){if(version===this.briefVersion)this.brief.replaceChildren(node('p','canvas-note',error instanceof Error?error.message:'Gemini unavailable.'));}
    finally{if(version===this.briefVersion){this.busyBrief=false;this.aiButton.disabled=false;}}
  }
  update(now:number,force=false):void{
    if(!force&&now-this.lastUpdate<150)return;this.lastUpdate=now;if(!this.ready||this.remote)return;
    const s=this.product.getEventState(),follow=this.product.getResponseFollowState(),units=this.product.getResponseUnits();const key=JSON.stringify([s.phase,s.run_id,Math.floor(s.time_s),this.product.metrics?.time_s,this.mode,s.error,Math.floor(s.route_preview_s??0),s.route_preview_playing,this.product.getResponsePlanTime(),follow.unitId,follow.phase,Math.round(follow.progress*100),follow.modelAvailable,units.map(unit=>[unit.id,Math.round(unit.progress*100),unit.modelAvailable])]);if(!force&&key===this.previous)return;this.previous=key;
    this.root.dataset.phase=s.phase;const active=s.phase!=='idle';this.inspector.hidden=!active;const admitted=this.product.active&&['running','paused','complete'].includes(s.phase);this.impactButton.disabled=!admitted;this.responseButton.disabled=!admitted;
    for(const [id,b] of this.eventButtons)b.setAttribute('aria-pressed',String(id===s.run_id));
    const responsePreview=this.mode==='response',routeRemaining=(s.route_preview_duration_s??0)>0&&(s.route_preview_s??0)<(s.route_preview_duration_s??0);
    this.pauseButton.hidden=responsePreview?!routeRemaining:!['running','paused'].includes(s.phase);this.pauseButton.textContent=responsePreview?s.route_preview_playing?'Pause':'Resume':s.phase==='paused'?'Resume':'Pause';this.pauseButton.setAttribute('aria-label',this.pauseButton.textContent);this.pauseButton.title=this.pauseButton.textContent;
    this.restartButton.hidden=!['complete','paused','error'].includes(s.phase);this.resetButton.hidden=!active;
    const seconds=Math.round(s.time_s);const clock=`${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}`;
    this.statusText.textContent=s.phase==='idle'?'Choose an event above. Orbit and zoom any time.':s.phase==='loading'?'Starting event…':s.phase==='error'?s.error??'This event could not load.':`${s.phase==='running'?'Event unfolding':s.phase==='paused'?'Hazard paused':'Event complete'} · ${clock} simulated${responsePreview&&s.route_preview_duration_s?s.route_preview_playing?' · routes playing':routeRemaining?' · routes paused':' · routes complete':''}`;
    if(!active)return;const entry=this.entries.find(e=>e.run_id===s.run_id);this.heading.textContent=entry?.label??'Starting event';this.subtitle.textContent='Hypothetical, not a forecast · Delhi model';this.impactButton.setAttribute('aria-pressed',String(this.mode==='impact'));this.responseButton.setAttribute('aria-pressed',String(this.mode==='response'));
    this.updateResponseControls();this.facts.replaceChildren();this.legend.replaceChildren();const m=this.product.metrics,r=this.product.getResponseData();
    const row=(label:string,value:string)=>{const item=node('div');item.append(node('dt','',label),node('dd','',value));this.facts.append(item);};
    if(m){
      if(this.mode==='impact'){row('Affected land',m.affected_area_km2!=null&&m.affected_area_km2>0&&m.affected_area_km2<.01?`${fmt(m.affected_area_km2*1e6)} m²`:`${fmt(m.affected_area_km2,2)} km²`);row('Buildings affected',fmt(m.buildings_per_band?.slice(1).reduce((a,b)=>a+b,0)));row('Road segments affected',fmt(m.roads_closed));const partial=m.people_affected==null&&m.people_in_known_cells!=null;row(partial?'Modelled residents · partial':'Modelled residents',fmt(partial?m.people_in_known_cells:m.people_affected));this.note.textContent=partial?'Population coverage is incomplete. These are modelled residents, not live occupancy or casualties.':'Metrics from this simulated state. Road exposure does not confirm a real closure.';}
      else{row('Planned units',fmt(r?.metrics.units_dispatched));row('Modelled ETA',r?.metrics.average_eta_s==null?'Unknown':`${fmt(r.metrics.average_eta_s/60,1)} min`);row('Unreached hotspots',fmt(r?.metrics.hotspots_unreached));this.note.textContent='Pinned plan snapshot, not the updating hazard. Mapped stations and computed routes; staff, vehicle availability and road access are unverified. Animation is a simulation, not an actual dispatch.';}
      if(['quake','chain','fire'].includes(this.product.manifest?.module??''))this.note.textContent+=' Animated damage and fire illustrate the saved simulation states; they are not structural or fire-physics predictions.';
      if(this.mode==='impact'&&this.product.manifest?.module==='breach')this.note.textContent+=' The river rises 3 m; the bank cut adds a small local change.';const caption=node('strong','canvas-legend-title',this.product.manifest?.module==='flood'||this.product.manifest?.module==='breach'?'Building exposure · water depth':'Computed severity');this.legend.append(caption);const bands=this.product.manifest?.labels.bands??[];for(const [i,label] of bands.entries()){if(!label||label==='—')continue;const item=node('span');const swatch=node('i');swatch.style.backgroundColor=['#66ad85','#e1cd65','#e19a4c','#d85b53','#74457f'][i];item.append(swatch,document.createTextNode(label));this.legend.append(item);}
    }else this.note.textContent='Loading the computed event state…';
    this.focusButton.disabled=!this.product.active;this.aiButton.hidden=!this.product.active;
  }
}
