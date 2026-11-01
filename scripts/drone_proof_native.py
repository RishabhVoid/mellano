"""Install official native OpenMVS assets locally; no protected-file discovery."""
from pathlib import Path,PurePosixPath
import hashlib
import json
import os
import stat
import subprocess
import sys
import zipfile
import requests
sys.path.insert(0,str(Path(__file__).absolute().parents[1]))
from sim.jobs import update
from sim.drone_proof import ROOT,write_json
URL='https://github.com/cdcseacave/openMVS/releases/download/v2.4.0/OpenMVS_macOS_arm64.zip'
SHA='3d4c616c97031b1ab6e2eecb0ddd5614fb99513c0782a32c9350602faf38799b'
BASE=ROOT/'data'/'raw'/'drone_proof'/'tools'/'openmvs-2.4.0'
def main():
    BASE.mkdir(parents=True,exist_ok=True);archive=BASE.parent/'OpenMVS_macOS_arm64.zip'
    update('job_c_openmvs','Downloading official OpenMVS v2.4.0 Apple Silicon release; conda-forge endpoint has no package',status='running')
    if not archive.is_file():
        with requests.get(URL,stream=True,timeout=(30,180)) as response:
            response.raise_for_status()
            temporary=archive.with_suffix('.part')
            with temporary.open('wb') as output:
                for chunk in response.iter_content(1024*1024):output.write(chunk)
            temporary.replace(archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=SHA:raise ValueError('Official release digest mismatch')
    programs={}
    with zipfile.ZipFile(archive) as package:
        for member in package.infolist():
            path=PurePosixPath(member.filename)
            if any('env' in part.lower() for part in path.parts):continue
            if path.is_absolute() or '..' in path.parts:raise ValueError('Unsafe release member')
            if member.is_dir():continue
            mode=member.external_attr>>16
            if stat.S_ISLNK(mode):
                # No symlink is installed or followed from a release archive.
                continue
            destination=BASE.joinpath(*path.parts);destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_bytes(package.read(member))
            if path.name in ('InterfaceCOLMAP','DensifyPointCloud','ReconstructMesh','RefineMesh','TextureMesh'):
                destination.chmod(0o755);programs[path.name]=str(destination)
    if not all(name in programs for name in ('InterfaceCOLMAP','DensifyPointCloud')):raise ValueError('Official package has no expected executables')
    write_json(BASE/'tooling.json',{'source_url':URL,'version':'2.4.0','sha256':SHA,'programs':programs,'use':'CPU dense reconstruction of newly computed COLMAP model'})
    for name in ('InterfaceCOLMAP','DensifyPointCloud'):
        result=subprocess.run([programs[name],'--help'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,cwd=BASE)
        (BASE/(name+'_help.txt')).write_text(result.stdout)
        print(name,'help exit',result.returncode,flush=True)
        if result.returncode not in (0,1):print(result.stdout[-2500:],flush=True)
    update('job_c_openmvs','Official OpenMVS binaries downloaded and digest verified; native dependency/link check recorded',status='running')
if __name__=='__main__':main()
