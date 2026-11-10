"use strict";
document.querySelectorAll(".share-invitation").forEach(section=>{
 const status=section.querySelector(".share-feedback");
 section.querySelectorAll("[data-copy-invite]").forEach(button=>button.addEventListener("click",async()=>{
  try{await navigator.clipboard.writeText(button.dataset.shareUrl);status.textContent=document.documentElement.lang==="fr"?"Lien copié. Vous pouvez le partager.":"Link copied. Share it with someone ready to learn.";}
  catch{status.textContent=document.documentElement.lang==="fr"?"Copiez le lien affiché ci-dessous.":"Copy the registration link shown below.";}
 }));
 const native=section.querySelector("[data-native-share]");
 if(native && navigator.share){native.hidden=false;native.addEventListener("click",async()=>{
  try{await navigator.share({title:"LionTech Academy",text:native.dataset.shareText,url:native.dataset.shareUrl});}
  catch(error){if(error.name!=="AbortError")status.textContent=document.documentElement.lang==="fr"?"Utilisez les options de partage ci-dessus.":"Use one of the sharing options above.";}
 });}
});
