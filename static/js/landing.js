/* Small, local-only interactions for the public product demo. */
(() => {
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  const selectTab = (tab, focus = false) => {
    tabs.forEach((item) => {
      const selected = item === tab;
      item.setAttribute('aria-selected', String(selected));
      item.tabIndex = selected ? 0 : -1;
      document.getElementById(item.getAttribute('aria-controls')).hidden = !selected;
    });
    if (focus) tab.focus();
  };
  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => selectTab(tab));
    tab.addEventListener('keydown', (event) => {
      let next;
      if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
      if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = tabs.length - 1;
      if (next !== undefined) {
        event.preventDefault();
        selectTab(tabs[next], true);
      }
    });
  });
  document.querySelectorAll('[data-show-tab]').forEach((link) => {
    link.addEventListener('click', () => selectTab(document.getElementById(`tab-${link.dataset.showTab}`)));
  });

  const menuButton = document.querySelector('.menu-toggle');
  const mobileNav = document.getElementById('mobile-nav');
  menuButton.hidden = false;
  const closeMenu = () => {
    mobileNav.hidden = true;
    menuButton.setAttribute('aria-expanded', 'false');
    menuButton.setAttribute('aria-label', 'Ouvrir le menu');
  };
  menuButton.addEventListener('click', () => {
    const open = menuButton.getAttribute('aria-expanded') !== 'true';
    mobileNav.hidden = !open;
    menuButton.setAttribute('aria-expanded', String(open));
    menuButton.setAttribute('aria-label', open ? 'Fermer le menu' : 'Ouvrir le menu');
  });
  mobileNav.querySelectorAll('a').forEach((link) => link.addEventListener('click', closeMenu));
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !mobileNav.hidden) {
      closeMenu();
      menuButton.focus();
    }
  });
  window.matchMedia('(min-width: 901px)').addEventListener('change', (event) => {
    if (event.matches) closeMenu();
  });

  const examples = {
    nova: ['Product Designer', 'Studio Nova · Bruxelles · Hybride', 'À préparer · 92 % de compatibilité', 'Relisez l’offre et rassemblez les projets qui montrent votre expérience en design produit. Préparez ensuite votre CV pour cette candidature.'],
    horizon: ['UX Researcher', 'Horizon · À distance', 'À préparer', 'Consultez les missions et notez les études utilisateur qui illustrent le mieux votre parcours.'],
    bloom: ['Senior UX Designer', 'Bloom · Louvain-la-Neuve · Hybride', 'Envoyée · Relance demain', 'Préparez un message de suivi pour confirmer votre intérêt et demander où en est le recrutement.'],
    forma: ['Designer d’interface', 'Forma · Bruxelles', 'Envoyée · Relance dans 7 jours', 'Votre candidature est envoyée. Gardez vos notes à jour et retrouvez ici la date de votre prochaine relance.'],
    lumen: ['Lead Product Designer', 'Lumen · Bruxelles · Hybride', 'Entretien · Jeudi 17 à 10:00', 'Préparez vos questions pour l’équipe et choisissez les projets que vous souhaitez présenter pendant cet entretien de 45 minutes.'],
  };
  const dialog = document.querySelector('.job-dialog');
  document.querySelectorAll('[data-job]').forEach((card) => {
    card.addEventListener('click', () => {
      const [title, company, status, next] = examples[card.dataset.job];
      document.getElementById('dialog-title').textContent = title;
      document.getElementById('dialog-company').textContent = company;
      document.getElementById('dialog-status').textContent = status;
      document.getElementById('dialog-next').textContent = next;
      dialog.showModal();
    });
  });
  dialog.querySelector('.dialog-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', (event) => {
    const bounds = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom)) dialog.close();
  });
})();
