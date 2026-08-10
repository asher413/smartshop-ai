// SmartShop AI — shared frontend behavior.
// Note: social proof and free-shipping progress use REAL numbers from the
// API only. The original prototype padded click counts with
// random.randint(10,50) and generated a random "N people bought this"
// figure client-side on a timer — that's fabricated social proof, a dark
// pattern that erodes trust the moment a user reloads and sees numbers
// jump for no reason. If there's no real recent activity, we simply don't
// show the bubble.

function toggleChat() {
    document.getElementById('chat-window').classList.toggle('hidden');
}

function recordAdClick(adId) {
    if (adId) fetch(`/api/ads/${adId}/click`, { method: 'POST' });
}

// --- One-click favorite from product cards (home + search) ---
// Reuses the same toggle endpoint as the product page; redirects to login
// when anonymous. The heart fills/empties live without a page reload so
// visitors can build a wishlist in seconds. Also backs up to localStorage
// so favorites survive refreshes and work offline.
const LS_FAVORITES_KEY = 'sm_favorites';

function getLocalFavorites() {
    try { return JSON.parse(localStorage.getItem(LS_FAVORITES_KEY)) || []; }
    catch (e) { return []; }
}

function saveLocalFavorites(ids) {
    try { localStorage.setItem(LS_FAVORITES_KEY, JSON.stringify(ids)); }
    catch (e) { /* quota exceeded */ }
}

async function toggleCardFavorite(productId, btn) {
    const icon = btn.querySelector('i') || btn;
    btn.disabled = true;
    try {
        const res = await fetch(`/api/favorites/${productId}/toggle`, { method: 'POST' });
        if (res.status === 401) { window.location.href = '/login'; return; }
        const data = await res.json();
        const active = !!data.favorited;
        icon.className = active ? 'fa-solid fa-heart text-red-500' : 'fa-regular fa-heart text-gray-400';
        btn.classList.toggle('faved', active);
        btn.title = active ? 'הסר מהמועדפים' : 'שמור למועדפים';
        btn.setAttribute('aria-label', active ? 'הסר ממועדפים' : 'שמור למועדפים');
        // Sync localStorage
        let favs = getLocalFavorites();
        if (active) { if (!favs.includes(productId)) favs.push(productId); }
        else { favs = favs.filter(id => id !== productId); }
        saveLocalFavorites(favs);
        // tiny pop for tactile feedback
        btn.animate(
            [{ transform: 'scale(1)' }, { transform: 'scale(1.35)' }, { transform: 'scale(1)' }],
            { duration: 220, easing: 'ease-out' }
        );
    } catch (e) { /* non-critical — API down, localStorage still works */ }
    finally { btn.disabled = false; }
}

// On page load, mark cards whose product IDs are in localStorage as faved
// so returning visitors see their hearts filled before the API responds.
document.addEventListener('DOMContentLoaded', () => {
    const favIds = getLocalFavorites();
    if (!favIds.length) return;
    document.querySelectorAll('.fav-btn').forEach(btn => {
        const match = btn.closest('[onclick*="toggleCardFavorite"]');
        if (!match) return;
        const onclick = match.getAttribute('onclick') || '';
        const idMatch = onclick.match(/toggleCardFavorite\((\d+)/);
        if (idMatch && favIds.includes(parseInt(idMatch[1]))) {
            const icon = btn.querySelector('i');
            if (icon) icon.className = 'fa-solid fa-heart text-red-500';
            btn.classList.add('faved');
            btn.title = 'הסר מהמועדפים';
            btn.setAttribute('aria-label', 'הסר ממועדפים');
        }
    });
});

// --- Deal-of-the-day countdown: counts down to midnight local time, then
// wraps to a fresh 24h cycle. Pure display — no fake urgency numbers.
document.addEventListener('DOMContentLoaded', () => {
    const wrap = document.getElementById('deal-countdown');
    if (wrap) {
        const hoursEl = document.getElementById('dc-hours');
        const minutesEl = document.getElementById('dc-minutes');
        const secondsEl = document.getElementById('dc-seconds');
        const tick = () => {
            const now = new Date();
            const end = new Date(now);
            end.setHours(24, 0, 0, 0);
            let diff = Math.max(0, end - now) / 1000;
            const h = Math.floor(diff / 3600);
            const m = Math.floor((diff % 3600) / 60);
            const s = Math.floor(diff % 60);
            const pad = (n) => String(n).padStart(2, '0');
            hoursEl.textContent = pad(h);
            minutesEl.textContent = pad(m);
            secondsEl.textContent = pad(s);
        };
        tick();
        setInterval(tick, 1000);
    }
});

document.addEventListener('DOMContentLoaded', () => {
    initCookieConsent();
    initNotificationBell();
    initSiteAds();
    // Non-essential features (marketing popup, social proof, Google Fonts)
    // only run after the visitor explicitly accepts them. initCookieConsent
    // calls initNonEssentialFeatures() below when consent === 'all'.
    if (getCookiePreference() === 'all') initNonEssentialFeatures();
    const chatInput = document.getElementById('chat-input');
    if (chatInput) {
        chatInput.addEventListener('keypress', async (e) => {
            if (e.key !== 'Enter' || !e.target.value.trim()) return;
            const query = e.target.value;
            const history = document.getElementById('chat-history');
            history.innerHTML += `<div class="bg-white/5 p-2 rounded-lg self-end text-right max-w-[85%]">${escapeHtml(query)}</div>`;
            e.target.value = '';

            const mode = (query.includes('מתנה')) ? 'gift' : 'standard';
            const formData = new FormData();
            formData.append('query', query);
            formData.append('mode', mode);

            try {
                const response = await fetch('/api/chat', { method: 'POST', body: formData });
                const data = await response.json();
                let answerHtml = `<div class="bg-indigo-500/20 p-2 rounded-lg self-start text-left max-w-[85%]">${escapeHtml(data.answer)}`;
                const idMatch = data.answer.match(/ID:?\s*(\d+)/i);
                if (idMatch) {
                    answerHtml += `<button onclick="window.location.href='/go/${idMatch[1]}'" class="mt-2 block w-full btn-primary py-1 rounded font-bold text-xs">קנה עכשיו 🛒</button>`;
                }
                answerHtml += `</div>`;
                history.innerHTML += answerHtml;
                history.scrollTop = history.scrollHeight;
            } catch (err) {
                history.innerHTML += `<div class="bg-red-500/20 p-2 rounded-lg self-start">מצטערים, הצ'אט לא זמין כרגע.</div>`;
            }
        });
    }

    if (getCookiePreference() === 'all') {
        pollSocialProof();
        setInterval(pollSocialProof, 20000);
    }

    const newsletterForm = document.getElementById('newsletter-form');
    if (newsletterForm) {
        newsletterForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const emailInput = document.getElementById('newsletter-email');
            const honeypot = document.getElementById('newsletter-website');
            const msg = document.getElementById('newsletter-message');
            const formData = new FormData();
            formData.append('email', emailInput.value);
            if (honeypot) formData.append('website', honeypot.value);
            try {
                const res = await fetch('/api/newsletter', { method: 'POST', body: formData });
                const data = await res.json();
                msg.textContent = data.message;
                msg.className = data.status === 'ok' ? 'text-xs text-green-400' : 'text-xs text-red-400';
                if (data.status === 'ok') emailInput.value = '';
            } catch (err) {
                msg.textContent = 'שגיאה בהרשמה, נסו שוב.';
                msg.className = 'text-xs text-red-400';
            }
        });
    }
});

