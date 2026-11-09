'use strict';
const toggle=document.querySelector('[data-toggle-sidebar]');
function closeSidebar(){document.body.classList.remove('sidebar-open');if(toggle)toggle.setAttribute('aria-expanded','false');const scrim=document.querySelector('.sidebar-scrim');if(scrim)scrim.hidden=true;}
if(toggle)toggle.addEventListener('click',()=>{const open=!document.body.classList.contains('sidebar-open');document.body.classList.toggle('sidebar-open',open);toggle.setAttribute('aria-expanded',String(open));document.querySelector('.sidebar-scrim').hidden=!open;});
document.querySelectorAll('[data-close-sidebar]').forEach(button=>button.addEventListener('click',closeSidebar));
document.querySelectorAll('.dashboard-sidebar nav a').forEach(link=>link.addEventListener('click',closeSidebar));
document.addEventListener('keydown',event=>{if(event.key==='Escape')closeSidebar();});
for(const element of document.querySelectorAll('progress[data-value]'))element.value=Number(element.dataset.value)||0;
for(const card of document.querySelectorAll('[data-course-option]')){const radio=card.querySelector('input[type="radio"]');if(radio)radio.addEventListener('change',()=>{document.querySelectorAll('[data-course-option]').forEach(item=>item.classList.toggle('selected',item.querySelector('input').checked));const select=document.querySelector('[data-cohort-select]');if(select){select.querySelectorAll('option[data-course]').forEach(option=>{option.hidden=option.dataset.course!==radio.value;option.disabled=option.hidden;});select.value='';}});}
const initial=document.querySelector('[data-course-option] input:checked');if(initial)initial.dispatchEvent(new Event('change'));
document.querySelectorAll('[data-print-certificate]').forEach(button=>button.addEventListener('click',()=>window.print()));
