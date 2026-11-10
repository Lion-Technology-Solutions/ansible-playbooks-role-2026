'use strict';
// Refresh server-checked access when a joining window opens or closes.
setInterval(() => { if (document.visibilityState === 'visible') location.reload(); }, 60000);
