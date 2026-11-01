import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync, lstatSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { Box3, Matrix4, Quaternion, Vector3 } from 'three';

// Offline asset integrity: no renderer, server, browser or network is required.
const publicRoot=fileURLToPath(new URL('../public/',import.meta.url));
const assetRoot=path.join(publicRoot,'assets/vehicles');
const origin='https://local.invalid';
function localResource(uri,base='/assets/vehicles/') {
  assert.equal(typeof uri,'string');
  assert.ok(!uri.includes('\\')&&!uri.includes('\0'),'resource path must be a normal URL');
  const url=new URL(uri,new URL(base,origin));
  assert.equal(url.origin,origin,'vehicle resources must not request an external host');
  assert.equal(url.search,'');assert.equal(url.hash,'');
  const decoded=decodeURIComponent(url.pathname);
  assert.ok(decoded.startsWith('/assets/vehicles/'));
  assert.ok(!decoded.toLowerCase().includes('.env'),'environment paths are forbidden');
  assert.ok(/\.(?:glb|png|jpe?g|bin)$/i.test(decoded));
  const file=path.resolve(publicRoot,'.'+decoded);
  assert.ok(file.startsWith(assetRoot+path.sep));
  // Reject links before opening dependencies; every resource stays in the package.
  let current=assetRoot;
  for(const part of path.relative(assetRoot,file).split(path.sep)){
    current=path.join(current,part);assert.equal(lstatSync(current).isSymbolicLink(),false);
  }
  return file;
}
const manifest=JSON.parse(readFileSync(path.join(assetRoot,'manifest.json'),'utf8'));
function readGlb(bytes) {
  assert.equal(bytes.toString('ascii',0,4),'glTF');assert.equal(bytes.readUInt32LE(4),2);
  assert.equal(bytes.readUInt32LE(8),bytes.length,'GLB byte length must be complete');
  let json,bin;const types=[];
  for(let offset=12;offset<bytes.length;){
    assert.ok(offset+8<=bytes.length);const length=bytes.readUInt32LE(offset),type=bytes.readUInt32LE(offset+4);
    assert.equal(length%4,0);assert.ok(offset+8+length<=bytes.length);types.push(type);
    const chunk=bytes.subarray(offset+8,offset+8+length);
    if(type===0x4e4f534a){assert.equal(json,undefined);json=JSON.parse(chunk.toString('utf8'));}
    if(type===0x004e4942){assert.equal(bin,undefined);bin=chunk;}
    offset+=8+length;
  }
  assert.equal(types[0],0x4e4f534a);assert.ok(json&&bin);assert.equal(json.asset.version,'2.0');
  assert.equal(json.buffers.length,1);assert.equal(json.buffers[0].uri,undefined);
  assert.ok(json.buffers[0].byteLength<=bin.length&&bin.length-json.buffers[0].byteLength<4);
  return {json,bin};
}
const component={5121:[1,'readUInt8'],5123:[2,'readUInt16LE'],5125:[4,'readUInt32LE'],5126:[4,'readFloatLE']};
const widths={SCALAR:1,VEC2:2,VEC3:3,VEC4:4};
function accessor(gltf,index) {
  const a=gltf.json.accessors[index],v=gltf.json.bufferViews[a.bufferView];assert.ok(a&&v);
  assert.equal(a.sparse,undefined);assert.equal(v.buffer,0);assert.ok(component[a.componentType]);
  const [bytes,reader]=component[a.componentType],width=widths[a.type],stride=v.byteStride??bytes*width;
  assert.ok(width>0&&a.count>0&&stride>=bytes*width);
  const start=(v.byteOffset??0)+(a.byteOffset??0),end=start+(a.count-1)*stride+width*bytes;
  assert.ok(end<=(v.byteOffset??0)+v.byteLength&&end<=gltf.bin.length);
  return Array.from({length:a.count},(_,i)=>Array.from({length:width},(_,j)=>gltf.bin[reader](start+i*stride+j*bytes)));
}
function geometry(gltf) {
  const parents=new Map();gltf.json.nodes.forEach((n,i)=>(n.children??[]).forEach(c=>{assert.ok(!parents.has(c));parents.set(c,i);}));
  const local=n=>n.matrix?new Matrix4().fromArray(n.matrix):new Matrix4().compose(new Vector3(...n.translation??[0,0,0]),new Quaternion(...n.rotation??[0,0,0,1]),new Vector3(...n.scale??[1,1,1]));
  const world=(i,seen=new Set())=>{assert.ok(!seen.has(i),'node tree has a cycle');seen.add(i);return parents.has(i)?world(parents.get(i),seen).multiply(local(gltf.json.nodes[i])):local(gltf.json.nodes[i]);};
  let triangles=0,nondegenerate=0;const points=[];
  gltf.json.nodes.forEach((node,i)=>{
    if(node.mesh===undefined)return;const matrix=world(i);
    for(const p of gltf.json.meshes[node.mesh].primitives){
      assert.equal(p.mode??4,4);assert.equal(gltf.json.accessors[p.attributes.POSITION].type,'VEC3');
      const vertices=accessor(gltf,p.attributes.POSITION).map(v=>{assert.ok(v.every(Number.isFinite));return new Vector3(...v).applyMatrix4(matrix);});points.push(...vertices);
      const indices=p.indices===undefined?vertices.map((_,i)=>i):accessor(gltf,p.indices).map(v=>v[0]);
      assert.equal(indices.length%3,0);assert.ok(indices.every(i=>Number.isInteger(i)&&i>=0&&i<vertices.length));triangles+=indices.length/3;
      for(let k=0;k<indices.length;k+=3){const [a,b,c]=indices.slice(k,k+3).map(i=>vertices[i]);if(new Vector3().subVectors(b,a).cross(new Vector3().subVectors(c,a)).lengthSq()>1e-16)nondegenerate++;}
    }
  });
  return {points,triangles,nondegenerate,bounds:new Box3().setFromPoints(points),world};
}

