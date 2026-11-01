"""Continue the current fresh proof after an explicit resource-limited restart."""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
# Set the numeric library limits before importing the scientific runtime.
for name in ('OMP_NUM_THREADS','OMP_THREAD_LIMIT','OPENBLAS_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
    os.environ[name]='2'
if os.getpriority(os.PRIO_PROCESS,0)<15:os.setpriority(os.PRIO_PROCESS,0,15)
sys.path.insert(0,str(Path(__file__).absolute().parents[1]))
from sim import drone_proof as proof
from sim.jobs import update


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',required=True);parser.add_argument('--archive-interrupted-sparse',action='store_true');parser.add_argument('--archive-interrupted-dense',action='store_true');args=parser.parse_args()
    run,config=proof.load_run(args.run)
    if args.archive_interrupted_sparse:print(proof.archive_interrupted_sparse(run),flush=True)
    if args.archive_interrupted_dense:print(proof.archive_interrupted_dense(run),flush=True)
    record=proof.read_json(run/'proof.json');record['resource_policy']={'max_threads':2,'nice':15,'gpu':False};proof.write_json(run/'proof.json',record);proof.write_json(proof.BASE/'status.json',record)
    for stage,cli,tools in [('sparse','sparse',['colmap']),('gps_alignment','align',['colmap']),('dense','dense',['colmap','InterfaceCOLMAP','DensifyPointCloud']),('ndsm','rasterize',['pdal']),('validation','validate',[])]:
        if proof.read_json(run/'proof.json')['steps'][stage].get('available'):continue
        while any(proof.executable(name) is None for name in tools):update('job_c_run','Waiting for native tools before '+stage,status='running');time.sleep(15)
        proof.load_run(run);update('job_c_run','Computing '+stage+' with at most two threads, nice15; current fresh run '+run.name,status='running')
        subprocess.run([sys.executable,'-B','-m','sim.drone_proof',cli,'--run',str(run)],check=True,env=proof.bounded_environment())
    update('job_c_run','Fresh reconstruction and independent LiDAR comparison completed at two-thread limit',status='running')
if __name__=='__main__':main()