// --- Marketing popup: show latest broadcast ONCE per browser (localStorage) ---
let popupLink = '/';
let popupId = null;

async function initMarketingPopup() {
    const modal = document.getElementById('marketing-popup');
    if (!modal) return;
    try {
        const res = await fetch('/api/popup');
        const data = await res.json();
        if (!data.popup) return;
        const key = `popup_seen_${data.popup.id}`;
        if (localStorage.getItem(key)) return;
        popupId = data.popup.id;
        popupLink = data.popup.link || '/';
        document.getElementById('popup-title').textContent = data.popup.title || 'דילים חמים! 🔥';
        document.getElementById('popup-message').textContent = data.popup.message || '';
        modal.classList.remove('hidden');
        modal.classList.add('flex');
    } catch (err) { /* popup is marketing — fail silently */ }
}

function dismissMarketingPopup() {
    const modal = document.getElementById('marketing-popup');
    if (modal) { modal.classList.add('hidden'); modal.classList.remove('flex'); }
    if (popupId) {
        localStorage.setItem(`popup_seen_${popupId}`, '1');
        fetch(`/api/popup/${popupId}/dismiss`, { method: 'POST' });
    }
}

function marketingPopupAction() {
    if (popupId) {
        localStorage.setItem(`popup_seen_${popupId}`, '1');
        fetch(`/api/popup/${popupId}/dismiss`, { method: 'POST' });
    }
    window.location.href = popupLink;
}

// --- Notification bell ---
async function initNotificationBell() {
    const bell = document.getElementById('notification-bell');
    if (!bell) return;
    try {
        const res = await fetch('/api/notifications');
        const data = await res.json();
        const items = data.notifications || [];
        const badge = document.getElementById('notification-badge');
        if (items.length && badge) badge.textContent = items.length;
        const dropdown = document.getElementById('notification-dropdown');
        if (!dropdown) return;
        dropdown.innerHTML = items.length ? items.slice(0, 8).map(n => `
            <a href="${n.link || '/personal-area'}" class="block p-3 hover:bg-white/5 transition border-b border-white/5 last:border-0 text-sm" onclick="markNotifRead(${n.id})">
                <p class="font-bold">${escapeHtml(n.title)}</p>
                <p class="text-xs text-gray-400 line-clamp-2">${escapeHtml(n.message)}</p>
            </a>`).join('') : '<div class="p-4 text-sm text-gray-400">אין התראות חדשות</div>';
        bell.addEventListener('click', (e) => {
            e.stopPropagation();
            dropdown.classList.toggle('hidden');
        });
        document.addEventListener('click', () => dropdown.classList.add('hidden'));
    } catch (err) { /* bell is non-critical */ }
}

function markNotifRead(id) {
    fetch(`/api/notifications/${id}/read`, { method: 'POST' });
}

// --- Site-wide ads (bottom banner + sticky side rail) on every page ---
// Ads are clearly and unambiguously labeled "פרסומת" — a distinct header
// bar above the creative plus a corner badge on the image, so visitors can
// never mistake a sponsored banner for editorial content. (Both are
// required for FTC-style affiliate disclosure and build trust.)
// Each ad always shows a clear image: if the ad has no image_url or the
// image fails to load, a branded gradient placeholder with the ad name is
// rendered instead — no blank/"broken image" tiles.
function adImageSrc(ad) {
    return (ad.image_url && ad.image_url.trim()) ? ad.image_url : '';
}

function adImageFallback(img, name) {
    // Replace a broken/empty image with a clear branded gradient tile.
    const div = document.createElement('div');
    div.className = 'w-full h-full min-h-[112px] flex flex-col items-center justify-center gap-1 bg-gradient-to-br from-indigo-600/40 via-purple-600/30 to-black text-center px-4';
    div.innerHTML = `<span class="text-2xl">🛍️</span><span class="text-xs font-bold text-white">${escapeHtml(name || 'מבצע חם')}</span><span class="text-[9px] text-white/60">פרסומת</span>`;
    img.replaceWith(div);
}

