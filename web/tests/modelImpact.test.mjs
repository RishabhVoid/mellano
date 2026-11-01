import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { stripTypeScriptTypes } from 'node:module';
import * as THREE from 'three';

// Run the actual render methods without constructing a WebGLRenderer or loading AOI data.
const source=await readFile(new URL('../src/model.ts',import.meta.url),'utf8');
const severity=source.slice(source.indexOf('  setSeverity('),source.indexOf('  nearestEmbankment('));
const visibility=source.slice(source.indexOf('  setImpactVisible('),source.indexOf('  setBuildingStates('));
const compiled=stripTypeScriptTypes(`class RenderHarness {${severity}${visibility}}`,{mode:'transform'});
const RenderHarness=new Function('THREE',`${compiled};return RenderHarness;`)(THREE);
function fixture(){
  const render=new RenderHarness();render.impactBlend={value:.88};render.hasTerrainSeverity=false;render.water={children:[{material:{opacity:.72}}]};render.terrainLod={setImpact(_material,visible){this.visible=visible;},setImpactVisible(visible){this.visible=visible;}};return render;
}
test('exact zero and unknown severity stay transparent while nonzero bands remain visible',()=>{
  const render=fixture();render.setSeverity(Uint8Array.of(0,1,2,3,4,255),6,1);
  assert.deepEqual([...render.impactTexture.image.data].filter((_,i)=>i%4===3),[0,155,155,155,155,0]);
  assert.equal(render.terrainLod.visible,true);render.impactTexture.dispose();render.impactSurface.geometry.dispose();render.impactSurface.material.dispose();
});
test('turning on building impact cannot resurrect a cleared terrain overlay or fade blue water',()=>{
  const render=fixture();render.setSeverity(Uint8Array.of(4),1,1);render.setImpactVisible(true);assert.equal(render.water.children[0].material.opacity,.32);
  render.setSeverity();render.setImpactVisible(true);assert.equal(render.terrainLod.visible,false);assert.equal(render.impactSurface.visible,false);assert.equal(render.water.children[0].material.opacity,.72);assert.equal(render.impactBlend.value,.88);
  render.impactTexture.dispose();render.impactSurface.geometry.dispose();render.impactSurface.material.dispose();
});
