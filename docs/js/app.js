document.addEventListener('DOMContentLoaded', () => {
    // --- Menu mobile (identique au portfolio) ---
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('mobile-overlay');
    const openBtn = document.getElementById('open-menu-btn');
    const closeBtn = document.getElementById('close-menu-btn');

    function openMenu() {
        sidebar.classList.remove('-translate-x-full');
        sidebar.classList.add('translate-x-0');
        overlay.classList.remove('hidden');
        requestAnimationFrame(() => overlay.classList.remove('opacity-0'));
    }

    function closeMenu() {
        sidebar.classList.remove('translate-x-0');
        sidebar.classList.add('-translate-x-full');
        overlay.classList.add('opacity-0');
        setTimeout(() => overlay.classList.add('hidden'), 300);
    }

    if (openBtn) openBtn.addEventListener('click', openMenu);
    if (closeBtn) closeBtn.addEventListener('click', closeMenu);
    if (overlay) overlay.addEventListener('click', closeMenu);
    document.querySelectorAll('#sidebar a[href^="#"]').forEach(a => {
        a.addEventListener('click', () => {
            if (window.innerWidth < 768) closeMenu();
        });
    });

    // --- Langue FR / EN ---
    const langBtn = document.getElementById('lang-btn');
    let lang = 'fr';
    try {
        lang = localStorage.getItem('easyshm-lang') || (navigator.language.startsWith('fr') ? 'fr' : 'en');
    } catch (e) { /* stockage indisponible */ }

    function applyLang() {
        document.documentElement.lang = lang;
        document.querySelectorAll('[data-en]').forEach(el => {
            const text = el.getAttribute('data-' + lang);
            if (text !== null) el.innerHTML = text;
        });
        if (langBtn) langBtn.querySelector('span').textContent = lang === 'fr' ? 'EN' : 'FR';
    }

    if (langBtn) {
        langBtn.addEventListener('click', () => {
            lang = lang === 'fr' ? 'en' : 'fr';
            try { localStorage.setItem('easyshm-lang', lang); } catch (e) { }
            applyLang();
        });
    }
    applyLang();

    // --- Copier la commande d'installation ---
    document.querySelectorAll('[data-copy]').forEach(btn => {
        btn.addEventListener('click', () => {
            navigator.clipboard.writeText(btn.getAttribute('data-copy')).then(() => {
                const icon = btn.querySelector('.material-symbols-outlined');
                icon.textContent = 'check';
                setTimeout(() => (icon.textContent = 'content_copy'), 1500);
            });
        });
    });

    // --- Scrollspy (page wiki) ---
    const scroller = document.getElementById('main-scroll');
    const tocLinks = document.querySelectorAll('.toc-link');
    const title = document.getElementById('topbar-title');
    if (scroller && tocLinks.length) {
        const sections = [...tocLinks].map(l => document.querySelector(l.getAttribute('href'))).filter(Boolean);
        const onScroll = () => {
            let current = sections[0];
            for (const s of sections) {
                if (s.getBoundingClientRect().top < 140) current = s;
            }
            tocLinks.forEach(l => l.classList.toggle('active', l.getAttribute('href') === '#' + current.id));
            const h2 = current.querySelector('h2');
            if (title && h2) title.textContent = h2.textContent.trim();
        };
        scroller.addEventListener('scroll', onScroll, { passive: true });
        onScroll();
    }
});
