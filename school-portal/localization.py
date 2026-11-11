"""Global residence metadata and honest USD-reference display quotes."""
from datetime import datetime, timezone, date
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path
from urllib.request import Request, urlopen
from babel import Locale
from babel.core import get_global
from babel.numbers import get_territory_currencies, get_currency_precision, format_currency
import pycountry
from zoneinfo import available_timezones

_EN=Locale('en')
_CURRENCY_DATA=json.loads(Path(__file__).with_name('country_currencies.json').read_text())['currencies']
COUNTRY_META={}
for territory in pycountry.countries:
    code=territory.alpha_2
    name=str(_EN.territories.get(code,territory.name))
    tender=get_territory_currencies(code,date.today())
    languages=get_global('territory_languages').get(code,{})
    primary=max(languages,key=lambda item:languages[item].get('population_percent',0),default='en')
    zones=get_global('territory_zones').get(code,[])
    COUNTRY_META[name]={'code':code,'currency':_CURRENCY_DATA.get(code,tender[0] if tender else 'USD'),'currency_known':bool(_CURRENCY_DATA.get(code) or tender),'language':primary if primary in ('en','es','fr') else 'en','timezone':zones[0] if zones else 'UTC'}
# Preserve the historical names stored on student records.
COUNTRY_META['United States']=COUNTRY_META.get('United States',COUNTRY_META.get('United States of America'))
COUNTRY_META['Other']={'code':'','currency_known':False,'currency':'USD','language':'en','timezone':'UTC'}
COUNTRY_META['Canada']['timezone']='America/Toronto'
COUNTRY_META['Spain']['timezone']='Europe/Madrid'
LEGACY_COUNTRIES=['Cameroon','Nigeria','Ghana','Canada','United States','United Kingdom','Other','Spain','France','Germany','Australia']
COUNTRIES=LEGACY_COUNTRIES+sorted(set(COUNTRY_META)-set(LEGACY_COUNTRIES))
CURRENCIES=tuple(sorted({item['currency'] for item in COUNTRY_META.values()}|{'USD','EUR','CAD','GBP','NGN','GHS','XAF'}))
TIMEZONES=tuple(sorted(available_timezones()))
for info in COUNTRY_META.values():
    if info['timezone'] not in TIMEZONES:info['timezone']='UTC'


def residence(country):
    return COUNTRY_META.get(country,COUNTRY_META['Other'])


def formatted(amount,currency,language='en'):
    locale={'en':'en_US','es':'es_ES','fr':'fr_FR'}.get(language,'en_US')
    return format_currency(amount,currency,locale=locale)


def converted(amount,currency,rate):
    return (Decimal(str(amount))*Decimal(str(rate))).quantize(Decimal(1).scaleb(-get_currency_precision(currency)),rounding=ROUND_HALF_UP)


def fetch_rates():
    req=Request('https://open.er-api.com/v6/latest/USD',headers={'User-Agent':'LionTechPortal/1.0','Accept':'application/json'})
    with urlopen(req,timeout=6) as response:
        raw=response.read(200001)
    if len(raw)>200000:raise ValueError('Invalid exchange-rate response.')
    data=json.loads(raw)
    if data.get('result')!='success' or data.get('base_code')!='USD':raise ValueError('Exchange rates are unavailable.')
    rates={key:str(Decimal(str(value))) for key,value in data['rates'].items() if key in CURRENCIES}
    if Decimal(rates.get('USD','0'))!=1 or any(not Decimal(v).is_finite() or not 0<Decimal(v)<Decimal('1000000000') for v in rates.values()):raise ValueError('Invalid exchange rates.')
    asof=datetime.fromtimestamp(int(data['time_last_update_unix']),timezone.utc)
    if not datetime.now(timezone.utc).timestamp()-72*3600<=asof.timestamp()<=datetime.now(timezone.utc).timestamp()+3600:raise ValueError('Exchange-rate source is out of date.')
    return rates,asof
