'use strict';
(() => {
  const checks=[...document.querySelectorAll('[data-select-student]')], all=document.querySelector('[data-select-all]'), bar=document.querySelector('[data-selection-bar]');
  if (!bar) return;
  const update=()=>{const count=checks.filter(c=>c.checked).length;bar.hidden=count===0;document.querySelector('[data-selected-count]').textContent=String(count);all.checked=checks.length>0&&count===checks.length;all.indeterminate=count>0&&count<checks.length;};
  checks.forEach(c=>c.addEventListener('change',update));
  all.addEventListener('change',()=>{checks.forEach(c=>{c.checked=all.checked;});update();});
  document.querySelector('[data-clear-selection]').addEventListener('click',()=>{checks.forEach(c=>{c.checked=false;});update();});
  document.querySelector('[data-export-selected]').addEventListener('click',()=>{const url=new URL(document.querySelector('[data-export-url]').href);checks.filter(c=>c.checked).forEach(c=>url.searchParams.append('id',c.value));location.assign(url.href);});
  document.querySelector('[data-assign-selected]')?.addEventListener('click',event=>{const url=new URL(event.currentTarget.dataset.assignmentUrl,location.origin);checks.filter(c=>c.checked).forEach(c=>url.searchParams.append('student_id',c.value));url.hash='assign-records';location.assign(url.href);});
  const dialog=document.querySelector('[data-invite-dialog]'),trigger=document.querySelector('[data-invite-student]');
  trigger.addEventListener('click',()=>{dialog.showModal();document.querySelector('[data-copy-invite]').focus();});
  document.querySelector('[data-close-invite]').addEventListener('click',()=>{dialog.close();trigger.focus();});
  document.querySelector('[data-copy-invite]').addEventListener('click',async()=>{const input=document.querySelector('[data-registration-link]'),status=document.querySelector('[data-invite-status]');try{await navigator.clipboard.writeText(input.value);status.textContent='Registration link copied.';}catch{input.focus();input.select();status.textContent='Select and copy the registration link.';}});
  const panel=document.querySelector('[data-student-panel]');
  if(panel){const heading=panel.querySelector('h2');heading.tabIndex=-1;heading.focus({preventScroll:true});document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!dialog.open&&!document.querySelector('[data-session-warning][open]')&&!event.target.closest('form,[data-profile-menu],details[open]')){location.assign(panel.querySelector('[aria-label="Close student overview"]').href);}});}
})();
