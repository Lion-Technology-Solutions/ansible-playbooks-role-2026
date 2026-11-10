"use strict";
(() => {
 const buttons = [...document.querySelectorAll('[data-reading-size-button]')];
 const apply = value => {
   const size = value === 'large' ? 'large' : 'standard';
   document.documentElement.dataset.readingSize = size;
   buttons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.readingSizeButton === size)));
 };
 try { apply(localStorage.getItem('liontech-reading-size')); } catch (_) { apply('standard'); }
 buttons.forEach(button => button.addEventListener('click', () => {
   const size = button.dataset.readingSizeButton;
   apply(size);
   try { localStorage.setItem('liontech-reading-size', size); } catch (_) {}
 }));
})();
