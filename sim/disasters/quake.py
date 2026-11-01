"""Height/distance earthquake illustration; not a structural vulnerability model."""
import numpy as np
from scipy import ndimage
from .base import Frame,Plugin,LABEL

class Quake(Plugin):
    inputs={'point':{'type':'point','required':True},'magnitude':{'min':3,'max':9,'default':6}}
    labels={'title':'Hypothetical earthquake','label':LABEL,'bands':['none','light','moderate','heavy','collapsed'],'limitations':['Dimensionless shaking proxy from magnitude and distance; not PGA, MMI, structural analysis or a damage forecast.','Building height is a vulnerability proxy only. Unknown heights remain unknown.','Collapsed footprints deposit assumed 15% of height, capped at 3m, with a 3m debris buffer; no material or volume claim.']}
    def apply(self,context,request):
        super().apply(context,request);point=np.asarray(request['point']);m=float(request['magnitude'])
        rr,cc=np.indices(context.shape,dtype='float32');distance=np.hypot((cc+.5)*context.cell-point[0],(rr+.5)*context.cell-point[1])
        self.shaking=np.clip((m-3)/4*np.exp(-distance/4000),0,1)
        distances=np.linalg.norm(context.centers-point,axis=1)
        self.risk=np.clip((m-3)/4*np.exp(-distances/4000)*(1+np.minimum(context.heights,60)/60*.6),0,1)
        return self
    def step(self,time_s):
        ctx=self.context;strength=min(1,float(time_s)/30);severity=np.digitize(self.shaking*strength,[.1,.25,.45,.7]).astype('uint8');severity[~ctx.valid]=255
        levels=np.digitize(self.risk*strength,[.1,.25,.45,.7]).astype('uint8');unknown=~np.isfinite(ctx.heights);levels[unknown]=255
        states=np.where(levels==4,2,np.where((levels>0)&(levels<4),1,0)).astype('uint8');states[unknown]=255
        collapsed=np.flatnonzero(states==2)
        debris=ctx.raster_buildings(collapsed,np.minimum(ctx.heights[collapsed]*.15,3),buffer_m=3)
        return Frame(float(time_s),severity,levels,states,debris=debris,extra={'shaking_model':'dimensionless magnitude-distance-height heuristic','shaking_proxy_peak':float((self.shaking*strength)[ctx.valid].max()) if ctx.valid.any() else None,'collapsed_buildings':ctx.unique_count(states==2),'unknown_height_parts':int(unknown.sum())})
    def frames(self):
        for time_s in (0,10,30,60):yield self.step(time_s)
