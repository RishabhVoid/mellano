import * as THREE from 'three';
import type { Building, Terrain } from './types';

export type Bounds = [number,number,number,number];
export type BuildingInfo = { index:number; x:number; z:number; area:number; bounds:Bounds };
export type DetailBundle = { group:THREE.Group; dispose:()=>void; triangles:number };
type World = { terrain:Terrain; ground:(x:number,z:number)=>number; point:(x:number,z:number,elevation?:number)=>THREE.Vector3; exaggeration:number; exaggerateBuildings:boolean; buildingHeightScale:number };
type Aggregate = { ids:number[]; x:number; z:number; area:number; known:boolean; chunk:string; slot:number; height:number; severity:number };
type BuildingChunk = { ids:number[]; aggregates:Aggregate[]; bounds:THREE.Box3; localBounds:Bounds; detail?:DetailBundle; detailWanted:boolean; visible:boolean };
const CHUNK_M=1000, AGGREGATE_M=100;
const intersects=(a:Bounds,b?:Bounds)=>!b||a[0]<b[2]&&a[2]>b[0]&&a[1]<b[3]&&a[3]>b[1];
const zero=new THREE.Matrix4().makeScale(0,0,0);

export function footprintInfo(building:Building,index:number):BuildingInfo {
  let xmin=Infinity,zmin=Infinity,xmax=-Infinity,zmax=-Infinity,area=0;
  building.footprint.forEach((ring,part)=>{let twice=0;for(let i=0;i<ring.length;i++){const p=ring[i],q=ring[(i+1)%ring.length];twice+=p[0]*q[1]-q[0]*p[1];xmin=Math.min(xmin,p[0]);zmin=Math.min(zmin,p[1]);xmax=Math.max(xmax,p[0]);zmax=Math.max(zmax,p[1]);}area+=(part===0?1:-1)*Math.abs(twice)/2;});
  return {index,x:(xmin+xmax)/2,z:(zmin+zmax)/2,area:Math.max(0,area),bounds:[xmin,zmin,xmax,zmax]};
}

