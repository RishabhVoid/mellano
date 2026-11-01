import type { DisasterModule, ProductCatalog } from './types';

export type EventPhase = 'idle'|'loading'|'running'|'paused'|'complete'|'error';
export interface EventState {
  phase: EventPhase;
  run_id?: string;
  module?: DisasterModule;
  time_s: number;
  duration_s: number;
  progress: number;
  /** Simulated seconds per real second, independent of cached frame spacing. */
  speed: number;
  error?: string;
  route_preview_s?: number;
  route_preview_duration_s?: number;
  route_preview_playing?: boolean;
}
export interface CachedEvent {
  run_id: string;
  preset_id: string;
  module: DisasterModule;
  label: string;
  manifest_url: string;
  created_at?: string;
}

/** The catalog publishes completed runs only; require its explicit freshness flag. */
export function currentCachedEvents(catalog?:ProductCatalog):CachedEvent[] {
  if(!catalog)return [];
  return catalog.presets.flatMap(preset=>{
    if(!preset.enabled||preset.id==='replay2023'||!catalog.modules.some(module=>module.id===preset.scenario.module&&module.enabled))return [];
    const current=(catalog.runs??[]).filter(run=>run.preset_id===preset.id&&run.module===preset.scenario.module&&run.source_current===true)
      .filter(run=>/^product_[A-Za-z0-9_-]+$/.test(run.run_id)&&run.manifest_url===`/data/product/runs/${run.run_id}/manifest.json`)
      .sort((a,b)=>(b.created_at??'').localeCompare(a.created_at??'')||b.run_id.localeCompare(a.run_id))[0];
    return current?[{run_id:current.run_id,preset_id:preset.id,module:current.module,label:preset.label,manifest_url:current.manifest_url,created_at:current.created_at}]:[];
  });
}

const idle=():EventState=>({phase:'idle',time_s:0,duration_s:0,progress:0,speed:0});

/** Pure admission and playback state. A ticket invalidates every older async completion. */
export class EventPlayback {
  private version=0;
  private state:EventState=idle();
  private startTime=0;
  private endTime=0;
  private presentationSeconds=30;

  snapshot():EventState{return {...this.state};}
  current(ticket:number):boolean{return ticket===this.version;}
  begin(run_id:string,module?:DisasterModule):number {
    this.state={...idle(),phase:'loading',run_id,module};this.presentationSeconds=module==='quake'?6:30;
    this.startTime=0;this.endTime=0;return ++this.version;
  }
  load(ticket:number,times:readonly number[]):boolean {
    if(!this.current(ticket))return false;
    if(!times.length||times.some((time,index)=>!Number.isFinite(time)||time<0||(index>0&&time<=times[index-1])))throw new Error('Computed event has an invalid simulation timeline.');
    this.startTime=times[0];this.endTime=times.at(-1)!;
    const duration=this.endTime-this.startTime;
    this.state={...this.state,phase:duration>0?'running':'complete',time_s:this.startTime,duration_s:duration,progress:duration>0?0:1,speed:duration/this.presentationSeconds,error:undefined};
    return true;
  }
  advance(delta:number):number {
    if(this.state.phase!=='running'||!Number.isFinite(delta)||delta<=0)return this.state.time_s;
    // A suspended browser must not skip the event when it returns to the foreground.
    const next=this.state.time_s+Math.min(delta,.25)*this.state.speed;
    this.state.time_s=next>=this.endTime-1e-9?this.endTime:next;
    this.state.progress=this.state.duration_s>0?(this.state.time_s-this.startTime)/this.state.duration_s:1;
    return this.state.time_s;
  }
  presented(ticket:number,time:number):void {
    if(this.current(ticket)&&this.state.phase==='running'&&time>=this.endTime&&this.state.time_s>=this.endTime)this.state.phase='complete';
  }
  pause():void{if(this.state.phase==='running')this.state.phase='paused';}
  resume():void{if(this.state.phase==='paused')this.state.phase='running';}
  seek(time:number):void {
    if(['idle','loading','error'].includes(this.state.phase)||!Number.isFinite(time))return;
    this.state.time_s=Math.max(this.startTime,Math.min(this.endTime,time));
    this.state.progress=this.state.duration_s>0?(this.state.time_s-this.startTime)/this.state.duration_s:1;
    this.state.phase='paused';
  }
  fail(ticket:number,message:string):void{if(this.current(ticket))this.state={...this.state,phase:'error',error:message};}
  reset():void{this.version++;this.state=idle();this.startTime=0;this.endTime=0;}
}
