'use strict';
// Keep anchored student sections selected without changing access or routing.
(() => {
  if (!document.body.classList.contains('portal-design')) return;
  function updateNavigation() {
    const current = new URL(window.location.href);
    const links = [...document.querySelectorAll('[data-portal-nav]')];
    const local = links.filter(link => new URL(link.href).pathname === current.pathname);
    if (!local.length) return;
    const selected = local.find(link => new URL(link.href).hash === current.hash) || local.find(link => !new URL(link.href).hash);
    links.forEach(link => {
      const active = link === selected;
      link.classList.toggle('is-active', active);
      if (active) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });
  }
  window.addEventListener('hashchange', updateNavigation);
  updateNavigation();
})();

// Keep the chosen local method on the existing invoice-bound receipt form.
document.addEventListener('click', event => {
  const link = event.target.closest('[data-payment-method]');
  if (!link) return;
  const field = document.querySelector('[data-manual-payment] select[name="method"]');
  if (field && [...field.options].some(option => option.value === link.dataset.paymentMethod)) {
    field.value = link.dataset.paymentMethod;
    field.dispatchEvent(new Event('change', {bubbles: true}));
  }
});
