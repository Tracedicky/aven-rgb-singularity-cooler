const views = ['home', 'bio', 'gallery'];
const panels = new Map([...document.querySelectorAll('[data-view-panel]')].map((panel) => [panel.dataset.viewPanel, panel]));
const viewButtons = [...document.querySelectorAll('[data-view-target]')];
const tabButtons = [...document.querySelectorAll('[role="tab"][data-view-target]')];
const menuButton = document.querySelector('[data-menu-button]');
const viewMenu = document.querySelector('[data-view-menu]');
const header = document.querySelector('[data-header]');
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
let activeView = 'home';
let switching = false;

const viewFromHash = () => {
  const candidate = window.location.hash.replace('#', '');
  return views.includes(candidate) ? candidate : 'home';
};

const closeMenu = () => {
  document.body.classList.remove('menu-open');
  header?.classList.remove('menu-visible');
  viewMenu?.classList.remove('is-open');
  menuButton?.setAttribute('aria-expanded', 'false');
};

const openMenu = () => {
  document.body.classList.add('menu-open');
  header?.classList.add('menu-visible');
  viewMenu?.classList.add('is-open');
  menuButton?.setAttribute('aria-expanded', 'true');
  viewMenu?.querySelector('[role="tab"][aria-selected="true"]')?.focus();
};

menuButton?.addEventListener('click', () => {
  const isOpen = menuButton.getAttribute('aria-expanded') === 'true';
  isOpen ? closeMenu() : openMenu();
});

const updateTabs = (nextView) => {
  tabButtons.forEach((button) => button.setAttribute('aria-selected', String(button.dataset.viewTarget === nextView)));
};

const revealPanelItems = (panel) => {
  const items = panel.querySelectorAll('.reveal:not(.is-visible)');
  if (reducedMotion || !('IntersectionObserver' in window)) {
    items.forEach((item) => item.classList.add('is-visible'));
    return;
  }
  const observer = new IntersectionObserver((entries, itemObserver) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add('is-visible');
      itemObserver.unobserve(entry.target);
    });
  }, { threshold: 0.12, rootMargin: '0px 0px -35px' });
  items.forEach((item) => observer.observe(item));
};

const switchView = (nextView, { updateHistory = true, moveFocus = true } = {}) => {
  if (!views.includes(nextView) || switching) return;
  closeMenu();
  if (nextView === activeView) {
    window.scrollTo({ top: 0, behavior: reducedMotion ? 'auto' : 'smooth' });
    return;
  }

  switching = true;
  const currentPanel = panels.get(activeView);
  const nextPanel = panels.get(nextView);
  currentPanel?.classList.add('is-leaving');

  window.setTimeout(() => {
    currentPanel?.classList.remove('is-active', 'is-leaving');
    if (currentPanel) {
      currentPanel.hidden = true;
      currentPanel.inert = true;
    }
    nextPanel.hidden = false;
    nextPanel.inert = false;
    document.body.dataset.currentView = nextView;
    window.scrollTo(0, 0);
    requestAnimationFrame(() => nextPanel.classList.add('is-active'));

    activeView = nextView;
    updateTabs(nextView);
    revealPanelItems(nextPanel);
    if (updateHistory) history.pushState({ view: nextView }, '', nextView === 'home' ? '#home' : `#${nextView}`);
    if (moveFocus) window.setTimeout(() => nextPanel.querySelector('h1, h2')?.focus({ preventScroll: true }), 350);
    switching = false;
  }, reducedMotion ? 10 : 280);
};

viewButtons.forEach((button) => button.addEventListener('click', () => switchView(button.dataset.viewTarget)));
window.addEventListener('popstate', () => switchView(viewFromHash(), { updateHistory: false, moveFocus: false }));
document.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeMenu(); });

activeView = viewFromHash();
panels.forEach((panel, name) => {
  const isActive = name === activeView;
  panel.hidden = !isActive;
  panel.inert = !isActive;
  panel.classList.toggle('is-active', isActive);
});
document.body.dataset.currentView = activeView;
updateTabs(activeView);
revealPanelItems(panels.get(activeView));

const lightbox = document.querySelector('[data-lightbox-dialog]');
const lightboxImage = document.querySelector('[data-lightbox-image]');
const lightboxCaption = document.querySelector('[data-lightbox-caption]');

document.querySelectorAll('[data-lightbox]').forEach((trigger) => {
  trigger.addEventListener('click', () => {
    if (!lightbox || !lightboxImage) return;
    lightboxImage.src = trigger.dataset.lightbox;
    lightboxImage.alt = trigger.querySelector('img')?.alt || 'Expanded portfolio image';
    if (lightboxCaption) lightboxCaption.textContent = trigger.dataset.caption || '';
    lightbox.showModal();
  });
});

const closeLightbox = () => {
  if (!lightbox?.open) return;
  lightbox.close();
  if (lightboxImage) lightboxImage.src = '';
};

document.querySelector('[data-lightbox-close]')?.addEventListener('click', closeLightbox);
lightbox?.addEventListener('click', (event) => { if (event.target === lightbox) closeLightbox(); });

document.querySelectorAll('video').forEach((video) => {
  video.addEventListener('play', () => document.querySelectorAll('video').forEach((other) => { if (other !== video) other.pause(); }));
});

