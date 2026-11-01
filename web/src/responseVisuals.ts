import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { Line2 } from 'three/addons/lines/Line2.js';
import { LineGeometry } from 'three/addons/lines/LineGeometry.js';
import { LineMaterial } from 'three/addons/lines/LineMaterial.js';
import { drapeRoute, remainingRouteSeconds, sampleRoute, smoothHeading, validRoute, vehicleDisplayScale } from './responseMotion';
import type { Diorama } from './model';
import type { ResponseData, ResponseUnit } from './types';

const unitColors:Record<string,string>={ambulance:'#eef4ef',fire_engine:'#f57758',police:'#7099e3',rescue_boat:'#edc264'};
export interface ResponseUnitSummary {id:string;type:ResponseUnit['type'];label:string;eta_s:number;departure_time_s:number;duration_s:number;progress:number;modelAvailable:boolean;routeAvailable:boolean}
export interface FollowState {unitId?:string;label?:string;phase:'off'|'following'|'arrived';progress:number;modelAvailable?:boolean}
export class ResponseVisuals {
  private group=new THREE.Group();
  private routes=new THREE.Group();
  private vehicles=new THREE.Group();
  private data?:ResponseData;
  private labels:HTMLDivElement[]=[];
  private objects=new Map<string,THREE.Group>();
  private models=new Map<string,THREE.Group>();
  private loader=new GLTFLoader();
  private modelLengths=new Map<string,number>();
  private unitLabels=new Map<string,string>();
  private modelStatus=new Map<string,'loading'|'ready'|'unavailable'>();
  private routeLines=new Map<string,{outline:Line2;line:Line2;points:[number,number][];type:ResponseUnit['type']}>();
  private follow:FollowState={phase:'off',progress:0};
  private followLastPosition?:THREE.Vector3;
  private followHeading=0;
  private previousTick=0;
  private selectionStart?:{x:number;y:number};
  private raycaster=new THREE.Raycaster();
  private modelPromise?:Promise<void>;
  private tooltip=document.createElement("div");
  private lastTime=NaN;
  visible=false;
  constructor(private view:Diorama,private onSelectUnit?:(id:string)=>void){this.group.add(this.routes,this.vehicles);view.scene.add(this.group);this.group.visible=false;this.tooltip.className='station-tooltip';this.tooltip.hidden=true;document.body.append(this.tooltip);
    view.renderer.domElement.addEventListener('pointermove',event=>{if(!this.visible||!this.data||this.follow.unitId){this.tooltip.hidden=true;return;}const rect=view.renderer.domElement.getBoundingClientRect();
      let best=22,chosen:ResponseData['stations'][number]|undefined,chosenUnit:ResponseUnit|undefined;
      for(const station of this.data.stations){const p=view.point(...station.position);p.y+=.035;p.project(view.camera);if(p.z>1||p.z< -1)continue;const d=Math.hypot(rect.left+(p.x+1)*rect.width/2-event.clientX,rect.top+(1-p.y)*rect.height/2-event.clientY);if(d<best){best=d;chosen=station;chosenUnit=undefined;}}
      for(const object of this.objects.values()){const p=object.position.clone().project(view.camera);if(p.z>1||p.z< -1)continue;const d=Math.hypot(rect.left+(p.x+1)*rect.width/2-event.clientX,rect.top+(1-p.y)*rect.height/2-event.clientY);if(d<best){best=d;chosen=undefined;chosenUnit=object.userData.unit as ResponseUnit;}}
      this.tooltip.hidden=!chosen&&!chosenUnit;
      if(chosen){this.tooltip.textContent=`${chosen.name||chosen.kind.replace('_',' ')} · ${chosen.kind==='school'?'Potential relief site':chosen.kind.replace('_',' ')} · ${(chosen.status??'unknown').replaceAll('_',' ')} · operational availability unknown`;this.tooltip.style.left=`${event.clientX+16}px`;this.tooltip.style.top=`${event.clientY+16}px`;}
      else if(chosenUnit){const sample=sampleRoute(chosenUnit,Number.isFinite(this.lastTime)?this.lastTime:chosenUnit.departure_time_s);this.tooltip.textContent=`${this.unitLabels.get(chosenUnit.id)??chosenUnit.type.replaceAll('_',' ')} · ${sample.traffic_description??`${sample.speed_kmh} km/h`} · ETA ${Math.ceil(remainingRouteSeconds(chosenUnit,this.lastTime)/60)} min`;this.tooltip.style.left=`${event.clientX+16}px`;this.tooltip.style.top=`${event.clientY+16}px`;}
    });view.renderer.domElement.addEventListener('pointerleave',()=>{this.tooltip.hidden=true;});
    view.controls.addEventListener('start',()=>{this.followUnit();});
    view.renderer.domElement.addEventListener('pointerdown',event=>{this.selectionStart=event.button===0?{x:event.clientX,y:event.clientY}:undefined;});
    view.renderer.domElement.addEventListener('pointerup',event=>{
      const start=this.selectionStart;this.selectionStart=undefined;if(!this.visible||!start||Math.hypot(event.clientX-start.x,event.clientY-start.y)>5)return;
      const rect=view.renderer.domElement.getBoundingClientRect();this.raycaster.setFromCamera(new THREE.Vector2((event.clientX-rect.left)/rect.width*2-1,1-(event.clientY-rect.top)/rect.height*2),view.camera);
      const hit=this.raycaster.intersectObjects([...this.objects.values()],true)[0];let object:THREE.Object3D|undefined=hit?.object;
      while(object&&!object.userData.unit)object=object.parent??undefined;
      if(object)this.selectUnit((object.userData.unit as ResponseUnit).id);
    });
  }
  async initialize():Promise<void>{
    if(this.modelPromise)return this.modelPromise;
    for(const kind of Object.keys(unitColors))this.modelStatus.set(kind,'loading');
    this.modelPromise=(async()=>{
      try{
        const response=await fetch('/assets/vehicles/manifest.json');if(!response.ok)throw new Error('Vehicle manifest unavailable');
        const manifest=await response.json();const entries=manifest.vehicles??manifest.models??{};
        await Promise.all(Object.keys(unitColors).map(async kind=>{
          const item=entries[kind] as {url?:string;path?:string;length_m?:number;rotation_y?:number}|undefined;
          const url=item?.url??item?.path,length=item?.length_m;
          try{
            if(!url||!/^\/assets\/vehicles\/[A-Za-z0-9/_-]+\.glb$/.test(url)||!Number.isFinite(length)||!length||length<=0)throw new Error('Vehicle model unavailable');
            const gltf=await this.loader.loadAsync(url),model=gltf.scene;model.updateMatrixWorld(true);
            const bounds=new THREE.Box3().setFromObject(model),size=bounds.getSize(new THREE.Vector3()),center=bounds.getCenter(new THREE.Vector3());
            if(!Number.isFinite(size.z)||Math.max(size.x,size.z)<=0)throw new Error('Vehicle model has no geometry');
            model.position.sub(new THREE.Vector3(center.x,bounds.min.y,center.z));
            model.traverse(child=>{const mesh=child as THREE.Mesh;if(!mesh.isMesh)return;mesh.castShadow=false;mesh.receiveShadow=true;mesh.renderOrder=8;for(const material of Array.isArray(mesh.material)?mesh.material:[mesh.material]){// Draw opaque vehicle surfaces after route overlays, retaining scene depth occlusion.
              if(!material.transparent&&material.opacity>=1){material.transparent=true;material.depthWrite=true;}material.depthTest=true;
              const matte=material as THREE.MeshStandardMaterial;if(matte.isMeshStandardMaterial){matte.roughness=Math.max(.65,matte.roughness);matte.metalness=Math.min(.15,matte.metalness);}}});
            const root=new THREE.Group();root.add(model);root.scale.setScalar(length*.001/Math.max(size.x,size.z));root.rotation.y=Number.isFinite(item?.rotation_y)?item!.rotation_y!:0;
            this.models.set(kind,root);this.modelLengths.set(kind,length);this.modelStatus.set(kind,'ready');
          }catch{this.modelStatus.set(kind,'unavailable');}
        }));
      }catch{for(const kind of Object.keys(unitColors))this.modelStatus.set(kind,'unavailable');}
      // set() may be called before local assets finish. Upgrade those objects in place.
      for(const object of this.objects.values())this.populateVehicle(object,object.userData.unit as ResponseUnit);
      this.view.dirty=true;
    })();return this.modelPromise;
  }
  private populateVehicle(object:THREE.Group,unit:ResponseUnit):void{
    object.traverse(child=>{const mesh=child as THREE.Mesh;if(mesh.userData.ownedVisual){mesh.geometry.dispose();(mesh.material as THREE.Material).dispose();}});object.clear();
    const model=this.models.get(unit.type);object.userData.modelAvailable=!!model;
    if(model){
      const clone=model.clone(true);
      // Boats always need to render above the translucent water surface.
      if(unit.type==='rescue_boat')clone.traverse(child=>{const mesh=child as THREE.Mesh;if(mesh.isMesh){mesh.renderOrder=10;for(const material of Array.isArray(mesh.material)?mesh.material:[mesh.material]){if(material){material.depthWrite=true;material.transparent=false;}}}});
      object.add(clone);
    }
    else if(this.modelStatus.get(unit.type)==='unavailable'){
      const marker=new THREE.Mesh(unit.type==='rescue_boat'?new THREE.BoxGeometry(.004,.0012,.0015):new THREE.BoxGeometry(.002,.0015,.005),new THREE.MeshStandardMaterial({color:unitColors[unit.type],roughness:.85,transparent:false,opacity:1,depthTest:true,depthWrite:true}));
      marker.position.y=.0012;marker.renderOrder=10;marker.userData.ownedVisual=true;object.add(marker);
    }
  }
  private selectUnit(id:string):void{if(this.onSelectUnit)this.onSelectUnit(id);else this.followUnit(id);}
  getUnits():ResponseUnitSummary[]{return [...this.objects.values()].map(object=>{const unit=object.userData.unit as ResponseUnit,sample=sampleRoute(unit,Number.isFinite(this.lastTime)?this.lastTime:unit.departure_time_s);return {id:unit.id,type:unit.type,label:this.unitLabels.get(unit.id)??unit.type.replaceAll('_',' '),eta_s:unit.eta_s,departure_time_s:unit.departure_time_s,duration_s:sample.duration_s,progress:sample.progress,modelAvailable:!!object.userData.modelAvailable,routeAvailable:unit.path.length===1||this.routeLines.has(unit.id)};});}
  getFollowState():FollowState{return {...this.follow};}
  followUnit(id?:string):void{
    this.tooltip.hidden=true;
    const object=id?this.objects.get(id):undefined;
    if(!object||!this.visible){this.follow={phase:'off',progress:0};this.followLastPosition=undefined;}
    else{const unit=object.userData.unit as ResponseUnit,sample=sampleRoute(unit,Number.isFinite(this.lastTime)?this.lastTime:unit.departure_time_s);this.follow={unitId:id,label:this.unitLabels.get(unit.id)??unit.type.replaceAll('_',' '),phase:sample.arrived?'arrived':'following',progress:sample.progress,modelAvailable:!!object.userData.modelAvailable};this.followHeading=sample.heading;this.followLastPosition=object.position.clone();}
    for(const marker of this.routes.children)if(marker.userData.responseContextMarker)marker.visible=!this.follow.unitId;
    for(const [unitId,route] of this.routeLines){const selected=unitId===this.follow.unitId;route.outline.material.linewidth=selected?8:6;route.line.material.linewidth=selected?4.5:3;route.line.material.opacity=selected?1:.9;}
    this.view.dirty=true;
  }
  setVisible(on:boolean):void{this.visible=on;this.group.visible=on;if(!on){this.tooltip.hidden=true;this.followUnit();}document.getElementById('response-labels')!.hidden=!on;document.getElementById('response-labels')!.setAttribute('aria-hidden',String(!on));this.view.dirty=true;}
  set(data?:ResponseData):void{
    if(data&&data===this.data)return;this.followUnit();this.routeLines.clear();this.unitLabels.clear();
    this.lastTime=NaN;this.data=data;this.view.clear(this.routes);this.vehicles.traverse(child=>{const mesh=child as THREE.Mesh;if(mesh.userData.ownedVisual){mesh.geometry.dispose();(mesh.material as THREE.Material).dispose();}});this.vehicles.clear();this.objects.clear();this.labels=[];document.getElementById('response-labels')!.replaceChildren();
    if(!data)return;
    const validUnits=(data.units??[]).filter(validRoute);
    for(const kind of Object.keys(unitColors)){const sameType=validUnits.filter(unit=>unit.type===kind).sort((a,b)=>a.id.localeCompare(b.id));sameType.forEach((unit,index)=>this.unitLabels.set(unit.id,`${kind.replaceAll('_',' ')}${sameType.length>1?` ${index+1}`:''}`));}
    const stations=data.stations??[];
    // Facilities share one geometry and one material; no DOM labels for all stations.
    const markers=new THREE.InstancedMesh(new THREE.OctahedronGeometry(.018),new THREE.MeshBasicMaterial({color:'#b9e8df',depthTest:false}),stations.length);
    const transform=new THREE.Object3D();
    stations.forEach((station,i)=>{transform.position.copy(this.view.point(...station.position));transform.position.y+=.035;transform.updateMatrix();markers.setMatrixAt(i,transform.matrix);markers.setColorAt(i,new THREE.Color(station.status==='cut_off'?'#d97067':station.kind==='school'?'#b2a0d7':'#b9e8df'));});markers.renderOrder=6;markers.userData.responseContextMarker=true;this.routes.add(markers);
    const hotspots=data.hotspots??[];const rings=new THREE.InstancedMesh(new THREE.RingGeometry(.028,.042,24),new THREE.MeshBasicMaterial({color:'#d95c50',side:THREE.DoubleSide,depthTest:false}),hotspots.length);
    hotspots.forEach((hotspot,i)=>{transform.rotation.x=-Math.PI/2;transform.position.copy(this.view.point(...hotspot.position));transform.position.y+=.015;transform.updateMatrix();rings.setMatrixAt(i,transform.matrix);rings.setColorAt(i,new THREE.Color(hotspot.reachable?'#df984b':'#d95c50'));});rings.renderOrder=7;rings.userData.responseContextMarker=true;this.routes.add(rings);
    for(const unit of validUnits){
      const samples=drapeRoute(unit.path);
      if(samples&&samples.length>1){
      const points=samples.map(p=>{const v=unit.type==='rescue_boat'?this.view.waterPoint(...p):this.view.point(...p);v.y+=.001;return v;});
      const makeLine=(color:string,width:number,order:number)=>{const geometry=new LineGeometry().setFromPoints(points);const material=new LineMaterial({color,linewidth:width,worldUnits:false,transparent:true,opacity:.9,depthTest:false,depthWrite:false,toneMapped:false});const line=new Line2(geometry,material);line.renderOrder=order;line.frustumCulled=false;this.routes.add(line);return line;};
      const outline=makeLine('#101b22',6,4),line=makeLine(unitColors[unit.type],3,5);this.routeLines.set(unit.id,{outline,line,points:samples,type:unit.type});
      }
      const object=new THREE.Group();this.populateVehicle(object,unit);
      object.userData.unit=unit;this.vehicles.add(object);this.objects.set(unit.id,object);
      const label=document.createElement('div');label.className='response-eta';label.textContent=`${this.unitLabels.get(unit.id)??unit.type.replaceAll('_',' ')} · ${Math.ceil(unit.eta_s/60)} min`;label.dataset.unitId=unit.id;label.style.pointerEvents='auto';label.style.cursor='pointer';label.setAttribute('role','button');label.tabIndex=0;label.title='Follow this modelled unit';label.addEventListener('click',()=>this.selectUnit(unit.id));label.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();this.selectUnit(unit.id);}});document.getElementById('response-labels')!.append(label);this.labels.push(label);
    }
    this.view.dirty=true;
  }
  refreshSurface():void{
    for(const route of this.routeLines.values()){
      const points=route.points.map(point=>{const position=route.type==='rescue_boat'?this.view.waterPoint(...point):this.view.point(...point);position.y+=.001;return position;});
      for(const line of [route.outline,route.line]){
        const start=line.geometry.getAttribute('instanceStart') as THREE.InterleavedBufferAttribute,end=line.geometry.getAttribute('instanceEnd') as THREE.InterleavedBufferAttribute;
        for(let index=0;index<points.length-1;index++){const a=points[index],b=points[index+1];start.setXYZ(index,a.x,a.y,a.z);end.setXYZ(index,b.x,b.y,b.z);}
        start.data.needsUpdate=true;line.geometry.computeBoundingBox();line.geometry.computeBoundingSphere();
      }
    }
    this.view.dirty=true;
  }
  drawCapture(context:CanvasRenderingContext2D,width:number,height:number):void {
    if(!this.visible)return;context.save();context.font='15px monospace';context.textAlign='center';
    for(const object of this.objects.values()){const unit=object.userData.unit as ResponseUnit;if(unit.id===this.follow.unitId)continue;const p=object.position.clone().project(this.view.camera);if(p.z>1||p.z< -1)continue;const sample=sampleRoute(unit,Number.isFinite(this.lastTime)?this.lastTime:unit.departure_time_s);const speedStr=sample.waiting?'staged':sample.arrived?'arrived':`${sample.speed_kmh} km/h`;const text=`${this.unitLabels.get(unit.id)??unit.type.replaceAll('_',' ')} · ${speedStr} · ${Math.ceil(remainingRouteSeconds(unit,this.lastTime)/60)} min (modelled)`,x=(p.x+1)*width/2,y=(1-p.y)*height/2-20;const size=context.measureText(text).width;context.fillStyle='#182125df';context.fillRect(x-size/2-6,y-18,size+12,25);context.fillStyle='#edf0e7';context.fillText(text,x,y);}
    context.restore();
  }
  tick(time:number,delta?:number):void{
    if(!this.visible||!this.data)return;
    const now=performance.now(),dt=Number.isFinite(delta)?Math.max(0,Math.min(.1,delta!)):this.previousTick?Math.min(.1,(now-this.previousTick)/1000):1/60;this.previousTick=now;
    if(time===this.lastTime&&!this.view.dirty&&this.follow.phase==='off')return;let visualChanged=time!==this.lastTime;this.lastTime=time;
    const rect=this.view.renderer.domElement.getBoundingClientRect();
    for(const object of this.objects.values()){
      const unit=object.userData.unit as ResponseUnit,sample=sampleRoute(unit,time);
      if(unit.type==='rescue_boat'){
        const waterPos=this.view.waterPoint(...sample.position);const groundPos=this.view.point(...sample.position);
        waterPos.y=Math.max(waterPos.y,groundPos.y)+.001;object.position.copy(waterPos);
      } else {
        object.position.copy(this.view.point(...sample.position));object.position.y+=.001;
      }
      object.rotation.y=sample.heading;
      // Overview enlargement is illustrative; following returns the vehicle to its listed dimensions.
      let scale=this.follow.unitId===unit.id?1:vehicleDisplayScale(this.modelLengths.get(unit.type)??5,this.view.camera.position.distanceTo(object.position),this.view.camera.fov,rect.height);
      // Boats were inflated up to 60× and towered over the city. Keep them at the same exaggeration as buildings.
      if(unit.type==='rescue_boat')scale=Math.min(scale,this.view.buildingHeightScale);
      if(object.scale.x!==scale)visualChanged=true;object.scale.setScalar(scale);
    }
    const followed=this.follow.unitId?this.objects.get(this.follow.unitId):undefined;
    if(followed){
      const unit=followed.userData.unit as ResponseUnit,sample=sampleRoute(unit,time);
      const length=(this.modelLengths.get(unit.type)??5)*.001;
      const camera=this.view.camera.position.clone(),target=this.view.controls.target.clone();
      // Soft follow: translate camera + orbit target with the vehicle so the user retains full orbit, zoom, and tilt.
      const desiredTarget=followed.position.clone(); desiredTarget.y += length*.3;
      if (this.followLastPosition) {
        const translation = followed.position.clone().sub(this.followLastPosition);
        camera.add(translation); target.add(translation);
      } else {
        // On first attach, place the camera behind the vehicle; after that, user controls the angle.
        this.followHeading = sample.heading;
        const forward = new THREE.Vector3(Math.sin(this.followHeading),0,Math.cos(this.followHeading));
        const initialCamera = desiredTarget.clone().addScaledVector(forward,-Math.max(.08,length*12));
        initialCamera.y += Math.max(.05,length*7);
        camera.copy(initialCamera); target.copy(desiredTarget);
      }
      this.followLastPosition = followed.position.clone();
      // Gently re-centre the orbit on the vehicle; translate camera by the same delta to preserve user zoom/angle.
      const targetBlend = 1 - Math.exp(-6 * dt);
      const recentre = desiredTarget.clone().sub(target).multiplyScalar(targetBlend);
      target.add(recentre); camera.add(recentre);
      if (camera.distanceTo(this.view.camera.position) > 1e-6 || target.distanceTo(this.view.controls.target) > 1e-6) {
        this.view.cameraAt(camera.toArray(), target.toArray());
      }
      this.follow={...this.follow,phase:sample.arrived?'arrived':'following',progress:sample.progress,modelAvailable:!!followed.userData.modelAvailable};
    }
    for(const label of this.labels){const object=this.objects.get(label.dataset.unitId!);if(!object)continue;const unit=object.userData.unit as ResponseUnit,sample=sampleRoute(unit,time);const modelNote=object.userData.modelAvailable?'':this.modelStatus.get(unit.type)==='unavailable'?' · model unavailable':' · loading model';const routeNote=unit.path.length>1&&!this.routeLines.has(unit.id)?' · route exceeds display budget':'';const speedStr=sample.waiting?'staged':sample.arrived?'on scene':`${sample.speed_kmh} km/h (${sample.traffic_label??''})`;label.textContent=`${this.unitLabels.get(unit.id)??unit.type.replaceAll('_',' ')} · ${speedStr} · ${sample.arrived?'planned destination':`${Math.ceil(remainingRouteSeconds(unit,time)/60)} min`}${modelNote}${routeNote}`;const p=object.position.clone().project(this.view.camera);label.hidden=unit.id===this.follow.unitId||p.z>1||p.z< -1;label.style.transform=`translate(${rect.left+(p.x+1)*rect.width/2}px,${rect.top+(1-p.y)*rect.height/2-24}px)`;}
    if(visualChanged)this.view.dirty=true;
  }
}