test('response manifest maps exactly four modelled CC0 vehicle types',()=>{
  assert.deepEqual(Object.keys(manifest.vehicles).sort(),['ambulance','fire_engine','police','rescue_boat']);
  assert.match(manifest.purpose,/not observed/i);assert.match(manifest.display_note,/display assumptions/i);
});
for(const [kind,item] of Object.entries(manifest.vehicles)){
  test(`${kind}: recorded local GLB and palette are intact without external dependencies`,()=>{
    const bytes=readFileSync(localResource(item.url));
    const pack=manifest.packs.find(p=>p.models.some(m=>m.url===item.url));assert.ok(pack);assert.equal(pack.creator,'Kenney');assert.equal(pack.license,'CC0 1.0');
    const record=pack.models.find(m=>m.url===item.url);assert.equal(bytes.length,record.bytes);assert.equal(createHash('sha256').update(bytes).digest('hex'),record.sha256);
    const gltf=readGlb(bytes);assert.ok(gltf.json.images.length>0);
    for(const image of gltf.json.images){
      const texture=readFileSync(localResource(image.uri,item.url));assert.ok(texture.length>32);
      assert.equal(texture.subarray(0,8).toString('hex'),'89504e470d0a1a0a','packaged palette must be a PNG');
      assert.ok(texture.readUInt32BE(16)>0&&texture.readUInt32BE(20)>0);
    }
  });
  test(`${kind}: actual 3D mesh has a forward +Z body and consistent dimensions`,()=>{
    const gltf=readGlb(readFileSync(localResource(item.url))),mesh=geometry(gltf),size=mesh.bounds.getSize(new Vector3());
    assert.ok(mesh.points.length>=200&&mesh.triangles>=100&&mesh.nondegenerate>=100,'requires vehicle mesh geometry, not a trivial marker');
    assert.ok(size.x>0.5&&size.y>0.5&&size.z>size.x&&size.z>size.y,'vehicle long axis is Z and up is Y');
    assert.ok(Math.abs(mesh.bounds.min.y)<1e-5,'wheels/hull rest at local ground');
    assert.ok(item.length_m>0&&Number.isFinite(item.length_m));assert.equal(item.rotation_y,0);
    if(kind==='rescue_boat'){
      const z=mesh.bounds.max.z*.65,front=new Box3().setFromPoints(mesh.points.filter(p=>p.z>z)),back=new Box3().setFromPoints(mesh.points.filter(p=>p.z< -z));
      assert.ok(front.max.x-front.min.x<(back.max.x-back.min.x)*.6,'bow narrows toward positive Z');
    }else{
      const positions=name=>gltf.json.nodes.map((n,i)=>n.name.includes(name)?new Vector3().setFromMatrixPosition(mesh.world(i)).z:null).filter(z=>z!==null);
      const front=positions('wheel-front'),back=positions('wheel-back');assert.equal(front.length,2);assert.equal(back.length,2);
      assert.ok(Math.min(...front)>Math.max(...back),'front wheels are toward positive Z');
    }
  });
}
