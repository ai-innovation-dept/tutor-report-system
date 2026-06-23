// @ts-check
// 新システム（イスト勤怠レポート for EMPS / port 8001）の
// ユーザー管理・契約管理にある「CSV一括取込」の「?」ヘルプポップアップを検証する。
// 観点: ?でポップアップが開く / 旧インライン説明文が撤去済み / 実挙動と一致する要点が表示される /
//        実スクリーンショット2枚が実際に読み込める / Esc で閉じる。
// 既存システム(existing-csv-help.spec.js)と対になる統一テスト。
// 事前条件: new_backend を create_test_users 済み（office1@example.com / school1@example.com / Passw0rd!）。
const { test } = require('@playwright/test');
const { login, NEW, expect } = require('./helpers');

const ADMIN = 'office1@example.com'; // 事務担当（CSV一括取込が使える運営ロール）

// オーバーレイ内の全 img が naturalWidth>0（=実際に読み込めている）ことを確認する。
async function expectImagesLoaded(overlay, expectedCount) {
  const imgs = overlay.locator('img');
  await expect(imgs).toHaveCount(expectedCount);
  for (let i = 0; i < expectedCount; i++) {
    const nw = await imgs.nth(i).evaluate(el => el.naturalWidth);
    expect(nw, `画像${i}が読み込めていない`).toBeGreaterThan(0);
  }
}

test.describe('新システム CSV一括取込ヘルプポップアップ', () => {
  test('① ユーザー管理：?で開く・要点表示・画像読込・Escで閉じる', async ({ page }) => {
    await login(page, NEW, ADMIN, '/admin/users');
    const btn = page.locator('#csvHelpBtn');
    const overlay = page.locator('#csvHelpOverlay');
    await expect(btn).toBeVisible();
    // 旧インライン説明文（撤去済み）の特徴的フレーズが本文に残っていない
    await expect(page.getByText('現在の登録状態を書き出して')).toHaveCount(0);
    // 既定は閉じている → ?で開く
    await expect(overlay).toBeHidden();
    await btn.click();
    await expect(overlay).toBeVisible();
    // 実挙動と一致する要点（メール・氏名のみ更新／No空欄=新規・初期パス／全件中止／EMPSのロール値）
    await expect(overlay).toContainText('メールアドレス・氏名だけ');
    await expect(overlay).toContainText('Passw0rd!');
    await expect(overlay).toContainText('1件でもエラーがあると、全件取り込みません');
    await expect(overlay).toContainText('office');
    await expect(overlay).toContainText('admin_chief');
    await expectImagesLoaded(overlay, 2);
    await page.keyboard.press('Escape');
    await expect(overlay).toBeHidden();
  });

  test('② 契約管理：?で開く・要点表示・画像読込・Escで閉じる', async ({ page }) => {
    await login(page, NEW, ADMIN, '/admin/contracts');
    const btn = page.locator('#csvHelpBtn');
    const overlay = page.locator('#csvHelpOverlay');
    await expect(btn).toBeVisible();
    await expect(page.getByText('現在の契約を書き出し')).toHaveCount(0);
    await expect(overlay).toBeHidden();
    await btn.click();
    await expect(overlay).toBeVisible();
    // 実挙動と一致する要点（講師番号＋学校番号で照合／担当業務①必須／全件中止）
    await expect(overlay).toContainText('講師番号');
    await expect(overlay).toContainText('学校番号');
    await expect(overlay).toContainText('担当業務①');
    await expect(overlay).toContainText('1件でもエラーがあると、全件取り込みません');
    await expectImagesLoaded(overlay, 2);
    await page.keyboard.press('Escape');
    await expect(overlay).toBeHidden();
  });

  test('③ 非運営ロール（school）は CSV取込ヘルプ画面に入れない', async ({ page }) => {
    // 念のため: 学校ロールは /admin/users に入れず、?ボタンも存在しない（権限境界の確認）。
    await login(page, NEW, 'school1@example.com', '/admin/users');
    await expect(page.locator('#csvHelpBtn')).toHaveCount(0);
  });
});
