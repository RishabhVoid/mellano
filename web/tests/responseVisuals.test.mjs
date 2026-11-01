import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { stripTypeScriptTypes } from 'node:module';
import * as THREE from 'three';
const source=await readFile(new URL('../src/responseVisuals.ts',import.meta.url),'utf8');
const compiled=stripTypeScriptTypes(source,{mode:'transform'})
  .replace(/from (['"])three\1/g,`from ${JSON.stringify(new URL('../node_modules/three/build/three.module.js',import.meta.url).href)}`)
  .replace(/from (['"])three\/addons\/([^'"]+)\1/g,(_all,_quote,path)=>`from ${JSON.stringify(new URL(`../node_modules/three/examples/jsm/${path}`,import.meta.url).href)}`)
  .replace(/from (['"])\.\/responseMotion\1/g,`from ${JSON.stringify(new URL('../src/responseMotion.ts',import.meta.url).href)}`);
const {ResponseVisuals}=await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const unit={id:'ambulance_a',type:'ambulance',departure_time_s:20,eta_s:100,path:[[0,0],[100,0],[100,100]],path_times_s:[0,50,100],station_id:'hospital',modelled:true};
const plan=()=>({available:true,stations:[],hotspots:[],units:[structuredClone(unit)]});
function fixture(onSelectUnit){
  const elements=new Map();const node=()=>({hidden:false,textContent:'',style:{},dataset:{},children:[],listeners:{},append(...items){this.children.push(...items);},replaceChildren(...items){this.children=items;},attributes:{},setAttribute(name,value){this.attributes[name]=value;},addEventListener(name,callback){this.listeners[name]=callback;}});
  globalThis.document={createElement:node,getElementById(id){if(!elements.has(id))elements.set(id,node());return elements.get(id);},body:node()};
  const controls=new THREE.EventDispatcher();controls.target=new THREE.Vector3();
  const camera=new THREE.PerspectiveCamera(36,1.5,.01,150);camera.position.set(2,3,4);camera.lookAt(controls.target);camera.updateMatrixWorld();
  const dom=node();dom.getBoundingClientRect=()=>({width:1200,height:800,left:0,top:0});
  const view={scene:new THREE.Scene(),camera,controls,terrain:{extent_m:[1000,1000]},dirty:true,renderer:{domElement:dom},cameraWrites:0,point:(x,z)=>new THREE.Vector3((x-500)*.001,0,(z-500)*.001),waterPoint:(x,z)=>new THREE.Vector3((x-500)*.001,.01,(z-500)*.001),cameraAt(position,target){this.cameraWrites++;camera.position.fromArray(position);controls.target.fromArray(target);camera.lookAt(controls.target);camera.updateMatrixWorld();this.dirty=true;},clear(group){group.traverse(child=>{if(child.geometry)child.geometry.dispose();for(const material of child.material?Array.isArray(child.material)?child.material:[child.material]:[])material.dispose();});group.clear();}};
  const visuals=new ResponseVisuals(view,onSelectUnit);return {visuals,view,controls,elements};
}
function mockModel(visuals){const mesh=new THREE.Mesh(new THREE.BoxGeometry(.002,.002,.0055),new THREE.MeshStandardMaterial()),root=new THREE.Group();root.add(mesh);visuals.models.set('ambulance',root);visuals.modelLengths.set('ambulance',5.5);visuals.modelStatus.set('ambulance','ready');return mesh;}

test('routes are bounded screen-space wide lines with a selected outline',()=>{
  const {visuals}=fixture();mockModel(visuals);visuals.set(plan());visuals.setVisible(true);
  const route=visuals.routeLines.get(unit.id);assert.equal(route.line.isLine2,true);assert.equal(route.line.material.worldUnits,false);assert.equal(route.line.material.linewidth,3);assert.equal(route.outline.material.linewidth,6);
  visuals.followUnit(unit.id);assert.equal(route.line.material.linewidth,4.5);assert.equal(route.outline.material.linewidth,8);
});
test('camera follows the whole saved path, settles at arrival, and then holds',()=>{
  const {visuals,view}=fixture();mockModel(visuals);visuals.set(plan());visuals.setVisible(true);visuals.tick(20,0);visuals.followUnit(unit.id);
  for(let i=0;i<90;i++)visuals.tick(20,1/60);
  const initial=view.controls.target.clone();visuals.tick(70,1/60);assert.ok(view.controls.target.x>initial.x+.09);assert.equal(visuals.getFollowState().progress,.5);
  for(let i=0;i<200;i++)visuals.tick(120,1/60);
  assert.equal(visuals.getFollowState().phase,'arrived');assert.equal(visuals.getFollowState().progress,1);const final=view.camera.position.clone(),writes=view.cameraWrites;
  for(let i=0;i<10;i++){view.dirty=false;visuals.tick(120,1/60);}assert.deepEqual(view.camera.position.toArray(),final.toArray());assert.equal(view.cameraWrites,writes);assert.equal(view.dirty,false);
});
test('user orbit starts cancel following without snapping the camera back',()=>{
  const {visuals,view,controls}=fixture();mockModel(visuals);visuals.set(plan());visuals.setVisible(true);visuals.tick(20,0);visuals.followUnit(unit.id);visuals.tick(30,.1);controls.dispatchEvent({type:'start'});const camera=view.camera.position.clone();visuals.tick(70,.1);assert.equal(visuals.getFollowState().phase,'off');assert.deepEqual(view.camera.position.toArray(),camera.toArray());
});
test('same immutable snapshot preserves follow; a different plan and hiding cancel it',()=>{
  const {visuals}=fixture();mockModel(visuals);const data=plan();visuals.set(data);visuals.setVisible(true);visuals.tick(20,0);visuals.followUnit(unit.id);const object=visuals.objects.get(unit.id);visuals.set(data);assert.equal(visuals.objects.get(unit.id),object);assert.equal(visuals.getFollowState().phase,'following');
  visuals.set(plan());assert.equal(visuals.getFollowState().phase,'off');visuals.followUnit(unit.id);visuals.setVisible(false);assert.equal(visuals.getFollowState().phase,'off');
});
test('clearing cloned GLBs never disposes geometry or material shared with cached models',()=>{
  const {visuals}=fixture();const mesh=mockModel(visuals);let disposals=0;mesh.geometry.addEventListener('dispose',()=>disposals++);mesh.material.addEventListener('dispose',()=>disposals++);visuals.set(plan());assert.equal(visuals.getUnits()[0].modelAvailable,true);visuals.set();assert.equal(disposals,0);visuals.set(plan());assert.equal(visuals.getUnits()[0].modelAvailable,true);
});
test('local asset completion upgrades already-present objects and normalizes listed length',async()=>{
  const {visuals}=fixture();visuals.set(plan());const object=visuals.objects.get(unit.id);assert.equal(object.children.length,0);
  globalThis.fetch=async()=>Response.json({vehicles:{ambulance:{url:'/assets/vehicles/car-kit/ambulance.glb',length_m:5.5,rotation_y:0}}});
  visuals.loader={loadAsync:async()=>{const scene=new THREE.Group(),mesh=new THREE.Mesh(new THREE.BoxGeometry(2,2,4),new THREE.MeshStandardMaterial());mesh.position.set(1,1,2);scene.add(mesh);return {scene};}};
  await visuals.initialize();assert.equal(visuals.objects.get(unit.id),object);assert.equal(visuals.getUnits()[0].modelAvailable,true);assert.equal(object.children.length,1);
  const size=new THREE.Box3().setFromObject(visuals.models.get('ambulance')).getSize(new THREE.Vector3());assert.ok(Math.abs(size.z-.0055)<1e-10);
});
test('missing assets are explicit and only then create a labelled fallback',async()=>{
  const {visuals,elements}=fixture();visuals.set(plan());globalThis.fetch=async()=>new Response('',{status:404});await visuals.initialize();visuals.setVisible(true);visuals.tick(20,0);
  assert.equal(visuals.getUnits()[0].modelAvailable,false);assert.equal(visuals.objects.get(unit.id).children[0].userData.ownedVisual,true);assert.match(elements.get('response-labels').children[0].textContent,/model unavailable/);
});

test('same-type unit names are distinct, markers hide while following, and labels stay accessible',()=>{
  const {visuals,elements}=fixture();mockModel(visuals);const data=plan();data.units.push({...structuredClone(unit),id:'ambulance_b'});data.units.reverse();visuals.set(data);visuals.setVisible(true);
  assert.deepEqual(visuals.getUnits().map(item=>item.label),['ambulance 2','ambulance 1']);assert.equal(elements.get('response-labels').attributes['aria-hidden'],'false');
  visuals.tooltip.hidden=false;visuals.followUnit(unit.id);assert.equal(visuals.tooltip.hidden,true);assert.ok(visuals.routes.children.filter(item=>item.userData.responseContextMarker).every(item=>!item.visible));assert.equal(visuals.getFollowState().label,'ambulance 1');
  visuals.followUnit();assert.ok(visuals.routes.children.filter(item=>item.userData.responseContextMarker).every(item=>item.visible));visuals.setVisible(false);assert.equal(elements.get('response-labels').attributes['aria-hidden'],'true');
});

test('surface refresh reuses route buffers and puts boat route on the current water height',()=>{
  const {visuals,view}=fixture();view.waterLevel=.01;view.waterPoint=(x,z)=>new THREE.Vector3((x-500)*.001,view.waterLevel,(z-500)*.001);const data=plan();data.units[0].type='rescue_boat';visuals.set(data);visuals.setVisible(true);
  const route=visuals.routeLines.get(unit.id),start=route.line.geometry.getAttribute('instanceStart'),buffer=start.data.array;assert.ok(Math.abs(start.getY(0)-.011)<1e-7);assert.ok(route.points.length>data.units[0].path.length);
  view.waterLevel=.09;visuals.refreshSurface();visuals.tick(70,0);assert.equal(start.data.array,buffer);assert.ok(Math.abs(start.getY(0)-.091)<1e-7);assert.ok(Math.abs(visuals.objects.get(unit.id).position.y-start.getY(0))<1e-7);assert.deepEqual(data.units[0].path,unit.path);
});
test('model clicks and keyboard labels delegate full-route replay to the owning clock',()=>{
  const selected=[],{visuals,view,elements}=fixture(id=>selected.push(id));mockModel(visuals);visuals.set(plan());visuals.setVisible(true);visuals.tick(120,0);const label=elements.get('response-labels').children[0];label.listeners.click();label.listeners.keydown({key:'Enter',preventDefault(){}});
  visuals.raycaster={setFromCamera(){},intersectObjects(){return [{object:visuals.objects.get(unit.id).children[0]}];}};view.renderer.domElement.listeners.pointerdown({button:0,clientX:10,clientY:10});view.renderer.domElement.listeners.pointerup({clientX:10,clientY:10});assert.deepEqual(selected,[unit.id,unit.id,unit.id]);assert.equal(visuals.getFollowState().phase,'off');
});
test('boat remaining-time label and capture exclude duplicate launch access; selected label stays off model',()=>{
  const {visuals,elements}=fixture();const data=plan();Object.assign(data.units[0],{type:'rescue_boat',departure_time_s:120,eta_s:360,path:[[0,0],[100,0]],path_times_s:[0,240]});visuals.set(data);visuals.setVisible(true);visuals.tick(180,0);assert.match(elements.get('response-labels').children[0].textContent,/3 min/);assert.equal(visuals.getUnits()[0].eta_s,360);
  const captured=[],context={save(){},restore(){},measureText(){return {width:30};},fillRect(){},fillText(text){captured.push(text);}};visuals.drawCapture(context,1200,800);assert.match(captured.join(' '),/3 min/);visuals.followUnit(unit.id);visuals.tick(180,0);assert.equal(elements.get('response-labels').children[0].hidden,true);
});
test('an already-at-destination unit keeps its model without inventing a route line',()=>{
  const {visuals}=fixture();mockModel(visuals);const data=plan();data.units[0].path=[[5,6]];data.units[0].path_times_s=[0];visuals.set(data);visuals.setVisible(true);visuals.tick(20,0);assert.equal(visuals.getUnits().length,1);assert.equal(visuals.getUnits()[0].modelAvailable,true);assert.equal(visuals.routeLines.size,0);visuals.followUnit(unit.id);assert.equal(visuals.getFollowState().phase,'arrived');
});

test('vehicle surfaces render after route overlays while keeping opacity and scene depth occlusion',async()=>{
  const {visuals}=fixture();visuals.set(plan());globalThis.fetch=async()=>Response.json({vehicles:{ambulance:{url:'/assets/vehicles/car-kit/ambulance.glb',length_m:5.5,rotation_y:0}}});
  visuals.loader={loadAsync:async()=>{const scene=new THREE.Group(),body=new THREE.Mesh(new THREE.BoxGeometry(2,2,4),new THREE.MeshStandardMaterial({opacity:1})),glass=new THREE.Mesh(new THREE.BoxGeometry(1,1,1),new THREE.MeshStandardMaterial({transparent:true,opacity:.4,depthWrite:false}));scene.add(body,glass);return {scene};}};await visuals.initialize();
  const route=visuals.routeLines.get(unit.id);let checked=0;visuals.objects.get(unit.id).traverse(mesh=>{if(!mesh.isMesh)return;checked++;assert.ok(mesh.renderOrder>route.line.renderOrder);assert.equal(mesh.material.transparent,true);assert.equal(mesh.material.depthTest,true);if(mesh.material.opacity===1)assert.equal(mesh.material.depthWrite,true);else{assert.equal(mesh.material.opacity,.4);assert.equal(mesh.material.depthWrite,false);}});assert.equal(checked,2);
});
