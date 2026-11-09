const themeButton = document.querySelector('#theme');
const themeLabel = document.querySelector('#theme-label');
let theme;
try { theme = localStorage.getItem('gardifund-launch-theme'); } catch { /* Session-only theme still works. */ }
if (!['light', 'dark'].includes(theme)) theme = 'light';
function applyTheme() {
  document.documentElement.dataset.theme = theme;
  themeLabel.textContent = theme === 'dark' ? 'Light mode' : 'Dark mode';
  themeButton.setAttribute('aria-label', `Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`);
}
applyTheme();
themeButton.addEventListener('click', () => {
  theme = theme === 'dark' ? 'light' : 'dark';
  applyTheme();
  try { localStorage.setItem('gardifund-launch-theme', theme); } catch { /* Session-only theme still works. */ }
});

// This page never fetches portfolio data, reads credentials, or contacts localhost.
fetch('./config.json', { cache: 'no-store' }).then(async response => {
  if (!response.ok) throw new Error('Configuration unavailable');
  const config = await response.json();
  if (!config.dashboard_url) return;
  const target = new URL(config.dashboard_url);
  if (target.protocol !== 'https:' || target.username || target.password || target.search || target.hash || target.pathname !== '/') throw new Error('Invalid address');
  const link = document.querySelector('#dashboard');
  link.href = target.href;
  link.setAttribute('aria-disabled', 'false');
  link.removeAttribute('tabindex');
  document.querySelector('#launch-label').textContent = 'Open the secure dashboard';
  document.querySelector('#connection-note').textContent = 'Sign in through Cloudflare Access. If the dashboard is offline, contact the admin to start the GARDiFUND server.';
}).catch(() => {
  document.querySelector('#connection-note').textContent = 'The dashboard address is unavailable. Contact the admin for access.';
});
