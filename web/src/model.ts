import * as THREE from 'three';
import proj4 from 'proj4';
import { BuildingChunks, TerrainChunks, footprintInfo, type Bounds, type DetailBundle } from './modelLod';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { SSAOPass } from 'three/addons/postprocessing/SSAOPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';
import type { Building, Features, Geo, Mask, Run, Terrain, Road } from './types';

const SCALE = 0.001;
type ElevationGeometry = { geometry: THREE.BufferGeometry; base: Float32Array; rise?: Float32Array; lift?: number };
type RoofRecord = { buildingIndex: number; elevation: number; height: number; rings: number[][][]; bounds: [number, number, number, number]; colors?: THREE.BufferAttribute; start: number; count: number; zoom: number };
const ROOF_INDEX_CELL_M = 500;
const FACADE_UNITS_PER_M = 20;
const NO_FACADE = 65535;

export class Diorama {
  renderer: THREE.WebGLRenderer;
  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(36, 1, 0.01, 150);
  controls: OrbitControls;
  composer: EffectComposer;
  ssao: SSAOPass;
  terrain!: Terrain;
  heights!: Float32Array;
  terrainMesh!: THREE.Mesh;
  buildings = new THREE.Group();
  embankments = new THREE.Group();
  outline = new THREE.Group();
  reported = new THREE.Group();
  freshness = new THREE.Group();
  edit = new THREE.Group();
  water = new THREE.Group();
  roads = new THREE.Group();
  riverVisible = true;
  greenVisible = true;
  private landcover?: Uint8Array;
  private surfaceTexture?: THREE.DataTexture;
  private cityTexture?: THREE.CanvasTexture;
  private roofPixels?: { data: Uint8ClampedArray; width: number; height: number };
  private wallTextureUniform: { value: THREE.CanvasTexture | null } = { value: null };
  private roofTextureUniform: { value: THREE.CanvasTexture | null } = { value: null };
  exaggeration = 4;
  exaggerateBuildings = true;
  buildingHeightScale = 5;
  ready = false;
  fires = new THREE.Group();
  private fireTime = { value: 0 };
  private fireSprites = new Map<number, THREE.Sprite>();
  private quakeStart = -Infinity;
  private shakeAmp = { value: 0 };
  private shakeTime = { value: 0 };
  private collapseProgress = { value: 1 };
  private collapseStart = -Infinity;
  private lastTickNow = 0;
  ao = true;
  split = false;
  private splitWidth = 0;
  private splitScene = new THREE.Scene();
  private splitCamera = new THREE.OrthographicCamera(-1,1,1,-1,0,1);
  private splitMaterial = new THREE.ShaderMaterial({uniforms:{image:{value:null}},vertexShader:'varying vec2 vUv;void main(){vUv=uv;gl_Position=vec4(position.xy,0.,1.);}',fragmentShader:'uniform sampler2D image;varying vec2 vUv;void main(){gl_FragColor=texture2D(image,vUv);}',depthTest:false,depthWrite:false,toneMapped:false});
  dirty = true;
  drawCount = 0;
  frameTimes: number[] = [];
  private elevations: ElevationGeometry[] = [];
  private mask?: Mask;
  private places?: Features;
  private freshFeatures?: Features;
  private bankLines: number[][][] = [];
  private currentDepth?: { data: Float32Array; run: Run };
  private transition?: { start: number; from: THREE.Vector3; to: THREE.Vector3; targetFrom: THREE.Vector3; targetTo: THREE.Vector3 };
  private raycaster = new THREE.Raycaster();
  private roofRecords: RoofRecord[] = [];
  private roofIndex = new Map<number, number[]>();
  private roofIndexColumns = 1;
  private impactBlend = { value: 0 };
  private collapseScale = { value: SCALE };
  private sourceBuildings:Building[]=[];
  private buildingStateData?:Uint8Array;
  private buildingPalette=['#66ad85','#e1cd65','#e19a4c','#d85b53','#74457f'];
  private buildingLod?:BuildingChunks;
  private terrainLod?:TerrainChunks;
  private areaBounds?:Bounds;
  private areaWalls=new THREE.Group();
  private lodPending=false;
  private buildingSpans: {color:THREE.BufferAttribute;state:THREE.BufferAttribute;start:number;count:number;severity:number;damage:number}[] = [];
  private impactSurface?: THREE.Mesh;
  private impactTexture?: THREE.DataTexture;
  private hasTerrainSeverity=false;
  private roofImageryBlend = { value: 0 };
  private roofLinearBytes = Uint8Array.from({ length: 256 }, (_, value) => Math.round(new THREE.Color(value / 255, value / 255, value / 255).convertSRGBToLinear().r * 255));

