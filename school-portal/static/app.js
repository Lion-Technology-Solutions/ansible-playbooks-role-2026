'use strict';
for(const element of document.querySelectorAll('[data-local-time]')){
 const instant=new Date(element.dataset.localTime);
 if(!Number.isNaN(instant.getTime())){
  const language=document.body.dataset.language==='es'?'es-ES':undefined;
  try{element.textContent=(language?'Tu hora: ':'Your time: ')+new Intl.DateTimeFormat(language,{dateStyle:'medium',timeStyle:'short',timeZone:document.body.dataset.timezone||undefined}).format(instant)+' · '+(document.body.dataset.timezone||Intl.DateTimeFormat().resolvedOptions().timeZone);}
  catch{element.textContent=instant.toLocaleString();}
 }
}