async function initSiteAds() {
    try {
        const res = await fetch('/api/site-ads');
        const data = await res.json();
        if (data.bottom && data.bottom.length) {
            const container = document.getElementById('site-bottom-ads');
            if (container) {
                container.innerHTML = `
                    <div class="glass-card overflow-hidden border border-white/10">
                        <div class="flex items-center justify-between px-4 py-2 bg-white/5 border-b border-white/10">
                            <span class="text-[11px] font-bold text-gray-300 flex items-center gap-1.5">
                                <i class="fa-solid fa-ad text-gray-400"></i> פרסומות של השותפים שלנו
                            </span>
                            <span class="text-[10px] text-gray-500">תוכן ממומן</span>
                        </div>
                        ${data.bottom.map(ad => `
                        <a href="${escapeHtml(ad.target_url)}" onclick="recordAdClick(${ad.id})" rel="sponsored" class="block relative group border-b border-white/5 last:border-0">
                            <div class="relative">
                                <img src="${escapeHtml(adImageSrc(ad))}" alt="${escapeHtml(ad.name)}" onerror="adImageFallback(this, ${JSON.stringify(ad.name)})" class="w-full h-28 object-cover group-hover:scale-[1.02] transition-transform duration-500">
                                <span class="absolute top-2 right-2 bg-red-600 text-white text-[10px] font-black px-2 py-1 rounded-md shadow-lg">פרסומת</span>
                                <div class="absolute bottom-0 inset-x-0 bg-gradient-to-t from-black/80 to-transparent p-3">
                                    <p class="text-sm font-bold text-white truncate">${escapeHtml(ad.name)}</p>
                                </div>
                            </div>
                        </a>`).join('')}
                    </div>`;
                container.classList.remove('hidden');
            }
        }
        if (data.side && data.side.length) {
            const side = document.getElementById('site-side-ad');
            if (side) {
                const ad = data.side[0];
                side.innerHTML = `
                    <a href="${escapeHtml(ad.target_url)}" onclick="recordAdClick(${ad.id})" rel="sponsored" class="block">
                        <div class="glass-card overflow-hidden border border-white/10">
                            <div class="flex items-center justify-between px-2 py-1 bg-white/5 border-b border-white/10">
                                <span class="text-[10px] font-bold text-gray-300"><i class="fa-solid fa-ad text-gray-400 ml-1"></i>פרסומת</span>
                            </div>
                            <div class="relative">
                                <img src="${escapeHtml(adImageSrc(ad))}" alt="${escapeHtml(ad.name)}" onerror="adImageFallback(this, ${JSON.stringify(ad.name)})" class="w-full h-40 object-cover">
                                <span class="absolute top-1 right-1 bg-red-600 text-white text-[9px] font-black px-1.5 py-0.5 rounded shadow">פרסומת</span>
                            </div>
                            <div class="p-2"><p class="text-[10px] font-bold truncate">${escapeHtml(ad.name)}</p></div>
                        </div>
                    </a>`;
                side.classList.remove('hidden');
            }
        }
    } catch (err) { /* ads are non-critical */ }
}

// Rotating social-proof messages — real data with storytelling flavor.
const SOCIAL_MESSAGES = [
    (d) => `${d.count} רכישות אושרו דרכנו בשעה האחרונה`,
    (d) => d.saved_amount ? `משתמש חסך ₪${Math.round(d.saved_amount)} בקנייה חכמה` : `${d.count} רכישות אושרו דרכנו בשעה האחרונה`,
    (d) => d.saved_amount ? `חיסכון ממוצע של ₪${Math.round(d.saved_amount / Math.max(1, d.count))} לרכישה — רק היום` : `${d.count} קונים מצאו דילים טובים יותר`,
];
let _socialMsgIdx = 0;

