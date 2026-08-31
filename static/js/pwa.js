/* Registers the service worker and offers an "Add to home screen" prompt.
 *
 * Students open this app several times a day; a home-screen icon removes the
 * URL-typing step entirely. The prompt is deliberately quiet: it appears once,
 * is dismissible, and stays dismissed.
 */
(function () {
    'use strict';

    // Service workers need a secure context. The app serves HTTPS with a
    // self-signed certificate, which counts, but a plain-HTTP deployment does
    // not — bail out rather than throwing on every page load.
    if (!('serviceWorker' in navigator) || !window.isSecureContext) return;

    window.addEventListener('load', function () {
        navigator.serviceWorker.register('/sw.js').catch(function (err) {
            console.warn('Service worker registration failed:', err);
        });
    });

    var DISMISS_KEY = 'pwa-install-dismissed';
    var deferred = null;

    function dismissed() {
        try { return localStorage.getItem(DISMISS_KEY) === '1'; }
        catch (e) { return false; }   // private mode: just don't nag
    }

    function remember() {
        try { localStorage.setItem(DISMISS_KEY, '1'); } catch (e) { /* ignore */ }
    }

    function showBanner() {
        if (document.getElementById('pwaInstallBar')) return;

        var bar = document.createElement('div');
        bar.id = 'pwaInstallBar';
        bar.setAttribute('role', 'region');
        bar.setAttribute('aria-label', 'Install this app');
        bar.style.cssText = [
            'position:fixed', 'left:12px', 'right:12px', 'bottom:12px', 'z-index:9999',
            'display:flex', 'align-items:center', 'gap:12px',
            'padding:12px 14px', 'border-radius:10px',
            'background:#172B4D', 'color:#fff',
            'box-shadow:0 6px 20px rgba(0,0,0,.28)',
            'font-size:.9rem', 'max-width:520px', 'margin:0 auto'
        ].join(';');

        var text = document.createElement('div');
        text.style.cssText = 'flex:1;line-height:1.35;';
        text.textContent = 'Add Attendance to your home screen for one-tap access.';

        var install = document.createElement('button');
        install.type = 'button';
        install.textContent = 'Add';
        install.style.cssText =
            'background:#fff;color:#172B4D;border:0;border-radius:6px;' +
            'padding:9px 14px;font-weight:600;cursor:pointer;min-height:38px;';

        var close = document.createElement('button');
        close.type = 'button';
        close.setAttribute('aria-label', 'Dismiss');
        close.innerHTML = '&times;';
        close.style.cssText =
            'background:transparent;color:#fff;border:0;font-size:22px;' +
            'line-height:1;cursor:pointer;padding:0 4px;';

        function hide() { bar.remove(); }

        install.addEventListener('click', function () {
            hide();
            if (!deferred) return;
            deferred.prompt();
            deferred.userChoice.then(function () { deferred = null; });
            // Whatever they choose, don't ask again from this browser.
            remember();
        });

        close.addEventListener('click', function () { remember(); hide(); });

        bar.appendChild(text);
        bar.appendChild(install);
        bar.appendChild(close);
        document.body.appendChild(bar);
    }

    window.addEventListener('beforeinstallprompt', function (e) {
        // Chrome would otherwise show its own mini-infobar; take control so the
        // prompt appears in our styling and only when we want it.
        e.preventDefault();
        deferred = e;
        if (!dismissed()) showBanner();
    });

    window.addEventListener('appinstalled', function () {
        remember();
        var bar = document.getElementById('pwaInstallBar');
        if (bar) bar.remove();
    });
})();
