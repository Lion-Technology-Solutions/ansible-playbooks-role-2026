"""Private Vercel Queue subscribers. Payloads contain database IDs, never credentials."""
import asyncio
from datetime import timedelta
from vercel.queue import Message, send, subscribe

TOPIC='liontech-class-updates'


def publish(payload):
    # Flask's request handler is synchronous; all external delivery happens in subscribers.
    return asyncio.run(send(TOPIC,payload,idempotency_key='request-'+payload['run'],retention=timedelta(days=7)))


def _invoke(operation,*args):
    from app import app
    with app.test_request_context('/internal/integration-worker',method='GET'):
        app.preprocess_request()
        return app.extensions['integrations'][operation](*args)


@subscribe(topic=TOPIC,consumer_group='liontech-integration-delivery',retry_after=60,max_concurrency=8,max_attempts=5)
async def deliver(message:Message[dict[str,object]]) -> None:
    payload=message.payload;kind=payload.get('kind')
    if kind=='probe':
        await asyncio.to_thread(_invoke,'worker_probe');return
    if kind=='delivery':
        identity=payload.get('id')
        if not isinstance(identity,int) or identity<1:raise ValueError('Invalid delivery identifier.')
        await asyncio.to_thread(_invoke,'process_delivery',identity);return
    if kind!='drain':raise ValueError('Unsupported integration queue message.')
    cursor=payload.get('cursor',0);run=payload.get('run')
    if not isinstance(cursor,int) or cursor<0 or not isinstance(run,str) or len(run)>100:raise ValueError('Invalid integration batch.')
    identities=await asyncio.to_thread(_invoke,'delivery_batch',cursor,30)
    for start in range(0,len(identities),8):
        await asyncio.gather(*(send(TOPIC,{'kind':'delivery','id':identity},idempotency_key=f'delivery-{run}-{identity}',retention=timedelta(days=7)) for identity in identities[start:start+8]))
    if len(identities)==30:
        await send(TOPIC,{'kind':'drain','cursor':identities[-1],'run':run},idempotency_key=f'batch-{run}-{identities[-1]}',retention=timedelta(days=7))