async function pollSocialProof() {
    const bubble = document.getElementById('social-proof');
    if (!bubble) return;
    try {
        const res = await fetch('/api/social-proof');
        const data = await res.json();
        if (!data.count || data.count < 1) {
            bubble.style.opacity = '0';
            return;
        }
        const fn = SOCIAL_MESSAGES[_socialMsgIdx % SOCIAL_MESSAGES.length];
        document.getElementById('social-proof-text').innerText = fn(data);
        _socialMsgIdx++;
        bubble.style.opacity = '1';
        bubble.style.transform = 'translateY(0)';
        setTimeout(() => { bubble.style.opacity = '0'; bubble.style.transform = 'translateY(4px)'; }, 5000);
    } catch (err) {
        // fail silently
    }
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// --- Cookie consent: 'essential' | 'all' | null (not yet decided) ---
function getCookiePreference() {
    return localStorage.getItem('cookie_consent_pref') || null;
}

function initCookieConsent() {
    const banner = document.getElementById('cookie-consent');
    if (!banner) return;
    if (getCookiePreference()) {
        banner.classList.add('hidden');
        return;
    }
    banner.classList.remove('hidden');
}

function saveCookiePreference(pref) {
    localStorage.setItem('cookie_consent_pref', pref);
    const banner = document.getElementById('cookie-consent');
    if (banner) banner.classList.add('hidden');
    if (pref === 'all') initNonEssentialFeatures();
}

function acceptEssentialCookies() {
    saveCookiePreference('essential');
}

function acceptAllCookies() {
    saveCookiePreference('all');
}

// Non-essential, consent-gated features: marketing popup + social proof
// bubble + Google Fonts. Blocked until the visitor clicks "accept all".
function initNonEssentialFeatures() {
    initMarketingPopup();
    pollSocialProof();
    // Google Fonts is a third-party request — only load after consent.
    if (!document.getElementById('google-fonts-link')) {
        const link = document.createElement('link');
        link.id = 'google-fonts-link';
        link.rel = 'stylesheet';
        link.href = 'https://fonts.googleapis.com/css2?family=Assistant:wght@300;400;700;800&display=swap';
        document.head.appendChild(link);
    }
}

// Live search autocomplete — debounced so it doesn't fire an API call on
// every single keystroke (which would both feel laggy and hit the rate
// limit on /api/search-suggest unnecessarily fast).
let searchDebounceTimer = null;

function bindSearchSuggestions(inputId, dropdownId) {
    const input = document.getElementById(inputId);
    const dropdown = document.getElementById(dropdownId);
    if (!input || !dropdown) return;

    input.addEventListener('input', () => {
        clearTimeout(searchDebounceTimer);
        const q = input.value.trim();
        if (q.length < 2) {
            dropdown.classList.add('hidden');
            return;
        }
        searchDebounceTimer = setTimeout(async () => {
            try {
                const res = await fetch(`/api/search-suggest?q=${encodeURIComponent(q)}`);
                const data = await res.json();
                renderSuggestions(dropdown, data.results || []);
            } catch (e) { /* autocomplete is non-critical, fail silently */ }
        }, 250);
    });

    document.addEventListener('click', (e) => {
        if (!dropdown.contains(e.target) && e.target !== input) dropdown.classList.add('hidden');
    });
}

function renderSuggestions(dropdown, results) {
    if (!results.length) { dropdown.classList.add('hidden'); return; }
    // Rich autocomplete: show category badge + rating + source icon when available
    dropdown.innerHTML = results.map(r => {
        const sourceIcon = r.source_adapter
            ? `<span class="text-[10px] text-gray-400">${escapeHtml(r.source_adapter)}</span>`
            : '';
        const catBadge = r.category
            ? `<span class="text-[10px] bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded">${escapeHtml(r.category)}</span>`
            : '';
        const ratingBadge = r.rating && r.rating > 0
            ? `<span class="text-[10px] text-amber-500"><i class="fa-solid fa-star"></i> ${Number(r.rating).toFixed(1)}</span>`
            : '';
        return `
        <a href="/product/${r.id}" class="flex items-center gap-3 p-3 hover:bg-white/5 transition border-b border-white/5 last:border-0">
            <img src="${escapeHtml(r.image_url || '')}" class="w-10 h-10 rounded-lg object-cover" onerror="this.style.display='none'">
            <div class="flex-1 min-w-0">
                <p class="text-sm truncate font-bold">${escapeHtml(r.name)}</p>
                <div class="flex items-center gap-1.5 mt-0.5">${catBadge}${ratingBadge}${sourceIcon}</div>
            </div>
            <span class="text-gold text-sm font-bold shrink-0">₪${Math.round(r.price || 0)}</span>
        </a>`;
    }).join('');
    dropdown.classList.remove('hidden');
}

document.addEventListener('DOMContentLoaded', () => {
    bindSearchSuggestions('nav-search-input', 'search-suggestions');
    bindSearchSuggestions('nav-search-input-mobile', 'search-suggestions-mobile');
    upgradeFancySelects();
});

// ============ Custom filter dropdowns (AliExpress-style) ============
// Native <select> with dark glass styling opens the OS option list, which
// on Windows renders dark-on-dark text that's unreadable. Any <select>
// with class="fancy-select" is rebuilt into a custom dropdown that always
// draws its own readable menu: white panel, blue selected row, gray rows.
// The hidden original <select> is kept (screen readers + form submit).

window.fancySelects = {};  // id -> {select, menu, options, onChange}

function upgradeFancySelect(select) {
    if (!select || select.dataset.fancyBuilt) return;
    select.dataset.fancyBuilt = '1';

    const wrap = document.createElement('div');
    wrap.className = 'fd';
    const id = select.id || ('fancy-' + Math.random().toString(36).slice(2, 8));
    wrap.id = 'fd-' + id;
    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(select);
    select.setAttribute('aria-hidden', 'true');
    select.setAttribute('tabindex', '-1');

    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'fd-trigger';
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');
    trigger.innerHTML = `<span class="fd-value truncate"></span><i class="fa-solid fa-chevron-down fd-caret"></i>`;

    const menu = document.createElement('div');
    menu.className = 'fd-menu';
    menu.setAttribute('role', 'listbox');

    wrap.appendChild(trigger);
    wrap.appendChild(menu);

    // Mirror the <select>'s native onChange so form.submit() etc. still
    // fires. Inline handlers like onchange="this.form.submit()" expect
    // `this` to be the <select>, so bind it explicitly.
    const originalOnChange = select.onchange;
    const state = {
        select, menu, options: [],
        onChange: originalOnChange ? originalOnChange.bind(select) : null,
        rebuild: null,
    };
    window.fancySelects[id] = state;

    function rebuild() {
        menu.innerHTML = '';
        state.options = [];
        Array.from(select.options).forEach((opt, idx) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'fd-option' + (opt.selected ? ' selected' : '');
            btn.setAttribute('role', 'option');
            btn.setAttribute('aria-selected', opt.selected ? 'true' : 'false');
            // ⭐ prefix renders as a gold star before the label. Strip any
            // star already in the option text (e.g. "4 ★ ומעלה") so the
            // rendered row is "★ 4 ומעלה" like the reference design.
            const raw = (opt.textContent || '').replace(/★/g, '').replace(/\s+/g, ' ').trim();
            btn.innerHTML = `<span class="fd-star">★</span><span class="fd-opt-label">${escapeHtml(raw)}</span>`;
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                select.selectedIndex = idx;
                rebuild();
                refreshValue();
                wrap.classList.remove('open');
                trigger.setAttribute('aria-expanded', 'false');
                if (state.onChange) state.onChange.call(select);
            });
            menu.appendChild(btn);
            state.options.push(btn);
        });
    }

    function refreshValue() {
        const label = trigger.querySelector('.fd-value');
        const raw = select.selectedOptions[0] ? select.selectedOptions[0].textContent : '';
        label.textContent = raw.replace(/★/g, '').replace(/\s+/g, ' ').trim();
    }

    trigger.addEventListener('click', (e) => {
        e.stopPropagation();
        toggleMenu();
    });

    function toggleMenu(forceOpen) {
        const isOpen = typeof forceOpen === 'boolean' ? forceOpen : !wrap.classList.contains('open');
        if (isOpen) {
            wrap.classList.add('open');
            trigger.setAttribute('aria-expanded', 'true');
            // Only one open at a time.
            document.querySelectorAll('.fd.open').forEach((other) => {
                if (other !== wrap) { other.classList.remove('open'); other.querySelector('.fd-trigger')?.setAttribute('aria-expanded', 'false'); }
            });
            const sel = menu.querySelector('.fd-option.selected') || menu.querySelector('.fd-option');
            if (sel) { sel.focus(); sel.scrollIntoView({ block: 'nearest' }); }
        } else {
            wrap.classList.remove('open');
            trigger.setAttribute('aria-expanded', 'false');
            trigger.focus();
        }
    }

    // Keyboard accessibility: Arrow Up/Down to navigate, Enter to select, Escape to close.
    // Tab also closes the menu and moves to the next focusable element.
    wrap.addEventListener('keydown', (e) => {
        const isOpen = wrap.classList.contains('open');
        if (!isOpen && (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown')) {
            e.preventDefault();
            toggleMenu(true);
            return;
        }
        if (!isOpen) return;

        const options = Array.from(menu.querySelectorAll('.fd-option'));
        const focused = document.activeElement;
        const idx = options.indexOf(focused);

        switch (e.key) {
            case 'ArrowDown':
                e.preventDefault();
                if (idx < options.length - 1) options[idx + 1].focus();
                else options[0].focus();
                break;
            case 'ArrowUp':
                e.preventDefault();
                if (idx > 0) options[idx - 1].focus();
                else options[options.length - 1].focus();
                break;
            case 'Enter':
            case ' ':
                e.preventDefault();
                if (focused && focused.classList.contains('fd-option')) {
                    focused.click();
                }
                break;
            case 'Escape':
                e.preventDefault();
                toggleMenu(false);
                break;
            case 'Tab':
                // Let natural tab order close the menu via the global click handler
                toggleMenu(false);
                break;
        }
    });

    // Make each option focusable for keyboard navigation
    const origRebuild = rebuild;
    rebuild = function() {
        origRebuild();
        menu.querySelectorAll('.fd-option').forEach(btn => {
            btn.setAttribute('tabindex', '-1');
        });
    };

    rebuild();
    refreshValue();
    state.rebuild = rebuild;  // for dynamically-populated selects (cf-supplier)
    return state;
}

