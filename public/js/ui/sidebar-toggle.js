// Sidebar hamburger toggle for mobile/tablet viewports
import { $ } from '../core/dom.js';

try {
  if (typeof document === 'undefined' || typeof window === 'undefined') {
    throw new Error('browser shell unavailable');
  }

  const btn = document.getElementById('sidebar-toggle-btn');
  const mobileBtn = document.getElementById('mobile-sidebar-toggle');
  const backdrop = document.getElementById('sidebar-backdrop');
  const mq = typeof window.matchMedia === 'function'
    ? window.matchMedia('(max-width: 1024px)')
    : { matches: false, addEventListener: null };

  function openSidebar()  { document.body.classList.add('sidebar-open'); }
  function closeSidebar() { document.body.classList.remove('sidebar-open'); }

  if (btn) btn.addEventListener('click', () => {
    document.body.classList.toggle('sidebar-open');
  });

  if (mobileBtn) mobileBtn.addEventListener('click', () => {
    document.body.classList.toggle('sidebar-open');
  });

  if (backdrop) backdrop.addEventListener('click', closeSidebar);

  if ($.sessionList) {
    $.sessionList.addEventListener('click', (e) => {
      if (mq.matches && e.target.closest('li')) {
        closeSidebar();
      }
    });
  }

  mq.addEventListener?.('change', (e) => {
    if (!e.matches) closeSidebar();
  });
} catch (err) {
  console.warn('[sidebar-toggle] init skipped:', err.message);
}
