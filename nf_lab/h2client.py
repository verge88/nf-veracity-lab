"""HTTP/2 prior-knowledge h2c client (httpx does not offer h2c prior knowledge).

One bounded request per connection, for reproducible small laboratory loads.
"""
import asyncio
import json
from dataclasses import dataclass
from urllib.parse import urlsplit
import h2.config
import h2.connection
import h2.events


@dataclass
class Response:
    status: int
    headers: dict
    body: bytes
    bytes_sent: int
    bytes_received: int

    def json(self):
        return json.loads(self.body)


async def request(url, method='GET', headers=None, body=b'', timeout=3):
    u=urlsplit(url)
    if u.scheme != 'http':
        raise ValueError('This isolated lab transport supports h2c only; TLS needs a separately validated deployment')
    if len(body)>32768:
        raise ValueError('Lab body limit: 32 KiB')
    async def exchange():
        reader,writer=await asyncio.open_connection(u.hostname,u.port or 80)
        conn=h2.connection.H2Connection(config=h2.config.H2Configuration(client_side=True,header_encoding='utf-8'))
        sent=received=0
        try:
            conn.initiate_connection()
            fields=[(':method',method),(':scheme','http'),(':authority',u.netloc),(':path',u.path + ('?'+u.query if u.query else '') or '/')]
            supplied={k.lower():str(v) for k,v in (headers or {}).items()}
            # Open5GS SCP derives requester NF type from the User-Agent prefix.
            # These experimental clients are registered as AF instances.
            supplied.setdefault('user-agent','AF-nf-veracity-lab')
            fields += [(k,v) for k,v in supplied.items() if not k.startswith(':') and k not in ['host','connection','transfer-encoding','content-length']]
            fields.append(('content-length',str(len(body))))
            conn.send_headers(1,fields,end_stream=not body)
            for p in range(0,len(body),16384):
                conn.send_data(1,body[p:p+16384],end_stream=p+16384>=len(body))
            data=conn.data_to_send(); sent+=len(data); writer.write(data); await writer.drain()
            response_headers={}; chunks=[]; size=0
            while True:
                data=await reader.read(65536)
                if not data: raise ConnectionError('HTTP/2 stream ended before response')
                received+=len(data)
                done=False
                for event in conn.receive_data(data):
                    if isinstance(event,h2.events.ResponseReceived): response_headers.update(event.headers)
                    elif isinstance(event,h2.events.DataReceived):
                        size+=len(event.data)
                        if size>2**20: raise ValueError('Response exceeds 1 MiB lab limit')
                        chunks.append(event.data);conn.acknowledge_received_data(event.flow_controlled_length,event.stream_id)
                    elif isinstance(event,h2.events.StreamEnded): done=True
                    elif isinstance(event,(h2.events.StreamReset,h2.events.ConnectionTerminated)):
                        raise ConnectionError(str(event))
                pending=conn.data_to_send()
                if pending: sent+=len(pending);writer.write(pending);await writer.drain()
                if done: return Response(int(response_headers[':status']),response_headers,b''.join(chunks),sent,received)
        finally:
            writer.close()
            await writer.wait_closed()
    return await asyncio.wait_for(exchange(),timeout)
