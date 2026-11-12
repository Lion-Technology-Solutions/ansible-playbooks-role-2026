'use strict';
document.addEventListener('change', event => {
  if (!event.target.matches('[data-level-jump]')) return;
  const destination = event.target.value;
  if (!destination) return;
  const url = new URL(destination, window.location.origin);
  if (url.origin === window.location.origin) window.location.assign(url.href);
});
