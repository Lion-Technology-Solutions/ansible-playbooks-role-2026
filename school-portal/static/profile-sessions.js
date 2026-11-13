'use strict';
(() => {
  const menus = [...document.querySelectorAll('[data-profile-menu]')];
  let dirty = false;
  document.addEventListener('input', event => {
    if (event.isTrusted && event.target.closest('form') && !event.target.closest('[data-profile-action],.preference-controls,.reading-toolbar') && !event.target.matches('[type="search"]')) dirty = true;
  });
  document.addEventListener('click', event => {
    menus.forEach(menu => { if (!menu.contains(event.target)) menu.open = false; });
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') menus.forEach(menu => {
      if (menu.open) { menu.open = false; menu.querySelector('summary').focus(); }
    });
  });
  document.querySelectorAll('[data-leave-workspace]').forEach(form => form.addEventListener('submit', event => {
    if (dirty && !window.confirm('Leave this page? Your unsaved changes will be lost.')) event.preventDefault();
  }));
  const controls = document.querySelector('[data-session-controls]');
  if (!controls) return;
  const warning = document.querySelector('[data-session-warning]');
  const seconds = document.querySelector('[data-session-seconds]');
  const feedback = document.querySelector('[data-session-warning-status]');
  const stay = document.querySelector('[data-stay-signed-in]');
  const endButton = document.querySelector('[data-sign-out-now]');
  const csrf = controls.dataset.csrf;
  const key = controls.dataset.channel;
  let expires = 0, offset = 0, ending = false, pendingActivity = false, busy = false, lastSent = 0;
  const channel = typeof BroadcastChannel === 'function' ? new BroadcastChannel('liontech-session-events') : null;
  function publish(type) {
    const payload = {type, key, at:Date.now()};
    channel?.postMessage(payload);
    try { localStorage.setItem('liontech-session-event', JSON.stringify(payload)); } catch { /* Private storage may be disabled. */ }
  }
  function signIn(reason = 'inactive') {
    if (ending) return;
    ending = true;
    const destination = new URL(controls.dataset.loginUrl, location.origin);
    destination.searchParams.set('reason', reason);
    location.assign(destination.href);
  }
  async function apply(response) {
    const value = await response.json();
    if (!response.ok || !value.authenticated) { signIn(value.reason || 'signed-out'); return; }
    if (key && value.channel !== key) { if(value.profile_switch_url){ending=true;location.assign(value.profile_switch_url);}else signIn('signed-out'); return; }
    const server = Date.parse(value.server_now), deadline = Date.parse(value.expires_at);
    if (!Number.isFinite(server) || !Number.isFinite(deadline)) throw new Error('Invalid session response.');
    offset = server - Date.now(); expires = deadline;
    if (expires - (Date.now() + offset) > 60000 && warning.open) { warning.close(); feedback.textContent = ''; }
    tick();
  }
  async function check() {
    if (ending) return;
    try { await apply(await fetch(controls.dataset.statusUrl, {cache:'no-store', credentials:'same-origin'})); }
    catch { /* Keep the last confirmed deadline; never extend it on network failure. */ }
  }
  async function touch(explicit = false) {
    if (ending || busy || (!explicit && (!pendingActivity || warning.open || document.hidden))) return;
    busy = true; pendingActivity = false; lastSent = Date.now(); stay.disabled = true;
    try {
      await apply(await fetch(controls.dataset.activityUrl, {method:'POST',credentials:'same-origin',headers:{'X-CSRF-Token':csrf}}));
      if (!ending) publish('activity');
    } catch { if (warning.open) feedback.textContent = 'We could not renew your session. Check your connection and try again before the timer ends.'; }
    finally { busy = false; stay.disabled = false; }
  }
  function tick() {
    if (!expires || ending) return;
    const remaining = Math.ceil((expires - (Date.now() + offset)) / 1000);
    if (remaining <= 0) { publish('expired'); signIn('inactive'); return; }
    seconds.textContent = String(Math.min(60, remaining));
    if (remaining <= 60 && !warning.open) {
      warning.showModal(); stay.focus();
      feedback.textContent = 'Choose Stay signed in to continue your session.';
    }
  }
  for (const name of ['pointerdown','keydown','wheel','touchstart']) document.addEventListener(name, event => {
    if (!event.isTrusted || warning.open || ending || document.hidden) return;
    pendingActivity = true;
    if (Date.now() - lastSent >= 20000) void touch();
  }, {passive:true});
  stay.addEventListener('click', () => void touch(true));
  warning.addEventListener('cancel', event => event.preventDefault());
  endButton.addEventListener('click', async () => {
    endButton.disabled = true;
    try {
      const response = await fetch(controls.dataset.endUrl, {method:'POST',credentials:'same-origin',headers:{'X-CSRF-Token':csrf}});
      if (!response.ok && response.status !== 401) throw new Error('Sign-out was not confirmed.');
      publish('signed-out'); signIn('signed-out');
    } catch { feedback.textContent = 'Sign-out could not reach the server. Retry or wait for the inactivity timer.'; endButton.disabled = false; }
  });
  document.querySelectorAll('[data-sign-out]').forEach(form => form.addEventListener('submit', event => {
    if (!event.defaultPrevented) publish('signed-out');
  }));
  function receive(message) {
    if (!message || message.key !== key) return;
    if (message.type === 'signed-out') signIn('signed-out');
    else if (message.type === 'activity' || message.type === 'expired') void check();
  }
  if (channel) channel.onmessage = event => receive(event.data);
  window.addEventListener('storage', event => { if (event.key === 'liontech-session-event') { try { receive(JSON.parse(event.newValue)); } catch { /* Ignore malformed messages. */ } } });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { tick(); void check(); } });
  window.addEventListener('pageshow', () => void check());
  setInterval(tick, 1000);
  setInterval(() => { if (!document.hidden) { void check(); void touch(); } }, 15000);
  void check();
})();
