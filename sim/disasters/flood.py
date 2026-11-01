"""Existing flood frames or fixed-step virtual pipes; never changes the static replay."""
import numpy as np
import rasterio
from scipy import ndimage
from scipy.spatial import cKDTree
from sim.dynamic import DynamicConfig
from sim.relative import RelativePipes,river_reference
from .base import Frame,Plugin,LABEL

class Flood(Plugin):
    inputs={'river_plus_m':{'min':1,'max':4,'default':3}}
    labels={'title':'Flood exposure','label':LABEL,'bands':['0–0.3m','>0.3–0.6m','>0.6–1.2m','>1.2–2m','>2m'],'limitations':['Mapped FABDEM/OSM river surface proxy, not gauge stage or surveyed bathymetry.','Closed AOI edges, local-inertial flow and uncalibrated roughness; no forecast.','Building colours show nearby external flood exposure, not indoor depth.']}
    def apply(self,context,request,quake=None):
        super().apply(context,request);self.quake=quake;self.cached=context.cached_frames(request)
        self.offset=60 if quake is not None else 0
        if request['module']=='breach' and request.get('bank_source')=='osm_river_shoreline':
            self.labels={**self.labels,'title':'ITO riverbank cut' if request.get('preset_id')=='breach_near_ito' else 'Hypothetical riverbank cut','label':'Hypothetical riverbank cut; engineered levee unconfirmed','limitations':[*Flood.labels['limitations'],'20m-radius dry-land terrain cut centred on the real OSM river shoreline, lowered to the7-cell local FABDEM minimum. No engineered levee is confirmed.']}
        if self.cached:
            if getattr(context,'cached_terrain_edit',None):self.breach_info=context.cached_terrain_edit
            return self
        cfg=DynamicConfig(dt_s=.25,duration_s=300,output_interval_s=10,river_ramp_s=60)
        bed=context.surface.astype(float).copy();blocked=context.blocked.copy();changed=np.zeros(context.shape,bool)
        if quake is not None:
            debris=quake.debris;collapsed=context.raster_buildings(np.flatnonzero(quake.building_state==2),np.ones(np.count_nonzero(quake.building_state==2)))>0
            blocked[collapsed]=False;bed[collapsed]=context.ground[collapsed];bed+=debris;changed|=debris>0
        if request['module']=='breach':
            shoreline=request.get('bank_source')=='osm_river_shoreline'
            if shoreline:
                from sim.replay import main_river
                mapped_river=main_river(context.water&context.valid);bank_cells=ndimage.binary_dilation(mapped_river)&~mapped_river&context.valid&~blocked
            else:bank_cells=context.banks&context.valid&~blocked
            rr,cc=np.nonzero(bank_cells)
            if not len(rr):raise ValueError('No mapped embankment cells')
            coords=np.column_stack(((cc+.5)*context.cell,(rr+.5)*context.cell));distance,index=cKDTree(coords).query(request['point'])
            if distance>80:raise ValueError('Choose a point within80m of the selected real mapped bank boundary')
            centre=coords[index];r,c=np.indices(context.shape);cut=((c+.5)*context.cell-centre[0])**2+((r+.5)*context.cell-centre[1])**2<=20**2;cut &=context.valid&~blocked
            cut &=~mapped_river if shoreline else context.banks
            before=bed[cut].copy();bed[cut]=ndimage.minimum_filter(context.ground,size=7)[cut];changed|=cut;self.breach_point=centre.tolist()
            self.breach_info={'label':self.labels['label'],'bank_source':request.get('bank_source','mapped_embankment'),'point':self.breach_point,'radius_m':20,'cut_cells':int(cut.sum()),'lowered_cells':int(np.count_nonzero(before-bed[cut]>.001)),'maximum_lowering_m':float(np.max(before-bed[cut])) if cut.any() else 0.,'engineered_levee_confirmed':False,'buildings_removed':False}
        river,reference,meta=river_reference(context.ground,context.water,context.valid)
        boundary=river&context.valid&~blocked&~changed
        if not boundary.any():raise ValueError('No mapped river reservoir cells remain')
        initial=np.where(river&context.valid&~blocked,np.maximum(0,reference-bed),0)
        self.solver=RelativePipes(bed,initial,context.valid,blocked,boundary,cfg,reference=reference);self.solver.impose_stage(0);self.cfg=cfg;self.reference=meta;self.completed_steps=0
        return self
    def from_depth(self,time_s,depth):
        ctx=self.context;levels=np.digitize(depth,np.array([.3,.6,1.2,2],dtype=depth.dtype),right=True).astype('uint8');levels[~ctx.valid]=255
        exposure=ctx.building_max(ndimage.maximum_filter(depth,size=3));blevels=np.digitize(exposure,np.array([.3,.6,1.2,2],dtype=exposure.dtype),right=True).astype('uint8');states=np.where(exposure>.1,6,0).astype('uint8')
        debris=None
        if self.quake is not None:
            levels=np.maximum(np.where(self.quake.severity==255,0,self.quake.severity),levels);blevels=np.maximum(blevels,self.quake.building_severity);states=np.where(self.quake.building_state!=0,self.quake.building_state,states).astype('uint8');debris=self.quake.debris
        extra={'river_plus_m':self.request['river_plus_m'],'source':'existing fresh static replay' if self.request.get('replay') else 'hypothetical virtual pipes'}
        if hasattr(self,'breach_info'):extra['terrain_edit']=self.breach_info
        return Frame(float(time_s)+self.offset,levels,blevels,states,depth=depth,debris=debris,extra=extra)
    def step(self,time_s):
        if self.cached:
            elapsed,path=min(self.cached,key=lambda item:abs(item[0]-time_s))
            with rasterio.open(path) as source:
                if source.shape!=self.context.shape or source.transform!=self.context.transform:raise ValueError('Cached flood grid differs from current terrain')
                depth=source.read(1).astype('float32')
            frame=self.from_depth(elapsed,depth)
            balance=getattr(self.context,'cached_water_balance',{}).get(elapsed)
            if balance:frame.extra['water_balance']=balance
            return frame
        target=round(time_s/self.cfg.dt_s)
        for step in range(self.completed_steps+1,target+1):self.solver.step(self.request['river_plus_m']*min(1,step*self.cfg.dt_s/self.cfg.river_ramp_s))
        self.completed_steps=target
        depth=np.where(self.solver.depth>1e-6,np.maximum(0,self.solver.bed+self.solver.depth-self.context.ground),0).astype('float32')
        frame=self.from_depth(time_s,depth);frame.extra['water_balance']=self.solver.water_balance();return frame
    def frames(self):
        if self.cached:
            for time_s,path in self.cached:
                with rasterio.open(path) as source:
                    if source.shape!=self.context.shape or source.transform!=self.context.transform:raise ValueError('Cached flood grid differs from current terrain')
                    depth=source.read(1).astype('float32')
                frame=self.from_depth(time_s,depth)
                balance=getattr(self.context,'cached_water_balance',{}).get(time_s)
                if balance:frame.extra['water_balance']=balance
                yield frame
        else:
            for t in range(0,301,10):yield self.step(t)