/** Display-only aggregates. No synthetic footprints/heights enter simulation or metrics. */
export class BuildingChunks {
  readonly group=new THREE.Group();
  private chunks=new Map<string,BuildingChunk>();
  private aggregates:Aggregate[]=[];
  private meshes:THREE.InstancedMesh[]=[];
  private states?:Uint8Array;
  private palette:THREE.Color[]=[];
  private impact=false;
  private transform=new THREE.Object3D();
  private infos:BuildingInfo[];
  private region?:Bounds;
  private lastCamera=new THREE.Matrix4();
  private changed=true;
  private dirtyChunks=new Set<BuildingChunk>();
  private dirtyBins=new Set<Aggregate>();
  private paletteKey="";
  private farTriangles=0;
  constructor(private world:World,private buildings:Building[],infos:BuildingInfo[],private buildDetail:(ids:number[])=>DetailBundle) {
    this.infos=infos;const bins=new Map<string,Aggregate>();
    for(const info of infos){if(!Number.isFinite(info.x)||!Number.isFinite(info.z)||!Number.isFinite(info.area)||info.area<=0)continue;const building=buildings[info.index],known=building.height!==null;
      const chunkKey=`${Math.floor(info.x/CHUNK_M)},${Math.floor(info.z/CHUNK_M)}`,key=`${Math.floor(info.x/AGGREGATE_M)},${Math.floor(info.z/AGGREGATE_M)},${known}`;
      let chunk=this.chunks.get(chunkKey);if(!chunk){chunk={ids:[],aggregates:[],bounds:new THREE.Box3(),localBounds:[Infinity,Infinity,-Infinity,-Infinity],detailWanted:false,visible:true};this.chunks.set(chunkKey,chunk);}
      chunk.ids.push(info.index);for(let i=0;i<2;i++){chunk.localBounds[i]=Math.min(chunk.localBounds[i],info.bounds[i]);chunk.localBounds[i+2]=Math.max(chunk.localBounds[i+2],info.bounds[i+2]);}
      let bin=bins.get(key);if(!bin){bin={ids:[],x:0,z:0,area:0,known,chunk:chunkKey,slot:0,height:0,severity:-1};bins.set(key,bin);chunk.aggregates.push(bin);}
      bin.ids.push(info.index);bin.x+=info.x*info.area;bin.z+=info.z*info.area;bin.area+=info.area;
    }
    this.aggregates=[...bins.values()];for(const bin of this.aggregates){bin.x/=bin.area;bin.z/=bin.area;bin.height=bin.ids.reduce((sum,id)=>sum+(buildings[id].height??0)*infos[id].area,0)/bin.area;}
    for(const known of [true,false]){const selected=this.aggregates.filter(bin=>bin.known===known);const geometry=known?new THREE.BoxGeometry(1,1,1):new THREE.PlaneGeometry(1,1);if(!known)geometry.rotateX(-Math.PI/2);
      const material=new THREE.MeshStandardMaterial({color:'#ffffff',roughness:.95,side:THREE.DoubleSide});
      material.onBeforeCompile=shader=>{
        shader.vertexShader=shader.vertexShader.replace('#include <common>',`#include <common>
varying vec3 vObjPos;
varying vec3 vObjNormal;`).replace('#include <begin_vertex>',`#include <begin_vertex>
vObjPos=position;
vObjNormal=normal;`);
        shader.fragmentShader=shader.fragmentShader.replace('#include <common>',`#include <common>
varying vec3 vObjPos;
varying vec3 vObjNormal;`).replace('#include <color_fragment>',`#include <color_fragment>
if(abs(vObjNormal.y)<0.5){
  // Aggregate box walls: horizontal floor lines and window rhythms
  float floors=sin(vObjPos.y*40.0);
  float cols=sin(vObjPos.x*28.0+vObjPos.z*28.0);
  float win=smoothstep(0.0,0.36,floors)*smoothstep(0.0,0.36,cols);
  diffuseColor.rgb*=0.68+0.32*(1.0-win*0.44);
}else if(vObjNormal.y>0.5){
  // Aggregate box roof: darker border and gravel texture
  float edge=max(abs(vObjPos.x),abs(vObjPos.z));
  float border=smoothstep(0.40,0.48,edge);
  diffuseColor.rgb*=(1.0-border*0.35);
}`);
      };
      const mesh=new THREE.InstancedMesh(geometry,material,selected.length);mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);mesh.frustumCulled=false;mesh.receiveShadow=true;mesh.castShadow=false;mesh.userData.aggregate=true;
      const warmPalette=['#d6c7a8','#c9b495','#bca787','#d3b998','#a89477','#b89a7a','#bfae95'];
      selected.forEach((bin,i)=>{bin.slot=i;const hash=(Math.abs(Math.floor(bin.x*0.13+bin.z*0.07))|0)%warmPalette.length;mesh.setColorAt(i,new THREE.Color(warmPalette[hash]));});this.meshes.push(mesh);this.group.add(mesh);this.farTriangles+=selected.length*(known?12:2);
    }
    this.refreshBounds();this.refreshAggregates();
  }
  private refreshBounds():void{for(const chunk of this.chunks.values()){const b=chunk.localBounds;chunk.bounds.min.copy(this.world.point(b[0],b[1],this.world.terrain.min_height_m));chunk.bounds.max.copy(this.world.point(b[2],b[3],this.world.terrain.max_height_m));chunk.bounds.min.y=-.23;let highest=chunk.bounds.max.y;for(const id of chunk.ids){const building=this.buildings[id];highest=Math.max(highest,this.world.point(0,0,building.ground_height_m).y+(building.height??0)*(this.world.exaggerateBuildings?this.world.buildingHeightScale:1)*.001);}chunk.bounds.max.y=highest;}}
  private refreshAggregates():void{
    const bins=this.changed?this.aggregates:[...new Set([...this.dirtyBins,...[...this.dirtyChunks].flatMap(chunk=>chunk.aggregates)])];
    for(const bin of bins){const chunk=this.chunks.get(bin.chunk)!,mesh=this.meshes[bin.known?0:1];if(!chunk.visible||chunk.detail){mesh.setMatrixAt(bin.slot,zero);continue;}
      const severity=bin.severity,height=bin.height*(this.world.exaggerateBuildings?this.world.buildingHeightScale:1)*.001;
      // Area-limited square masses summarize coverage; they are not replacement footprints.
      const width=Math.sqrt(Math.min(bin.area,AGGREGATE_M*AGGREGATE_M))*.001;
      this.transform.position.copy(this.world.point(bin.x,bin.z));this.transform.position.y+=height/2+.0007;this.transform.scale.set(width,bin.known?Math.max(height,.00001):1,width);this.transform.rotation.set(0,0,0);this.transform.updateMatrix();mesh.setMatrixAt(bin.slot,this.transform.matrix);
      const warmPalette=['#d6c7a8','#c9b495','#bca787','#d3b998','#a89477','#b89a7a','#bfae95'];
      const hash=(Math.abs(Math.floor(bin.x*0.13+bin.z*0.07))|0)%warmPalette.length;
      const color=new THREE.Color(warmPalette[hash]);if(this.impact&&this.palette[severity])color.lerp(this.palette[severity],.88);mesh.setColorAt(bin.slot,color);
    }
    for(const mesh of this.meshes){mesh.instanceMatrix.needsUpdate=true;if(mesh.instanceColor)mesh.instanceColor.needsUpdate=true;}this.changed=false;this.dirtyChunks.clear();this.dirtyBins.clear();
  }
  setStates(states:Uint8Array|undefined,palette:string[],impact:boolean):void{this.states=states;const key=palette.join();if(key!==this.paletteKey||this.impact!==impact)this.changed=true;this.paletteKey=key;this.palette=palette.map(color=>new THREE.Color(color));this.impact=impact;
    for(const bin of this.aggregates){let weightedHeight=0,severity=-1;for(const id of bin.ids){const building=this.buildings[id],state=states?.[id*2+1],band=states?.[id*2];let height=building.height??0;if(state===2)height=Math.min(height*.15,3);weightedHeight+=height*this.infos[id].area;if(band!==undefined&&band<5)severity=Math.max(severity,band);}const height=weightedHeight/bin.area;if(height!==bin.height||severity!==bin.severity)this.dirtyBins.add(bin);bin.height=height;bin.severity=severity;}
  }
  setImpact(impact:boolean):void{if(this.impact!==impact){this.impact=impact;this.changed=true;}}
  setRegion(bounds?:Bounds):void{this.region=bounds;this.changed=true;this.lastCamera.identity();}
  invalidateHeight():void{for(const chunk of this.chunks.values())if(chunk.detail){chunk.detail.dispose();this.group.remove(chunk.detail.group);chunk.detail=undefined;}this.refreshBounds();this.changed=true;this.lastCamera.identity();}
  update(camera:THREE.PerspectiveCamera,settle=false):boolean{
    camera.updateMatrixWorld();const matrix=new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix,camera.matrixWorldInverse);const frustum=new THREE.Frustum().setFromProjectionMatrix(matrix);
    const candidates:{chunk:BuildingChunk;distance:number}[]=[];
    for(const chunk of this.chunks.values()){
      const visible=intersects(chunk.localBounds,this.region)&&frustum.intersectsBox(chunk.bounds),distance=chunk.bounds.distanceToPoint(camera.position);
      if(chunk.visible!==visible){chunk.visible=visible;this.dirtyChunks.add(chunk);}if(chunk.detail)chunk.detail.group.visible=visible;
      const near=visible&&distance<(chunk.detailWanted?3.4:2.6);chunk.detailWanted=near;
      if(chunk.detail&&(!intersects(chunk.localBounds,this.region)||distance>3.8)){chunk.detail.dispose();this.group.remove(chunk.detail.group);chunk.detail=undefined;this.dirtyChunks.add(chunk);}
      if(near&&!chunk.detail)candidates.push({chunk,distance});
    }
    candidates.sort((a,b)=>a.distance-b.distance);const existing=[...this.chunks.values()].filter(chunk=>chunk.detail).length;const BUDGET=96;const count=Math.min(candidates.length,settle?Math.max(0,BUDGET-existing):Math.min(6,Math.max(0,BUDGET-existing)));
    for(let i=0;i<count;i++){const chunk=candidates[i].chunk;chunk.detail=this.buildDetail(chunk.ids);this.group.add(chunk.detail.group);this.dirtyChunks.add(chunk);}
    if(this.changed||this.dirtyChunks.size||this.dirtyBins.size)this.refreshAggregates();this.lastCamera.copy(matrix);
    return candidates.length>count&&existing+count<BUDGET;
  }
  stats():Record<string,number|string>{const detail=[...this.chunks.values()].filter(chunk=>chunk.detail);return {sourceFootprints:this.buildings.length,aggregateCells:this.aggregates.length,farTriangles:this.farTriangles,detailChunks:detail.length,detailTriangles:detail.reduce((n,chunk)=>n+chunk.detail!.triangles,0),chunkSizeM:CHUNK_M,aggregateCellSizeM:AGGREGATE_M,mode:'100m area-weighted display aggregates / nearby real footprints'};}
  dispose():void{for(const chunk of this.chunks.values())chunk.detail?.dispose();for(const mesh of this.meshes){mesh.geometry.dispose();(mesh.material as THREE.Material).dispose();}this.group.clear();this.chunks.clear();this.aggregates=[];}
}

