"""Geometric footprint/wind spread illustration with no assumed observed fuel data."""
import heapq,math
import numpy as np
from shapely.strtree import STRtree
from .base import Frame,Plugin,LABEL

class Fire(Plugin):
    inputs={'building_index':{'type':'building','required':True},'wind_direction_deg':{'min':0,'max':360,'default':90},'wind_speed_m_s':{'min':0,'max':30,'default':3}}
    labels={'title':'Hypothetical geometric fire spread','label':LABEL,'bands':['unburnt','at risk',None,'burning','burnt'],'limitations':['Footprint gaps and assumed wind transport only; no observed fuel, construction, humidity, fire intensity or suppression data.','Assumed 8m maximum gap, 20s ignition delay plus gap-dependent delay, 120s burning duration; not a fire forecast.','Wind direction means travel toward the compass bearing, clockwise from north.']}
    def apply(self,context,request):
        super().apply(context,request);tree=STRtree(context.polygons);self.arrival=np.full(len(context.buildings),np.inf);start=int(request['building_index']);self.arrival[start]=0
        angle=math.radians(request.get('wind_direction_deg',90));wind=np.array([math.sin(angle),-math.cos(angle)]);speed=request.get('wind_speed_m_s',3)
        queue=[(0.,start)];visited=0
        while queue:
            t,i=heapq.heappop(queue)
            if t!=self.arrival[i] or t>330:continue
            visited+=1
            for raw in sorted(tree.query(context.polygons[i],predicate='dwithin',distance=8).tolist()):
                j=int(raw)
                if i==j:continue
                gap=context.polygons[i].distance(context.polygons[j]);delta=context.centers[j]-context.centers[i];length=np.linalg.norm(delta)
                along=float(delta@wind/length) if length else 0
                delay=20+gap*6/max(.4,1+speed*.12*along);arrival=t+delay
                if arrival<self.arrival[j] and arrival<=330:self.arrival[j]=arrival;heapq.heappush(queue,(arrival,j))
        return self
    def step(self,time_s):
        ctx=self.context;burning=(self.arrival<=time_s)&(self.arrival+120>time_s);burnt=self.arrival+120<=time_s;risk=(self.arrival>time_s)&(self.arrival<=time_s+30)
        levels=np.zeros(len(self.arrival),dtype='uint8');levels[risk]=1;levels[burning]=3;levels[burnt]=4
        states=np.zeros(len(levels),dtype='uint8');states[risk]=3;states[burning]=4;states[burnt]=5
        indices=np.flatnonzero(levels);severity=ctx.raster_buildings(indices,levels[indices]).astype('uint8');severity[~ctx.valid]=255
        fire=ctx.raster_buildings(np.flatnonzero(burning|burnt),np.ones(np.count_nonzero(burning|burnt)),buffer_m=3)>0
        return Frame(float(time_s),severity,levels,states,fire=fire,extra={'burning_buildings':ctx.unique_count(burning),'burnt_buildings':ctx.unique_count(burnt),'spread_model':'footprint gap and wind heuristic'})
