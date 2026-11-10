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


def _invoke_security(identity):
    from app import app
    with app.test_request_context('/internal/security-worker',method='GET'):
        app.preprocess_request()
        return app.extensions['auth_security']['process_mail'](identity)


def _invoke_lifecycle(identity):
    from app import app
    with app.test_request_context('/internal/notification-worker',method='GET'):
        app.preprocess_request()
        return app.extensions['notifications']['process'](identity)


@subscribe(topic=TOPIC,consumer_group='liontech-integration-delivery',retry_after=60,max_concurrency=8,max_attempts=5)
async def deliver(message:Message[dict[str,object]]) -> None:
    payload=message.payload;kind=payload.get('kind')
    if kind=='lifecycle_batch':
        identities=payload.get('ids')
        if not isinstance(identities,list) or not 1<=len(identities)<=30 or any(not isinstance(value,str) or len(value)!=32 or any(c not in '0123456789abcdef' for c in value) for value in identities):raise ValueError('Invalid notification batch.')
        for start in range(0,len(identities),8):
            await asyncio.gather(*(asyncio.to_thread(_invoke_lifecycle,value) for value in identities[start:start+8]))
        return
    if kind=='lifecycle':
        identity=payload.get('id')
        if not isinstance(identity,str) or len(identity)!=32 or any(c not in '0123456789abcdef' for c in identity):raise ValueError('Invalid notification identifier.')
        await asyncio.to_thread(_invoke_lifecycle,identity);return
    if kind=='security_email':
        identity=payload.get('id')
        if not isinstance(identity,str) or len(identity)!=32 or any(c not in '0123456789abcdef' for c in identity):raise ValueError('Invalid security mail identifier.')
        await asyncio.to_thread(_invoke_security,identity);return
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
