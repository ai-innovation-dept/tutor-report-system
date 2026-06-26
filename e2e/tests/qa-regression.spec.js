// @ts-check
// QA回帰スイート：直近改修（ブランド改称／指導報告作成／ユーザー管理カード順／自分自身ガード）と
// 全ロールのログインを e2e で検証する。メール送信を伴わない操作のみ（承認フロー系は別途）。
// ※「ロール最後の1人は削除/無効不可」は共有DBのデータに依存するため pytest（制御フィクスチャ）で検証済み。
const { login, EXISTING, NEW, expect } = require('./helpers');
const { test } = require('@playwright/test');

const PW = 'Passw0rd!';

// 各アカウント1テスト（1テストに詰め込むと30sタイムアウトに達するため分割）。
const LOGIN_CASES = [
  [EXISTING, 'tutor1@example.com', '既存:講師'],
  [EXISTING, 'parent1@example.com', '既存:保護者'],
  [EXISTING, 'receiver1@example.com', '既存:受付'],
  [EXISTING, 'reviewer1@example.com', '既存:再鑑'],
  [EXISTING, 'master1@example.com', '既存:管理者'],
  [EXISTING, 'supervisor@example.com', '既存:管理責任者'],
  [NEW, 'office1@example.com', '新:事務'],
  [NEW, 'sales1@example.com', '新:営業'],
  [NEW, 'school1@example.com', '新:学校'],
  [NEW, 'emps.manager@example.com', '新:経理'],
  [NEW, 'emps.administrator@example.com', '新:管理責任者'],
];

test.describe('QA回帰: 認証', () => {
  for (const [base, email, label] of LOGIN_CASES) {
    test(`C-CMN-001 ${label} ログイン可能`, async ({ page }) => {
      await page.goto(`${base}/login`, { waitUntil: 'domcontentloaded' });
      await page.fill('#email', email);
      await page.fill('#password', PW);
      await Promise.all([
        page.waitForResponse(r => r.url().includes('/api/auth/login') && r.request().method() === 'POST', { timeout: 15000 }),
        page.click('#loginForm button'),
      ]);
      await page.waitForTimeout(700);
      expect(page.url(), `${email} はログイン後 /login を離れる`).not.toContain('/login');
    });
  }
});

test.describe('QA回帰: 直近改修', () => {
  test('L-REG-001 既存ログイン画面のブランド名が新名称', async ({ page }) => {
    await page.goto(`${EXISTING}/login`, { waitUntil: 'domcontentloaded' });
    await expect(page.locator('body')).toContainText('指導報告・指導時間確認票');
    await expect(page.locator('body')).not.toContainText('Tutor Reports');
    await expect(page.locator('body')).not.toContainText('指導実績報告システム');
  });

  test('L-REG-001b 既存サイドバーのブランド名＋作成見出しが「指導報告作成」', async ({ page }) => {
    await login(page, EXISTING, 'tutor1@example.com', '/tutor/reports');
    await expect(page.locator('aside')).toContainText('指導報告・指導時間確認票');
    await expect(page.locator('#formTitle')).toHaveText('指導報告作成');
  });

  test('N-OFF-103 ユーザー管理カードは 事務→営業 の順', async ({ page }) => {
    await login(page, NEW, 'office1@example.com', '/admin/users');
    await page.waitForSelector('#roleTabs button');
    const labels = await page.locator('#roleTabs button').allTextContents();
    const idxOffice = labels.findIndex(t => t.includes('事務'));
    const idxSales = labels.findIndex(t => t.includes('営業'));
    expect(idxOffice, '事務カードが存在').toBeGreaterThanOrEqual(0);
    expect(idxSales, '営業カードが存在').toBeGreaterThanOrEqual(0);
    expect(idxSales, '営業は事務より後ろ').toBeGreaterThan(idxOffice);
  });

  test('N-OFF-101 事務は自分自身を無効化できない（ボタン無効＋理由）', async ({ page }) => {
    await login(page, NEW, 'office1@example.com', '/admin/users');
    const row = page.locator('tr', { hasText: 'office1@example.com' }).first();
    await row.getByRole('button', { name: '詳細' }).click();
    const btn = page.getByRole('button', { name: '無効化する' });
    await expect(btn).toBeVisible();
    await expect(btn).toBeDisabled();
    await expect(btn).toHaveAttribute('title', /自分自身/);
  });
});