function upgradeFancySelects() {
    document.querySelectorAll('select.fancy-select').forEach(upgradeFancySelect);
}

// Close any open custom dropdown when clicking elsewhere.
document.addEventListener('click', (e) => {
    document.querySelectorAll('.fd.open').forEach((wrap) => {
        if (!wrap.contains(e.target)) {
            wrap.classList.remove('open');
            const trig = wrap.querySelector('.fd-trigger');
            if (trig) trig.setAttribute('aria-expanded', 'false');
        }
    });
});

// Client-side filter dropdowns built dynamically (e.g. supplier list from
// loaded product cards): pass a <select> + a change callback.
function makeFancySelect(select, onChange) {
    const state = upgradeFancySelect(select);
    if (state && onChange) state.onChange = onChange;
    return state;
}

/* ── Sticky glass header on scroll ── */
(function() {
    const nav = document.querySelector('nav');
    if (!nav) return;
    let ticking = false;
    window.addEventListener('scroll', () => {
        if (!ticking) {
            requestAnimationFrame(() => {
                nav.classList.toggle('glass-scrolled', window.scrollY > 40);
                ticking = false;
            });
            ticking = true;
        }
    });
})();

/* ── Search focus: subtle border glow, NO full-page blur ── */
(function() {
    ['nav-search-input', 'nav-search-input-mobile'].forEach(id => {
        const input = document.getElementById(id);
        if (!input) return;
        const form = input.closest('form');
        input.addEventListener('focus', () => form && form.classList.add('search-focused'));
        input.addEventListener('blur', () => form && form.classList.remove('search-focused'));
    });
})();

/* ── Favorite fly animation ── */
(function() {
    const orig = window.toggleCardFavorite;
    window.toggleCardFavorite = function(productId, btn) {
        if (btn && !btn.classList.contains('faved')) {
            const rect = btn.getBoundingClientRect();
            const particle = document.createElement('span');
            particle.className = 'fly-particle';
            particle.innerHTML = '<i class="fa-solid fa-heart text-red-500"></i>';
            particle.style.left = rect.left + 'px';
            particle.style.top = rect.top + 'px';
            document.body.appendChild(particle);
            particle.addEventListener('animationend', () => particle.remove());
        }
        if (orig) orig(productId, btn);
    };
})();

