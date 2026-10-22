// What the phone keeps, so opening the app is not a blank screen.
//
// Until the phone was served over https none of this could exist: a page on a
// plain-http origin is refused service workers and caches outright. With it,
// the app opens on what you last saw -- on the train, with the Mac asleep --
// and quietly replaces it when the Mac is reachable again.
//
// Two caches, because they expire differently. The shell is the app itself and
// changes only when the app does. The data is about the people you talk to, so
// it is kept apart and can be thrown away on its own, which is what unpairing
// and "forget what's on this phone" do.
// The shell is answered from cache first, so bump this whenever app.js,
// styles.css or index.html change, or a paired phone keeps the old look.
const SHELL = 'mg-shell-v3';
const DATA = 'mg-data-v1';
const SHELL_FILES = [
  '/', '/static/app.js', '/static/styles.css', '/static/theme.js',
  '/static/manifest.webmanifest', '/static/icon-180.png', '/static/icon-512.png',
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== SHELL && k !== DATA).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

// The page asks for this when you unpair, so nothing about anyone is left behind.
self.addEventListener('message', e => {
  if (e.data === 'forget') {
    e.waitUntil(caches.keys().then(keys => Promise.all(keys.map(k => caches.delete(k)))));
  }
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;                  // never replay a write
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  // Pairing decides whether this phone is allowed in at all. A cached answer
  // to that question is worse than no answer.
  if (url.pathname.startsWith('/api/pair') || url.pathname === '/pair') return;

  if (url.pathname.startsWith('/api/')) {
    // What you last saw, immediately, with the real thing right behind it.
    e.respondWith((async () => {
      const cache = await caches.open(DATA);
      try {
        const fresh = await fetch(req);
        // 401 means this phone is no longer paired; do not keep it, and do
        // not answer it from a time when it was.
        if (fresh.ok) cache.put(req, fresh.clone());
        else if (fresh.status === 401) await cache.delete(req);
        return fresh;
      } catch {
        const seen = await cache.match(req);
        if (seen) {
          const copy = new Headers(seen.headers);
          copy.set('x-mg-from-cache', '1');           // the page says so on screen
          return new Response(await seen.blob(), { status: 200, headers: copy });
        }
        throw new Error('offline and nothing cached');
      }
    })());
    return;
  }

  e.respondWith(caches.match(req).then(hit => hit || fetch(req).then(res => {
    if (res.ok) caches.open(SHELL).then(c => c.put(req, res.clone()));
    return res;
  })));
});