type TerrainChunk = {bounds:Bounds;box:THREE.Box3;mesh:THREE.Mesh;overlay?:THREE.Mesh;level:number};
export class TerrainChunks {
  readonly group=new THREE.Group();
  private chunks:TerrainChunk[]=[];
  private region?:Bounds;
  private impactMaterial?:THREE.MeshBasicMaterial;
  private impactVisible=false;
  constructor(private world:World,private material:THREE.MeshStandardMaterial){
    for(let z=0;z<world.terrain.extent_m[1];z+=CHUNK_M)for(let x=0;x<world.terrain.extent_m[0];x+=CHUNK_M){const bounds:Bounds=[x,z,Math.min(x+CHUNK_M,world.terrain.extent_m[0]),Math.min(z+CHUNK_M,world.terrain.extent_m[1])],geometry=this.geometry(bounds,12),mesh=new THREE.Mesh(geometry,material);mesh.receiveShadow=true;geometry.computeBoundingBox();this.chunks.push({bounds,box:geometry.boundingBox!.clone(),mesh,level:12});this.group.add(mesh);}
  }
  private geometry(bounds:Bounds,stride:number):THREE.BufferGeometry{
    const cell=this.world.terrain.cell_size_m,step=cell*stride,cols=Math.ceil((bounds[2]-bounds[0])/step),rows=Math.ceil((bounds[3]-bounds[1])/step);
    const positions:number[]=[],normals:number[]=[],uv:number[]=[],indices:number[]=[],vertices=new Map<string,number>();
    const vertex=(x:number,z:number)=>{const key=`${x},${z}`,existing=vertices.get(key);if(existing!==undefined)return existing;
      const id=positions.length/3;positions.push(...this.world.point(x,z).toArray());uv.push(x/this.world.terrain.extent_m[0],z/this.world.terrain.extent_m[1]);
      const xa=Math.max(0,x-cell),xb=Math.min(this.world.terrain.extent_m[0],x+cell),za=Math.max(0,z-cell),zb=Math.min(this.world.terrain.extent_m[1],z+cell);
      const dx=(this.world.ground(xb,z)-this.world.ground(xa,z))/(xb-xa||1),dz=(this.world.ground(x,zb)-this.world.ground(x,za))/(zb-za||1);
      const normal=new THREE.Vector3(-dx*this.world.exaggeration,1,-dz*this.world.exaggeration).normalize();normals.push(normal.x,normal.y,normal.z);vertices.set(key,id);return id;};
    for(let r=0;r<rows;r++)for(let c=0;c<cols;c++){
      const x0=Math.min(bounds[0]+c*step,bounds[2]),x1=Math.min(x0+step,bounds[2]),z0=Math.min(bounds[1]+r*step,bounds[3]),z1=Math.min(z0+step,bounds[3]);
      if(stride===1||(r>0&&r<rows-1&&c>0&&c<cols-1)){const a=vertex(x0,z0),b=vertex(x1,z0),d=vertex(x0,z1),e=vertex(x1,z1);indices.push(a,d,b,b,d,e);continue;}
      // All shared boundaries keep native-grid vertices, regardless of interior LOD.
      // A surface fan stitches the coarse interior to that identical boundary; no vertical skirts.
      const perimeter:number[]=[];
      const edge=(ax:number,az:number,bx:number,bz:number,dense:boolean)=>{const length=Math.hypot(bx-ax,bz-az),n=dense?Math.ceil(length/cell):1;for(let i=0;i<n;i++){const t=dense?Math.min(i*cell/length,1):0;perimeter.push(vertex(ax+(bx-ax)*t,az+(bz-az)*t));}};
      edge(x0,z0,x1,z0,r===0);edge(x1,z0,x1,z1,c===cols-1);
      // Reverse the same forward sample sequence on the south/west boundaries.
      const reverseEdge=(ax:number,az:number,bx:number,bz:number,dense:boolean)=>{if(!dense){perimeter.push(vertex(bx,bz));return;}const length=Math.hypot(bx-ax,bz-az),n=Math.ceil(length/cell);perimeter.push(vertex(bx,bz));for(let i=n-1;i>0;i--){const t=Math.min(i*cell/length,1);perimeter.push(vertex(ax+(bx-ax)*t,az+(bz-az)*t));}};
      reverseEdge(x0,z1,x1,z1,r===rows-1);reverseEdge(x0,z0,x0,z1,c===0);
      const center=vertex((x0+x1)/2,(z0+z1)/2);for(let i=0;i<perimeter.length;i++)indices.push(center,perimeter[(i+1)%perimeter.length],perimeter[i]);
    }
    const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));geometry.setAttribute('normal',new THREE.Float32BufferAttribute(normals,3));geometry.setAttribute('uv',new THREE.Float32BufferAttribute(uv,2));geometry.setIndex(indices);geometry.computeBoundingBox();geometry.computeBoundingSphere();return geometry;
  }
  boundarySegments(bounds:Bounds):number[][][]{
    const result:number[][][]=[],cell=this.world.terrain.cell_size_m;
    for(const chunk of this.chunks){if(!intersects(chunk.bounds,bounds))continue;const b:Bounds=[Math.max(chunk.bounds[0],bounds[0]),Math.max(chunk.bounds[1],bounds[1]),Math.min(chunk.bounds[2],bounds[2]),Math.min(chunk.bounds[3],bounds[3])];if(b[0]>=b[2]||b[1]>=b[3])continue;
      const edge=(x0:number,z0:number,x1:number,z1:number)=>{const n=Math.ceil(Math.hypot(x1-x0,z1-z0)/cell),line:number[][]=[];for(let i=0;i<=n;i++){const t=Math.min(i*cell/(Math.hypot(x1-x0,z1-z0)||1),1);line.push([x0+(x1-x0)*t,z0+(z1-z0)*t]);}result.push(line);};
      if(b[1]===bounds[1]&&bounds[1]>0)edge(b[0],b[1],b[2],b[1]);if(b[3]===bounds[3]&&bounds[3]<this.world.terrain.extent_m[1])edge(b[0],b[3],b[2],b[3]);
      if(b[0]===bounds[0]&&bounds[0]>0)edge(b[0],b[1],b[0],b[3]);if(b[2]===bounds[2]&&bounds[2]<this.world.terrain.extent_m[0])edge(b[2],b[1],b[2],b[3]);
    }return result;
  }
  setRegion(bounds?:Bounds):void{this.region=bounds;for(const chunk of this.chunks)if(intersects(chunk.bounds,bounds))this.replace(chunk,chunk.level);}
  setImpact(material:THREE.MeshBasicMaterial,visible:boolean):void{this.impactMaterial=material;this.impactVisible=visible;for(const chunk of this.chunks){if(!chunk.overlay){chunk.overlay=new THREE.Mesh(chunk.mesh.geometry,material);chunk.overlay.position.y=.0025;chunk.overlay.renderOrder=3;this.group.add(chunk.overlay);}chunk.overlay.visible=visible&&chunk.mesh.visible;}}
  setImpactVisible(visible:boolean):void{this.impactVisible=visible;for(const chunk of this.chunks)if(chunk.overlay)chunk.overlay.visible=visible&&chunk.mesh.visible;}
  invalidateHeight():void{for(const chunk of this.chunks)this.replace(chunk,chunk.level);}
  private replace(chunk:TerrainChunk,level:number):void{const old=chunk.mesh.geometry;const b=this.region&&intersects(chunk.bounds,this.region)?[Math.max(chunk.bounds[0],this.region[0]),Math.max(chunk.bounds[1],this.region[1]),Math.min(chunk.bounds[2],this.region[2]),Math.min(chunk.bounds[3],this.region[3])] as Bounds:chunk.bounds;if(b[0]>=b[2]||b[1]>=b[3])return;chunk.mesh.geometry=this.geometry(b,level);chunk.level=level;chunk.box.copy(chunk.mesh.geometry.boundingBox!);if(chunk.overlay)chunk.overlay.geometry=chunk.mesh.geometry;old.dispose();}
  update(camera:THREE.PerspectiveCamera,settle=false):boolean{
    camera.updateMatrixWorld();const frustum=new THREE.Frustum().setFromProjectionMatrix(new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix,camera.matrixWorldInverse));let upgrades=0,pending=false;
    const ordered=[...this.chunks].sort((a,b)=>a.box.distanceToPoint(camera.position)-b.box.distanceToPoint(camera.position));
    for(const chunk of ordered){const visible=intersects(chunk.bounds,this.region)&&frustum.intersectsBox(chunk.box);chunk.mesh.visible=visible;if(chunk.overlay)chunk.overlay.visible=visible&&this.impactVisible;
      const distance=chunk.box.distanceToPoint(camera.position);let level=12;const inRegion=intersects(chunk.bounds,this.region);if(inRegion&&(visible||chunk.level===1)&&distance<(chunk.level===1?1.1:.8))level=1;else if(inRegion&&(visible||chunk.level<=4)&&distance<(chunk.level<=4?3.5:3))level=4;
      if(level===chunk.level)continue;if(level>chunk.level||settle||upgrades<2){this.replace(chunk,level);upgrades++;}else pending=true;
    }return pending;
  }
  stats():Record<string,number>{return {chunks:this.chunks.length,visibleChunks:this.chunks.filter(c=>c.mesh.visible).length,triangles:this.chunks.filter(c=>c.mesh.visible).reduce((n,c)=>n+c.mesh.geometry.index!.count/3,0),nearChunks:this.chunks.filter(c=>c.level===1).length};}
  dispose():void{for(const chunk of this.chunks)chunk.mesh.geometry.dispose();this.chunks=[];this.group.clear();}
}
