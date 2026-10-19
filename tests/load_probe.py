"""Bounded localhost HTTP load probe using synthetic data and a disposable DB.
Run: python tests/load_probe.py --users 25 --requests 250
This measures this machine and fixture, not production capacity.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor
import requests
from waitress import create_server
from test_flows import Flows, backend


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--users',type=int,default=25)
    parser.add_argument('--requests',type=int,default=250)
    args=parser.parse_args()
    if not 1 <= args.users <= 100 or not 1 <= args.requests <= 2000:
        parser.error('Use 1-100 users and 1-2000 requests.')
    Flows.setUpClass()
    server=None
    try:
        fixture=Flows()
        fixture.setUp()
        fixture.session()
        users=[fixture.learner]+[fixture.user() for _ in range(args.users-1)]
        for uid in users:
            fixture.sql("INSERT INTO messages(sender_id,receiver_id,content) VALUES (%s,%s,'Synthetic message')",(fixture.mentor,uid))
        server=create_server(backend.app,host='127.0.0.1',port=0,threads=8,connection_limit=150)
        thread=threading.Thread(target=server.run,daemon=True)
        thread.start()
        url=f'http://127.0.0.1:{server.effective_port}/api/app'
        def fetch(i):
            started=time.perf_counter()
            try:
                response=requests.get(url,headers={'X-GotTime-Auth':backend.make_auth_token(users[i % len(users)])},timeout=30)
                body=response.json()
                ok=response.status_code==200 and body.get('user',{}).get('id')==users[i % len(users)]
                return time.perf_counter()-started,ok,response.status_code
            except requests.RequestException:
                return time.perf_counter()-started,False,0
        fetch(0)
        start=time.perf_counter()
        with ThreadPoolExecutor(max_workers=args.users) as pool:
            results=list(pool.map(fetch,range(args.requests)))
        elapsed=time.perf_counter()-start
        durations=sorted(r[0] for r in results)
        print(json.dumps({'concurrent_clients':args.users,'requests':args.requests,'successful':sum(r[1] for r in results),
            'errors':sum(not r[1] for r in results),'seconds':round(elapsed,2),'requests_per_second':round(args.requests/elapsed,2),
            'p50_ms':round(durations[len(durations)//2]*1000,1),'p95_ms':round(durations[min(len(durations)-1,int(len(durations)*.95))]*1000,1)},indent=2))
    finally:
        if server:
            server.task_dispatcher.shutdown()
            server.close()
        Flows.tearDownClass()

if __name__=='__main__': main()
