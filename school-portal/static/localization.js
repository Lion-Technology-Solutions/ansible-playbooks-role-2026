'use strict';
for(const country of document.querySelectorAll('[data-residence-country]')){
 const form=country.closest('form');let controller=null;const language=form.querySelector('[data-residence-language]');const zone=form.querySelector('[data-residence-timezone]');let languageEdited=false,zoneEdited=false;
 if(language)language.addEventListener('change',()=>{languageEdited=true;});if(zone)zone.addEventListener('change',()=>{zoneEdited=true;});
 country.addEventListener('change',async()=>{if(!country.value)return;if(controller)controller.abort();controller=new AbortController();try{
 const response=await fetch('/localization?'+new URLSearchParams({country:country.value,language:language?.value||'en'}),{signal:controller.signal});if(!response.ok)throw Error();const data=await response.json();
 document.querySelectorAll('[data-local-currency]').forEach(el=>el.textContent=data.currency);document.querySelectorAll('[data-local-fee]').forEach(el=>el.textContent=data.fee+' '+(data.available?data.currency:'USD'));
 document.querySelectorAll('[data-fx-status]').forEach(el=>el.textContent=!data.available?'Local reference rate unavailable. USD price: US$5.00.':data.currency==='USD'?'USD is the base currency.':(data.stale?'Last available reference rate':'Reference rate')+(data.as_of?' · '+new Date(data.as_of).toLocaleString():'')+'. Checkout confirms the supported charge currency and final amount; bank fees may apply.');
 if(language&&!languageEdited)language.value=data.language;if(zone&&!zoneEdited)zone.value=data.timezone;
 }catch(error){if(error.name==='AbortError')return;document.querySelectorAll('[data-fx-status]').forEach(el=>el.textContent='Currency preview is unavailable. USD enrollment fee: US$5.00.');}});
}
for(const form of document.querySelectorAll('[data-manual-payment]')){
 const button=form.querySelector('[data-check-number]');const status=form.querySelector('[data-number-status]');const field=form.querySelector('[data-verify-number]');
 button.addEventListener('click',async()=>{button.disabled=true;try{const body=new FormData();body.set('student_number',field.value);body.set('csrf',form.querySelector('[name="csrf"]').value);const response=await fetch('/payments/verify-student',{method:'POST',body});if(!response.ok)throw Error();const result=await response.json();status.textContent=result.verified?'Verified for your signed-in account.':'Student number does not match your signed-in account.';status.className=result.verified?'identity-verified':'identity-error';}catch{status.textContent='Verification unavailable. Please reload and try again.';}finally{button.disabled=false;}});
 field.addEventListener('input',()=>{status.textContent='Verify your student number. Submission is also checked by the server.';status.className='';});
}
