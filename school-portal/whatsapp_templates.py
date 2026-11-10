"""Validate approved plain-text WhatsApp templates without sending any message."""
import base64,hashlib,re,time
from urllib.parse import quote
from flask import current_app
from providers import api


class TemplateError(ValueError):
    def __init__(self,status,message):
        self.status=status
        super().__init__(message)


def validate(values,sid,variables,transport=None):
    if not re.fullmatch(r'HX[0-9a-fA-F]{32}',sid):raise TemplateError('awaiting_whatsapp_template','Enter an approved HX template SID.')
    transport=transport or current_app.config.get('WHATSAPP_TEMPLATE_HTTP',api)
    credentials=base64.b64encode((values['TWILIO_ACCOUNT_SID']+':'+values['TWILIO_AUTH_TOKEN']).encode()).decode()
    headers={'Authorization':'Basic '+credentials}
    cache=current_app.extensions.setdefault('whatsapp_template_cache',{})
    key=(sid,values['TWILIO_ACCOUNT_SID'],hashlib.sha256(values['TWILIO_AUTH_TOKEN'].encode()).hexdigest(),tuple(sorted(variables)))
    previous=cache.get(key)
    if previous and previous['until']>time.monotonic():return previous['result']
    url='https://content.twilio.com/v1/Content/'+quote(sid,safe='')
    content=transport(url,headers=headers)
    types=content.get('types',{})
    if set(types)!= {'twilio/text'} or not isinstance(types['twilio/text'].get('body'),str):
        raise TemplateError('unsupported_whatsapp_template','Use a Text-only template. Media, cards, attachments and interactive actions are not supported for portal alerts.')
    body=types['twilio/text']['body']
    placeholders=set(re.findall(r'\{\{\s*([0-9]+)\s*\}\}',body))
    if placeholders!=set(variables):raise TemplateError('template_fields_mismatch','The template variables do not match this notification. Use the variable mapping shown in Notifications.')
    approval=transport(url+'/ApprovalRequests',headers=headers)
    whatsapp=approval.get('whatsapp',{})
    if whatsapp.get('status')!='approved':raise TemplateError('template_approval_pending','WhatsApp has not approved this template. Review its status in Twilio.')
    if whatsapp.get('content_type') not in (None,'twilio/text'):
        raise TemplateError('unsupported_whatsapp_template','The WhatsApp-approved content type must be Text only.')
    result={'type':'twilio/text','approval':'approved'}
    if len(cache)>100:cache.clear()
    cache[key]={'until':time.monotonic()+300,'result':result}
    return result
