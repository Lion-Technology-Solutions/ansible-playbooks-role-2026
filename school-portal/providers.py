"""Small server-only adapters. Credentials are read only from runtime configuration."""
import base64
import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

ORIGIN='https://www.liontechacademy.com'
LEGACY_ORIGIN='https://liontech-student-portal.vercel.app'

def setting(key,default=''):
    from integrations import credential
    return credential(key,default)



def api(url,method='GET',payload=None,headers=None,form=False):
    body=(urlencode(payload).encode() if form else json.dumps(payload).encode()) if payload is not None else None
    values={'Content-Type':'application/x-www-form-urlencoded' if form else 'application/json',**(headers or {})}
    with urlopen(Request(url,data=body,headers=values,method=method),timeout=15) as response:
        data=response.read(2000000)
    return json.loads(data) if data else {}


def stripe_request(path,payload=None,idempotency=None):
    headers={'Authorization':'Bearer '+setting('STRIPE_SECRET_KEY')}
    if idempotency:headers['Idempotency-Key']=idempotency
    return api('https://api.stripe.com/v1/'+path,'POST' if payload else 'GET',payload,headers,form=True)


def signed_stripe_event(raw,signature,secret):
    try:
        parts=[item.split('=',1) for item in signature.split(',')]
        stamp=next(value for key,value in parts if key=='t')
        if abs(time.time()-int(stamp))>300:raise ValueError()
        expected=hmac.new(secret.encode(),stamp.encode()+b'.'+raw,hashlib.sha256).hexdigest()
        if not any(key=='v1' and hmac.compare_digest(value,expected) for key,value in parts):raise ValueError()
        return json.loads(raw)
    except (StopIteration,ValueError,TypeError,KeyError):raise ValueError('Invalid payment event signature.')


def zoom_request(method,path,payload):
    basic=base64.b64encode((setting('ZOOM_CLIENT_ID')+':'+setting('ZOOM_CLIENT_SECRET')).encode()).decode()
    token=api('https://zoom.us/oauth/token','POST',{'grant_type':'account_credentials','account_id':setting('ZOOM_ACCOUNT_ID')},{'Authorization':'Basic '+basic},True)['access_token']
    return api('https://api.zoom.us/v2/'+path,method,payload,{'Authorization':'Bearer '+token})


def send_class_email(student,lesson,calendar):
    data={'personalizations':[{'to':[{'email':student.email}]}],
          'from':{'email':setting('SENDGRID_FROM_EMAIL')},'subject':'LionTech class update: '+lesson.title,
          'content':[{'type':'text/plain','value':f'Hello {student.name},\nYour {lesson.course.name} class schedule has been updated.\nStudent number: {student.student_number}\nOpen your portal for current class details: {ORIGIN}/live-classes\nThe attached calendar event can be added to your calendar.'}],
          'attachments':[{'content':base64.b64encode(calendar.encode()).decode(),'type':'text/calendar; method=REQUEST','filename':'liontech-class.ics','disposition':'attachment'}]}
    api('https://api.sendgrid.com/v3/mail/send','POST',data,{'Authorization':'Bearer '+setting('SENDGRID_API_KEY')})
