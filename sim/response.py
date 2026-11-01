"""Modelled response planning on actual OSM topology; no operational availability claims."""
import heapq,math
import networkx as nx
import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

ASSUMPTIONS=['Demonstration fleet: at most one assumed ambulance, fire engine, police unit and rescue boat selected from real mapped facilities; actual vehicles, staffing and capacity are unknown.','Road speed comes from OSM maxspeed or explicitly documented class defaults; no live traffic.','Facility and staging connectors are unsurveyed straight-line access assumptions at 5 km/h, limited to 150m and 500m respectively.','Schools are potential relief sites only, never confirmed camps.','Vehicle movement is modelled. Planning is greedy shortest travel time with pairwise local improvement, not a global optimum.']
LIMITATIONS=['Road closures are modelled, not observed. Bridge decks and tunnel elevations are unavailable; ground-depth sampling can falsely close grade-separated roads.','Routing uses shared OSM nodes and directed edges only; no invented intersection at line crossings.','Station cut-off means disconnected from the largest open weak road component, not a verified loss of access.','Routes end at safe road staging nodes near hotspots; last-metre rescue and service durations are not modelled.']

def position_at(unit,time_s):
    path=unit.get('path',[]);times=unit.get('path_times_s',[])
    if not path:return unit.get('position',[0,0])
    elapsed=max(0,time_s-unit.get('departure_time_s',time_s))
    if not times or elapsed>=times[-1]:return path[-1]
    i=max(0,int(np.searchsorted(times,elapsed,side='right'))-1)
    fraction=(elapsed-times[i])/max(1e-9,times[i+1]-times[i])
    return (np.asarray(path[i])*(1-fraction)+np.asarray(path[i+1])*fraction).tolist()

def water_path(wet,start,end,cell,max_visits=30000):
    """A* on actual wet cells; diagonal dry-corner shortcuts are forbidden."""
    start=tuple(start);end=tuple(end)
    if not wet[start] or not wet[end]:return None,'dry_endpoint'
    def heuristic(p):return math.hypot(p[0]-end[0],p[1]-end[1])
    queue=[(heuristic(start),0,start)];distance={start:0};parent={};visits=0
    while queue:
        _,cost,p=heapq.heappop(queue)
        if cost!=distance.get(p):continue
        visits+=1
        if p==end:
            path=[p]
            while path[-1]!=start:path.append(parent[path[-1]])
            return list(reversed(path)),None
        if visits>max_visits:return None,'bounded_search_limit'
        for dr,dc in ((-1,0),(1,0),(0,-1),(0,1),(-1,-1),(-1,1),(1,-1),(1,1)):
            q=(p[0]+dr,p[1]+dc)
            if not (0<=q[0]<wet.shape[0] and 0<=q[1]<wet.shape[1]) or not wet[q]:continue
            if dr and dc and (not wet[p[0],q[1]] or not wet[q[0],p[1]]):continue
            new=cost+math.hypot(dr,dc)
            if new<distance.get(q,float('inf')):distance[q]=new;parent[q]=p;heapq.heappush(queue,(new+heuristic(q),new,q))
    return None,'no_connected_water_route'

