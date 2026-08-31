/* Shared navbar behaviour for templates/_navbar.html.
 *
 * Three jobs:
 *   1. Reveal only the links the current role can actually reach.
 *   2. Mark the active page.
 *   3. Provide performLogout() for any page that does not define its own.
 *
 * Role gating matters: every admin page (/dashboard, /students, /admin,
 * /admin/batches, /attendance-management) is behind require_admin_access, so
 * showing those links to a teacher just hands them a 403. Links start hidden
 * and are revealed once the session is known — briefly showing nothing beats
 * briefly showing links that don't work.
 */
(function () {
    'use strict';

    var LINKS_SELECTOR = '.app-navbar-links a[data-roles]';

    // Where "home" is depends on who you are.
    var HOME_BY_ROLE = {
        admin: '/dashboard',
        teacher: '/teacher',
        student: '/student',
        user: '/student',
        terminal: '/attendance'
    };

    function hideAllGatedLinks() {
        var links = document.querySelectorAll(LINKS_SELECTOR);
        for (var i = 0; i < links.length; i++) {
            var roles = links[i].getAttribute('data-roles') || '';
            // Public links (data-roles="*") never need hiding.
            if (roles !== '*') links[i].style.display = 'none';
        }
    }

    function applyRole(role) {
        var links = document.querySelectorAll(LINKS_SELECTOR);
        for (var i = 0; i < links.length; i++) {
            var el = links[i];
            var roles = (el.getAttribute('data-roles') || '').split(',');
            var allowed = roles.indexOf('*') !== -1 || roles.indexOf(role) !== -1;
            el.style.display = allowed ? '' : 'none';
        }

        var brand = document.getElementById('nav-brand');
        if (brand && HOME_BY_ROLE[role]) brand.setAttribute('href', HOME_BY_ROLE[role]);
    }

    function markActive() {
        var path = window.location.pathname.replace(/\/+$/, '') || '/';
        var links = document.querySelectorAll(LINKS_SELECTOR);
        var best = null, bestLen = -1;

        for (var i = 0; i < links.length; i++) {
            var href = (links[i].getAttribute('href') || '').replace(/\/+$/, '') || '/';
            // Longest prefix match so /admin/batches highlights Batches, not Admin.
            if (path === href || path.indexOf(href + '/') === 0) {
                if (href.length > bestLen) { best = links[i]; bestLen = href.length; }
            }
        }
        if (best) best.classList.add('active');
    }

    function init() {
        hideAllGatedLinks();
        markActive();

        fetch('/api/session/status', { credentials: 'include' })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (!d || !d.authenticated) return;   // public page: links stay hidden
                // user_type is the session's role ('user' is the legacy student value).
                applyRole(d.user_type || d.role || '');
            })
            .catch(function () { /* offline: a nav with no links is still usable */ });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Fallback only — pages that already define performLogout keep theirs.
    // Inline page scripts run before this deferred file, so their definition
    // wins and nothing is overwritten.
    if (typeof window.performLogout !== 'function') {
        window.performLogout = async function () {
            if (!confirm('Are you sure you want to logout?')) return;
            try {
                var r = await fetch('/api/logout', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'include'
                });
                var d = await r.json();
                if (d.success) {
                    localStorage.clear();
                    sessionStorage.clear();
                    window.location.href = '/login';
                } else {
                    alert('Logout failed: ' + d.message);
                }
            } catch (e) {
                window.location.href = '/login';
            }
        };
    }
})();