class Breach(Flood):
    inputs={**Flood.inputs,'point':{'type':'point','required':True}}
    labels={**Flood.labels,'title':'Point breach on mapped embankment','limitations':[*Flood.labels['limitations'],'Hypothetical 20m-radius cut in actual mapped embankment cells, lowered to local 7-cell ground minimum; not an observed failure.']}

class Chain(Plugin):
    inputs={'point':{'type':'point','required':True},'magnitude':{'min':3,'max':9,'default':6},'river_plus_m':{'min':1,'max':4,'default':3}}
    labels={'title':'Earthquake then flood on changed ground','label':LABEL,'bands':['none/low','light','moderate','heavy','collapsed/deep'],'limitations':['Combined heuristic quake and unvalidated flood model.','Collapse debris modifies the flood bed and opens collapsed building footprints; no structural or hydrological forecast.']}
    def apply(self,context,request):
        from .quake import Quake
        super().apply(context,request);self.quake=Quake().apply(context,request);self.flood=None
        return self
    def step(self,time_s):
        if time_s<=60:return self.quake.step(time_s)
        if self.flood is None:self.flood=Flood().apply(self.context,self.request,quake=self.quake.step(60))
        return self.flood.step(time_s-60)
    def frames(self):
        for t in (0,30,60,*range(70,361,10)):yield self.step(t)
