// # === Phase 8: フロントエンド共通 START ===
async function api(url, options = {}) {
  const {suppressFlash = false, ...fetchOptions} = options;
  const headers = Object.assign({'Content-Type': 'application/json'}, options.headers || {});
  const token = localStorage.getItem('access_token') || localStorage.getItem('token');
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(url, Object.assign({}, fetchOptions, {headers}));
  if (res.status === 401) {
    location.href = '/login';
    throw new Error('unauthorized');
  }
  if (!res.ok) {
    const text = await res.text();
    let message = text;
    try {
      const data = JSON.parse(text);
      message = data.detail || text;
    } catch (_) {
      message = text;
    }
    const flash = document.querySelector('#flash');
    if (!suppressFlash && flash) { flash.textContent = message; flash.classList.remove('hidden'); }
    throw new Error(message);
  }
  return res.status === 204 ? null : res.json();
}

function logout() {
  fetch('/api/auth/logout', {
    method: 'POST',
    headers: (localStorage.getItem('access_token') || localStorage.getItem('token')) ? {Authorization: `Bearer ${localStorage.getItem('access_token') || localStorage.getItem('token')}`} : {}
  }).finally(() => {
  localStorage.removeItem('access_token');
  localStorage.removeItem('token');
  location.href = '/login';
  });
}
// # === Phase 8 END ===
