import asyncio
import json
import socket
from nf_lab.h2client import request
from nf_lab.live import start_server, body_of, respond, run_live


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1',0));return s.getsockname()[1]


def test_live_pipeline_against_protocol_fixture(tmp_path):
    """Fixture checks Python integration; it is NOT an Open5GS conformance test."""
    async def run():
        nrf_port,scp_port,gateway_port=free_port(),free_port(),free_port()
        # Reserve a free consecutive range for the three surrogate AF endpoints.
        base=free_port()
        while base>65000:
            base=free_port()
        instances={}
        async def fixture(scope,receive,send):
            if scope['type']!='http':return
            body=await body_of(receive)
            headers={k.decode():v.decode() for k,v in scope['headers']}
            if '3gpp-sbi-target-apiroot' in headers:
                root=headers.pop('3gpp-sbi-target-apiroot')
                query=scope.get('query_string',b'').decode()
                r=await request(root+scope['path']+('?' + query if query else ''),scope['method'],headers,body)
                await send({'type':'http.response.start','status':r.status,'headers':[(b'content-type',b'application/json')]})
                await send({'type':'http.response.body','body':r.body});return
            method=scope['method'];nf=scope['path'].split('/')[-1]
            if method=='PUT':instances[nf]=json.loads(body);await respond(send,201,{**instances[nf],'heartBeatTimer':1})
            elif method=='PATCH':await respond(send,200,instances[nf])
            elif method=='DELETE':instances.pop(nf);await respond(send,204,{})
            else:await respond(send,200,{'nfInstances':list(instances.values())})
        stop=asyncio.Event()
        nrf=f'http://127.0.0.1:{nrf_port}';scp=f'http://127.0.0.1:{scp_port}'
        tasks=[asyncio.create_task(start_server(fixture,u,stop)) for u in [nrf,scp]]
        try:
            await asyncio.sleep(.2)
            cfg=dict(nrf=nrf,scp=scp,gateway=f'http://127.0.0.1:{gateway_port}',producer_host='127.0.0.1',producer_base_port=base,peers=3,windows=2,window_seconds=2,attack_start=1,attack_end=2,rate_per_nf=2,max_rate_per_nf=20,timeout=1)
            rows=await run_live(cfg,'falsified-report',tmp_path/'live',7)
            assert len(rows)==6
            assert all(r['observation_complete'] and not r['kie_failed'] for r in rows)
            changed=[r for r in rows if r['label_falsification']]
            assert len(changed)==1 and changed[0]['rep_count']<changed[0]['obs_count']
            assert not instances
            assert (tmp_path/'live'/'completion.json').exists()
        finally:
            stop.set();await asyncio.gather(*tasks)
    asyncio.run(run())