/* ── Quick View modal ── */
(function() {
    const overlay = document.createElement('div');
    overlay.className = 'qv-overlay';
    overlay.id = 'qv-overlay';
    overlay.innerHTML = '<div class="qv-card" id="qv-card"></div>';
    overlay.addEventListener('click', function(e) { if (e.target === overlay) overlay.classList.remove('open'); });
    document.body.appendChild(overlay);

    window.openQuickView = function(productId) {
        const qv = document.getElementById('qv-overlay');
        const card = document.getElementById('qv-card');
        card.innerHTML = '<div class="text-center py-8 text-gray-400"><i class="fa-solid fa-spinner fa-spin text-2xl"></i><p class="mt-3 text-sm">טוען פרטי מוצר...</p></div>';
        qv.classList.add('open');
        fetch('/product/' + productId)
            .then(r => r.text())
            .then(html => {
                const parser = new DOMParser();
                const doc = parser.parseFromString(html, 'text/html');
                const name = doc.querySelector('h1')?.textContent || '';
                const price = doc.querySelector('.text-3xl')?.textContent || '';
                const img = doc.querySelector('.gallery-main img')?.src || '';
                const desc = doc.querySelector('.prose')?.textContent?.slice(0, 300) || '';
                card.innerHTML = `
                    <button onclick="document.getElementById('qv-overlay').classList.remove('open')" class="float-left text-gray-400 hover:text-gray-600 text-xl">&times;</button>
                    <div class="flex flex-col sm:flex-row gap-4 mt-2">
                        <img src="${img}" class="w-full sm:w-48 h-48 object-cover rounded-lg" onerror="this.src='https://placehold.co/400x300/f8fafc/94a3b8'">
                        <div class="flex-1">
                            <h3 class="font-bold text-lg mb-2">${name}</h3>
                            <p class="text-2xl font-bold text-gray-900 mb-2">${price}</p>
                            <p class="text-sm text-gray-500 mb-4">${desc}...</p>
                            <a href="/product/${productId}" class="btn-primary inline-block px-6 py-2 rounded-lg text-sm font-bold">צפה בפרטים מלאים</a>
                        </div>
                    </div>`;
            })
            .catch(() => { card.innerHTML = '<p class="text-red-500 text-center py-4">שגיאה בטעינת המוצר</p>'; });
    };
})();

// --- Web Push Notifications ---
let pushSubscription = null;
let pushVapidKey = null;

async function initPushBell() {
    const btn = document.getElementById('push-bell-btn');
    if (!btn) return;
    // Only show the bell if push is supported and not already subscribed.
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;

    // Check if VAPID is configured server-side.
    try {
        const res = await fetch('/api/push/vapid-public-key');
        const data = await res.json();
        if (!data.enabled) return;
        pushVapidKey = data.publicKey;
    } catch (e) { return; }

    // Check current permission + subscription status.
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (sub && Notification.permission === 'granted') {
        pushSubscription = sub;
        btn.classList.add('text-green-600');
        btn.title = 'התראות דפדפן פעילות';
    } else {
        btn.classList.remove('hidden');
    }
}

async function requestPushPermission() {
    const btn = document.getElementById('push-bell-btn');
    if (!btn) return;

    if (!('Notification' in window)) {
        alert('הדפדפן שלך לא תומך בהתראות Push.');
        return;
    }

    const permission = await Notification.requestPermission();
    if (permission !== 'granted') {
        btn.title = 'לא ניתן לשלוח התראות (ההרשאה נדחתה)';
        return;
    }

    // Subscribe to push.
    try {
        const reg = await navigator.serviceWorker.ready;
        // Pass the VAPID key to the SW so it can re-subscribe on pushsubscriptionchange.
        if (reg.active) {
            reg.active.postMessage({ type: 'SET_VAPID_KEY', key: pushVapidKey });
        }
        const sub = await reg.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: urlB64ToUint8Array(pushVapidKey),
        });
        pushSubscription = sub;

        // Send to server.
        await fetch('/api/push/subscribe', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(sub.toJSON()),
        });

        btn.classList.add('text-green-600');
        btn.title = 'התראות דפדפן פעילות — דילים חמים יגיעו ישירות אליכם!';
        btn.querySelector('i').className = 'fa-solid fa-bell-concierge';  // filled bell
    } catch (e) {
        console.error('Push subscription failed:', e);
        btn.title = 'שגיאה בהרשמה — נסו שוב מאוחר יותר';
    }
}

async function unsubscribePush() {
    if (pushSubscription) {
        try {
            await fetch('/api/push/unsubscribe', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ endpoint: pushSubscription.endpoint }),
            });
            await pushSubscription.unsubscribe();
        } catch (e) { /* silent */ }
        pushSubscription = null;
    }
    const btn = document.getElementById('push-bell-btn');
    if (btn) {
        btn.classList.remove('text-green-600');
        btn.title = 'קבלו דילים חמים ישירות לדפדפן';
        btn.querySelector('i').className = 'fa-solid fa-bell-concierge';
    }
    // Also refresh the personal-area panel if visible.
    const statusEl = document.getElementById('push-status-text');
    if (statusEl) {
        statusEl.textContent = 'לא פעילות';
        statusEl.className = 'text-sm text-gray-500';
    }
}

// Convert base64url VAPID public key to Uint8Array for pushManager.subscribe.
function urlB64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/\-/g, '+').replace(/_/g, '/');
    const rawData = atob(base64);
    const outputArray = new Uint8Array(rawData.length);
    for (let i = 0; i < rawData.length; ++i) {
        outputArray[i] = rawData.charCodeAt(i);
    }
    return outputArray;
}

// Auto-init on DOM ready.
document.addEventListener('DOMContentLoaded', () => {
    initPushBell();
    initPushStatusPanel();
    initSpinWheel();
});

