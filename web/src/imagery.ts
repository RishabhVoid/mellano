import * as THREE from 'three';
import proj4 from 'proj4';
import type { Diorama } from './model';

type Bounds = [number, number, number, number];
type ImageryStatus = { available?:boolean; attribution?:string; reason?:string; provider?:string|null; providers?:Record<string,{attribution?:string}>; tile_availability?:string; backoff_reason?:string|null; retry_after_s?:number };
type Tile = { key:string; z:number; x:number; y:number; bounds:Bounds; box:THREE.Box3; distance:number; used:number; provider?:string; mesh?:THREE.Mesh<THREE.BufferGeometry,THREE.MeshStandardMaterial>; image?:ImageBitmap; loading?:boolean; retryAt?:number; controller?:AbortController };
const UTM = '+proj=utm +zone=43 +datum=WGS84 +units=m +no_defs';
const MAX_TILES = 96, MAX_LEAVES = 64, CONCURRENCY = 6;
const longitude = (x:number,z:number) => x/2**z*360-180;
const latitude = (y:number,z:number) => Math.atan(Math.sinh(Math.PI*(1-2*y/2**z)))*180/Math.PI;
const xy = (lon:number,lat:number,z:number):[number,number] => [(lon+180)/360*2**z,(1-Math.asinh(Math.tan(lat*Math.PI/180))/Math.PI)/2*2**z];

