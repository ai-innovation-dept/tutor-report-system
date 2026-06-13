// @ts-check
const { expect } = require('@playwright/test');

const EXISTING = 'http://localhost:8000';
const NEW = 'http://localhost:8001';
const PASSWORD = 'Passw0rd!';

// ログインフォーム経由でログインする（両システムとも #email / #password / submit）。
// 新システムはログイン後にロール選択を挟む場合があるため、最終的に目的パスへ遷移させる。
async function login(page, baseUrl, email, gotoPath) {
  await page.goto(`${baseUrl}/login`);
  await page.fill('#email', email);
  await page.fill('#password', PASSWORD);
  // ログインボタンは type 未指定（form内の暗黙 submit）。フォーム内の button を押す。
  await page.click('#loginForm button');
  // ログイン処理（fetch→cookie→redirect）の完了を待ってから目的パスへ
  await page.waitForLoadState('networkidle');
  await page.goto(`${baseUrl}${gotoPath}`);
  await page.waitForLoadState('networkidle');
}

module.exports = { login, EXISTING, NEW, PASSWORD, expect };
