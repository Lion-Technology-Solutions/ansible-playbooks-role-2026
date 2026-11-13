'use strict';
(() => {
  const help = document.getElementById('site-support');
  if (!help) return;
  const trigger = help.querySelector('summary');
  function closeHelp(restoreFocus) {
    help.open = false;
    if (restoreFocus) trigger.focus();
  }
  const closeButton = help.querySelector('[data-close-support]');
  closeButton.hidden = false;
  closeButton.addEventListener('click', () => closeHelp(true));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && help.open) {
      event.preventDefault();
      closeHelp(true);
    }
  });
  document.addEventListener('pointerdown', event => {
    if (help.open && !help.contains(event.target)) closeHelp(false);
  });
})();
