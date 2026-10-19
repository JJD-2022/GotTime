document.addEventListener('DOMContentLoaded', () => {
  const themeToggle = document.getElementById('themeToggle');
  if (themeToggle) {
    themeToggle.addEventListener('click', () => {
      const currentTheme = document.body.getAttribute('data-theme') || 'dark';
      const nextTheme = currentTheme === 'dark' ? 'light' : 'dark';
      document.body.setAttribute('data-theme', nextTheme);
      localStorage.setItem('gottime-theme', nextTheme);
    });
  }

  const savedTheme = localStorage.getItem('gottime-theme');
  if (savedTheme) {
    document.body.setAttribute('data-theme', savedTheme);
  }

  const confettiBtn = document.getElementById('confettiBtn');
  if (confettiBtn) {
    confettiBtn.addEventListener('click', () => {
      confetti({ particleCount: 120, spread: 80, origin: { y: 0.7 } });
      Swal.fire({ title: 'Reward unlocked!', text: 'You earned a celebration boost!', icon: 'success' });
    });
  }
});