/** Display-only adaptive terrain chunks. Nothing in this class writes analysis data. */
export class SatelliteImagery {
  enabled=false; available=false; opacity=0; attribution=''; reason='Matte view';
  activeProviders:string[]=[];
  private providerCredits=new Map<string,string>([['esri','<a href="https://www.esri.com/">Esri</a>, Maxar, Earthstar Geographics, and the GIS User Community']]);
  private failureStreak=0;
  private cacheHits=0;
  private group=new THREE.Group();
  private tiles=new Map<string,Tile>();
  private roots:Tile[]=[];
  private wanted=new Set<string>();
  private queue:Tile[]=[];
  private pending=0;
  private lastSelect=0;
  private generation=0;
  private lastExaggeration=0;
  private lastCamera='';
  private frustum=new THREE.Frustum();
  private matrix=new THREE.Matrix4();
  private peakTextures=0;
  private failures=0;
  private retryUntil=0;
  private burstStatusChecked=false;
  private tileAvailability='untested';
  private backoffReason:string|null=null;
  private toGeo=proj4(UTM,'EPSG:4326');
  private toUtm=proj4('EPSG:4326',UTM);
  private imageCanvas=document.createElement('canvas');
  private imageContext=this.imageCanvas.getContext('2d',{willReadFrequently:true})!;
  onChange:()=>void=()=>{};
  constructor(private view:Diorama) { view.scene.add(this.group); this.group.visible=false; this.imageCanvas.width=this.imageCanvas.height=512; }
  async initialize():Promise<void> {
    this.available=false;
    try { const response=await fetch('/api/imagery/status');if(!response.ok)throw new Error('unavailable');const status:ImageryStatus=await response.json();this.applyStatus(status); } catch { this.reason='Satellite unavailable · matte view'; }
    // Reset after the await as overlapping status refreshes must not duplicate roots.
    this.roots=[];
    const [w,h]=this.view.terrain.extent_m;
    const corners=[[0,0],[w,0],[0,h],[w,h]].map(([x,z])=>this.geo(x,z));
    const coordinates=corners.map(([lon,lat])=>xy(lon,lat,13));
    const xmin=Math.floor(Math.min(...coordinates.map(p=>p[0]))),xmax=Math.floor(Math.max(...coordinates.map(p=>p[0])));
    const ymin=Math.floor(Math.min(...coordinates.map(p=>p[1]))),ymax=Math.floor(Math.max(...coordinates.map(p=>p[1])));
    for(let x=xmin;x<=xmax;x++)for(let y=ymin;y<=ymax;y++)this.roots.push(this.node(13,x,y));
    this.onChange();
  }
  async setEnabled(on:boolean):Promise<void> {
    if(on&&!this.available)await this.initialize();
    this.enabled=on&&this.available;this.group.visible=this.enabled;this.lastSelect=0;this.lastCamera='';
    if(!this.enabled){this.generation++;this.queue=[];this.wanted.clear();for(const tile of this.tiles.values()){tile.controller?.abort();this.dispose(tile);}this.opacity=0;this.view.setRoofImageryBlend(0);}
    this.updateAttribution();this.onChange();this.view.dirty=true;
  }
  geo(x:number,z:number):number[]{return this.toGeo.forward([this.view.terrain.origin[0]+x,this.view.terrain.origin[1]-z]);}
  private local(lon:number,lat:number):number[]{const p=this.toUtm.forward([lon,lat]);return[p[0]-this.view.terrain.origin[0],this.view.terrain.origin[1]-p[1]];}
  private node(z:number,x:number,y:number):Tile {
    const key=`${z}/${x}/${y}`,existing=this.tiles.get(key);if(existing)return existing;
    const points=[[x,y],[x+1,y],[x,y+1],[x+1,y+1]].map(([tx,ty])=>this.local(longitude(tx,z),latitude(ty,z)));
    const bounds:Bounds=[Math.min(...points.map(p=>p[0])),Math.min(...points.map(p=>p[1])),Math.max(...points.map(p=>p[0])),Math.max(...points.map(p=>p[1]))];
    const a=this.view.point(bounds[0],bounds[1],this.view.terrain.min_height_m),b=this.view.point(bounds[2],bounds[3],this.view.terrain.max_height_m);
    const tile:Tile={key,z,x,y,bounds,box:new THREE.Box3(a,b),distance:Infinity,used:0};this.tiles.set(key,tile);return tile;
  }
  private select(now:number):void {
    this.view.camera.updateMatrixWorld();this.matrix.multiplyMatrices(this.view.camera.projectionMatrix,this.view.camera.matrixWorldInverse);this.frustum.setFromProjectionMatrix(this.matrix);
    const leaves:Tile[]=[];const [w,h]=this.view.terrain.extent_m;
    const visit=(tile:Tile)=>{
      const b=tile.bounds;if(b[2]<0||b[3]<0||b[0]>w||b[1]>h)return;
      tile.box.max.y=(this.view.terrain.max_height_m-this.view.terrain.min_height_m)*this.view.exaggeration*.001+.1;
      if(!this.frustum.intersectsBox(tile.box))return;
      tile.distance=tile.box.distanceToPoint(this.view.camera.position);
      const desired=THREE.MathUtils.clamp(Math.ceil(18-Math.log2(Math.max(.12,tile.distance)/.22)),13,18);
      if(tile.z<desired){for(let dx=0;dx<2;dx++)for(let dy=0;dy<2;dy++)visit(this.node(tile.z+1,tile.x*2+dx,tile.y*2+dy));}else leaves.push(tile);
    };
    this.roots.forEach(visit);leaves.sort((a,b)=>a.distance-b.distance);
    // Root coverage is sufficient while detail arrives; ancestors must never consume
    // the entire budget before the nearest z17–18 leaves are selected.
    const chosenMap=new Map<string,Tile>();
    const visibleRoots=this.roots.filter(root=>this.frustum.intersectsBox(root.box)).sort((a,b)=>a.distance-b.distance);
    for(const root of visibleRoots)if(chosenMap.size<MAX_TILES)chosenMap.set(root.key,root);
    const detailed=leaves.slice(0,MAX_LEAVES);
    for(const leaf of detailed)if(chosenMap.size<MAX_TILES)chosenMap.set(leaf.key,leaf);
    const ancestors=new Map<string,Tile>();
    for(const leaf of detailed){let tile=leaf;while(tile.z>14){tile=this.node(tile.z-1,Math.floor(tile.x/2),Math.floor(tile.y/2));tile.distance=tile.box.distanceToPoint(this.view.camera.position);ancestors.set(tile.key,tile);}}
    for(const tile of [...ancestors.values()].sort((a,b)=>a.distance-b.distance||b.z-a.z))if(chosenMap.size<MAX_TILES)chosenMap.set(tile.key,tile);
    const chosen=[...chosenMap.values()];
    this.wanted=new Set(chosen.map(t=>t.key));
    for(const tile of this.tiles.values()){
      if(tile.loading&&!this.wanted.has(tile.key))tile.controller?.abort();
      if(tile.mesh){tile.mesh.visible=this.wanted.has(tile.key);if(tile.mesh.visible)tile.used=now;}
    }
    this.queue=chosen.filter(t=>!t.mesh&&!t.loading&&now>(t.retryAt??0));
    this.updateAttribution();this.pump();
  }
  private applyStatus(status:ImageryStatus):void {
    this.available=!!status.available;this.attribution=status.attribution??this.attribution;
    for(const [provider,value] of Object.entries(status.providers??{}))if(value.attribution)this.providerCredits.set(provider,value.attribution);
    if(status.retry_after_s&&status.retry_after_s>0){this.deferRequests(status.retry_after_s*1000);this.burstStatusChecked=true;}
    this.tileAvailability=status.tile_availability??this.tileAvailability;this.backoffReason=status.backoff_reason??null;
    this.reason=status.reason??(this.backoffReason==='rate_limited'?'Mapbox rate limit · retry cooldown':this.retryUntil>performance.now()?'Satellite unavailable · retry cooldown':'Satellite ready to request');
    this.updateAttribution();this.onChange();
  }
  private updateAttribution():void {
    const previous=this.activeProviders.join(',');
    this.activeProviders=[...new Set([...this.tiles.values()].filter(tile=>tile.mesh?.visible&&tile.provider).map(tile=>tile.provider!))].sort();
    if(this.activeProviders.length)this.attribution=this.activeProviders.map(provider=>this.providerCredits.get(provider)??provider).join(' | ');
    if(previous!==this.activeProviders.join(','))this.onChange();
  }
  private deferRequests(milliseconds:number):void {
    const now=performance.now();
    if(now>=this.retryUntil){this.failureStreak=Math.min(5,this.failureStreak+1);this.burstStatusChecked=false;this.tileAvailability='backoff';this.backoffReason=null;this.reason='Satellite temporarily unavailable · retry cooldown';}
    const exponential=30000*2**Math.max(0,this.failureStreak-1);
    this.retryUntil=Math.max(this.retryUntil,now+THREE.MathUtils.clamp(Math.max(milliseconds,exponential),30000,300000));
  }
  private async refreshFailureStatus(generation:number):Promise<void> {
    // Mark synchronously before awaiting: concurrent tile failures share one lookup.
    if(this.burstStatusChecked)return;this.burstStatusChecked=true;
    try{
      const response=await fetch('/api/imagery/status',{signal:AbortSignal.timeout(10000)});
      if(!response.ok)return;const status:ImageryStatus=await response.json();
      if(generation===this.generation&&this.enabled)this.applyStatus(status);
    }catch{/* Retain the safe fallback message; no polling during this failure burst. */}
  }
  private pump():void {
    if(performance.now()<this.retryUntil)return;
    while(this.enabled&&this.pending<CONCURRENCY&&this.queue.length){const tile=this.queue.shift()!;if(!this.wanted.has(tile.key)||tile.mesh||tile.loading)continue;tile.loading=true;this.pending++;const generation=this.generation;void this.load(tile,generation).finally(()=>{tile.loading=false;this.pending--;this.pump();});}
  }
  private async load(tile:Tile,generation:number):Promise<void> {
    const controller=new AbortController();tile.controller=controller;
    const timeout=window.setTimeout(()=>controller.abort(),10000);
    try {
      const response=await fetch(`/api/imagery/tiles/${tile.key}.jpg`,{signal:controller.signal});
      if(!response.ok||response.status===204){
        if(generation===this.generation&&this.enabled&&this.wanted.has(tile.key)){
          const header=response.headers.get('Retry-After'),seconds=header!==null&&header.trim()!==''?Number(header):NaN;
          const delay=Number.isFinite(seconds)?seconds*1000:header?Date.parse(header)-Date.now():NaN;
          this.deferRequests(Number.isFinite(delay)?delay:30000);
        }
        throw new Error('unavailable');
      }
      // ImageBitmap upload ignores Texture.flipY. Keep row zero at UV v=0 explicitly.
      const image=await createImageBitmap(await response.blob(),{imageOrientation:'none',premultiplyAlpha:'none',colorSpaceConversion:'none'});
      if(generation!==this.generation||!this.enabled||!this.wanted.has(tile.key)){image.close();return;}
      // Evict before upload; there is a hard bound on resident imagery textures.
      const resident=[...this.tiles.values()].filter(t=>t.mesh).sort((a,b)=>Number(this.wanted.has(a.key))-Number(this.wanted.has(b.key))||a.used-b.used);
      if(resident.length>=MAX_TILES)this.dispose(resident[0]);
      tile.provider=response.headers.get('X-Imagery-Provider')??'mapbox';
      if(response.headers.get('X-Imagery-Cache')==='hit')this.cacheHits++;
      if(!this.providerCredits.has(tile.provider)){this.burstStatusChecked=false;void this.refreshFailureStatus(generation);}
      tile.image=image;tile.mesh=this.mesh(tile,image);tile.mesh.visible=this.wanted.has(tile.key);tile.used=performance.now();this.group.add(tile.mesh);
      this.failureStreak=0;this.updateAttribution();
      this.peakTextures=Math.max(this.peakTextures,[...this.tiles.values()].filter(t=>t.mesh).length);
      if(tile.z>=16)this.sampleRoofs(tile,image);if(performance.now()>=this.retryUntil){this.reason='Real satellite imagery · display only';this.tileAvailability='available';this.backoffReason=null;this.burstStatusChecked=false;}this.onChange();this.view.dirty=true;
    } catch {
      if(generation!==this.generation||!this.enabled||!this.wanted.has(tile.key))return;
      this.failures++;if(performance.now()>=this.retryUntil)this.deferRequests(30000);
      tile.retryAt=this.retryUntil;void this.refreshFailureStatus(generation);this.onChange();
    }
    finally{window.clearTimeout(timeout);if(tile.controller===controller)tile.controller=undefined;}
  }
  private mesh(tile:Tile,image:ImageBitmap):THREE.Mesh<THREE.BufferGeometry,THREE.MeshStandardMaterial> {
    const [w,h]=this.view.terrain.extent_m,b=tile.bounds;
    const step=this.view.terrain.render_stride*this.view.terrain.cell_size_m;
    const c0=Math.max(0,Math.floor(b[0]/step)),c1=Math.min(Math.ceil(w/step),Math.ceil(b[2]/step));
    const r0=Math.max(0,Math.floor(b[1]/step)),r1=Math.min(Math.ceil(h/step),Math.ceil(b[3]/step));
    type Vertex={x:number;z:number;height:number;u:number;v:number;index?:number};
    const positions:number[]=[],uv:number[]=[],base:number[]=[],indices:number[]=[];
    const grid=new Map<number,Vertex>(),columns=Math.ceil(w/step)+1;
    const vertex=(c:number,r:number):Vertex=>{
      const key=r*columns+c,existing=grid.get(key);if(existing)return existing;
      const x=Math.min(c*step,w),z=Math.min(r*step,h),geo=this.geo(x,z),coordinate=xy(geo[0],geo[1],tile.z);
      const value={x,z,height:this.view.renderedGround(x,z),u:coordinate[0]-tile.x,v:coordinate[1]-tile.y};grid.set(key,value);return value;
    };
    const clip=(polygon:Vertex[],axis:'u'|'v',edge:number,lower:boolean):Vertex[]=>{
      const result:Vertex[]=[];
      for(let i=0;i<polygon.length;i++){
        const a=polygon[i],b=polygon[(i+1)%polygon.length],aInside=lower?a[axis]>=edge:a[axis]<=edge,bInside=lower?b[axis]>=edge:b[axis]<=edge;
        if(aInside)result.push(a);
        if(aInside!==bInside){const fraction=(edge-a[axis])/(b[axis]-a[axis]);result.push({x:a.x+(b.x-a.x)*fraction,z:a.z+(b.z-a.z)*fraction,height:a.height+(b.height-a.height)*fraction,u:a.u+(b.u-a.u)*fraction,v:a.v+(b.v-a.v)*fraction});}
      }
      return result;
    };
    const index=(vertex:Vertex):number=>{
      if(vertex.index!==undefined)return vertex.index;
      vertex.index=base.length;const point=this.view.point(vertex.x,vertex.z,vertex.height);point.y+=.00015;
      positions.push(point.x,point.y,point.z);uv.push(vertex.u,vertex.v);base.push(vertex.height);return vertex.index;
    };
    // Clip the actual terrain triangles in tile UV space. A separate tile grid can
    // cross terrain diagonals and sink below the surface even with a small lift.
    for(let r=r0;r<r1;r++)for(let c=c0;c<c1;c++){
      const a=vertex(c,r),b=vertex(c,r+1),d=vertex(c+1,r),e=vertex(c+1,r+1);
      for(const triangle of [[a,b,d],[d,b,e]]){
        if(triangle.every(point=>point.u>=0&&point.u<=1&&point.v>=0&&point.v<=1)){indices.push(...triangle.map(index));continue;}
        if(triangle.every(point=>point.u<0)||triangle.every(point=>point.u>1)||triangle.every(point=>point.v<0)||triangle.every(point=>point.v>1))continue;
        let polygon=triangle;for(const [axis,edge,lower] of [['u',0,true],['u',1,false],['v',0,true],['v',1,false]] as ['u'|'v',number,boolean][])polygon=clip(polygon,axis,edge,lower);
        for(let i=1;i<polygon.length-1;i++){
          const [a,b,c]=[polygon[0],polygon[i],polygon[i+1]];
          if(Math.abs((b.x-a.x)*(c.z-a.z)-(b.z-a.z)*(c.x-a.x))>1e-8)indices.push(index(a),index(b),index(c));
        }
      }
    }
    const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));geometry.setAttribute('uv',new THREE.Float32BufferAttribute(uv,2));geometry.setIndex(indices);geometry.computeVertexNormals();geometry.userData.base=new Float32Array(base);
    const texture=new THREE.Texture(image);texture.colorSpace=THREE.SRGBColorSpace;texture.flipY=false;texture.anisotropy=Math.min(4,this.view.renderer.capabilities.getMaxAnisotropy());texture.needsUpdate=true;
    const material=new THREE.MeshStandardMaterial({map:texture,roughness:1,metalness:0,transparent:true,opacity:this.opacity,depthWrite:false,polygonOffset:true,polygonOffsetFactor:-1,polygonOffsetUnits:-tile.z});
    const mesh=new THREE.Mesh(geometry,material);mesh.renderOrder=tile.z;mesh.receiveShadow=true;return mesh;
  }
  private sampleRoofs(tile:Tile,image:ImageBitmap):void {
    if(tile.z<16)return;
    const context=this.imageContext;context.clearRect(0,0,512,512);context.drawImage(image,0,0,512,512);const pixels=context.getImageData(0,0,512,512).data;
    const inside=(p:number[],ring:number[][])=>{let hit=false;for(let i=0,j=ring.length-1;i<ring.length;j=i++){const a=ring[i],b=ring[j];if((a[1]>p[1])!==(b[1]>p[1])&&p[0]<(b[0]-a[0])*(p[1]-a[1])/(b[1]-a[1])+a[0])hit=!hit;}return hit;};
    this.view.colorRoofs(tile.bounds,rings=>{
      const pixelRings=rings.map(ring=>ring.map(([x,z])=>{const p=this.geo(x,z),t=xy(p[0],p[1],tile.z);return[(t[0]-tile.x)*512,(t[1]-tile.y)*512];}));
      const points=pixelRings[0],holes=pixelRings.slice(1);if(!points?.length)return;
      const x0=Math.max(0,Math.floor(Math.min(...points.map(p=>p[0])))),x1=Math.min(511,Math.ceil(Math.max(...points.map(p=>p[0]))));
      const y0=Math.max(0,Math.floor(Math.min(...points.map(p=>p[1])))),y1=Math.min(511,Math.ceil(Math.max(...points.map(p=>p[1]))));
      if(x1<x0||y1<y0)return;const stride=Math.max(1,Math.ceil(Math.sqrt((x1-x0+1)*(y1-y0+1)/49)));let r=0,g=0,b=0,count=0;
      for(let y=y0;y<=y1;y+=stride)for(let x=x0;x<=x1;x+=stride){const point=[x+.5,y+.5];if(!inside(point,points)||holes.some(ring=>inside(point,ring)))continue;const i=(y*512+x)*4;r+=pixels[i];g+=pixels[i+1];b+=pixels[i+2];count++;}
      return count?[r/count,g/count,b/count]:undefined;
    },tile.z);
  }
  private dispose(tile:Tile):void {if(!tile.mesh)return;tile.mesh.removeFromParent();tile.mesh.geometry.dispose();tile.mesh.material.map?.dispose();tile.mesh.material.dispose();tile.image?.close();tile.mesh=undefined;tile.image=undefined;tile.provider=undefined;}
  tick(now:number):void {
    if(!this.enabled||!this.view.ready)return;
    if(this.retryUntil>0&&now>=this.retryUntil){this.retryUntil=0;this.burstStatusChecked=false;this.tileAvailability='retry_ready';this.reason='Satellite retrying · display only';this.lastSelect=0;this.onChange();}
    const distance=this.view.camera.position.distanceTo(this.view.controls.target),target=this.group.children.length?THREE.MathUtils.clamp((30-distance)/25,.16,1):0;
    const next=THREE.MathUtils.lerp(this.opacity,target,.12);if(Math.abs(next-this.opacity)>.001){this.opacity=next;this.view.setRoofImageryBlend(next);for(const tile of this.tiles.values())if(tile.mesh)tile.mesh.material.opacity=next;this.view.dirty=true;}
    if(this.lastExaggeration!==this.view.exaggeration){for(const tile of this.tiles.values())if(tile.mesh){const p=tile.mesh.geometry.getAttribute('position'),base=tile.mesh.geometry.userData.base as Float32Array;for(let i=0;i<p.count;i++)p.setY(i,(base[i]-this.view.terrain.min_height_m)*this.view.exaggeration*.001+.00015);p.needsUpdate=true;tile.mesh.geometry.computeVertexNormals();tile.mesh.geometry.computeBoundingSphere();}this.lastExaggeration=this.view.exaggeration;}
    if(now-this.lastSelect>300){const state=this.view.camera.matrixWorld.elements.join(',')+this.view.camera.projectionMatrix.elements.join(',');const retry=now>=this.retryUntil&&[...this.wanted].some(key=>{const tile=this.tiles.get(key);return tile&&!tile.mesh&&!tile.loading&&now>(tile.retryAt??0);});if(state!==this.lastCamera||this.lastSelect===0||retry){this.select(now);this.lastCamera=state;}this.lastSelect=now;}
  }
  diagnostics():Record<string,unknown>{const resident=[...this.tiles.values()].filter(t=>t.mesh);return{enabled:this.enabled,available:this.available,opacity:this.opacity,residentTextures:resident.length,peakTextures:this.peakTextures,maxTextures:MAX_TILES,pending:this.pending,maxConcurrentRequests:CONCURRENCY,cacheHits:this.cacheHits,activeProviders:this.activeProviders,zoomLevels:[...new Set(resident.filter(t=>t.mesh!.visible).map(t=>t.z))].sort(),imageryGpuEstimateBytes:resident.reduce((sum,tile)=>sum+(tile.image?.width??512)*(tile.image?.height??512)*4*4/3,0),failures:this.failures,tileAvailability:this.tileAvailability,backoffReason:this.backoffReason,cooldownRemainingSeconds:Math.max(0,Math.ceil((this.retryUntil-performance.now())/1000)),reason:this.reason};}
}
