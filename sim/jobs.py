"""Duplicate-safe nohup jobs with durable local status; no environment-file loading."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).absolute().parents[1]
LOGS=ROOT/'logs'
IST=timezone(timedelta(hours=5,minutes=30))

def stamp():return datetime.now(IST).isoformat(timespec='seconds')
def validate(name):
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,48}',name) or 'env' in name:raise ValueError('Invalid job name')
    return name

@contextmanager
def serialized(name):
    """Serialize a complete admission transaction across threads and processes."""
    validate(name);LOGS.mkdir(exist_ok=True)
    with (LOGS/f'{name}.admission.lock').open('a') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX)
        yield

@contextmanager
def registry():
    LOGS.mkdir(exist_ok=True)
    with (LOGS/'status.lock').open('a') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX)
        path=LOGS/'jobs.json'
        values=json.loads(path.read_text()) if path.is_file() else {}
        yield values
        temp=path.with_suffix('.tmp');temp.write_text(json.dumps(values,indent=2)+'\n');temp.replace(path)
        lines=['# Otec background jobs','',f'Updated: {stamp()} (IST)','', '| Job | Status | PID | Updated | Detail | Log |','|---|---|---:|---|---|---|']
        for name,item in sorted(values.items()):
            detail=str(item.get('detail','')).replace('|','/').replace('\n',' ')
            lines.append(f"| {name} | {item['status']} | {item.get('pid','—')} | {item.get('updated_at','')} | {detail} | [{name}.log]({name}.log) |")
        temp=LOGS/'STATUS.tmp';temp.write_text('\n'.join(lines)+'\n');temp.replace(LOGS/'STATUS.md')

def update(name,detail,status=None,**extra):
    validate(name)
    with registry() as values:
        entry=values.setdefault(name,{'status':'pending'})
        entry.update(extra,detail=detail,updated_at=stamp())
        if status:entry['status']=status

def state(name=None):
    path=LOGS/'jobs.json';values=json.loads(path.read_text()) if path.is_file() else {}
    return values.get(name) if name else values

def active(name):
    validate(name);LOGS.mkdir(exist_ok=True)
    with (LOGS/f'{name}.lock').open('a') as handle:
        try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return True
        finally:
            try:fcntl.flock(handle,fcntl.LOCK_UN)
            except OSError:pass
    record=state(name) or {}
    if record.get('status')=='starting' and time.time()-record.get('launched_epoch',0)<30:
        try:os.kill(record['pid'],0);return True
        except (ProcessLookupError,KeyError):pass
    return False

def reconciled_state(name):
    """Report interrupted jobs instead of leaving clients polling forever."""
    validate(name);LOGS.mkdir(exist_ok=True)
    with (LOGS/f'{name}.launch.lock').open('a') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX)
        current=state(name) or {}
        if current.get('status') not in ('starting','running') or active(name):return current
        with registry() as values:
            current=values.get(name,{})
            if current.get('status') in ('starting','running'):
                current.update(status='failed',detail='Job interrupted before completion; retry to resume.',updated_at=stamp())
            return current.copy()

def launch(name,command,detail='Starting',force=False):
    validate(name);LOGS.mkdir(exist_ok=True)
    with (LOGS/f'{name}.launch.lock').open('a') as launch_lock:
        fcntl.flock(launch_lock,fcntl.LOCK_EX)
        if active(name):return {'job':name,'launched':False,'reason':'already running',**(state(name) or {})}
        old=state(name) or {}
        if old.get('status')=='complete' and not force:return {'job':name,'launched':False,'reason':'already complete',**old}
        with (LOGS/f'{name}.log').open('a',buffering=1) as log:
            log.write(f'\n[{stamp()}] New authorized job attempt: {name}\n')
            with registry() as values:
                child=subprocess.Popen(['nohup',sys.executable,'-u','-m','sim.jobs','worker',name,'--',*command],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                values[name]={'status':'starting','pid':child.pid,'started_at':stamp(),'updated_at':stamp(),'detail':detail,'launched_epoch':time.time()}
        return {'job':name,'launched':True,'pid':child.pid,'log':str(LOGS/f'{name}.log')}

def worker(name,command):
    validate(name)
    with (LOGS/f'{name}.lock').open('a') as handle:
        try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print('Duplicate worker refused.',flush=True);return 0
        update(name,'Running',status='running',pid=os.getpid())
        stop=threading.Event()
        def heartbeat():
            while not stop.wait(30):
                with registry() as values:
                    if name in values:values[name]['heartbeat_at']=stamp()
        thread=threading.Thread(target=heartbeat,daemon=True);thread.start()
        started=time.monotonic()
        try:
            # The computation retains the flock even if its supervising worker
            # dies. A retry must not start a duplicate while that child runs.
            child=subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,pass_fds=(handle.fileno(),))
            update(name,'Running',child_pid=child.pid)
            code=child.wait()
        except Exception as exc:
            print(f'Job could not execute ({type(exc).__name__}).',flush=True);code=1
        finally:stop.set();thread.join(timeout=1)
        previous=state(name) or {};terminal='complete' if code==0 else 'failed'
        if previous.get('status') in ('blocked','needs_input') and code:terminal=previous['status']
        detail=previous.get('detail','')
        if detail in ('Running','Starting'):detail='Finished' if code==0 else 'Failed; inspect log'
        update(name,detail,status=terminal,exit_code=code,elapsed_s=round(time.monotonic()-started,3),finished_at=stamp())
        print(f'[{stamp()}] {name}: {terminal}; exit={code}; elapsed={time.monotonic()-started:.1f}s',flush=True)
        return code

def main():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='action',required=True)
    for action in ('start','worker'):
        p=sub.add_parser(action);p.add_argument('name');p.add_argument('command',nargs=argparse.REMAINDER)
    p=sub.add_parser('status');p.add_argument('name',nargs='?')
    args=parser.parse_args()
    if args.action=='status':print(json.dumps(state(args.name),indent=2));return 0
    command=args.command[1:] if args.command[:1]==['--'] else args.command
    if not command:parser.error('A command is required after --')
    if args.action=='start':print(json.dumps(launch(args.name,command),indent=2));return 0
    return worker(args.name,command)

if __name__=='__main__':raise SystemExit(main())