class Planner:
    def __init__(self,context,graph,shape,cell,valid,water):
        self.context=context or {};self.graph_data=graph or {};self.shape=shape;self.cell=cell;self.valid=valid;self.normal_water=water;self.previous={};self.fleet={}
        self.stations=self.context.get('stations',[]);self.nodes={str(n['id']):n['position'] for n in self.graph_data.get('nodes',[])};self.edges=[e for e in self.graph_data.get('edges',[]) if e.get('routing_allowed',True)]
        self.available=bool(self.nodes and self.edges and self.stations)
        rows=[];columns=[];owners=[]
        for i,edge in enumerate(self.edges):
            points=np.asarray(edge['geometry'],float)
            for a,b in zip(points[:-1],points[1:]):
                samples=np.linspace(a,b,max(2,int(math.ceil(np.linalg.norm(b-a)/cell))+1));rr=np.clip((samples[:,1]/cell).astype(int),0,shape[0]-1);cc=np.clip((samples[:,0]/cell).astype(int),0,shape[1]-1)
                rows.extend(rr);columns.extend(cc);owners.extend([i]*len(rr))
        self.rows=np.asarray(rows,dtype='int32');self.columns=np.asarray(columns,dtype='int32');self.owners=np.asarray(owners,dtype='int32')
    def _cell(self,p):return min(self.shape[0]-1,max(0,int(p[1]/self.cell))),min(self.shape[1]-1,max(0,int(p[0]/self.cell)))
    def connector_clear(self,a,b,hazard):
        a=np.asarray(a,float);b=np.asarray(b,float)
        points=np.linspace(a,b,max(2,int(math.ceil(np.linalg.norm(b-a)/(self.cell/2)))+1))
        return not any(hazard[self._cell(point)] for point in points)
    def hotspots(self,frame,population=None):
        severe=(frame.severity>=3)&(frame.severity!=255)&self.valid&~self.normal_water
        components,count=ndimage.label(severe);sizes=np.bincount(components.ravel());ids=np.argsort(sizes[1:])[::-1][:8]+1;result=[]
        for label in ids:
            if sizes[label]<4:continue
            rr,cc=np.nonzero(components==label);centre=np.array([rr.mean(),cc.mean()]);j=int(np.argmin((rr-centre[0])**2+(cc-centre[1])**2));r,c=int(rr[j]),int(cc[j])
            result.append({'id':'hotspot_'+str(len(result)),'position':[(c+.5)*self.cell,(r+.5)*self.cell],'area_m2':int(sizes[label])*self.cell**2,'severity':int(frame.severity[r,c]),'population':float(np.sum(population[components==label])) if population is not None and np.isfinite(population[components==label]).all() else None,'reachable':False,'assigned_unit_ids':[]})
        return result
    def closures(self,frame):
        hazard=np.zeros(self.shape,bool)
        if frame.depth is not None:hazard|=frame.depth>.3
        if frame.debris is not None:hazard|=frame.debris>.05
        if frame.fire is not None:hazard|=frame.fire
        closed=np.zeros(len(self.edges),bool)
        if len(self.owners):np.logical_or.at(closed,self.owners,hazard[self.rows,self.columns])
        return closed,hazard
    def _route(self,G,nodes,start,source):
        path=[list(map(float,start))];times=[0.]
        if not nodes:return path,times
        first=self.nodes[nodes[0]];distance=float(np.linalg.norm(np.array(path[-1])-first))
        if distance:path.append(first);times.append(distance/(5/3.6))
        for a,b in zip(nodes[:-1],nodes[1:]):
            edge=G[a][b]['edge'];geometry=edge['geometry'];lengths=[float(np.linalg.norm(np.array(v)-u)) for u,v in zip(geometry[:-1],geometry[1:])];total=max(sum(lengths),1e-9)
            for point,length in zip(geometry[1:],lengths):path.append(point);times.append(times[-1]+edge['travel_time_s']*length/total)
        return path,times
    def frame(self,frame,population=None):
        hotspots=self.hotspots(frame,population);closed,hazard=self.closures(frame);closed_ids=[str(e['id']) for e,c in zip(self.edges,closed) if c]
        physical={value.rsplit(':',1)[0] for value in closed_ids}
        segments=[];seen=set()
        for index in np.flatnonzero(closed):
            edge=self.edges[index];identity=str(edge['id']).rsplit(':',1)[0]
            if identity in seen:continue
            seen.add(identity);sample=self.owners==index;rr=self.rows[sample];cc=self.columns[sample]
            reasons=[name for name,values,threshold in [('depth',frame.depth,.3),('debris',frame.debris,.05),('fire',frame.fire,0)] if values is not None and np.any(values[rr,cc]>threshold)]
            segments.append({'road_name_or_class':str(edge.get('name') or edge.get('highway') or 'Unnamed mapped segment')[:120],'reason_hazard':reasons[0] if reasons else None,'length_m':edge.get('length_m')})
            if len(segments)==5:break
        result={'available':self.available,'label':'Hypothetical, not a forecast','time_s':frame.time_s,'facilities_status':self.context.get('facilities_status'),'missing':[],'assumptions':ASSUMPTIONS,'limitations':LIMITATIONS,'stations':[],'hotspots':hotspots,'units':[],'closed_edge_ids':closed_ids,'closed_segments':segments,'optimization':{'method':'greedy shortest modelled travel time with pairwise local improvement','global_optimum':False},'metrics':{'units_dispatched':None,'average_eta_s':None,'hotspots_unreached':None,'stations_cut_off':None,'roads_closed':len(physical) if self.edges else None}}
        if not self.available:
            result['missing']=['Real response stations and road topology are required'];return result
        G=nx.DiGraph();G.add_nodes_from(self.nodes)
        for edge,is_closed in zip(self.edges,closed):
            if is_closed:continue
            a,b=str(edge['from']),str(edge['to']);weight=float(edge['travel_time_s'])
            if not np.isfinite(weight) or weight<=0 or a not in self.nodes or b not in self.nodes:continue
            if not G.has_edge(a,b) or weight<G[a][b]['weight']:G.add_edge(a,b,weight=weight,edge=edge)
        components=nx.weakly_connected_components(G);main=max(components,key=len,default=set());open_ids=sorted(n for n in main if G.degree(n)>0);tree=cKDTree([self.nodes[n] for n in open_ids]) if open_ids else None
        for station in self.stations:
            record=dict(station);node=str(record.get('nearest_node_id',''));in_zone=bool(hazard[self._cell(record['position'])]);connected=node in main and G.degree(node)>0 and record.get('nearest_node_distance_m',float('inf'))<=150 and self.connector_clear(record['position'],self.nodes[node],hazard)
            record.update(in_zone=in_zone,reachable=connected and not in_zone,cut_off=not connected,status='in_zone' if in_zone else 'reachable' if connected else 'cut_off');result['stations'].append(record)
        if not hotspots or tree is None:
            result['metrics'].update(units_dispatched=0,hotspots_unreached=len(hotspots),stations_cut_off=sum(s['cut_off'] for s in result['stations']));return result
        targets={}
        for hotspot in hotspots:
            distance,index=tree.query(hotspot['position'])
            if distance<=500:targets[hotspot['id']]=open_ids[int(index)];hotspot['staging_position']=self.nodes[open_ids[int(index)]];hotspot['staging_distance_m']=float(distance)
        kinds={'ambulance':('hospital','clinic'),'fire_engine':('fire_station',),'police':('police',)};routes={};costs={}
        for kind,eligible in kinds.items():
            if kind not in self.fleet:
                candidates=[s for s in result['stations'] if s['kind'] in eligible and s['reachable']]
                if candidates:self.fleet[kind]=min(candidates,key=lambda s:(min(np.linalg.norm(np.array(s['position'])-h['position']) for h in hotspots),str(s['id'])))
            station=self.fleet.get(kind)
            if not station:continue
            unit_id='model_'+kind;start=position_at(self.previous[unit_id],frame.time_s) if unit_id in self.previous else station['position']
            if hazard[self._cell(start)]:continue
            distance,index=tree.query(start)
            if distance>150:continue
            source=open_ids[int(index)]
            if not self.connector_clear(start,self.nodes[source],hazard):continue
            distances,paths=nx.single_source_dijkstra(G,source,weight='weight')
            for hotspot_id,target in targets.items():
                if target in distances:
                    path,times=self._route(G,paths[target],start,source);costs[(kind,hotspot_id)]=times[-1];routes[(kind,hotspot_id)]=(path,times,station)
        remaining=set(kinds)&{key[0] for key in costs};assignments={};usage={h['id']:0 for h in hotspots};capacity=max(1,math.ceil(len(remaining)/len(hotspots)))
        while remaining:
            candidates=[(cost,kind,h) for (kind,h),cost in costs.items() if kind in remaining and usage[h]<capacity]
            if not candidates:break
            _,kind,h=min(candidates);assignments[kind]=h;remaining.remove(kind);usage[h]+=1
        improvements=0
        for a in sorted(assignments):
            for b in sorted(assignments):
                if a>=b:continue
                x,y=assignments[a],assignments[b]
                if (a,y) in costs and (b,x) in costs and costs[a,y]+costs[b,x]+1e-9<costs[a,x]+costs[b,y]:assignments[a],assignments[b]=y,x;improvements+=1
        for kind,hotspot_id in assignments.items():
            path,times,station=routes[kind,hotspot_id];unit={'id':'model_'+kind,'type':kind,'station_id':station['id'],'modelled':True,'hotspot_id':hotspot_id,'position':path[0],'path':path,'path_times_s':times,'eta_s':times[-1],'departure_time_s':frame.time_s,'status':'dispatched' if times[-1]>0 else 'in_zone'};result['units'].append(unit)
        if frame.depth is not None:
            boat=self._boat(frame,hotspots,result['stations'],G,tree,open_ids,hazard)
            if boat:result['units'].append(boat)
        for unit in result['units']:
            self.previous[unit['id']]=unit
            for h in hotspots:
                if h['id']==unit['hotspot_id']:h['reachable']=True;h['assigned_unit_ids'].append(unit['id'])
        etas=[u['eta_s'] for u in result['units']];result['optimization']['pairwise_improvements']=improvements
        result['metrics'].update(units_dispatched=len(etas),average_eta_s=float(np.mean(etas)) if etas else None,hotspots_unreached=sum(not h['reachable'] for h in hotspots),stations_cut_off=sum(s['cut_off'] for s in result['stations']))
        return result
    def _boat(self,frame,hotspots,stations,G,tree,open_ids,hazard):
        eligible=[s for s in stations if s['kind']=='fire_station' and s['reachable']]
        if not eligible:return None
        target=next((h for h in hotspots if frame.depth[self._cell(h['position'])]>.3),None)
        if target is None:return None
        wet=(frame.depth>.3)&self.valid;labels,_=ndimage.label(wet);end=self._cell(target['position']);component=labels==labels[end]
        prior=self.previous.get('model_rescue_boat');start=None;launch=None;access_time=0
        station=min(eligible,key=lambda s:(np.linalg.norm(np.array(s['position'])-target['position']),str(s['id'])))
        if prior and frame.time_s<prior['departure_time_s']:
            pending=dict(prior);pending['eta_s']=prior['departure_time_s']-frame.time_s+prior['path_times_s'][-1];return pending
        if prior:
            current=position_at(prior,frame.time_s);candidate=self._cell(current)
            if wet[candidate]:start=candidate;launch=prior.get('launch_point')
        if start is None:
            bank=self.valid&(frame.depth<=.1)&ndimage.binary_dilation(component);rr,cc=np.nonzero(bank)
            if not len(rr):return None
            points=np.column_stack(((cc+.5)*self.cell,(rr+.5)*self.cell));dist,index=tree.query(points);order=np.argsort(np.linalg.norm(points-station['position'],axis=1))[:200]
            source=str(station['nearest_node_id']);distances=nx.single_source_dijkstra_path_length(G,source,weight='weight')
            options=[(distances.get(open_ids[int(index[j])],float('inf'))+dist[j]/(5/3.6),j) for j in order if dist[j]<=150 and self.connector_clear(points[j],self.nodes[open_ids[int(index[j])]],hazard)]
            if not options:return None
            access_time,j=min(options)
            if not np.isfinite(access_time):return None
            launch=points[j].tolist();r,c=int(rr[j]),int(cc[j]);neighbors=[(r+dr,c+dc) for dr,dc in ((-1,0),(1,0),(0,-1),(0,1)) if 0<=r+dr<wet.shape[0] and 0<=c+dc<wet.shape[1] and component[r+dr,c+dc]]
            if not neighbors:return None
            start=min(neighbors,key=lambda p:math.hypot(p[0]-end[0],p[1]-end[1]))
        cells,reason=water_path(component,start,end,self.cell)
        if cells is None:target['boat_route_reason']=reason;return None
        path=[[(c+.5)*self.cell,(r+.5)*self.cell] for r,c in cells]
        if access_time>0 and launch:path.insert(0,launch)
        times=[0.]
        for a,b in zip(path[:-1],path[1:]):times.append(times[-1]+float(np.linalg.norm(np.array(b)-a))/(12/3.6))
        return {'id':'model_rescue_boat','type':'rescue_boat','station_id':station['id'],'modelled':True,'hotspot_id':target['id'],'position':path[0],'path':path,'path_times_s':times,'eta_s':access_time+times[-1],'departure_time_s':frame.time_s+access_time,'launch_point':launch,'launch_access_eta_s':access_time,'status':'dispatched','assumption':'One assumed rescue boat associated with a real fire station; 0.3m minimum modelled draft, 12km/h water speed; road access to dry bank modelled, not an observed boat asset.'}
