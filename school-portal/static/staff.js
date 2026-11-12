'use strict';
(() => {
 const path=location.pathname,tab=new URLSearchParams(location.search).get('tab')||'personal';
 document.querySelectorAll('.staff-sidebar nav a').forEach(a=>{const url=new URL(a.href);if(url.pathname===path&&!url.search)a.setAttribute('aria-current','page');});
 document.querySelectorAll('.staff-profile-tabs a').forEach(a=>{if(new URL(a.href).searchParams.get('tab')===tab)a.setAttribute('aria-current','page');});
 const bank=document.querySelector('[data-bank-form]');
 if(bank){const country=bank.querySelector('[data-bank-country]');const apply=()=>bank.querySelectorAll('[data-bank-field]').forEach(label=>{label.hidden=label.dataset.bankField!==country.value;});country.addEventListener('change',apply);apply();}
 let dirty=false;
 document.querySelectorAll('.staff-main form').forEach(form=>{form.addEventListener('input',e=>{if(e.isTrusted)dirty=true;});form.addEventListener('submit',()=>{dirty=false;});});
 document.querySelectorAll('.staff-workspace a').forEach(a=>a.addEventListener('click',e=>{if(dirty&&a.target!=='_blank'&&!a.hash&&!confirm('Leave this page? Unsaved changes will be lost.'))e.preventDefault();}));
})();
