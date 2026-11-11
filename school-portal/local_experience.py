"""Country-scoped, server-rendered money and recipient-specific local times."""
import os,re
from urllib.parse import unquote
from datetime import datetime,time,timedelta
from decimal import Decimal,InvalidOperation,DecimalException
from zoneinfo import ZoneInfo
from markupsafe import Markup,escape
from flask import g,request,session,redirect,url_for,jsonify,abort
from babel.dates import format_datetime,format_date
from babel.numbers import format_currency
from babel import Locale as BabelLocale
from localization import COUNTRY_META,COUNTRIES,TIMEZONES,CURRENCIES,residence,converted,formatted

ERROR='Local pricing is temporarily unavailable. Please try again shortly or contact support.'
UNKNOWN='Choose your country to see local pricing.'
ACADEMY='America/Toronto'


def install(app,db,m):
    Locale=m['StudentLocale'];Preference=m['StudentPreference'];now,aware=m['now'],m['aware']
    by_code={v['code']:name for name,v in COUNTRY_META.items() if v['code']}
    def browser_zone():
        value=unquote(request.cookies.get('liontech_timezone','')) or session.get('browser_timezone')
        return value if value in TIMEZONES else None
    def state(student=None):
        if student is None and hasattr(g,'local_state'):return g.local_state
        student=student or getattr(g,'student',None)
        saved=db.session.get(Locale,student.id) if student else None
        pref=db.session.get(Preference,student.id) if student else None
        explicit=session.get('local_country') if student is getattr(g,'student',None) else None
        trusted=bool(os.environ.get('VERCEL')=='1' or app.config.get('TRUST_GEO_HEADERS',False))
        geo=by_code.get(request.headers.get('x-vercel-ip-country','').upper()) if trusted and student is getattr(g,'student',None) else None
        country=explicit or (saved.country if saved and saved.country_confirmed else None) or geo or (saved.country if saved and saved.country!='Other' else None) or (student.country if student and student.country!='Other' else None)
        zone=(saved.timezone if saved and (saved.timezone_manual or student is not getattr(g,'student',None)) else None) or (session.get('browser_timezone') if session.get('local_zone_manual') and student is getattr(g,'student',None) else None) or (browser_zone() if student is getattr(g,'student',None) else None) or (request.headers.get('x-vercel-ip-timezone') if trusted and geo else None) or (pref.timezone if pref else None) or (residence(country)['timezone'] if country else ACADEMY)
        if zone not in TIMEZONES:zone=ACADEMY
        return dict(country=country,currency=residence(country)['currency'] if country and residence(country).get('currency_known',True) else None,timezone=zone,language=pref.language if pref else session.get('ui_language','en'),confirmed=bool(saved and saved.country_confirmed),zone_confirmed=bool(saved and saved.timezone_confirmed))
    @app.before_request
    def detect():
        if request.endpoint=='static':return
        g.local_state=state()
    def format_local(amount,currency,student=None):
        context=state(student);code=residence(context['country'])['code'];language=context['language'];locale=language+'_'+code if code else language
        try:BabelLocale.parse(locale)
        except Exception:locale={'fr':'fr_FR','es':'es_ES'}.get(language,'en_US')
        return format_currency(amount,currency,locale=locale).replace('US$','$').replace('USD','Dollar')
    def quote(value,source='USD',student=None,offset=0,country=None):
        context=state(student).copy()
        if country:context.update(country=country,currency=residence(country)['currency'] if residence(country).get('currency_known',True) else None)
        target=context['currency'];language=context['language']
        result=dict(available=False,currency=target,local=({'fr':'Choisissez votre pays pour voir les prix locaux.','es':'Elija su país para ver los precios locales.'}.get(language,UNKNOWN) if not target else {'fr':'Les tarifs locaux sont temporairement indisponibles. Réessayez ou contactez l’assistance.','es':'Los precios locales no están disponibles. Inténtelo de nuevo o contacte con soporte.'}.get(language,ERROR)),amount=None,rate=None,as_of=None,stale=False)
        if not target:return result
        try:
            amount=Decimal(str(value));offset=Decimal(str(offset))
            if not amount.is_finite() or not offset.is_finite():return result
            pinned=getattr(g,'pinned_local_quote',None) if student is None else None
            if pinned and pinned.currency==target and pinned.base_currency==source:rate=Decimal(pinned.rate);asof=pinned.as_of
            elif target==source:rate=Decimal(1);asof=now()
            else:
                rates,asof=app.extensions['global_experience']['rates']()
                if not asof or aware(asof)<now()-timedelta(hours=48):
                    result.update(as_of=asof,stale=True);return result
                rate=Decimal(str(rates.get(target,1 if target=='USD' else 'NaN')))/Decimal(str(rates.get(source,1 if source=='USD' else 'NaN')))
                if not rate.is_finite() or rate<=0:return result
            local=converted(amount+offset,target,rate)-converted(offset,target,rate)
            label=format_local(local,target,student)
            result.update(available=True,amount=str(local),rate=str(rate),as_of=asof,local=label)
        except (KeyError,ValueError,DecimalException,TypeError):pass
        return result
    def money(value,source='USD',student=None,offset=0):return quote(value,source,student,offset)['local']
    def stage_money(billing,index):
        part=billing['parts'][index]
        offset=sum((p['amount'] for p in billing['parts'][:index]),Decimal(0))
        return money(part['amount'],billing['terms'].currency,offset=offset)
    def local_time(value,student=None):
        context=state(student)
        return format_datetime(aware(value).astimezone(ZoneInfo(context['timezone'])),format='long',locale={'fr':'fr_FR','es':'es_ES'}.get(context['language'],'en_US'))+' · '+context['timezone']
    def deadline_instant(value):return datetime.combine(value,time(23,59,59),ZoneInfo(ACADEMY))
    def deadline(value,student=None):
        if not value:return 'Date to be confirmed'
        return local_time(deadline_instant(value),student)
    def deadline_html(value):
        if not value:return deadline(value)
        return Markup('<time data-local-time="%s">%s</time>')%(escape(deadline_instant(value).isoformat()),escape(deadline(value)))
    def calendar_deadlines(start):
        from program_link_plans import dated_stages,DEFAULT_STAGES
        stages=dated_stages(DEFAULT_STAGES,start,m['academy_today']())
        from datetime import date
        return [deadline_instant(date.fromisoformat(part['due'])).isoformat() for part in stages]
    def intake(value):return format_date(value,format='long',locale={'fr':'fr_FR','es':'es_ES'}.get(state()['language'],'en_US'))+' · academy intake date'
    def text(value,student=None):
        value=str(value or '')
        currencies='|'.join(sorted(CURRENCIES))
        pattern=r'(?:US\$|\b('+currencies+r')\s*)([0-9]+(?:,[0-9]{3})*(?:\.[0-9]{1,3})?)|([0-9]+(?:\.[0-9]{1,3})?)\s*('+currencies+r')\b'
        def replace(match):
            amount=(match[2] or match[3]).replace(',','');currency=match[1] or match[4] or 'USD'
            return money(amount,currency,student)
        return re.sub(pattern,replace,value)
    @app.post('/local-preferences',endpoint='local_preferences')
    def preferences():
        country=request.form.get('country');zone=request.form.get('timezone');explicit_zone=request.form.get('zone_choice')=='manual'
        if country is not None and (country not in COUNTRIES or country=='Other'):abort(400)
        if zone is not None and zone not in TIMEZONES:abort(400)
        if country:session['local_country']=country
        if zone and explicit_zone:session['ui_timezone']=zone;session['browser_timezone']=zone;session['local_zone_manual']=True
        current=state();student=getattr(g,'student',None)
        if student:
            row=db.session.get(Locale,student.id) or Locale(student_id=student.id,country=current['country'] or student.country,currency=current['currency'] or residence(student.country)['currency'],timezone=current['timezone'])
            if country:row.country=country;row.currency=residence(country)['currency'];row.country_confirmed=True;student.country=country
            if zone and (explicit_zone or not row.timezone_manual):row.timezone=zone;row.timezone_confirmed=True;row.timezone_manual=explicit_zone
            pref=db.session.get(Preference,student.id) or Preference(student_id=student.id)
            pref.timezone=row.timezone;db.session.add_all([row,pref]);db.session.commit()
        if request.headers.get('X-Requested-With')=='localization':return jsonify(saved=True)
        target=request.form.get('next','/')
        if not target.startswith('/') or target.startswith('//') or '\\' in target or any(ord(c)<32 for c in target):target='/'
        return redirect(target)
    @app.context_processor
    def context():
        current=state();saved=db.session.get(Locale,g.student.id) if getattr(g,'student',None) else None
        return dict(local_context=current,display_money=money,display_quote=quote,stage_money=stage_money,policy_stage_money=lambda policy,index:money(policy.stages[index]['amount'],policy.currency,offset=sum((Decimal(p['amount']) for p in policy.stages[:index]),Decimal(0))),localized_time=local_time,local_deadline=deadline_html,calendar_deadlines=calendar_deadlines,intake_date=intake,student_text=text,ui_timezone=current['timezone'],browser_zone_manual=bool((saved and saved.timezone_manual) or session.get('local_zone_manual')))
    return dict(browser_zone=browser_zone,state=state,quote=quote,money=money,time=local_time,deadline=deadline,text=text,format=format_local)
