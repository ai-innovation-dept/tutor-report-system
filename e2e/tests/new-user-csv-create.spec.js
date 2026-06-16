// @ts-check
// 新システム ユーザー管理 CSV フェーズ②：新規作成（No空欄＋初期パスPassW0rd!）＋ 初回ログイン時パスワード変更必須。
//  1) 管理者(office1)がCSVインポートで新規ユーザーを作成（ロール必須・No自動採番）。
//  2) その新規ユーザーで初回ログイン → パスワード変更画面へ強制誘導される（ゲート）。
//  3) 新パスワードを設定 → ダッシュボードへ遷移できる。
// 再実行できるよう、作成メールはタイムスタンプで一意化する（dev DBに少数のE2Eユーザーが残る）。
const { test } = require('@playwright/test');
const { login, NEW, expect } = require('./helpers');

const HEADERS = 'No,メールアドレス,氏名,ロール,状態(参考),学校承認スキップ(参考),登録日(参考)';

test.describe('新システム ユーザー管理 CSV 新規作成＋初回パスワード変更', () => {
  test('CSV新規作成→初回ログインで変更画面→変更後ダッシュボード', async ({ page }) => {
    const ts = Date.now();
    const email = `e2ecreate${ts}@example.com`;
    const name = `E2E新規${ts}`;

    // 1) 管理側(office1)でCSVインポート → office ロールの新規ユーザー作成
    await login(page, NEW, 'office1@example.com', '/admin/users');
    await page.waitForSelector('#csvImportBtn', { state: 'visible' });
    const csv = '﻿' + HEADERS + '\n' + `,${email},${name},office,,,\n`;
    const res = await page.request.post(`${NEW}/api/w/users/import`, {
      multipart: { file: { name: 'u.csv', mimeType: 'text/csv', buffer: Buffer.from(csv, 'utf-8') } },
    });
    expect(res.status(), await res.text()).toBe(200);
    expect((await res.json()).created).toBe(1);

    // 2) 新規ユーザーで初回ログイン（Cookieを切替）→ ゲートで /change-password へ
    await page.context().clearCookies();
    await login(page, NEW, email, '/office/queue');  // 初期パスPassw0rd! ＋ gateで変更画面へ誘導
    await expect(page).toHaveURL(/\/change-password/);
    await expect(page.locator('#newPassword')).toBeVisible();

    // 3) 新パスワードを設定 → 単一ロール(office)のダッシュボードへ
    await page.fill('#newPassword', 'E2eNewPass1');
    await page.fill('#confirmPassword', 'E2eNewPass1');
    await Promise.all([
      page.waitForURL(/\/office\/queue/, { timeout: 15000 }),
      page.click('#changeForm button[type="submit"]'),
    ]);
    await expect(page).toHaveURL(/\/office\/queue/);

    // 4) 変更後は通常利用できる（再ログインで変更画面に飛ばない）
    await page.context().clearCookies();
    await login(page, NEW, email, '/office/queue', 'E2eNewPass1');
    await expect(page).toHaveURL(/\/office\/queue/);
  });
});
