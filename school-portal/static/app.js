'use strict';
for (const element of document.querySelectorAll('[data-local-time]')) {
  const instant = new Date(element.dataset.localTime);
  if (!Number.isNaN(instant.getTime())) element.textContent = 'Your time: ' + instant.toLocaleString(undefined, {dateStyle:'medium', timeStyle:'short'});
}
