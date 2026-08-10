// SmartShop Service Worker — PWA offline support
// Caches static assets + key pages so the site works even without internet.
// Strategy: Cache-first for static assets, Network-first for dynamic pages.

const CACHE_NAME = 'smartshop-v2';
const STATIC_ASSETS = [
    '/',
    '/static/css/design_system.css',
    '/static/js/main.js',
    '/static/manifest.json',
    '/static/icon-192.png',
    '/static/icon-512.png',
    '/offline',
];

// Install: pre-cache static assets
self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            return cache.addAll(STATIC_ASSETS).catch(() => {
                // Some assets may 404 — that's OK, cache what we can
            });
        })
    );
    self.skipWaiting();
});

// Activate: clean old caches
self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) => {
            return Promise.all(
                keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
            );
        })
    );
    self.clients.claim();
});

// Fetch: Cache-first for static, Network-first for dynamic
self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);

    // Skip non-GET requests
    if (event.request.method !== 'GET') return;

    // Static assets: cache-first
    if (
        url.pathname.startsWith('/static/') ||
        url.pathname === '/manifest.json' ||
        url.pathname.endsWith('.png') ||
        url.pathname.endsWith('.jpg') ||
        url.pathname.endsWith('.svg') ||
        url.pathname.endsWith('.ico') ||
        url.pathname.endsWith('.css') ||
        url.pathname.endsWith('.js')
    ) {
        event.respondWith(
            caches.match(event.request).then((cached) => {
                const fetched = fetch(event.request).then((response) => {
                    if (response && response.status === 200) {
                        const clone = response.clone();
                        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
                    }
                    return response;
                }).catch(() => cached);
                return cached || fetched;
            })
        );
        return;
    }

    // HTML pages: network-first, fall back to cache then offline page
    if (event.request.headers.get('Accept')?.includes('text/html')) {
        event.respondWith(
            fetch(event.request)
                .then((response) => {
                    if (response && response.status === 200) {
                        const clone = response.clone();
                        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
                    }
                    return response;
                })
                .catch(() => {
                    return caches.match(event.request).then((cached) => {
                        return cached || caches.match('/offline');
                    });
                })
        );
        return;
    }

    // API calls: network-only (no caching of dynamic data)
});

// Push notifications — full Web Push API support with actions
self.addEventListener('push', (event) => {
    const data = event.data ? event.data.json() : {};
    const title = data.title || 'סמארטשופ';
    const options = {
        body: data.message || 'דיל חדש מחכה לכם!',
        icon: '/static/icon-192.png',
        badge: '/static/icon-192.png',
        data: { url: data.url || '/' },
        // Action buttons — two quick choices without opening the site
        actions: [
            { action: 'view', title: 'צפה בדיל' },
            { action: 'dismiss', title: 'סגור' }
        ],
        // Require explicit user interaction to dismiss (not auto-dismiss)
        requireInteraction: data.requireInteraction || false,
        tag: data.tag || 'smartshop-deal',  // dedup same notification
        vibrate: [200, 100, 200],
    };
    event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    const url = event.notification.data?.url || '/';
    if (event.action === 'dismiss') {
        // Just close — no navigation
        return;
    }
    event.waitUntil(
        clients.matchAll({ type: 'window' }).then((windowClients) => {
            for (const client of windowClients) {
                if (client.url.includes(url) && 'focus' in client) {
                    return client.focus();
                }
            }
            return clients.openWindow(url);
        })
    );
});

// VAPID public key — stored at SW install time so pushsubscriptionchange
// can re-subscribe even when the old subscription is lost entirely.
let _vapidPublicKey = null;
self.addEventListener('message', (event) => {
    if (event.data && event.data.type === 'SET_VAPID_KEY') {
        _vapidPublicKey = event.data.key;
    }
});

// When the browser's push subscription changes (e.g. expiration),
// notify the server so it can update the stored endpoint.
self.addEventListener('pushsubscriptionchange', (event) => {
    const key = _vapidPublicKey
        || (event.oldSubscription && event.oldSubscription.options && event.oldSubscription.options.applicationServerKey);
    const options = key
        ? { userVisibleOnly: true, applicationServerKey: key }
        : { userVisibleOnly: true };
    event.waitUntil(
        self.registration.pushManager.subscribe(options).then((newSub) => {
            return fetch('/api/push/subscribe', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    endpoint: newSub.endpoint,
                    keys: { p256dh: newSub.toJSON().keys.p256dh, auth: newSub.toJSON().keys.auth },
                    old_endpoint: event.oldSubscription?.endpoint || null,
                }),
            });
        }).catch(() => {
            // If re-subscription fails, the server will clean up stale endpoints
            // when it tries to send and gets a 410 Gone.
        })
    );
});
