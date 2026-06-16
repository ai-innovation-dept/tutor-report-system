// @ts-check
const { expect } = require('@playwright/test');

const EXISTING = 'http://localhost:8000';
const NEW = 'http://localhost:8001';
const PASSWORD = 'Passw0rd!';

// ログインフォーム経由でログインする（両システムとも #email / #password / submit / POST /api/auth/login）。
// 新システムはログイン後にロール選択を挟む場合があるため、最終的に目的パスへ遷移させる。
async function login(page, baseUrl, email, gotoPath, password = PASSWORD) {
  await page.goto(`${baseUrl}/login`);
  await page.fill('#email', email);
  await page.fill('#password', password);
  // ログインボタンは type 未指定（form内の暗黙 submit）。フォーム内の button を押す。
  // 認証fetchのレスポンス受領(=Set-Cookie適用)を待ってから遷移する。networkidle 待ちだけでは
  // fetch 完了前に解決し、未認証のまま目的パスへ goto→ログイン画面へ戻るレースが起きるため。
  await Promise.all([
    page.waitForResponse(r => r.url().includes('/api/auth/login') && r.request().method() === 'POST', { timeout: 15000 }),
    page.click('#loginForm button'),
  ]);
  await page.goto(`${baseUrl}${gotoPath}`);
  await page.waitForLoadState('domcontentloaded');
}

module.exports = { login, EXISTING, NEW, PASSWORD, expect };
