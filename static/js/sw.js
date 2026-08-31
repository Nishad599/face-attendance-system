/* Service worker for the attendance PWA.
 *
 * Deliberately conservative. Attendance data must never be served stale — a
 * student looking at a cached 82% that is really 71% is worse than no offline
 * support at all. So:
 *
 *   - API calls (/api/*) are never cached. Network only.
 *   - HTML pages are network-first, falling back to the offline notice.
 *   - Only static assets (CSS/JS/images/fonts) are cached, and even those are
 *     revalidated in the background.
 *
 * Served from / (see the /sw.js route in main_with_face_recognition.py) so its
 * scope covers the whole app rather than just /static/js/.
 */

const VERSION = 'v2';
const STATIC_CACHE = `attendance-static-${VERSION}`;
const OFFLINE_URL = '/static/offline.html';

const PRECACHE = [
  OFFLINE_URL,
  '/static/css/app.css',
  '/static/js/theme.js',
  '/static/images/icon-192.png'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      // addAll is atomic: one 404 would throw away the whole install, so add
      // them individually and tolerate misses.
      .then((cache) => Promise.all(
        PRECACHE.map((url) => cache.add(url).catch(() => null))
      ))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k.startsWith('attendance-') && k !== STATIC_CACHE)
            .map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

function isStaticAsset(url) {
  return url.pathname.startsWith('/static/') &&
         !url.pathname.endsWith('/sw.js') &&
         !url.pathname.endsWith('.webmanifest');
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Never cache API responses or the login/logout surface — a cached session
  // redirect would strand the user on the wrong page.
  if (url.pathname.startsWith('/api/') ||
      url.pathname === '/login' ||
      url.pathname === '/logout') {
    return;
  }

  if (isStaticAsset(url)) {
    // Stale-while-revalidate: instant paint, fresh next load.
    event.respondWith(
      caches.open(STATIC_CACHE).then((cache) =>
        cache.match(req).then((cached) => {
          const network = fetch(req).then((res) => {
            if (res && res.status === 200) cache.put(req, res.clone());
            return res;
          }).catch(() => cached);
          return cached || network;
        })
      )
    );
    return;
  }

  // Navigations: always try the network so figures are current; show the
  // offline notice only when the network genuinely fails.
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req).catch(() => caches.match(OFFLINE_URL))
    );
  }
});