// Push notification management UI in the personal area.
function initPushStatusPanel() {
    const area = document.getElementById('push-status-area');
    if (!area) return;
    const statusEl = document.getElementById('push-status-text');
    const detailEl = document.getElementById('push-status-detail');
    const btn = document.getElementById('push-manage-btn');

    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
        if (statusEl) statusEl.textContent = 'לא נתמך';
        if (detailEl) detailEl.textContent = 'הדפדפן שלך לא תומך בהתראות Push. נסה כרום או אדג".';
        return;
    }

    // Check current push state.
    navigator.serviceWorker.ready.then(async (reg) => {
        const sub = await reg.pushManager.getSubscription();
        if (sub && Notification.permission === 'granted') {
            pushSubscription = sub;
            if (statusEl) { statusEl.textContent = 'פעילות'; statusEl.className = 'text-sm font-bold text-green-600'; }
            if (detailEl) detailEl.textContent = 'אתם מקבלים התראות Push — דילים חמים יגיעו ישירות לדפדפן.';
            if (btn) { btn.textContent = 'בטל התראות'; btn.className = 'btn-secondary px-5 py-2 rounded-xl text-sm font-bold'; btn.onclick = unsubscribePush; }
        } else if (Notification.permission === 'denied') {
            if (statusEl) { statusEl.textContent = 'חסומות'; statusEl.className = 'text-sm font-bold text-red-500'; }
            if (detailEl) detailEl.textContent = 'ההרשאה להתראות נדחתה. אפשר לשנות זאת בהגדרות הדפדפן.';
            if (btn) btn.classList.add('hidden');
        } else {
            if (statusEl) { statusEl.textContent = 'לא פעילות'; statusEl.className = 'text-sm text-gray-500'; }
            if (detailEl) detailEl.textContent = 'לחצו להפעלת התראות Push וקבלו דילים חמים ישירות לדפדפן.';
            if (btn) { btn.textContent = 'הפעל התראות'; btn.className = 'btn-primary px-5 py-2 rounded-xl text-sm font-bold'; btn.onclick = requestPushPermission; btn.classList.remove('hidden'); }
        }
        if (btn) btn.classList.remove('hidden');
    }).catch(() => {
        if (statusEl) { statusEl.textContent = 'לא זמין'; statusEl.className = 'text-sm text-gray-500'; }
        if (detailEl) detailEl.textContent = 'לא ניתן לבדוק את סטטוס ההתראות כרגע.';
    });
}

// Wrapper for the button in the personal area.
function managePushSubscription() {
    if (Notification.permission === 'granted' && pushSubscription) {
        unsubscribePush().then(() => setTimeout(initPushStatusPanel, 300));
    } else if (Notification.permission === 'granted' || Notification.permission === 'default') {
        requestPushPermission().then(() => setTimeout(initPushStatusPanel, 500));
    } else {
        // Permission denied — user needs to change browser settings.
        alert('ההרשאה להתראות נדחתה. אפשר לשנות זאת בהגדרות הדפדפן (לחצו על המנעול ליד כתובת האתר).');
    }
}

/* ── Exit-intent popup: detects when mouse leaves the page (top edge),
   shows a last-chance 5% coupon. Fires once per session (localStorage). ── */
(function() {
    var EXIT_KEY = 'exit_popup_shown';
    var COUPON_KEY = 'exit_coupon_claimed';
    var fired = false;

    function shouldShow() {
        if (localStorage.getItem(EXIT_KEY)) return false;
        // Don't show on small screens (confusing on mobile where "exit" isn't mouse-based)
        if (window.innerWidth < 768) return false;
        return true;
    }

    document.addEventListener('mouseout', function(e) {
        if (fired || !shouldShow()) return;
        // Only trigger when mouse leaves via the TOP edge (toward browser chrome)
        if (e.clientY > 10) return;
        // Only if the related target is null (left the document entirely)
        if (e.relatedTarget !== null) return;

        fired = true;
        localStorage.setItem(EXIT_KEY, '1');
        var popup = document.getElementById('exit-popup');
        if (popup) {
            popup.classList.remove('hidden');
            popup.classList.add('flex');
            document.body.style.overflow = 'hidden';
        }
    });

    window.dismissExitPopup = function() {
        var popup = document.getElementById('exit-popup');
        if (popup) {
            popup.classList.add('hidden');
            popup.classList.remove('flex');
            document.body.style.overflow = '';
        }
    };

    window.exitPopupClaim = function() {
        localStorage.setItem(COUPON_KEY, 'BYE5');
        dismissExitPopup();
        // Navigate to signup or just keep browsing
        // Flash a subtle toast
        var toast = document.createElement('div');
        toast.className = 'fixed bottom-24 left-1/2 -translate-x-1/2 z-[100] bg-green-600 text-white px-5 py-3 rounded-xl shadow-2xl font-bold text-sm';
        toast.innerHTML = '<i class="fa-solid fa-check-circle mr-1"></i> הקופון BYE5 נשמר! 5% הנחה על כל הרכישות';
        document.body.appendChild(toast);
        setTimeout(function() { toast.remove(); }, 4000);
    };
})();

function focusMobileSearch() { var x = document.getElementById('nav-search-input-mobile'); if(x) { x.scrollIntoView({behavior:'smooth'}); setTimeout(function(){ x.focus(); }, 300); } }

/* ── Confetti burst on coupon / link copy ── */
function burstConfetti(x, y) {
    const colors = ['#fbbf24','#ef4444','#22c55e','#3b82f6','#a855f7','#f97316','#ec4899'];
    for (let i = 0; i < 14; i++) {
        const el = document.createElement('span');
        el.className = 'confetti-particle';
        el.style.left = x + 'px';
        el.style.top = y + 'px';
        el.style.backgroundColor = colors[Math.floor(Math.random() * colors.length)];
        const angle = Math.random() * Math.PI * 2;
        const dist = 40 + Math.random() * 60;
        el.style.setProperty('--tx', Math.cos(angle) * dist + 'px');
        el.style.setProperty('--ty', Math.sin(angle) * dist - 20 + 'px');
        el.style.animationDuration = (0.5 + Math.random() * 0.4) + 's';
        document.body.appendChild(el);
        el.addEventListener('animationend', function() { el.remove(); });
    }
}

// Hook into all copy-to-clipboard actions to add confetti
(function() {
    var origWrite = navigator.clipboard.writeText.bind(navigator.clipboard);
    navigator.clipboard.writeText = function(text) {
        // Find the last-clicked button (likely the copy trigger)
        var el = document.activeElement;
        if (el && (el.tagName === 'BUTTON' || el.tagName === 'A')) {
            var rect = el.getBoundingClientRect();
            burstConfetti(rect.left + rect.width / 2, rect.top);
        }
        return origWrite(text);
    };
})();

