"""Stage an explicit, secret-free build context and verified cached demo assets."""
import argparse
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import shutil
import subprocess

from sim import product, drone_proof
from sim.config import ROOT, DERIVED


def stage(destination):
    destination=Path(destination).absolute()
    if destination.exists():raise ValueError('Use a new staging directory')
    destination.mkdir(parents=True)
    copied=[]
    def copy(relative, runtime=False):
        source=ROOT/relative
        if any('env' in part.lower() for part in source.parts) or any(p.is_symlink() for p in (source,*source.parents)):
            raise ValueError('Protected or linked source is excluded from deployment')
        target=destination/('runtime' if runtime else '')/relative
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target);copied.append((str(relative),source.stat().st_size))
    for path in ('Dockerfile','pyproject.toml','uv.lock','web/package.json','web/package-lock.json','web/tsconfig.json','web/vite.config.ts','web/index.html'):
        copy(path)
    for root in ('sim','web/src','web/scenes','web/public/assets/vehicles','web/public/data'):
        paths=subprocess.check_output(['rg','--files','--hidden','--no-ignore',root,'-g','!**/*env*','-g','!**/__pycache__/**','-g','!**/*.pyc','-g','!**/.git/**'],cwd=ROOT,text=True).splitlines()
        for path in paths:copy(path,runtime=root=='web/public/data')
    derived=('ground_10m.tif','surface_10m.tif','aoi_mask.tif','building_mask.tif','embankment_mask.tif','provenance.json','osm_water.geojson','osm_roads.geojson','what_if_scenarios.json','relative_scenarios.json','stage_2023.csv','datum_offset.json','s1_meta.json','s1_water_before.tif','s1_water_peak.tif','current_observations.json','gemini_config.json','google_api_check.json','region_packs.json')
    for name in derived:
        source=DERIVED/name
        if source.is_symlink():raise ValueError('Linked derived data is excluded')
        if source.is_file():copy(Path('data/derived')/name,runtime=True)
    for root in ('runs/product','runs/relative_river_plus_1m','runs/relative_river_plus_2m','runs/relative_river_plus_3m','runs/relative_breach_near_ito','runs/relative_debris_ring_road','runs/replay2023'):
        if not (ROOT/root).is_dir():continue
        paths=subprocess.check_output(['rg','--files','--no-ignore',root,'-g','*.json','-g','*.tif','-g','!**/*env*'],cwd=ROOT,text=True).splitlines()
        for path in paths:copy(path,runtime=True)
    drone=drone_proof.get_status()
    for value in drone.get('assets',{}).values():
        for relative in value if isinstance(value,list) else [value]:
            path=ROOT/relative
            if path.is_file():copy(Path(relative),runtime=True)
            if relative.endswith(('_ndsm_0p5m.json',)) and path.with_suffix('.bin').is_file():
                copy(Path(relative).with_suffix('.bin'),runtime=True)
    if drone.get('metrics') and (ROOT/drone['metrics']).is_file():copy(Path(drone['metrics']),runtime=True)
    snapshot={'schema_version':1,'created_at':datetime.now(timezone.utc).isoformat(),'mode':'immutable verified precomputed demo',
              'product_catalog':product.catalog(),'drone_status':drone}
    product.write(destination/'runtime/data/derived/deployment_snapshot.json',snapshot)
    # This staging tree contains only the explicit copies above. No automatic
    # gitignore, repository traversal or credentials are used by the upload.
    (destination/'.gcloudignore').write_text('.gcloudignore\n')
    (destination/'.dockerignore').write_text('**/__pycache__/**\n**/*.pyc\n**/*env*\n**/.git/**\n')
    receipt={'staging_directory':str(destination),'files':len(copied),'bytes':sum(size for _,size in copied),
             'created_at':snapshot['created_at'],'cached_runs':len(snapshot['product_catalog']['runs'])}
    product.write(destination/'staging-receipt.json',receipt)
    return receipt


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('destination');args=parser.parse_args()
    print(stage(args.destination))
