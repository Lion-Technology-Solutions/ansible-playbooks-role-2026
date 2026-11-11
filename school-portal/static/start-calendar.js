'use strict';
(()=>{
 const parse=s=>{const [y,m,d]=s.split('-').map(Number);return new Date(y,m-1,d,12);};
 const iso=d=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
 const locale=()=>document.documentElement.lang==='fr'?'fr-CA':'en-CA';
 const full=d=>d.toLocaleDateString(locale(),{weekday:'long',year:'numeric',month:'long',day:'numeric'});
 const addMonth=(date,offset)=>new Date(date.getFullYear(),date.getMonth()+offset,Math.min(date.getDate(),new Date(date.getFullYear(),date.getMonth()+offset+1,0).getDate()),12);
 for(const root of document.querySelectorAll('[data-start-picker]')){
  const form=root.closest('form'),select=root.querySelector('[data-cohort-select]'),all=[...select.options].filter(o=>o.value).map(o=>({value:o.value,label:o.textContent,course:o.dataset.course,date:o.dataset.date,program:o.dataset.program,deadlines:JSON.parse(o.dataset.deadlines)}));
  const ui=root.querySelector('[data-calendar-ui]'),month=root.querySelector('[data-calendar-month]'),days=root.querySelector('[data-calendar-days]'),selection=root.querySelector('[data-calendar-selection]'),preview=root.querySelector('[data-calendar-payments]');
  let options=[],months=[],activeMonth='';
  const course=()=>root.dataset.fixedCourse||form.querySelector('[name="course_id"]:checked')?.value||form.querySelector('[name="course_id"]')?.value;
  function payments(option){
   preview.hidden=!option||option.program!=='data-analytics';if(preview.hidden)return;
   const start=parse(option.date),balance=new Date(start);balance.setDate(balance.getDate()+7);
   const dates=option.deadlines.map(value=>new Date(value));
   const labels=['Enrollment payment — due immediately','Balance of first payment','Second payment','Final payment'],amounts=[root.dataset.enrollmentPrice,root.dataset.balancePrice,root.dataset.secondPrice,root.dataset.finalPrice];
   const list=root.querySelector('[data-calendar-payment-list]');list.replaceChildren();
   dates.forEach((date,index)=>{const li=document.createElement('li'),strong=document.createElement('strong'),detail=document.createElement('span');strong.textContent=`${labels[index]}: ${amounts[index]}`;detail.textContent=new Intl.DateTimeFormat(locale(),{dateStyle:'long',timeStyle:'short',timeZone:document.body.dataset.timezone}).format(date)+' · '+document.body.dataset.timezone;li.append(strong,document.createElement('br'),detail);list.append(li);});
  }
  function render(){
   const first=parse(activeMonth+'-01'),last=new Date(first.getFullYear(),first.getMonth()+1,0,12),offset=(first.getDay()+6)%7,byDate=new Map(options.map(o=>[o.date,o]));
   month.value=activeMonth;root.querySelector('[data-calendar-caption]').textContent=first.toLocaleDateString(locale(),{month:'long',year:'numeric'});
   root.querySelector('[data-calendar-previous]').disabled=months.indexOf(activeMonth)<=0;root.querySelector('[data-calendar-next]').disabled=months.indexOf(activeMonth)>=months.length-1;
   days.replaceChildren();let row;
   for(let cell=0;cell<Math.ceil((offset+last.getDate())/7)*7;cell++){
    if(cell%7===0){row=document.createElement('tr');days.append(row);}const td=document.createElement('td');row.append(td);const day=cell-offset+1;if(day<1||day>last.getDate())continue;
    const date=new Date(first.getFullYear(),first.getMonth(),day,12),option=byDate.get(iso(date)),button=document.createElement('button');button.type='button';button.className='calendar-day';button.textContent=String(day);button.disabled=!option;button.setAttribute('aria-label',full(date));button.setAttribute('aria-pressed',String(Boolean(option&&select.value===option.value)));
    if(option)button.addEventListener('click',()=>{select.value=option.value;selection.textContent=full(date);payments(option);render();const picked=[...days.querySelectorAll('button')].find(b=>b.getAttribute('aria-pressed')==='true');picked?.focus();});
    td.append(button);
   }
  }
  function refresh(){
   options=all.filter(o=>o.course===course());select.replaceChildren(new Option('Choose an upcoming start',''),...options.map(o=>new Option(o.label,o.value)));months=[];
   if(options.length){const end=options[options.length-1].date.slice(0,7);let d=parse(options[0].date.slice(0,7)+'-01');while(iso(d).slice(0,7)<=end){months.push(iso(d).slice(0,7));d=new Date(d.getFullYear(),d.getMonth()+1,1,12);}}
   month.replaceChildren(...months.map(value=>new Option(parse(value+'-01').toLocaleDateString(locale(),{month:'long',year:'numeric'}),value)));activeMonth=months[0]||'';
   root.querySelector('[data-calendar-hint]').textContent=options[0]?.program==='data-analytics'?'Classes start every Saturday. Choose an available date through December 2027. Dates follow Toronto time.':'Select an available program intake. Dates follow Toronto time.';
   selection.textContent=options.length?'Select an available date to continue.':'No start dates are currently available. Please contact LionTech.';payments(null);if(activeMonth)render();else days.replaceChildren();
  }
  month.addEventListener('change',()=>{activeMonth=month.value;render();});
  for(const [key,offset]of [['previous',-1],['next',1]])root.querySelector('[data-calendar-'+key+']').addEventListener('click',()=>{activeMonth=months[months.indexOf(activeMonth)+offset];render();});
  form.querySelectorAll('[name="course_id"]').forEach(input=>input.addEventListener('change',refresh));
  form.addEventListener('submit',event=>{if(!select.value){event.preventDefault();selection.textContent='Choose your preferred start date before continuing.';root.querySelector('.calendar-day:not(:disabled)')?.focus();}});
  root.querySelector('[data-calendar-fallback]').hidden=true;select.required=false;ui.hidden=false;refresh();
 }
})();