/* ── Daily Spin Wheel (gamification) ── */
var SPIN_PRIZES = [
    { label: '10 מטבעות', icon: 'fa-solid fa-coins', color: '#fbbf24', coins: 10 },
    { label: '5 מטבעות', icon: 'fa-solid fa-coins', color: '#34d399', coins: 5 },
    { label: '15 מטבעות', icon: 'fa-solid fa-coins', color: '#f472b6', coins: 15 },
    { label: 'קופון 5%', icon: 'fa-solid fa-ticket', color: '#60a5fa', coupon: 'SPIN5' },
    { label: '3 מטבעות', icon: 'fa-solid fa-coins', color: '#a78bfa', coins: 3 },
    { label: 'קופון 10%', icon: 'fa-solid fa-ticket', color: '#fb923c', coupon: 'SPIN10' },
    { label: '20 מטבעות', icon: 'fa-solid fa-coins', color: '#f87171', coins: 20 },
    { label: '25 מטבעות', icon: 'fa-solid fa-coins', color: '#22d3ee', coins: 25 },
];
var _spinSpunToday = false;
var _spinAnimationRunning = false;

function initSpinWheel() {
    // Check if already spun today
    var today = new Date().toISOString().slice(0, 10);
    if (localStorage.getItem('spin_date') === today) _spinSpunToday = true;

    // Draw the wheel on the canvas if the overlay exists
    var canvas = document.getElementById('spin-canvas');
    if (canvas) drawSpinWheel(canvas);

    // Show floating spin button only if not spun today
    if (!_spinSpunToday) {
        var fb = document.getElementById('spin-floating-btn');
        if (fb) fb.classList.remove('hidden');
    }
}

function drawSpinWheel(canvas) {
    if (!canvas) return;
    var ctx = canvas.getContext('2d');
    var segs = SPIN_PRIZES.length;
    var arc = (2 * Math.PI) / segs;
    var r = canvas.width / 2;
    var cx = r, cy = r;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    for (var i = 0; i < segs; i++) {
        var start = i * arc - Math.PI / 2;
        var end = start + arc;
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.arc(cx, cy, r - 2, start, end);
        ctx.closePath();
        ctx.fillStyle = SPIN_PRIZES[i].color;
        ctx.fill();
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = 2;
        ctx.stroke();
        // Label
        ctx.save();
        ctx.translate(cx, cy);
        ctx.rotate(start + arc / 2);
        ctx.textAlign = 'center';
        ctx.fillStyle = '#1e293b';
        ctx.font = 'bold 10px Heebo, Arial';
        ctx.fillText(SPIN_PRIZES[i].label, r * 0.55, 4);
        ctx.restore();
    }
    // Center circle
    ctx.beginPath();
    ctx.arc(cx, cy, r * 0.28, 0, 2 * Math.PI);
    ctx.fillStyle = '#fff';
    ctx.fill();
    ctx.strokeStyle = '#fbbf24';
    ctx.lineWidth = 3;
    ctx.stroke();
}

function openSpinWheel() {
    var overlay = document.getElementById('spin-wheel-overlay');
    if (!overlay) return;
    overlay.classList.add('flex');
    overlay.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
    var canvas = document.getElementById('spin-canvas');
    if (canvas) drawSpinWheel(canvas);
    document.getElementById('spin-result').textContent = 'סובבו וזכו במטבעות, קופונים והטבות!';
    document.getElementById('spin-btn').disabled = _spinSpunToday;
}

function closeSpinWheel() {
    var overlay = document.getElementById('spin-wheel-overlay');
    if (!overlay) return;
    overlay.classList.add('hidden');
    overlay.classList.remove('flex');
    document.body.style.overflow = '';
}

function spinTheWheel() {
    if (_spinAnimationRunning || _spinSpunToday) return;
    _spinAnimationRunning = true;
    var today = new Date().toISOString().slice(0, 10);
    var btn = document.getElementById('spin-btn');
    var canvas = document.getElementById('spin-canvas');
    var resultEl = document.getElementById('spin-result');
    if (btn) btn.disabled = true;

    // Random prize
    var idx = Math.floor(Math.random() * SPIN_PRIZES.length);
    var segArc = (2 * Math.PI) / SPIN_PRIZES.length;
    // Target: align segment idx to the top (offset by half-segment for center of segment)
    var targetAngle = (2 * Math.PI) - (idx * segArc) - (segArc / 2) + (Math.PI / 2);
    var totalSpin = targetAngle + (Math.PI * 2) * (6 + Math.floor(Math.random() * 4)); // 6-9 full rotations

    var duration = 4000;
    var startTime = null;
    var startAngle = parseFloat(canvas.dataset.angle || '0');

    function animate(ts) {
        if (!startTime) startTime = ts;
        var elapsed = ts - startTime;
        var progress = Math.min(elapsed / duration, 1);
        // Ease-out cubic
        var eased = 1 - Math.pow(1 - progress, 3);
        var currentAngle = startAngle + totalSpin * eased;
        canvas.style.transform = 'rotate(' + currentAngle + 'rad)';
        canvas.dataset.angle = currentAngle;

        if (progress < 1) {
            requestAnimationFrame(animate);
        } else {
            // Done spinning
            _spinAnimationRunning = false;
            _spinSpunToday = true;
            localStorage.setItem('spin_date', today);
            var prize = SPIN_PRIZES[idx];
            resultEl.innerHTML = '<i class="' + prize.icon + ' text-amber-500"></i> זכיתם ב-<b>' + prize.label + '</b>!';
            if (btn) { btn.disabled = true; btn.textContent = 'בוצע'; }

            // Send reward to server
            fetch('/api/spin-reward', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ prize: prize.label, coins: prize.coins || 0, coupon: prize.coupon || '' })
            }).catch(function(){});

            // Hide floating button
            setTimeout(function() {
                var fb = document.getElementById('spin-floating-btn');
                if (fb) fb.classList.add('hidden');
            }, 3000);
        }
    }
    requestAnimationFrame(animate);
}
