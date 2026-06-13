// @ts-check
// 新システム（業務連絡表 / port 8001）サイドメニューの左ライン揃え検証（前回追加改修②）。
// ロゴ「業務連絡表」・ユーザー情報「ログイン中」・ナビ文字の左端Xが一致することを確認する。
// サイドメニュー(base.html)は全ロール共通テンプレートのため、新システム権限を持つ office1 で検証すれば
// 講師を含む全ロールの表示と同一（依頼の「他の全ロールの画面も」を満たす）。
const { test } = require('@playwright/test');
const { login, NEW, expect } = require('./helpers');

test.describe('新システム サイドメニュー 左ライン揃え', () => {
  test('ロゴ・ログイン中・ナビ文字の左端が一致', async ({ page }) => {
    await login(page, NEW, 'office1@example.com', '/');

    const logo = page.locator('#sidebarPanel h1', { hasText: '業務連絡表' }).first();
    const loginLabel = page.locator('#sidebarPanel p', { hasText: 'ログイン中' }).first();
    const navLink = page.locator('#navItems a[data-nav-link]').first();

    await expect(logo).toBeVisible();
    await expect(loginLabel).toBeVisible();
    await expect(navLink).toBeVisible();

    const logoBox = await logo.boundingBox();
    const loginBox = await loginLabel.boundingBox();
    const navBox = await navLink.boundingBox();
    expect(logoBox).not.toBeNull();
    expect(loginBox).not.toBeNull();
    expect(navBox).not.toBeNull();

    // ロゴとログイン中の左端Xが一致（2px以内）
    expect(Math.abs(logoBox.x - loginBox.x)).toBeLessThanOrEqual(2);
    // ナビは <a> 自体の内側 px-3 ぶん（約12px）右に文字があるので、a左端+12 がロゴ左端に一致
    expect(Math.abs((navBox.x + 12) - logoBox.x)).toBeLessThanOrEqual(3);
  });
});
