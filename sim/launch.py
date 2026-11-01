"""Single foreground command: build once, serve viewer and API, stop on Ctrl-C."""
import argparse
import os
from pathlib import Path
import shutil
import socket
import subprocess

from sim.config import ROOT


def main(argv=None):
    parser = argparse.ArgumentParser(description='Otec: one local server, no background simulations or downloads')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=5173)
    parser.add_argument('--no-build', action='store_true', help='Use an existing production build, e.g. in the server image')
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('Port must be between 1 and 65535')
    with socket.socket() as probe:
        try:
            probe.bind((args.host, args.port))
        except OSError:
            parser.exit(2, f'Port {args.port} is already in use. Stop the existing Otec command or choose --port.\n')
    for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS'):
        os.environ.setdefault(key, '2')
    os.environ['OTEC_ENABLE_LOCAL_COMPUTE'] = '0'
    os.environ['OTEC_WATCH_EXPORTS'] = '0'
    if not args.no_build:
        npm = shutil.which('npm')
        if not npm:
            parser.exit(2, 'Node.js/npm is required for the viewer build.\n')
        if not (ROOT/'web/node_modules/three/package.json').is_file():
            subprocess.run([npm, '--prefix', str(ROOT/'web'), 'ci', '--no-audit', '--no-fund'], check=True)
        subprocess.run([npm, '--prefix', str(ROOT/'web'), 'run', 'build'], check=True)
    if not (ROOT/'web/dist/index.html').is_file():
        parser.exit(2, 'No production viewer build is available. Run make run without --no-build.\n')
    print(f'\nOtec → http://{args.host}:{args.port}\nOne foreground server. Ctrl-C stops it. Background compute and downloads are off.\n', flush=True)
    import uvicorn
    uvicorn.run('sim.app:app', host=args.host, port=args.port, access_log=False, log_level='warning')


if __name__ == '__main__':
    main()
