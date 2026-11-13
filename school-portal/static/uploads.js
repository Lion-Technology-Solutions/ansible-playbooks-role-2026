'use strict';
const uploadForm=document.querySelector('#file-upload-form');
if(uploadForm){
  uploadForm.addEventListener('submit',async(event)=>{
    event.preventDefault();
    const button=uploadForm.querySelector('button[type="submit"]');
    const status=document.querySelector('#upload-status');
    const progress=document.querySelector('#upload-progress');
    const files=Array.from(uploadForm.elements.files.files);
    const csrf=uploadForm.elements.csrf.value;
    const purpose=uploadForm.elements.purpose.value;
    const headers={'X-CSRF-Token':csrf};
    let finished=0;
    const total=files.reduce((sum,file)=>sum+file.size,0);
    async function checked(response){
      let result;try{result=await response.json();}catch{throw new Error('Upload interrupted. Sign in again if your session expired, then retry.');}
      if(!response.ok)throw new Error(result.error||'Upload failed. Please retry.');
      return result;
    }
    button.disabled=true;progress.hidden=false;progress.value=0;
    try{
      for(const file of files){
        status.textContent='Preparing '+file.name+'…';
        const fields=new FormData();fields.set('filename',file.name);fields.set('size',file.size);fields.set('purpose',purpose);
        const upload=await checked(await fetch(uploadForm.dataset.startUrl,{method:'POST',headers,body:fields}));
        for(let part=0;part<upload.parts;part++){
          const data=file.slice(part*upload.chunk_size,Math.min(file.size,(part+1)*upload.chunk_size));
          let lastError;
          for(let attempt=0;attempt<3;attempt++){
            try{await checked(await fetch((uploadForm.dataset.assetBase||'/admin/assets')+'/'+upload.id+'/chunks/'+part,{method:'POST',headers:{...headers,'Content-Type':'application/octet-stream'},body:data}));lastError=null;break;}
            catch(error){lastError=error;if(attempt<2)await new Promise(resolve=>setTimeout(resolve,1000*(attempt+1)));}
          }
          if(lastError)throw lastError;
          finished+=data.size;progress.value=total?Math.round(finished/total*100):0;
          status.textContent='Uploading '+file.name+' · '+progress.value+'% overall';
        }
        await checked(await fetch((uploadForm.dataset.assetBase||'/admin/assets')+'/'+upload.id+'/finish',{method:'POST',headers}));
      }
      status.textContent='Upload complete. Opening your library…';window.location.reload();
    }catch(error){status.textContent=error.message+' Completed files remain in your library. Archive incomplete entries before starting again.';}
    finally{button.disabled=false;}
  });
}