  constructor(host: HTMLElement) {
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true, powerPreference: 'high-performance' });
    this.renderer.info.autoReset = false;
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.25));
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 0.95;
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFShadowMap;
    host.append(this.renderer.domElement);
    this.renderer.domElement.setAttribute('aria-label', 'Offline 3D model of the Delhi Yamuna study area. Drag to orbit; right drag to pan; scroll to zoom.');
    this.renderer.domElement.tabIndex = 0;
    this.renderer.setClearColor(0x000000, 0);
    this.scene.add(new THREE.AmbientLight('#e9e8e2', 0.65));
    const key = new THREE.DirectionalLight('#fff5e4', 2.5);
    key.position.set(-8, 18, -10); key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048); key.shadow.camera.left = -14; key.shadow.camera.right = 14;
    key.shadow.camera.top = 14; key.shadow.camera.bottom = -14; key.shadow.camera.far = 65;
    key.shadow.bias = -0.00015; key.shadow.normalBias = 0.005; key.shadow.radius = 3;
    this.scene.add(key);
    this.scene.add(new THREE.HemisphereLight('#e6edf2', '#716a60', 0.65));
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true; this.controls.dampingFactor = 0.09;
    this.controls.minDistance = 0.02; this.controls.maxDistance = 60; this.controls.maxPolarAngle = Math.PI * 0.485;
    this.controls.addEventListener('change', () => { this.dirty = true; });
    this.controls.addEventListener('start', () => { this.transition = undefined; });
    this.composer = new EffectComposer(this.renderer);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.ssao = new SSAOPass(this.scene, this.camera, 960, 540, 8);
    this.ssao.kernelRadius = 10; this.ssao.minDistance = 0.0003; this.ssao.maxDistance = 0.015;
    // Fix AO samples and noise so repeated scene seeks/reloads use the same shading.
    const seededAO = this.ssao as unknown as { kernel: THREE.Vector3[]; noiseTexture: THREE.DataTexture };
    seededAO.kernel.forEach((sample, i) => { const angle = i * 2.399963229728653; const z = (i + 0.5) / seededAO.kernel.length; sample.set(Math.cos(angle) * Math.sqrt(1-z*z), Math.sin(angle) * Math.sqrt(1-z*z), z).multiplyScalar(0.1 + 0.9 * (i / seededAO.kernel.length) ** 2); });
    const noise = seededAO.noiseTexture.image.data as Float32Array;
    for (let i = 0; i < noise.length; i++) noise[i] = Math.sin(i * 2.399963229728653) * 0.5;
    seededAO.noiseTexture.needsUpdate = true;
    this.composer.addPass(this.ssao); this.composer.addPass(new OutputPass());
    // Composite the exact sRGB studio gradient after tone mapping; it is part of the captured canvas.
    this.composer.addPass(new ShaderPass({ uniforms: { tDiffuse: { value: null } }, vertexShader: 'varying vec2 vUv; void main(){vUv=uv;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}', fragmentShader: 'uniform sampler2D tDiffuse; varying vec2 vUv; void main(){vec4 c=texture2D(tDiffuse,vUv);vec3 bg=mix(vec3(42.,43.,47.)/255.,vec3(28.,29.,32.)/255.,vUv.y);gl_FragColor=vec4(c.rgb+bg*(1.0-c.a),1.0);}' }));
    this.splitScene.add(new THREE.Mesh(new THREE.PlaneGeometry(2,2),this.splitMaterial));
    this.scene.add(this.buildings, this.embankments, this.outline, this.reported, this.freshness, this.edit, this.water, this.roads, this.fires);
    this.embankments.visible = false; this.outline.visible = false; this.reported.visible = false; this.freshness.visible = false;
    new ResizeObserver(() => this.resize()).observe(host);
    this.resize();
  }

  resize(): void {
    const rect = this.renderer.domElement.parentElement!.getBoundingClientRect();
    this.camera.aspect = rect.width / (this.split ? 2 : 1) / rect.height;
    const advanced = document.getElementById('panel');
    const areaPanel=document.body.classList.contains('area-flow-open')?document.getElementById('area-flow'):null;
    const panel = areaPanel??(document.body.classList.contains('canvas-workspace')?document.getElementById('canvas-inspector'):(advanced && !advanced.hidden ? advanced : document.getElementById('workflow-card')));
    const hasPanel = panel && !panel.hidden && panel.getBoundingClientRect().width>0 && !new URLSearchParams(location.search).has('scene');
    const availableWidth = hasPanel ? panel.getBoundingClientRect().left : rect.width;
    this.splitWidth=availableWidth;document.documentElement.style.setProperty('--split-end',`${availableWidth}px`);
    if(this.split)this.camera.aspect=availableWidth/2/rect.height;
    const shift = (rect.width - availableWidth) / 2;
    if(this.split)this.camera.clearViewOffset();else this.camera.setViewOffset(rect.width, rect.height, shift, hasPanel ? rect.height * 0.10 : 0, rect.width, rect.height); this.camera.updateProjectionMatrix();
    this.renderer.setSize(rect.width, rect.height); this.composer.setSize(this.split ? availableWidth/2 : rect.width, rect.height);
    this.dirty = true;
  }

  captureSize(width:number,height:number):void {
    this.renderer.setPixelRatio(1);this.composer.setPixelRatio(1);this.renderer.setSize(width,height,false);
    this.splitWidth=width;this.composer.setSize(this.split?width/2:width,height);
    this.camera.clearViewOffset();this.camera.aspect=width/(this.split?2:1)/height;this.camera.updateProjectionMatrix();this.dirty=true;
  }

  ground(x: number, z: number): number {
    const c = THREE.MathUtils.clamp(Math.floor(x / this.terrain.cell_size_m), 0, this.terrain.width - 1);
    const r = THREE.MathUtils.clamp(Math.floor(z / this.terrain.cell_size_m), 0, this.terrain.height - 1);
    return this.heights[r * this.terrain.width + c];
  }

  point(x: number, z: number, elevation?: number): THREE.Vector3 {
    return new THREE.Vector3((x - this.terrain.extent_m[0] / 2) * SCALE,
      ((elevation ?? this.ground(x, z)) - this.terrain.min_height_m) * this.exaggeration * SCALE,
      (z - this.terrain.extent_m[1] / 2) * SCALE);
  }

  async load(metadata: Terrain, heights: Float32Array, buildings: Building[], lines: number[][][]): Promise<void> {
    this.buildingLod?.dispose();this.terrainLod?.dispose();
    this.terrain = metadata; this.heights = heights; this.bankLines = lines;this.sourceBuildings=buildings;
    if (heights.length !== metadata.width * metadata.height) throw new Error('Terrain binary dimensions do not match metadata.');
    const stride = metadata.render_stride;
    const cols = Math.ceil(metadata.width / stride), rows = Math.ceil(metadata.height / stride);
    const positions = new Float32Array((cols + 1) * (rows + 1) * 3);
    const bases = new Float32Array((cols + 1) * (rows + 1));
    const indices: number[] = [];
    for (let r = 0; r <= rows; r++) for (let c = 0; c <= cols; c++) {
      const i = r * (cols + 1) + c;
      const x = Math.min(c * stride * metadata.cell_size_m, metadata.extent_m[0]);
      const z = Math.min(r * stride * metadata.cell_size_m, metadata.extent_m[1]);
      const point = this.point(x, z); positions.set(point.toArray(), i * 3); bases[i] = this.ground(x, z);
      if (c < cols && r < rows) { const b = i + cols + 1; indices.push(i, b, i + 1, i + 1, b, b + 1); }
    }
    const geometry = new THREE.BufferGeometry(); geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3)); geometry.setIndex(indices); geometry.computeVertexNormals();
    this.elevations.push({ geometry, base: bases });
    const uv = new Float32Array((cols + 1) * (rows + 1) * 2);
    for (let r=0;r<=rows;r++) for(let c=0;c<=cols;c++){const i=r*(cols+1)+c;uv[i*2]=Math.min(c*stride*metadata.cell_size_m,metadata.extent_m[0])/metadata.extent_m[0];uv[i*2+1]=Math.min(r*stride*metadata.cell_size_m,metadata.extent_m[1])/metadata.extent_m[1];}
    geometry.setAttribute('uv', new THREE.BufferAttribute(uv,2));
    this.terrainMesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ color: '#b8b5a9', roughness: 0.92, metalness: 0 }));
    this.terrainMesh.receiveShadow = true; this.terrainMesh.castShadow = true;
    this.terrainLod=new TerrainChunks(this,this.terrainMesh.material as THREE.MeshStandardMaterial);this.scene.add(this.terrainLod.group);
    const edge: [number, number][] = [],edgeStep=metadata.cell_size_m;
    for(let x=0;x<=metadata.extent_m[0];x+=edgeStep)edge.push([x,0]);
    for(let z=edgeStep;z<=metadata.extent_m[1];z+=edgeStep)edge.push([metadata.extent_m[0],z]);
    for(let x=metadata.extent_m[0]-edgeStep;x>=0;x-=edgeStep)edge.push([x,metadata.extent_m[1]]);
    for(let z=metadata.extent_m[1]-edgeStep;z>0;z-=edgeStep)edge.push([0,z]);
    const wallVertices: number[] = [], wallBases: number[] = [];
    for (let i = 0; i < edge.length; i++) {
      const a = this.point(...edge[i]), b = this.point(...edge[(i + 1) % edge.length]);
      for (const [p, top] of [[a, true], [b, true], [a, false], [b, true], [b, false], [a, false]] as [THREE.Vector3, boolean][]) {
        wallVertices.push(p.x, top ? p.y : -0.22, p.z);
        wallBases.push(top ? p.y / this.exaggeration / SCALE + metadata.min_height_m : NaN);
      }
    }
    const wall = new THREE.BufferGeometry(); wall.setAttribute('position', new THREE.Float32BufferAttribute(wallVertices, 3)); wall.computeVertexNormals();
    this.elevations.push({ geometry: wall, base: new Float32Array(wallBases) });
    const block = new THREE.Mesh(wall, new THREE.MeshStandardMaterial({ color: '#98968e', roughness: 1, side: THREE.DoubleSide })); block.castShadow = true; block.receiveShadow = true; this.scene.add(block);
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(160, 160), new THREE.ShadowMaterial({ opacity: 0.32 })); floor.rotation.x = -Math.PI / 2; floor.position.y = -0.225; floor.receiveShadow = true; this.scene.add(floor);
    const solidBottom = new THREE.Mesh(new THREE.BoxGeometry(metadata.extent_m[0] * SCALE, 0.015, metadata.extent_m[1] * SCALE), new THREE.MeshStandardMaterial({ color: '#939087', roughness: 1 })); solidBottom.position.y = -0.22; this.scene.add(solidBottom);
    this.roofRecords = []; this.roofIndex.clear();this.buildingSpans.length=buildings.length;
    this.roofIndexColumns = Math.ceil(metadata.extent_m[0] / ROOF_INDEX_CELL_M) + 1;
    const infos=buildings.map((building,index)=>footprintInfo(building,index));
    for(const info of infos){const building=buildings[info.index];if(!Number.isFinite(info.x)||!Number.isFinite(info.z)||!Number.isFinite(info.area)||info.area<=0)continue;
      this.roofRecords[info.index]={buildingIndex:info.index,elevation:building.ground_height_m,height:building.height??0,rings:building.footprint,bounds:info.bounds,start:0,count:0,zoom:-1};
      const [xmin,zmin,xmax,zmax]=info.bounds;
      for(let r=Math.max(0,Math.floor(zmin/ROOF_INDEX_CELL_M));r<=Math.min(Math.ceil(metadata.extent_m[1]/ROOF_INDEX_CELL_M),Math.floor(zmax/ROOF_INDEX_CELL_M));r++)for(let c=Math.max(0,Math.floor(xmin/ROOF_INDEX_CELL_M));c<=Math.min(this.roofIndexColumns-1,Math.floor(xmax/ROOF_INDEX_CELL_M));c++){
        const key=r*this.roofIndexColumns+c,bucket=this.roofIndex.get(key);if(bucket)bucket.push(info.index);else this.roofIndex.set(key,[info.index]);
      }
      if(info.index%8000===0){const progress=document.querySelector('#loading small');if(progress)progress.textContent=`Indexing real footprints · ${info.index.toLocaleString()} / ${buildings.length.toLocaleString()}`;await new Promise<void>(resolve=>{const channel=new MessageChannel();channel.port1.onmessage=()=>{channel.port1.close();channel.port2.close();resolve();};channel.port2.postMessage(null);});}
    }
    this.buildingLod=new BuildingChunks(this,buildings,infos,ids=>this.buildBuildingChunk(ids));this.buildings.add(this.buildingLod.group);
    this.updateOverlays(); this.ready = true; this.preset('oblique', false); this.dirty = true;
  }

  private buildBuildingChunk(ids:number[]):DetailBundle {
    const buildings=this.sourceBuildings,group=new THREE.Group();
    for(const known of [true,false]) {
      const selected=ids.map(index=>({building:buildings[index],index})).filter(item=>(item.building.height!==null)===known);
      const ringPoints=(building:Building)=>building.footprint.map(ring=>ring.length>1&&ring[0][0]===ring[ring.length-1][0]&&ring[0][1]===ring[ring.length-1][1]?ring.slice(0,-1):ring);
      const capacity=selected.reduce((sum,{building})=>sum+ringPoints(building).reduce((n,ring)=>n+ring.length,0)*(known?5:1),0);
      if(!capacity)continue;
      const positions=new Float32Array(capacity*3),base=new Float32Array(capacity),rise=new Float32Array(capacity);
      const roofColors = new THREE.BufferAttribute(new Uint8Array(capacity * 4), 4, true).setUsage(THREE.DynamicDrawUsage);
      const impactColors=new THREE.BufferAttribute(new Uint8Array(capacity*4),4,true).setUsage(THREE.DynamicDrawUsage);
      const impactStates=new THREE.BufferAttribute(new Uint8Array(capacity),1).setUsage(THREE.DynamicDrawUsage);
      const facade = new Uint16Array(capacity * 2); facade.fill(NO_FACADE);
      const indices:number[]=[];let cursor=0;
      const vertex=(x:number,z:number,elevation:number,height:number)=>{const i=cursor++;const point=this.point(x,z,elevation);positions.set([point.x,point.y+height*(this.exaggerateBuildings?this.buildingHeightScale:1)*SCALE+.0005,point.z],i*3);base[i]=elevation;rise[i]=height;return i;};
      for(let i=0;i<selected.length;i++) {
        const {building,index:buildingIndex}=selected[i],rings=ringPoints(building);if(!rings[0]||rings[0].length<3)continue;
        const contour=rings[0].map(([x,z])=>new THREE.Vector2(x,z)),holes=rings.slice(1).map(ring=>ring.map(([x,z])=>new THREE.Vector2(x,z)));
        const flat=rings.flat(),start=cursor,height=building.height??0;
        for(const [x,z] of flat)vertex(x,z,building.ground_height_m,height);
        let xmin=Infinity,zmin=Infinity,xmax=-Infinity,zmax=-Infinity;
        for(const [x,z] of flat){xmin=Math.min(xmin,x);zmin=Math.min(zmin,z);xmax=Math.max(xmax,x);zmax=Math.max(zmax,z);}
        const roof=this.roofRecords[buildingIndex];if(roof){roof.colors=roofColors;roof.start=start;roof.count=flat.length;roof.zoom=-1;}
        for(const face of THREE.ShapeUtils.triangulateShape(contour,holes)) {
          const [a,b,c]=face,pa=flat[a],pb=flat[b],pc=flat[c];
          const up=(pb[1]-pa[1])*(pc[0]-pa[0])-(pb[0]-pa[0])*(pc[1]-pa[1]);
          indices.push(start+a,start+(up>=0?b:c),start+(up>=0?c:b));
        }
        if(known)for(const ring of rings)for(let j=0;j<ring.length;j++) {
          const [ax,az]=ring[j],[bx,bz]=ring[(j+1)%ring.length];
          const a=vertex(ax,az,building.ground_height_m,0),b=vertex(bx,bz,building.ground_height_m,0),c=vertex(ax,az,building.ground_height_m,height),d=vertex(bx,bz,building.ground_height_m,height);
          const width=Math.min(NO_FACADE-1,Math.round(Math.hypot(bx-ax,bz-az)*FACADE_UNITS_PER_M));
          const top=Math.min(NO_FACADE-1,Math.round(height*FACADE_UNITS_PER_M));
          facade.set([0,0,width,0,0,top,width,top],a*2);
          indices.push(a,b,c,c,b,d);
        }
        this.buildingSpans[buildingIndex]={color:impactColors,state:impactStates,start,count:cursor-start,severity:255,damage:0};

      }
      const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.BufferAttribute(positions.subarray(0,cursor*3),3));geometry.setIndex(indices);geometry.computeVertexNormals();
      roofColors.array = (roofColors.array as Uint8Array).subarray(0,cursor*4);
      impactColors.array=(impactColors.array as Uint8Array).subarray(0,cursor*4);impactStates.array=(impactStates.array as Uint8Array).subarray(0,cursor);
      geometry.setAttribute('impactColor',impactColors);geometry.setAttribute('impactState',impactStates);geometry.setAttribute('buildingRise',new THREE.BufferAttribute(rise.subarray(0,cursor),1));
      geometry.setAttribute('roofColor',roofColors);geometry.setAttribute('facade',new THREE.BufferAttribute(facade.subarray(0,cursor*2),2));
      this.elevations.push({geometry,base:base.subarray(0,cursor),rise:rise.subarray(0,cursor)});
      const mesh=new THREE.Mesh(geometry,this.buildingMaterial(known));mesh.castShadow=known;mesh.receiveShadow=true;group.add(mesh);
    }
    this.setBuildingStates(this.buildingStateData,this.buildingPalette);
    this.paintRoofs(ids);
    const owned=new Set(group.children.map(item=>(item as THREE.Mesh).geometry));
    const triangles=[...owned].reduce((n,geometry)=>n+(geometry.index?.count??0)/3,0);
    return {group,triangles,dispose:()=>{
      for(const id of ids){delete this.buildingSpans[id];const roof=this.roofRecords[id];if(roof)roof.colors=undefined;}
      this.elevations=this.elevations.filter(entry=>!owned.has(entry.geometry));
      for(const child of group.children){const mesh=child as THREE.Mesh;mesh.geometry.dispose();(mesh.material as THREE.Material).dispose();}group.clear();
    }};
  }

  private ensureBuildingTextures(): void {
    if (!this.wallTextureUniform.value) {
      const canvas = document.createElement('canvas'); canvas.width = 1024; canvas.height = 1024;
      const ctx = canvas.getContext('2d')!;
      let seed = 42; const rand = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };
      // Warm concrete / plaster base
      const grad = ctx.createLinearGradient(0, 0, 0, 1024); grad.addColorStop(0, '#e2d9c6'); grad.addColorStop(0.5, '#d3c7af'); grad.addColorStop(1, '#b5a98f');
      ctx.fillStyle = grad; ctx.fillRect(0, 0, 1024, 1024);
      // Horizontal brick / shutter banding
      ctx.save(); ctx.globalAlpha = 0.08; ctx.fillStyle = '#50463a';
      for (let y = 0; y < 1024; y += 4) ctx.fillRect(0, y, 1024, 1);
      ctx.restore();
      // Vertical masonry columns separating window bays
      const cols = 10, rows = 16, cellW = 1024 / cols, cellH = 1024 / rows;
      for (let c = 0; c <= cols; c++) {
        ctx.fillStyle = 'rgba(80,70,55,0.18)'; ctx.fillRect(c * cellW - 2, 0, 4, 1024);
      }
      // Fine stucco noise
      const img = ctx.getImageData(0, 0, 1024, 1024), data = img.data;
      for (let i = 0; i < data.length; i += 4) { const n = (rand() - 0.5) * 36; data[i] = Math.max(0, Math.min(255, data[i] + n)); data[i+1] = Math.max(0, Math.min(255, data[i+1] + n * 0.9)); data[i+2] = Math.max(0, Math.min(255, data[i+2] + n * 0.8)); }
      ctx.putImageData(img, 0, 0);
      // Windows
      for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
        const x = c * cellW, y = r * cellH;
        // Dark floor slab shadow
        ctx.fillStyle = 'rgba(45,38,30,0.42)'; ctx.fillRect(x, y, cellW, cellH * 0.12);
        // Sill under window
        ctx.fillStyle = 'rgba(90,80,65,0.55)'; ctx.fillRect(x + cellW * 0.08, y + cellH * 0.82, cellW * 0.84, cellH * 0.06);
        const wx = x + cellW * 0.18, wy = y + cellH * 0.22, ww = cellW * 0.64, wh = cellH * 0.60;
        // Dark window recess
        ctx.fillStyle = '#1b1b1a'; ctx.fillRect(wx - 2, wy - 2, ww + 4, wh + 4);
        // Window color varies by column (building type)
        const roll = rand(); const lit = rand() < 0.22;
        const glass = ctx.createLinearGradient(wx, wy, wx, wy + wh);
        if (lit) {
          glass.addColorStop(0, '#fff2c6'); glass.addColorStop(0.6, '#f0bd54'); glass.addColorStop(1, '#8c6424');
        } else if (roll < 0.35) {
          // Teal/blue reflective office glass
          glass.addColorStop(0, '#406d7b'); glass.addColorStop(0.5, '#2b4a56'); glass.addColorStop(1, '#17313b');
        } else if (roll < 0.6) {
          // Daytime grey glass
          glass.addColorStop(0, '#9babb3'); glass.addColorStop(0.5, '#6c7a81'); glass.addColorStop(1, '#3f484d');
        } else if (roll < 0.78) {
          // Curtained interior
          glass.addColorStop(0, '#c9b48b'); glass.addColorStop(1, '#6c5a3c');
        } else {
          // Dark office / shaded
          glass.addColorStop(0, '#2c3542'); glass.addColorStop(0.5, '#1d2530'); glass.addColorStop(1, '#131922');
        }
        ctx.fillStyle = glass; ctx.fillRect(wx, wy, ww, wh);
        // Highlight on glass top-left
        if (!lit) {
          const spec = ctx.createLinearGradient(wx, wy, wx + ww * 0.5, wy + wh * 0.4);
          spec.addColorStop(0, 'rgba(255,255,255,0.22)'); spec.addColorStop(1, 'rgba(255,255,255,0)');
          ctx.fillStyle = spec; ctx.fillRect(wx, wy, ww, wh);
        }
        // Dark frame
        ctx.strokeStyle = '#1a1612'; ctx.lineWidth = 2.2; ctx.strokeRect(wx, wy, ww, wh);
        // Mullions
        ctx.lineWidth = 1.2;
        ctx.beginPath(); ctx.moveTo(wx + ww / 2, wy); ctx.lineTo(wx + ww / 2, wy + wh); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(wx, wy + wh * 0.5); ctx.lineTo(wx + ww, wy + wh * 0.5); ctx.stroke();
        // AC unit on some window sills (Delhi-style)
        if (rand() < 0.14) {
          const acw = ww * 0.42, ach = wh * 0.18, ax = wx + (ww - acw) / 2, ay = wy + wh - ach + ach * 0.1;
          ctx.fillStyle = '#8a8676'; ctx.fillRect(ax, ay, acw, ach); ctx.strokeStyle = '#524d42'; ctx.lineWidth = 1; ctx.strokeRect(ax, ay, acw, ach);
          ctx.fillStyle = '#2d2a24'; for (let k = 0; k < 4; k++) ctx.fillRect(ax + 2 + k * (acw - 4) / 4, ay + 2, (acw - 6) / 4, ach - 4);
        }
        // Occasional balcony railing
        if (rand() < 0.09) {
          ctx.strokeStyle = 'rgba(30,25,20,0.75)'; ctx.lineWidth = 1.2;
          const by = y + cellH * 0.86;
          for (let k = 0; k < 8; k++) { ctx.beginPath(); ctx.moveTo(x + cellW * 0.12 + k * cellW * 0.1, by); ctx.lineTo(x + cellW * 0.12 + k * cellW * 0.1, by + cellH * 0.08); ctx.stroke(); }
          ctx.strokeRect(x + cellW * 0.1, by, cellW * 0.8, cellH * 0.08);
        }
      }
      // Water stains streaking downward near sills
      ctx.save(); ctx.globalAlpha = 0.11; ctx.fillStyle = '#3a2f20';
      for (let i = 0; i < 50; i++) {
        const sx = rand() * 1024, sy = rand() * 1024, sh = 20 + rand() * 80;
        ctx.fillRect(sx, sy, 1 + rand() * 2, sh);
      }
      ctx.restore();
      const texture = new THREE.CanvasTexture(canvas);
      texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
      texture.colorSpace = THREE.SRGBColorSpace; texture.anisotropy = 8;
      this.wallTextureUniform.value = texture;
    }
    if (!this.roofTextureUniform.value) {
      const canvas = document.createElement('canvas'); canvas.width = 512; canvas.height = 512;
      const ctx = canvas.getContext('2d')!;
      ctx.fillStyle = '#9a9387'; ctx.fillRect(0, 0, 512, 512);
      // Gravel grain
      let seed = 2026; const rand = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };
      for (let i = 0; i < 5000; i++) {
        const r = 1 + rand() * 2.5; ctx.fillStyle = `rgba(${80 + rand()*120|0},${75 + rand()*110|0},${65 + rand()*100|0},${0.3 + rand()*0.4})`;
        ctx.beginPath(); ctx.arc(rand() * 512, rand() * 512, r, 0, Math.PI * 2); ctx.fill();
      }
      // Water tanks
      for (let i = 0; i < 10; i++) {
        const x = 40 + rand() * 432, y = 40 + rand() * 432, r = 10 + rand() * 14;
        ctx.fillStyle = '#2a394d'; ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = 'rgba(255,255,255,0.1)'; ctx.beginPath(); ctx.arc(x - r*0.3, y - r*0.3, r*0.3, 0, Math.PI * 2); ctx.fill();
      }
      // HVAC blocks
      for (let i = 0; i < 8; i++) {
        const x = rand() * 460, y = rand() * 460, w = 20 + rand() * 40, h = 20 + rand() * 30;
        ctx.fillStyle = '#5e6166'; ctx.fillRect(x, y, w, h); ctx.strokeStyle = '#3a3d42'; ctx.strokeRect(x, y, w, h);
      }
      const texture = new THREE.CanvasTexture(canvas);
      texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
      texture.colorSpace = THREE.SRGBColorSpace; texture.anisotropy = 4;
      this.roofTextureUniform.value = texture;
    }
  }

  private buildingMaterial(known: boolean): THREE.MeshStandardMaterial {
    this.ensureBuildingTextures();
    const material = new THREE.MeshStandardMaterial({ color: known ? '#f4f0e5' : '#e6e1d6', roughness: 0.95, metalness: 0, side: THREE.DoubleSide });
    material.onBeforeCompile = shader => {
      shader.uniforms.roofImageryBlend = this.roofImageryBlend;shader.uniforms.impactBlend=this.impactBlend;shader.uniforms.collapseScale=this.collapseScale;shader.uniforms.shakeTime=this.shakeTime;shader.uniforms.shakeAmp=this.shakeAmp;shader.uniforms.collapseProgress=this.collapseProgress;
      shader.uniforms.wallTexture=this.wallTextureUniform;shader.uniforms.roofTexture=this.roofTextureUniform;
      shader.vertexShader = shader.vertexShader.replace('#include <common>', `#include <common>
varying vec3 vWorldPosition;
attribute vec4 impactColor;
attribute float impactState;
attribute float buildingRise;
uniform float collapseScale;
uniform float shakeTime;
uniform float shakeAmp;
uniform float collapseProgress;
varying vec4 vImpactColor;
attribute vec4 roofColor;
attribute vec2 facade;
varying vec4 vRoofColor;
varying vec3 vFacade;`).replace('#include <begin_vertex>', `#include <begin_vertex>
vWorldPosition = (modelMatrix * vec4(transformed, 1.0)).xyz;
vImpactColor=impactColor;
// Walls store height=0 at the base and the real height at the top; use that to shake only upper geometry.
float topFrac = buildingRise > 0.01 ? 1.0 : 0.0;
if (shakeAmp > 0.0001) {
  float wobble = sin(shakeTime * 11.0 + position.x * 420.0 + position.z * 260.0);
  float lateral = cos(shakeTime * 7.3 + position.z * 310.0);
  transformed.x += wobble * shakeAmp * topFrac * 0.0035;
  transformed.z += lateral * shakeAmp * topFrac * 0.0028;
  transformed.y += sin(shakeTime * 19.0 + position.x * 180.0) * shakeAmp * topFrac * 0.0012;
}
if (impactState > 1.5 && impactState < 2.5) {
  float fall = (buildingRise - min(buildingRise * 0.15, 3.0)) * collapseScale * collapseProgress;
  transformed.y -= fall;
  // Mild tilt on top vertices so the collapse reads as structural rather than a slide.
  float tilt = collapseProgress * 0.004;
  transformed.x += topFrac * tilt * sin(position.x * 540.0 + position.z * 230.0);
  transformed.z += topFrac * tilt * cos(position.x * 310.0 + position.z * 410.0);
}
vRoofColor = roofColor;
vFacade = vec3(facade / ${FACADE_UNITS_PER_M}.0, facade.x < ${NO_FACADE - 1}.0 ? 1.0 : 0.0);`);
      shader.fragmentShader = shader.fragmentShader.replace('#include <common>', `#include <common>
varying vec3 vWorldPosition;
uniform float impactBlend;
varying vec4 vImpactColor;
uniform float roofImageryBlend;
uniform sampler2D wallTexture;
uniform sampler2D roofTexture;
varying vec4 vRoofColor;
varying vec3 vFacade;`).replace('#include <color_fragment>', `#include <color_fragment>
if (vFacade.z > 0.5) {
  // --- BUILDING WALLS / FACADES ---
  float wallDist = vFacade.x;
  float wallHeight = vFacade.y;
  
  // Architectural style variation seed based on building location
  float bSeed = fract(sin(dot(floor(vWorldPosition.xz * 10.0), vec2(12.9898, 78.233))) * 43758.5453);
  
  vec3 wallTone = diffuseColor.rgb;
  vec3 glassTone = vec3(0.08, 0.12, 0.17);
  vec3 frameTone = vec3(0.88, 0.86, 0.82);
  
  if (bSeed < 0.28) {
    // Modern Commercial Tower (blue glass / dark steel)
    wallTone = vec3(0.55, 0.60, 0.66);
    glassTone = vec3(0.07, 0.13, 0.21);
    frameTone = vec3(0.38, 0.42, 0.46);
  } else if (bSeed < 0.56) {
    // Delhi Terracotta / Red Brick residential
    wallTone = vec3(0.70, 0.46, 0.36);
    glassTone = vec3(0.07, 0.09, 0.13);
    frameTone = vec3(0.92, 0.90, 0.86);
  } else if (bSeed < 0.80) {
    // Warm Sandstone / Dholpur stone commercial block
    wallTone = vec3(0.80, 0.74, 0.64);
    glassTone = vec3(0.08, 0.11, 0.15);
    frameTone = vec3(0.68, 0.63, 0.54);
  } else {
    // Cast Architectural Concrete
    wallTone = vec3(0.66, 0.65, 0.62);
    glassTone = vec3(0.09, 0.13, 0.18);
    frameTone = vec3(0.52, 0.51, 0.49);
  }

  // Multi-story floor and window grid: floor height 3.1m, window column 2.2m
  float floorH = 3.1;
  float colW = 2.2;
  vec2 gridUv = vec2(wallDist / colW, wallHeight / floorH);
  vec2 cell = fract(gridUv);
  
  // Horizontal concrete floor slab band separating floors
  float slabFrac = 0.15;
  float isSlab = step(cell.y, slabFrac);
  
  // Window openings
  vec2 winMin = vec2(0.18, 0.26);
  vec2 winMax = vec2(0.82, 0.88);
  vec2 edge = max(fwidth(gridUv), vec2(0.004));
  vec2 win2d = smoothstep(winMin, winMin + edge, cell) * (1.0 - smoothstep(winMax - edge, winMax, cell));
  float winMask = win2d.x * win2d.y * (1.0 - isSlab);
  
  // Window frame & sill
  vec2 frameMin = vec2(0.11, 0.19);
  vec2 frameMax = vec2(0.89, 0.94);
  vec2 frame2d = smoothstep(frameMin, frameMin + edge, cell) * (1.0 - smoothstep(frameMax - edge, frameMax, cell));
  float isFrame = frame2d.x * frame2d.y * (1.0 - isSlab) * (1.0 - winMask);
  
  // Ground floor entrance / retail storefront level
  if (wallHeight < 3.4) {
    if (wallHeight < 0.8) {
      wallTone *= 0.68; // Dark stone foundation base
    } else {
      float storeGlass = smoothstep(0.10, 0.14, cell.x) * (1.0 - smoothstep(0.86, 0.90, cell.x)) * smoothstep(0.16, 0.20, cell.y);
      winMask = max(winMask, storeGlass * 0.92);
    }
  }
  
  // Sky reflection highlight on glass panes
  vec3 glassReflect = glassTone + vec3(0.03, 0.07, 0.13) * (1.0 - cell.y);
  
  // Smooth distance anti-aliasing:
  // At close/medium distance: crisp windows, frames, sills and mullions.
  // At far distance: smoothly blends into readable architectural floor banding and facade materials.
  float viewDist = length(vViewPosition);
  float detailBlend = 1.0 - smoothstep(1.2, 9.0, viewDist);
  
  vec3 nearFacade = wallTone;
  nearFacade = mix(nearFacade, frameTone, isFrame * 0.9);
  nearFacade = mix(nearFacade, glassReflect, winMask);
  nearFacade = mix(nearFacade, wallTone * 0.82, isSlab * 0.65); // Floor slab shadow
  
  // Subtle brick / concrete masonry texture grain
  float masonryNoise = sin(wallDist * 42.0) * sin(wallHeight * 48.0) * 0.035;
  nearFacade += masonryNoise;
  
  vec3 farFacade = mix(wallTone, glassTone, 0.40 * (1.0 - isSlab * 0.75));
  diffuseColor.rgb = mix(farFacade, nearFacade, detailBlend);
  // Baked facade texture tiled by metric wall UVs. Scale tuned to ~22m horizontal × 30m vertical per tile.
  vec2 wallUv = vec2(wallDist / 22.0, wallHeight / 30.0);
  vec3 wallTex = texture2D(wallTexture, wallUv).rgb;
  // Replace rather than modulate — read as photographic facade near-camera, retain tint at distance.
  vec3 texturedFacade = wallTex * (0.65 + 0.35 * (wallTone.r + wallTone.g + wallTone.b));
  diffuseColor.rgb = mix(diffuseColor.rgb * 0.4 + texturedFacade * 0.9, texturedFacade, detailBlend * 0.85);
} else {
  // --- BUILDING ROOFS ---
  vec2 roofM = vWorldPosition.xz / 0.001;
  
  // Weathered concrete / gravel rooftop surface
  vec3 baseRoof = vec3(0.73, 0.71, 0.67);
  float gravelNoise = sin(roofM.x * 1.6) * cos(roofM.y * 1.6) * 0.035;
  baseRoof += gravelNoise;
  
  // Iconic Delhi rooftop water storage tanks (Sintex cylindrical/cube tanks)
  vec2 tankCell = fract(roofM * 0.045);
  float tankDist = length(tankCell - vec2(0.5));
  float isTank = smoothstep(0.18, 0.14, tankDist);
  vec3 tankColor = vec3(0.16, 0.22, 0.29);
  
  // Rooftop HVAC units / elevator penthouse access boxes
  vec2 mechCell = fract(roofM * 0.075 + 0.3);
  float isMech = step(0.68, mechCell.x) * step(0.68, mechCell.y);
  vec3 mechColor = vec3(0.42, 0.44, 0.47);
  
  vec3 texturedRoof = baseRoof;
  texturedRoof = mix(texturedRoof, mechColor, isMech * 0.85);
  texturedRoof = mix(texturedRoof, tankColor, isTank * 0.90);
  
  // Sample the baked roof texture (gravel, water tanks, HVAC) tiled per building footprint.
  vec2 roofUv = vWorldPosition.xz * 24.0;
  vec3 roofTex = texture2D(roofTexture, roofUv).rgb;
  texturedRoof = mix(texturedRoof, texturedRoof * (roofTex * 1.4 + 0.05), 0.7);
  if (vRoofColor.a > 0.0 && roofImageryBlend > 0.0) {
    // Enhance satellite aerial imagery with 3D rooftop structural relief
    vec3 aerialEnhanced = vRoofColor.rgb * (0.86 + 0.14 * (1.0 - isMech * 0.22 - isTank * 0.32));
    diffuseColor.rgb = mix(texturedRoof, aerialEnhanced, vRoofColor.a * roofImageryBlend);
  } else {
    diffuseColor.rgb = texturedRoof;
  }
}
diffuseColor.rgb=mix(diffuseColor.rgb,vImpactColor.rgb,vImpactColor.a*impactBlend);`);
    };
    material.customProgramCacheKey = () => 'otec-roof-facade-impact-v5-tex';
    return material;
  }

  private fireMode = false;
  private quakeMode = false;
  private fireTexture?: THREE.CanvasTexture;
  private makeFireTexture(): THREE.CanvasTexture {
    const canvas = document.createElement('canvas'); canvas.width = 128; canvas.height = 256;
    const ctx = canvas.getContext('2d')!;
    const grad = ctx.createRadialGradient(64, 220, 10, 64, 180, 110);
    grad.addColorStop(0, 'rgba(255, 245, 180, 1)');
    grad.addColorStop(0.35, 'rgba(255, 160, 60, 0.95)');
    grad.addColorStop(0.7, 'rgba(214, 70, 30, 0.55)');
    grad.addColorStop(1, 'rgba(80, 10, 0, 0)');
    ctx.fillStyle = grad; ctx.beginPath();
    const points = 24;
    for (let i = 0; i <= points; i++) {
      const t = i / points, angle = Math.PI + Math.PI * t;
      const radius = 55 + Math.sin(t * Math.PI * 3) * 10;
      const x = 64 + Math.sin(angle) * radius, y = 220 - Math.cos(angle) * (radius + 90);
      if (i === 0) ctx.moveTo(x, y); else ctx.quadraticCurveTo(64 + Math.sin(angle + 0.1) * radius * 1.3, y - 20, x, y);
    }
    ctx.closePath(); ctx.fill();
    ctx.globalCompositeOperation = 'lighter';
    const inner = ctx.createRadialGradient(64, 210, 5, 64, 190, 50);
    inner.addColorStop(0, 'rgba(255, 240, 200, 1)'); inner.addColorStop(1, 'rgba(255, 100, 20, 0)');
    ctx.fillStyle = inner; ctx.fillRect(0, 0, 128, 256);
    const texture = new THREE.CanvasTexture(canvas); texture.colorSpace = THREE.SRGBColorSpace; return texture;
  }

  setFireMode(enabled: boolean): void {
    this.fireMode = enabled;
    if (!enabled) { this.clearFires(); }
    this.dirty = true;
  }

  setQuakeMode(enabled: boolean): void {
    if (enabled && !this.quakeMode) { this.quakeStart = performance.now() / 1000; this.collapseStart = performance.now() / 1000; this.collapseProgress.value = 0; }
    if (!enabled) { this.shakeAmp.value = 0; this.collapseProgress.value = 1; }
    this.quakeMode = enabled;
    this.dirty = true;
  }

  triggerQuakeShake(duration_s = 2.5): void {
    this.quakeStart = performance.now() / 1000 + 0; this.collapseStart = performance.now() / 1000;
    this.collapseProgress.value = 0; this.shakeAmp.value = 1;
    this.quakeMode = true;
    void duration_s;
    this.dirty = true;
  }

  private clearFires(): void {
    for (const sprite of this.fireSprites.values()) {
      sprite.material.dispose();
      this.fires.remove(sprite);
    }
    this.fireSprites.clear();
  }

  private updateFireSprites(states?: Uint8Array): void {
    if (!this.fireMode || !states) { this.clearFires(); return; }
    if (!this.fireTexture) this.fireTexture = this.makeFireTexture();
    const activeIndices = new Set<number>();
    for (let i = 0; i < this.sourceBuildings.length; i++) {
      const band = states[i * 2];
      if (band !== 3) continue;
      activeIndices.add(i);
      if (this.fireSprites.has(i)) continue;
      const roof = this.roofRecords[i]; if (!roof) continue;
      const cx = (roof.bounds[0] + roof.bounds[2]) / 2, cz = (roof.bounds[1] + roof.bounds[3]) / 2;
      const base = this.point(cx, cz, roof.elevation);
      const topY = base.y + roof.height * (this.exaggerateBuildings ? this.buildingHeightScale : 1) * SCALE;
      const material = new THREE.SpriteMaterial({ map: this.fireTexture, color: 0xffffff, transparent: true, opacity: 0.95, depthWrite: false, depthTest: true, blending: THREE.AdditiveBlending, toneMapped: false });
      const sprite = new THREE.Sprite(material);
      const scale = Math.max(0.015, Math.min(0.08, roof.height * SCALE * 2.5));
      sprite.scale.set(scale * 0.6, scale, scale);
      sprite.position.set(base.x, topY + scale * 0.4, base.z);
      sprite.userData.baseScale = scale; sprite.userData.baseY = topY + scale * 0.4;
      sprite.userData.phase = Math.random() * Math.PI * 2;
      sprite.renderOrder = 9;
      this.fires.add(sprite); this.fireSprites.set(i, sprite);
    }
    for (const [index, sprite] of this.fireSprites) {
      if (!activeIndices.has(index)) { sprite.material.dispose(); this.fires.remove(sprite); this.fireSprites.delete(index); }
    }
    this.dirty = true;
  }

  private tickFires(now: number): boolean {
    if (!this.fireSprites.size) return false;
    const t = now / 1000;
    for (const sprite of this.fireSprites.values()) {
      const phase = sprite.userData.phase as number, base = sprite.userData.baseScale as number, baseY = sprite.userData.baseY as number;
      const flicker = 0.85 + Math.sin(t * 9 + phase) * 0.1 + Math.sin(t * 17 + phase * 2.3) * 0.07;
      sprite.scale.set(base * 0.6 * flicker, base * (1 + Math.sin(t * 7 + phase) * 0.08), base * flicker);
      sprite.position.y = baseY + Math.sin(t * 5 + phase) * base * 0.03;
      (sprite.material as THREE.SpriteMaterial).opacity = 0.85 + Math.sin(t * 11 + phase) * 0.1;
    }
    return true;
  }

  setImpactVisible(visible:boolean):void {
    this.buildingLod?.setImpact(visible);this.terrainLod?.setImpactVisible(visible&&this.hasTerrainSeverity);
    this.impactBlend.value=visible?0.88:0;if(this.impactSurface)this.impactSurface.visible=visible&&this.hasTerrainSeverity;
    for(const child of this.water.children){const material=(child as THREE.Mesh).material as THREE.MeshPhysicalMaterial;material.opacity=visible&&this.hasTerrainSeverity?.32:.72;}
    this.dirty=true;
  }

  setBuildingStates(states?:Uint8Array,palette:string[]=['#66ad85','#e1cd65','#e19a4c','#d85b53','#74457f']):void {
    if(states&&states.length!==this.sourceBuildings.length*2)throw new Error('Building state count does not match the exported footprint order.');
    this.buildingStateData=states;this.buildingPalette=palette;this.buildingLod?.setStates(states,palette,this.impactBlend.value>0);
    const colors=palette.map(value=>new THREE.Color(value));const changed=new Set<THREE.BufferAttribute>();
    for(let i=0;i<this.buildingSpans.length;i++){
      const span=this.buildingSpans[i];if(!span)continue;const severity=states?.[i*2]??255,damage=states?.[i*2+1]??0;
      if(span.severity===severity&&span.damage===damage)continue;
      const color=colors[severity];for(let v=span.start;v<span.start+span.count;v++){
        span.color.setXYZW(v,color?color.r:0,color?color.g:0,color?color.b:0,color?1:0);span.state.setX(v,damage===255?0:damage);
      }
      span.severity=severity;span.damage=damage;changed.add(span.color);changed.add(span.state);
    }
    for(const attribute of changed){attribute.clearUpdateRanges();attribute.needsUpdate=true;}this.updateFireSprites(states);this.dirty=true;
  }

  setSeverity(values?:Uint8Array,width=0,height=0,palette:string[]=['#66ad85','#e1cd65','#e19a4c','#d85b53','#74457f']):void {
    this.hasTerrainSeverity=!!values;
    if(!values){if(this.impactSurface)this.impactSurface.visible=false;this.terrainLod?.setImpactVisible(false);return;}
    if(values.length!==width*height)throw new Error('Severity grid dimensions do not match the manifest.');
    const data=new Uint8Array(width*height*4),colors=palette.map(value=>new THREE.Color(value));
    for(let i=0;i<values.length;i++){const color=colors[values[i]];if(!color)continue;data.set([Math.round(color.r*255),Math.round(color.g*255),Math.round(color.b*255),values[i]===0?0:155],i*4);}
    if(!this.impactSurface)this.impactSurface=new THREE.Mesh(new THREE.BufferGeometry(),new THREE.MeshBasicMaterial({transparent:true,depthWrite:false,side:THREE.DoubleSide,polygonOffset:true,polygonOffsetFactor:-3}));
    this.impactTexture?.dispose();this.impactTexture=new THREE.DataTexture(data,width,height,THREE.RGBAFormat);this.impactTexture.needsUpdate=true;
    (this.impactSurface.material as THREE.MeshBasicMaterial).map=this.impactTexture;(this.impactSurface.material as THREE.MeshBasicMaterial).needsUpdate=true;
    this.impactSurface.visible=this.impactBlend.value>0;this.terrainLod?.setImpact(this.impactSurface.material as THREE.MeshBasicMaterial,this.impactSurface.visible);this.dirty=true;
  }

  nearestEmbankment(point:[number,number],maximum=150):[number,number]|null {
    let best=maximum,result:[number,number]|null=null;
    for(const line of this.bankLines)for(let i=1;i<line.length;i++){
      const a=line[i-1],b=line[i],dx=b[0]-a[0],dz=b[1]-a[1],t=THREE.MathUtils.clamp(((point[0]-a[0])*dx+(point[1]-a[1])*dz)/(dx*dx+dz*dz||1),0,1);
      const p:[number,number]=[a[0]+t*dx,a[1]+t*dz],distance=Math.hypot(p[0]-point[0],p[1]-point[1]);if(distance<best){best=distance;result=p;}
    }return result;
  }

  pickBuilding(event:PointerEvent):{index:number;point:[number,number]}|null {
    const hit=this.pick(event);if(!hit)return null;
    const inside=(p:number[],ring:number[][])=>{let yes=false;for(let i=0,j=ring.length-1;i<ring.length;j=i++){const a=ring[i],b=ring[j];if((a[1]>p[1])!==(b[1]>p[1])&&p[0]<(b[0]-a[0])*(p[1]-a[1])/(b[1]-a[1])+a[0])yes=!yes;}return yes;};
    const candidates=new Set<number>();const c=Math.floor(hit[0]/ROOF_INDEX_CELL_M),r=Math.floor(hit[1]/ROOF_INDEX_CELL_M);
    for(let dz=-1;dz<=1;dz++)for(let dx=-1;dx<=1;dx++)for(const index of this.roofIndex.get((r+dz)*this.roofIndexColumns+c+dx)??[])candidates.add(index);
    let nearest=Infinity,result:{index:number;point:[number,number]}|null=null;
    for(const index of candidates){const roof=this.roofRecords[index];const y=(roof.elevation-this.terrain.min_height_m)*this.exaggeration*SCALE+roof.height*(this.exaggerateBuildings?this.buildingHeightScale:1)*SCALE;
      const t=(y-this.raycaster.ray.origin.y)/this.raycaster.ray.direction.y;if(t<0||t>=nearest)continue;
      const world=this.raycaster.ray.at(t,new THREE.Vector3()),p:[number,number]=[world.x/SCALE+this.terrain.extent_m[0]/2,world.z/SCALE+this.terrain.extent_m[1]/2];
      if(inside(p,roof.rings[0])&&!roof.rings.slice(1).some(ring=>inside(p,ring))){nearest=t;result={index:roof.buildingIndex,point:p};}
    }return result;
  }

  setRoofImageryBlend(value: number): void {
    const blend = THREE.MathUtils.clamp(value, 0, 1);
    if (blend !== this.roofImageryBlend.value) { this.roofImageryBlend.value = blend; this.dirty = true; }
  }

  colorRoofs(bounds: [number, number, number, number], sample: (rings: number[][][]) => [number, number, number] | undefined, zoom: number): void {
    const candidates = new Set<number>();
    const xmin=Math.max(0,bounds[0]),zmin=Math.max(0,bounds[1]),xmax=Math.min(this.terrain.extent_m[0],bounds[2]),zmax=Math.min(this.terrain.extent_m[1],bounds[3]);
    if(xmin>xmax||zmin>zmax)return;
    for(let r=Math.floor(zmin/ROOF_INDEX_CELL_M);r<=Math.floor(zmax/ROOF_INDEX_CELL_M);r++)for(let c=Math.floor(xmin/ROOF_INDEX_CELL_M);c<=Math.floor(xmax/ROOF_INDEX_CELL_M);c++){
      for(const id of this.roofIndex.get(r*this.roofIndexColumns+c)??[])candidates.add(id);
    }
    const updates = new Map<THREE.BufferAttribute, [number, number][]>();
    for(const id of candidates){
      const roof=this.roofRecords[id];if(!roof?.colors)continue;const b=roof.bounds;
      if(roof.zoom>=zoom||b[2]<xmin||b[0]>xmax||b[3]<zmin||b[1]>zmax)continue;
      const rgb=sample(roof.rings);if(!rgb||!rgb.every(Number.isFinite))continue;
      const color=rgb.map(value=>this.roofLinearBytes[THREE.MathUtils.clamp(Math.round(value),0,255)]);
      const values=roof.colors.array as Uint8Array;
      for(let i=roof.start;i<roof.start+roof.count;i++){const offset=i*4;values[offset]=color[0];values[offset+1]=color[1];values[offset+2]=color[2];values[offset+3]=255;}
      roof.zoom=zoom;
      const ranges=updates.get(roof.colors)??[];ranges.push([roof.start*4,roof.count*4]);updates.set(roof.colors,ranges);
    }
    for(const [attribute,ranges] of updates){
      // Coalesce nearby roofs; preserve other pending tile updates until upload.
      ranges.sort((a,b)=>a[0]-b[0]);let start=ranges[0][0],end=start+ranges[0][1];
      for(let i=1;i<ranges.length;i++){const [next,count]=ranges[i];if(next<=end+256)end=Math.max(end,next+count);else{attribute.addUpdateRange(start,end-start);start=next;end=next+count;}}
      attribute.addUpdateRange(start,end-start);attribute.needsUpdate=true;
    }
    if(updates.size)this.dirty=true;
  }

  estimateMemoryBytes(): { geometryBytes: number; textureBytes: number; renderTargetBytes: number; totalBytes: number; geometries: number; textures: number } {
    const geometries=new Set<THREE.BufferGeometry>(),textures=new Set<THREE.Texture>(),targets=new Set<THREE.WebGLRenderTarget>();
    let geometryBytes=0,textureBytes=0,renderTargetBytes=0;
    this.scene.traverse(child=>{
      const mesh=child as THREE.Mesh;
      if(mesh.geometry&&!geometries.has(mesh.geometry)){
        geometries.add(mesh.geometry);const arrays=new Set<ArrayBufferView>();
        if(mesh.geometry.index)arrays.add(mesh.geometry.index.array);
        for(const attribute of Object.values(mesh.geometry.attributes))arrays.add(attribute instanceof THREE.InterleavedBufferAttribute?attribute.data.array:attribute.array);
        if(mesh instanceof THREE.InstancedMesh){arrays.add(mesh.instanceMatrix.array);if(mesh.instanceColor)arrays.add(mesh.instanceColor.array);}
        for(const array of arrays)geometryBytes+=array.byteLength;
      }
      for(const material of Array.isArray(mesh.material)?mesh.material:[mesh.material])if(material){
        for(const value of Object.values(material))if(value instanceof THREE.Texture)textures.add(value);
        if(material instanceof THREE.ShaderMaterial)for(const uniform of Object.values(material.uniforms))if(uniform.value instanceof THREE.Texture)textures.add(uniform.value);
      }
    });
    const textureSize=(texture:THREE.Texture)=>{
      const image=texture.image as {width?:number;height?:number;data?:ArrayBufferView}|undefined;
      const pixelBytes=texture.type===THREE.FloatType?16:texture.type===THREE.HalfFloatType?8:4;
      return Math.ceil((image?.data?.byteLength??((image?.width??0)*(image?.height??0)*pixelBytes))*(texture.generateMipmaps?4/3:1));
    };
    targets.add(this.composer.renderTarget1);targets.add(this.composer.renderTarget2);
    for(const value of Object.values(this.ssao))if(value instanceof THREE.WebGLRenderTarget)targets.add(value);
    for(const target of targets){for(const texture of target.textures){renderTargetBytes+=textureSize(texture);textures.delete(texture);}if(target.depthBuffer)renderTargetBytes+=target.width*target.height*4;}
    for(const texture of textures)textureBytes+=textureSize(texture);
    return {geometryBytes,textureBytes,renderTargetBytes,totalBytes:geometryBytes+textureBytes+renderTargetBytes,geometries:geometries.size,textures:textures.size};
  }

  setExaggeration(value: number, buildings: boolean): void {
    this.exaggeration = value; this.exaggerateBuildings = buildings;const buildingMultiplier=buildings?this.buildingHeightScale:1;this.collapseScale.value=SCALE*buildingMultiplier;
    this.buildingLod?.invalidateHeight();this.terrainLod?.invalidateHeight();
    for (const entry of this.elevations) {
      const p = entry.geometry.getAttribute('position');
      for (let i = 0; i < p.count; i++) if (Number.isFinite(entry.base[i])) p.setY(i, (entry.base[i] - this.terrain.min_height_m) * value * SCALE + (entry.rise?.[i] ?? 0) * buildingMultiplier * SCALE + (entry.lift ?? (entry.rise ? 0.0005 : 0)));
      p.needsUpdate = true; entry.geometry.computeVertexNormals(); entry.geometry.computeBoundingSphere();
    }
    if(this.areaBounds)this.updateAreaWalls();
    this.updateOverlays(); if (this.currentDepth) this.setWater(this.currentDepth.data, this.currentDepth.run); this.dirty = true;
  }

  setBuildingExaggeration(factor: number): void {
    if (!Number.isFinite(factor) || factor <= 0) return;
    this.buildingHeightScale = factor;
    this.exaggerateBuildings = true;
    this.setExaggeration(this.exaggeration, true);
  }

  clear(group: THREE.Group): void {
    group.traverse(child => { const item = child as THREE.Mesh; item.geometry?.dispose(); const materials = Array.isArray(item.material) ? item.material : [item.material]; for (const material of materials) { if (material) { (material as THREE.MeshBasicMaterial).map?.dispose(); material.dispose(); } } });
    group.clear(); this.dirty = true;
  }

  private lines(group: THREE.Group, rings: number[][][], color: string, lift = 0.003): void {
    const points: THREE.Vector3[] = [];
    for (const ring of rings) for (let i = 1; i < ring.length; i++) { const a = this.point(ring[i-1][0], ring[i-1][1]); const b = this.point(ring[i][0], ring[i][1]); a.y += lift; b.y += lift; points.push(a, b); }
    if (!points.length) return;
    group.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(points), new THREE.LineBasicMaterial({ color, depthTest: true })));
  }

  private rings(geometry: Geo): number[][][] { return geometry.type === 'Polygon' ? geometry.coordinates : geometry.type === 'MultiPolygon' ? geometry.coordinates.flat() : []; }
  setOutline(mask?: Mask): void { this.mask = mask; this.updateOverlays(); }
  setReported(data?: Features): void { this.places = data; this.updateOverlays(); }
  setFreshness(data?: Features): void { this.freshFeatures = data; this.updateOverlays(); }

  private updateOverlays(): void {
    if (!this.terrain) return;
    this.clear(this.embankments); this.lines(this.embankments, this.bankLines, '#ddb97a');
    this.clear(this.outline); if (this.mask) this.lines(this.outline, this.mask.geometries.flatMap(g => this.rings(g)), '#ff645d', 0.007);
    this.clear(this.freshness); if (this.freshFeatures) for (const f of this.freshFeatures.features) this.lines(this.freshness, this.rings(f.geometry), f.properties.fresh ? '#a9c9b0' : '#737983', 0.006);
    this.clear(this.reported);
    if (this.places) for (const feature of this.places.features) {
      const ring = this.rings(feature.geometry)[0];
      const xy = feature.geometry.type === 'Point' ? feature.geometry.coordinates : ring?.[0];
      if (!xy) continue;
      const p = this.point(xy[0], xy[1]);
      const sphere = new THREE.Mesh(new THREE.SphereGeometry(0.025, 8, 6), new THREE.MeshBasicMaterial({ color: '#e7b9a2' })); sphere.position.copy(p); sphere.position.y += 0.06; this.reported.add(sphere);
      const canvas = document.createElement('canvas'); canvas.width = 768; canvas.height = 100; const ctx = canvas.getContext('2d')!;
      ctx.fillStyle = '#1c1d20e8'; ctx.fillRect(0, 0, canvas.width, canvas.height); ctx.fillStyle = '#f5ece2'; ctx.font = '24px monospace';
      ctx.fillText(String(feature.properties.name ?? 'Reported flooded place').slice(0, 46), 12, 34);
      ctx.font = '16px monospace'; ctx.fillStyle = '#b2ada5'; ctx.fillText('Reported: ' + String(feature.properties.source_url ?? 'source missing').slice(0, 70), 12, 73);
      const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(canvas), depthTest: false })); sprite.position.copy(p); sprite.position.y += 0.23; sprite.scale.set(1.6, 0.21, 1); this.reported.add(sprite);
    }
  }

  setWater(depth?: Float32Array, run?: Run): void {
    this.clear(this.water); this.currentDepth = depth && run ? { data: depth, run } : undefined;
    if (!depth || !run) return;
    if (depth.length !== run.width * run.height) throw new Error('Depth grid dimensions do not match the run manifest.');
    const positions: number[] = [];
    for (let row = 0; row < run.height; row++) for (let col = 0; col < run.width; col++) {
      const d = depth[row * run.width + col]; if (!Number.isFinite(d) || d <= 0.001) continue;
      const x = col * run.cell_size_m, z = row * run.cell_size_m;
      const level = this.ground(x, z) + d;
      const a = this.point(x, z, level), b = this.point(Math.min(x + run.cell_size_m, this.terrain.extent_m[0]), z, level), c = this.point(x, Math.min(z + run.cell_size_m, this.terrain.extent_m[1]), level), e = this.point(Math.min(x + run.cell_size_m, this.terrain.extent_m[0]), Math.min(z + run.cell_size_m, this.terrain.extent_m[1]), level);
      for (const p of [a, c, b, b, c, e]) positions.push(p.x, p.y + 0.0008, p.z);
    }
    const geometry = new THREE.BufferGeometry(); geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3)); geometry.computeVertexNormals();
    const mesh = new THREE.Mesh(geometry, new THREE.MeshPhysicalMaterial({ color: '#679dac', transparent: true, opacity: this.impactBlend.value>0&&this.hasTerrainSeverity?.32:.72, roughness: 0.24, metalness: 0.15, clearcoat: 0.5, clearcoatRoughness: 0.3, depthWrite: false, side: THREE.DoubleSide })); mesh.renderOrder = 2; this.water.add(mesh); this.dirty = true;
  }

  waterPoint(x:number,z:number):THREE.Vector3 {
    const current=this.currentDepth;if(!current)return this.point(x,z);
    const col=Math.floor(x/current.run.cell_size_m),row=Math.floor(z/current.run.cell_size_m);
    const depth=col>=0&&row>=0&&col<current.run.width&&row<current.run.height?current.data[row*current.run.width+col]:0;
    return this.point(x,z,this.ground(x,z)+(Number.isFinite(depth)?Math.max(0,depth):0));
  }

  setSurfaceContext(landcover: Uint8Array, roads: Road[]): void {
    if (landcover.length !== this.terrain.width * this.terrain.height) throw new Error('Landcover grid dimensions do not match terrain.');
    this.landcover = landcover; this.updateSurface();
    const regular: number[] = [], bridges: number[] = [];
    const regularBases: number[] = [], bridgeBases: number[] = [];
    for (const road of roads) {
      const vertices = road.bridge ? bridges : regular, bases = road.bridge ? bridgeBases : regularBases;
      for (let i=1;i<road.points.length;i++) {
        const [ax,az]=road.points[i-1], [bx,bz]=road.points[i]; const dx=bx-ax,dz=bz-az,length=Math.hypot(dx,dz);
        if (!length) continue;
        const nx=-dz/length*road.width_m/2,nz=dx/length*road.width_m/2;
        const segments=Math.max(1,Math.ceil(length/15));
        for(let j=0;j<segments;j++) {
          const a=[ax+dx*j/segments,az+dz*j/segments],b=[ax+dx*(j+1)/segments,az+dz*(j+1)/segments];
          const corners=[[a[0]+nx,a[1]+nz],[a[0]-nx,a[1]-nz],[b[0]+nx,b[1]+nz],[b[0]-nx,b[1]-nz]];
          for(const k of [0,1,2,2,1,3]) { const [x,z]=corners[k]; const base=this.renderedGround(x,z); const p=this.point(x,z,base); vertices.push(p.x,p.y+(road.bridge?.0016:.0008),p.z); bases.push(base); }
        }
      }
    }
    for(const [vertices,bases,color,lift] of [[regular,regularBases,'#666b68',.0008],[bridges,bridgeBases,'#737977',.0016]] as [number[],number[],string,number][]) {
      if(!vertices.length) continue;
      const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(vertices,3));geometry.computeVertexNormals();
      this.elevations.push({geometry,base:new Float32Array(bases),lift});
      const mesh=new THREE.Mesh(geometry,new THREE.MeshStandardMaterial({color,roughness:1,metalness:0,side:THREE.DoubleSide,polygonOffset:true,polygonOffsetFactor:-2,polygonOffsetUnits:-2}));mesh.receiveShadow=true;mesh.renderOrder=1;this.roads.add(mesh);
    }
    this.dirty=true;
  }

  renderedGround(x: number, z: number): number {
    const step=this.terrain.render_stride*this.terrain.cell_size_m;
    const x0=Math.floor(x/step)*step,z0=Math.floor(z/step)*step;
    const x1=Math.min(x0+step,this.terrain.extent_m[0]),z1=Math.min(z0+step,this.terrain.extent_m[1]);
    const u=(x-x0)/(x1-x0||1),v=(z-z0)/(z1-z0||1);
    const a=this.ground(x0,z0),b=this.ground(x1,z0),c=this.ground(x0,z1),d=this.ground(x1,z1);
    return u+v<=1?a+(b-a)*u+(c-a)*v:d+(c-d)*(1-u)+(b-d)*(1-v);
  }

  /** Display-only city texture (real imagery or labelled procedural fallback). */
  setCityTexture(canvas: HTMLCanvasElement): void {
    this.cityTexture?.dispose();
    const texture = new THREE.CanvasTexture(canvas); texture.colorSpace = THREE.SRGBColorSpace; texture.flipY = false;
    texture.anisotropy = Math.min(8, this.renderer.capabilities.getMaxAnisotropy()); texture.needsUpdate = true;
    this.cityTexture = texture;
    const context = canvas.getContext('2d', { willReadFrequently: true });
    if (context) this.roofPixels = { data: context.getImageData(0, 0, canvas.width, canvas.height).data, width: canvas.width, height: canvas.height };
    for (const roof of this.roofRecords) if (roof) roof.zoom = -1;
    this.paintRoofs(this.roofRecords.map((_, index) => index));
    this.setRoofImageryBlend(0.85);
    this.updateSurface();
  }

  private paintRoofs(ids: number[]): void {
    const pixels = this.roofPixels; if (!pixels || !this.terrain) return;
    const [ex, ez] = this.terrain.extent_m, touched = new Set<THREE.BufferAttribute>();
    for (const id of ids) {
      const roof = this.roofRecords[id]; if (!roof?.colors || roof.zoom >= 99) continue;
      const [x0, z0, x1, z1] = roof.bounds; let r = 0, g = 0, b = 0, n = 0;
      for (let i = 1; i <= 3; i++) for (let j = 1; j <= 3; j++) {
        const u = THREE.MathUtils.clamp(Math.floor((x0 + (x1 - x0) * i / 4) / ex * pixels.width), 0, pixels.width - 1);
        const v = THREE.MathUtils.clamp(Math.floor((z0 + (z1 - z0) * j / 4) / ez * pixels.height), 0, pixels.height - 1);
        const k = (v * pixels.width + u) * 4; r += pixels.data[k]; g += pixels.data[k + 1]; b += pixels.data[k + 2]; n++;
      }
      const color = [r / n, g / n, b / n].map(value => this.roofLinearBytes[THREE.MathUtils.clamp(Math.round(value), 0, 255)]);
      const values = roof.colors.array as Uint8Array;
      for (let i = roof.start; i < roof.start + roof.count; i++) { const o = i * 4; values[o] = color[0]; values[o + 1] = color[1]; values[o + 2] = color[2]; values[o + 3] = 255; }
      roof.zoom = 99; touched.add(roof.colors);
    }
    for (const attribute of touched) { attribute.clearUpdateRanges(); attribute.needsUpdate = true; }
    if (touched.size) this.dirty = true;
  }

  updateSurface(): void {
    if (this.cityTexture) {
      const material = this.terrainMesh.material as THREE.MeshStandardMaterial; material.color.set('#ffffff'); material.map = this.cityTexture; material.needsUpdate = true; this.dirty = true; return;
    }
    if(!this.landcover) return;
    const colors=new Uint8Array(this.landcover.length*4);
    for(let i=0;i<this.landcover.length;i++) {
      const kind=this.landcover[i];
      const color=this.riverVisible&&(kind&1)?[83,133,153]:this.greenVisible&&(kind&2)?[145,161,125]:[184,181,169];
      colors.set([...color,255],i*4);
    }
    this.surfaceTexture?.dispose();
    this.surfaceTexture=new THREE.DataTexture(colors,this.terrain.width,this.terrain.height,THREE.RGBAFormat);this.surfaceTexture.colorSpace=THREE.SRGBColorSpace;this.surfaceTexture.magFilter=THREE.LinearFilter;this.surfaceTexture.minFilter=THREE.LinearFilter;this.surfaceTexture.needsUpdate=true;
    const material=this.terrainMesh.material as THREE.MeshStandardMaterial;material.color.set('#ffffff');material.map=this.surfaceTexture;material.needsUpdate=true;this.dirty=true;
  }

  getLodStats():Record<string,unknown>{return {buildings:this.buildingLod?.stats(),terrain:this.terrainLod?.stats(),areaBounds:this.areaBounds??null,displayOnly:true};}

  setAreaBounds(bounds:Bounds|undefined):void {
    if(bounds){if(bounds.length!==4||!bounds.every(Number.isFinite))throw new Error('Area bounds must contain four finite local metre coordinates.');
      bounds=[Math.max(0,bounds[0]),Math.max(0,bounds[1]),Math.min(this.terrain.extent_m[0],bounds[2]),Math.min(this.terrain.extent_m[1],bounds[3])];if(bounds[0]>=bounds[2]||bounds[1]>=bounds[3])throw new Error('Selected area does not intersect the loaded terrain.');}
    this.areaBounds=bounds;this.buildingLod?.setRegion(bounds);this.terrainLod?.setRegion(bounds);
    if(bounds){const a=this.point(bounds[0],bounds[1]),b=this.point(bounds[2],bounds[3]);this.renderer.clippingPlanes=[new THREE.Plane(new THREE.Vector3(1,0,0),-a.x+.00002),new THREE.Plane(new THREE.Vector3(-1,0,0),b.x+.00002),new THREE.Plane(new THREE.Vector3(0,0,1),-a.z+.00002),new THREE.Plane(new THREE.Vector3(0,0,-1),b.z+.00002)];}
    else this.renderer.clippingPlanes=[];
    this.updateAreaWalls();this.resize();this.fit();this.dirty=true;
  }

  private updateAreaWalls():void {
    this.clear(this.areaWalls);if(!this.areaWalls.parent)this.scene.add(this.areaWalls);const b=this.areaBounds;if(!b)return;
    const vertices:number[]=[];
    for(const line of this.terrainLod?.boundarySegments(b)??[])for(let i=1;i<line.length;i++){
      const p=this.point(line[i-1][0],line[i-1][1]),q=this.point(line[i][0],line[i][1]);vertices.push(p.x,p.y,p.z,q.x,q.y,q.z,p.x,-.22,p.z,q.x,q.y,q.z,q.x,-.22,q.z,p.x,-.22,p.z);
    }
    const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(vertices,3));geometry.computeVertexNormals();const mesh=new THREE.Mesh(geometry,new THREE.MeshStandardMaterial({color:'#98968e',roughness:1,side:THREE.DoubleSide}));mesh.receiveShadow=true;this.areaWalls.add(mesh);
  }

  fit(animate = true, name = 'oblique'): void {
    const rect=this.renderer.domElement.parentElement!.getBoundingClientRect();
    const advanced=document.getElementById('panel');
    const areaPanel=document.body.classList.contains('area-flow-open')?document.getElementById('area-flow'):null;
    const panel=areaPanel??(document.body.classList.contains('canvas-workspace')?document.getElementById('canvas-inspector'):(advanced&&!advanced.hidden?advanced:document.getElementById('workflow-card')));
    const availableWidth=panel&&!panel.hidden&&panel.getBoundingClientRect().width>0&&!new URLSearchParams(location.search).has('scene')?panel.getBoundingClientRect().left:rect.width;
    const direction=name==='top'?new THREE.Vector3(0,1,.0001):new THREE.Vector3(.3,Math.tan(35*Math.PI/180)*Math.hypot(.3,1),1).normalize();
    const right=new THREE.Vector3().crossVectors(new THREE.Vector3(0,1,0),direction).normalize();
    const up=new THREE.Vector3().crossVectors(direction,right);
    const bounds=this.areaBounds??[0,0,...this.terrain.extent_m];
    const target=this.point((bounds[0]+bounds[2])/2,(bounds[1]+bounds[3])/2);target.y=.03;
    const tanY=Math.tan(THREE.MathUtils.degToRad(this.camera.fov/2));
    const tanX=tanY*availableWidth/(this.split?2:1)/rect.height*.96, vertical=tanY*.76;
    let distance=1;
    for(const x of [-1,1]) for(const z of [-1,1]) for(const y of [-.24,.55]) {
      const point=new THREE.Vector3(target.x+x*(bounds[2]-bounds[0])*SCALE/2,y,target.z+z*(bounds[3]-bounds[1])*SCALE/2).sub(target);
      distance=Math.max(distance,Math.abs(point.dot(right))/tanX+point.dot(direction),Math.abs(point.dot(up))/vertical+point.dot(direction));
    }
    this.moveCamera(target.clone().addScaledVector(direction,distance),target,animate);
  }

  private moveCamera(position: THREE.Vector3,target: THREE.Vector3,animate: boolean): void {
    if (animate && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) this.transition = { start: performance.now(), from: this.camera.position.clone(), to: position, targetFrom: this.controls.target.clone(), targetTo: target };
    else { this.camera.position.copy(position); this.controls.target.copy(target); this.controls.update(); }
    this.dirty=true;
  }

  getBuildingPosition(index:number):[number,number]|undefined {
    const ring=this.sourceBuildings[index]?.footprint[0];if(!ring?.length)return undefined;
    return [(Math.min(...ring.map(p=>p[0]))+Math.max(...ring.map(p=>p[0])))/2,(Math.min(...ring.map(p=>p[1]))+Math.max(...ring.map(p=>p[1])))/2];
  }

  focusPoint(point:[number,number],radius_m:number):void {
    if(!this.ready||!point.every(Number.isFinite)||!Number.isFinite(radius_m))return;
    const target=this.point(point[0],point[1],this.renderedGround(...point));const span=Math.max(120,radius_m)*SCALE;
    this.moveCamera(target.clone().add(new THREE.Vector3(span*.35,span*.9,span*1.35)),target,true);
  }

  preset(name: string, animate = true): void {
    if(name==='ito'){
      // Approximate ITO neighbourhood camera preset, not event geometry.
      const [east,north]=proj4('EPSG:4326','+proj=utm +zone=43 +datum=WGS84 +units=m +no_defs',[77.249,28.627]);
      const x=east-this.terrain.origin[0],z=this.terrain.origin[1]-north;
      const target=this.point(x,z,this.renderedGround(x,z));
      this.moveCamera(target.clone().add(new THREE.Vector3(.08,.09,.14)),target,animate);return;
    }
    if(name!=='river'){this.fit(animate,name);return;}
    const size=Math.max(...this.terrain.extent_m)*SCALE;
    this.moveCamera(new THREE.Vector3(size*.15,size*.09,size*.8),new THREE.Vector3(0,.05,0),animate);
  }

  cameraAt(position: number[], target: number[]): void { this.transition = undefined; this.camera.position.fromArray(position); this.controls.target.fromArray(target); this.controls.update(); this.dirty = true; }

  pick(event: PointerEvent): [number, number] | null {
    const rect = this.renderer.domElement.getBoundingClientRect(); this.raycaster.setFromCamera(new THREE.Vector2((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1), this.camera);
    const hit = this.raycaster.intersectObject(this.terrainMesh)[0];
    if(!hit)return null;const point:[number,number]=[hit.point.x/SCALE+this.terrain.extent_m[0]/2,hit.point.z/SCALE+this.terrain.extent_m[1]/2];
    return this.areaBounds&&(point[0]<this.areaBounds[0]||point[0]>this.areaBounds[2]||point[1]<this.areaBounds[1]||point[1]>this.areaBounds[3])?null:point;
  }

  drawEdit(points: number[][], closed: boolean): void { this.clear(this.edit); this.lines(this.edit, [closed && points.length > 2 ? [...points, points[0]] : points], '#f3ce8a', 0.012); for (const xy of points) { const marker = new THREE.Mesh(new THREE.SphereGeometry(0.025, 8, 6), new THREE.MeshBasicMaterial({color:'#f3ce8a'})); marker.position.copy(this.point(xy[0], xy[1])); marker.position.y += 0.03; this.edit.add(marker); } }

  tick(now: number): void {
    if (this.transition) { const t = Math.min(1, (now - this.transition.start) / 850); const k = t*t*(3-2*t); this.camera.position.lerpVectors(this.transition.from, this.transition.to, k); this.controls.target.lerpVectors(this.transition.targetFrom, this.transition.targetTo, k); if (t === 1) this.transition = undefined; this.dirty = true; }
    this.controls.update();
    const nowSeconds = now / 1000;
    if (this.quakeMode) {
      const elapsed = nowSeconds - this.quakeStart;
      const prevShake = this.shakeAmp.value;
      this.shakeAmp.value = elapsed < 2.5 ? Math.max(0, 1 - elapsed / 2.5) : 0;
      this.shakeTime.value = nowSeconds;
      const prevProgress = this.collapseProgress.value;
      const collapseElapsed = nowSeconds - this.collapseStart;
      this.collapseProgress.value = Math.min(1, Math.max(0, collapseElapsed / 2.2));
      if (prevShake !== this.shakeAmp.value || prevProgress !== this.collapseProgress.value || this.shakeAmp.value > 0) this.dirty = true;
    }
    if (this.tickFires(now)) this.dirty = true;
    this.lastTickNow = now;
    if (this.dirty) this.render();
  }

  setSplit(on:boolean):void { this.split=on;this.resize(); }

  render(): void {
    const start=performance.now();const settle=new URLSearchParams(location.search).has('scene');
    const buildingsPending=this.buildings.visible?(this.buildingLod?.update(this.camera,settle)??false):false,terrainPending=this.terrainLod?.update(this.camera,settle)??false;this.lodPending=buildingsPending||terrainPending;
    this.ssao.enabled=this.ao;this.renderer.info.reset();
    if(this.split){
      const size=this.renderer.getSize(new THREE.Vector2());const visible=this.water.visible;
      this.composer.renderToScreen=false;this.renderer.setRenderTarget(null);this.renderer.setScissorTest(false);this.renderer.clear();
      for(let side=0;side<2;side++){
        this.water.visible=side===1&&visible;this.renderer.setScissorTest(false);this.composer.render();
        this.splitMaterial.uniforms.image.value=this.composer.readBuffer.texture;
        this.renderer.setRenderTarget(null);this.renderer.setViewport(side*this.splitWidth/2,0,this.splitWidth/2,size.y);this.renderer.setScissor(side*this.splitWidth/2,0,this.splitWidth/2,size.y);this.renderer.setScissorTest(true);this.renderer.render(this.splitScene,this.splitCamera);
      }
      this.water.visible=visible;this.renderer.setScissorTest(false);this.renderer.setViewport(0,0,size.x,size.y);
    }else{this.composer.renderToScreen=true;this.composer.render();}
    this.drawCount++;this.frameTimes.push(performance.now()-start);if(this.frameTimes.length>120)this.frameTimes.shift();this.dirty=this.lodPending;
  }
}
