'use strict';
for(const form of document.querySelectorAll('[data-manual-payment]')){
 const button=form.querySelector('[data-check-number]');const status=form.querySelector('[data-number-status]');const field=form.querySelector('[data-verify-number]');
 button.addEventListener('click',async()=>{button.disabled=true;try{const body=new FormData();body.set('student_number',field.value);body.set('csrf',form.querySelector('[name="csrf"]').value);const response=await fetch('/payments/verify-student',{method:'POST',body});if(!response.ok)throw Error();const result=await response.json();status.textContent=result.verified?'Verified for your signed-in account.':'Student number does not match your signed-in account.';status.className=result.verified?'identity-verified':'identity-error';}catch{status.textContent='Verification unavailable. Please reload and try again.';}finally{button.disabled=false;}});
 field.addEventListener('input',()=>{status.textContent='Verify your student number. Submission is also checked by the server.';status.className='';});
}
