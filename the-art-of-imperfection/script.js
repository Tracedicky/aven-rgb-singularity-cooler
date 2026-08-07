
const header = document.querySelector('[data-header]');
const menuButton = document.querySelector('[data-menu-toggle]');
const nav = document.querySelector('[data-nav]');

const updateHeader = () => header.classList.toggle('scrolled', window.scrollY > 48);
updateHeader();
window.addEventListener('scroll', updateHeader, { passive: true });

menuButton.addEventListener('click', () => {
  const open = nav.classList.toggle('open');
  menuButton.setAttribute('aria-expanded', String(open));
  document.body.style.overflow = open ? 'hidden' : '';
});

nav.querySelectorAll('a').forEach(link => link.addEventListener('click', () => {
  nav.classList.remove('open');
  menuButton.setAttribute('aria-expanded', 'false');
  document.body.style.overflow = '';
}));

const observer = new IntersectionObserver(entries => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      entry.target.classList.add('visible');
      observer.unobserve(entry.target);
    }
  });
}, { threshold: 0.14 });
document.querySelectorAll('.reveal').forEach(element => observer.observe(element));

const dialog = document.querySelector('[data-lightbox-dialog]');
const dialogImage = document.querySelector('[data-lightbox-image]');
document.querySelectorAll('[data-lightbox]').forEach(card => card.addEventListener('click', () => {
  dialogImage.src = card.dataset.lightbox;
  dialog.showModal();
}));
document.querySelector('[data-lightbox-close]').addEventListener('click', () => dialog.close());
dialog.addEventListener('click', event => {
  if (event.target === dialog) dialog.close();
});

document.querySelector('[data-enquiry-form]').addEventListener('submit', event => {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  const recipient = '';
  const status = document.querySelector('[data-form-status]');

  if (!recipient) {
    status.textContent = 'Thank you — Mel’s enquiry address will be connected before the studio opens.';
    status.focus?.();
    return;
  }

  const subject = encodeURIComponent(`Cake enquiry from ${data.get('name')}`);
  const body = encodeURIComponent([
    `Name: ${data.get('name')}`,
    `Email: ${data.get('email')}`,
    `Celebration date: ${data.get('date') || 'Not decided'}`,
    `Guests: ${data.get('guests') || 'Not decided'}`,
    '',
    data.get('message')
  ].join('\n'));
  window.location.href = `mailto:${recipient}?subject=${subject}&body=${body}`;
});

document.querySelector('[data-year]').textContent = new Date().getFullYear();
