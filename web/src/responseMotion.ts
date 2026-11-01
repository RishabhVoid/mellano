import type { ResponseUnit } from './types';
import { calculateDelhiTraffic } from './trafficSim.ts';

export interface RouteSample {
  position:[number,number];
  heading:number;
  progress:number;
  duration_s:number;
  waiting:boolean;
  arrived:boolean;
  speed_kmh?:number;
  traffic_index?:number;
  traffic_description?:string;
  traffic_label?:string;
}
export function validRoute(unit:ResponseUnit):boolean {
  return unit.path.length>=1&&unit.path.length===unit.path_times_s.length&&Number.isFinite(unit.departure_time_s)&&unit.path.every(point=>point.length===2&&point.every(Number.isFinite))&&unit.path_times_s.every((time,index)=>Number.isFinite(time)&&time>=0&&(index===0||time>=unit.path_times_s[index-1]));
}
/** Interpolate route with realistic simulated vehicle speed and calculated Delhi traffic congestion. */
export function sampleRoute(unit:ResponseUnit,time:number):RouteSample {
  const path=unit.path,times=unit.path_times_s,duration=times.at(-1)??0;
  const elapsed=Number.isFinite(time)?Math.max(0,time-unit.departure_time_s):0;
  const waiting=time<unit.departure_time_s;
  const arrived=time>=unit.departure_time_s+duration;
  if(path.length===1){
    const traffic=calculateDelhiTraffic(path[0] as [number,number],time,unit.type);
    return {position:[...path[0]],heading:0,progress:duration>0?Math.min(1,elapsed/duration):time>=unit.departure_time_s?1:0,duration_s:duration,waiting,arrived,speed_kmh:0,traffic_index:traffic.congestion,traffic_description:arrived?'Arrived at target location':'Staged at facility',traffic_label:traffic.label};
  }
  let low=0,high=times.length;
  while(low<high){const middle=(low+high)>>>1;if(times[middle]<=elapsed)low=middle+1;else high=middle;}
  const b=Math.max(1,Math.min(path.length-1,low)),a=b-1;
  const mix=waiting?0:elapsed>=duration?1:Math.max(0,Math.min(1,(elapsed-times[a])/(times[b]-times[a]||1)));
  const position:[number,number]=[path[a][0]+(path[b][0]-path[a][0])*mix,path[a][1]+(path[b][1]-path[a][1])*mix];
  let heading=0;
  for(let offset=0;offset<path.length-1;offset++){
    const start=offset<=a?a-offset:offset,end=start+1;
    const dx=path[end][0]-path[start][0],dz=path[end][1]-path[start][1];if(dx||dz){heading=Math.atan2(dx,dz);break;}
  }
  const traffic=calculateDelhiTraffic(position,time,unit.type);
  const currentSpeed=waiting||arrived?0:traffic.speedKmh;
  return {
    position,
    heading,
    progress:duration>0?Math.min(1,elapsed/duration):time>=unit.departure_time_s?1:0,
    duration_s:duration,
    waiting,
    arrived,
    speed_kmh:currentSpeed,
    traffic_index:traffic.congestion,
    traffic_description:traffic.description,
    traffic_label:traffic.label
  };
}
export function vehicleDisplayScale(length_m:number,distance:number,fov:number,height:number):number {
  const worldPerPixel=2*Math.max(0,distance)*Math.tan(fov*Math.PI/360)/Math.max(1,height);
  return Math.max(1,Math.min(60,22*worldPerPixel/(Math.max(.1,length_m)*.001)));
}
export function smoothHeading(from:number,to:number,blend:number):number{return from+Math.atan2(Math.sin(to-from),Math.cos(to-from))*Math.max(0,Math.min(1,blend));}

export function remainingRouteSeconds(unit:ResponseUnit,time:number):number{return Math.max(0,unit.departure_time_s+(unit.path_times_s.at(-1)??0)-time);}

/** Extra display samples stay on each saved segment. Retain every turn, cap extra work. */
export function drapeRoute(path:[number,number][],spacing=20,cap=2048):[number,number][]|undefined {
  if(path.length>cap)return undefined; // Never shortcut an over-budget source route.
  const divisions=path.slice(1).map((point,index)=>Math.max(1,Math.ceil(Math.hypot(point[0]-path[index][0],point[1]-path[index][1])/spacing)));
  const extra=divisions.map(value=>value-1),wanted=extra.reduce((sum,value)=>sum+value,0),budget=cap-path.length;
  if(wanted>budget){
    const allocation=extra.map(value=>value*budget/wanted),parts=allocation.map(Math.floor);
    let left=budget-parts.reduce((sum,value)=>sum+value,0);
    for(const index of parts.map((_,index)=>index).sort((a,b)=>(allocation[b]-parts[b])-(allocation[a]-parts[a])||a-b)){if(left--<=0)break;parts[index]++;}
    for(let index=0;index<divisions.length;index++)divisions[index]=parts[index]+1;
  }
  const result:[number,number][]=path.length?[[...path[0]]]:[];
  for(let index=0;index<divisions.length;index++){const a=path[index],b=path[index+1];for(let step=1;step<=divisions[index];step++){const fraction=step/divisions[index];result.push([a[0]+(b[0]-a[0])*fraction,a[1]+(b[1]-a[1])*fraction]);}}
  return result;
}
