// @ts-check
// 既存システム（イスト勤怠レポート for 代々木進学会 / port 8000）の
// ユーザー管理・担当管理にある「CSV一括取込」の「?」ヘルプポップアップを検証する。
// 観点: ?でポップアップが開く / 旧インライン説明文が撤去済み / 実挙動と一致する要点が表示される /
//        実スクリーンショット2枚が実際に読み込める / Esc で閉じる。
// 事前条件: backend を seed 済み（receiver1@example.com / Passw0rd! が存在すること）。
const { test } = require('@playwright/test');
const { login, EXISTING, expect } = require('./helpers');

const ADMIN = 'receiver1@example.com'; // 受付担当（CSV一括取込が使える運営ロール）

// オーバーレイ内の全 img が naturalWidth>0（=実際に読み込めている）ことを確認する。
async function expectImagesLoaded(overlay, expectedCount) {
  const imgs = overlay.locator('img');
  await expect(imgs).toHaveCount(expectedCount);
  for (let i = 0; i < expectedCount; i++) {
    const nw = await imgs.nth(i).evaluate(el => el.naturalWidth);
    expect(nw, `画像${i}が読み込めていない`).toBeGreaterThan(0);
  }
}

test.describe('既存システム CSV一括取込ヘルプポップアップ', () => {
  test('① ユーザー管理：?で開く・要点表示・画像読込・Escで閉じる', async ({ page }) => {
    await login(page, EXISTING, ADMIN, '/admin/users');
    const btn = page.locator('#csvHelpBtn');
    const overlay = page.locator('#csvHelpOverlay');
    await expect(btn).toBeVisible();
    // 旧インライン説明文（撤去済み）の特徴的フレーズが本文に残っていない
    await expect(page.getByText('現在の登録状態を書き出して')).toHaveCount(0);
    // 既定は閉じている → ?で開く
    await expect(overlay).toBeHidden();
    await btn.click();
    await expect(overlay).toBeVisible();
    // 実挙動と一致する要点（メール・氏名のみ更新／No空欄=新規・初期パス／全件中止／ロール値）
    await expect(overlay).toContainText('メール・氏名だけ');
    await expect(overlay).toContainText('Passw0rd!');
    await expect(overlay).toContainText('1件でもエラーがあると、全件取り込みません');
    await expect(overlay).toContainText('admin_receiver');
    await expectImagesLoaded(overlay, 2);
    await page.keyboard.press('Escape');
    await expect(overlay).toBeHidden();
  });

  test('② 担当管理：?で開く・要点表示・画像読込・Escで閉じる', async ({ page }) => {
    await login(page, EXISTING, ADMIN, '/admin/assignments');
    const btn = page.locator('#csvHelpBtn');
    const overlay = page.locator('#csvHelpOverlay');
    await expect(btn).toBeVisible();
    await expect(page.getByText('現在の担当を書き出し')).toHaveCount(0);
    await expect(overlay).toBeHidden();
    await btn.click();
    await expect(overlay).toBeVisible();
    // 実挙動と一致する要点（講師No＋生徒名で照合／保護者No空欄=未設定／全件中止）
    await expect(overlay).toContainText('講師No');
    await expect(overlay).toContainText('生徒名');
    await expect(overlay).toContainText('保護者No');
    await expect(overlay).toContainText('未設定');
    await expect(overlay).toContainText('1件でもエラーがあると、全件取り込みません');
    await expectImagesLoaded(overlay, 2);
    await page.keyboard.press('Escape');
    await expect(overlay).toBeHidden();
  });

  test('③ tutor は CSV取込ヘルプ画面（運営ページ）に入れない', async ({ page }) => {
    // 念のため: 講師ロールは /admin/users に入れず、?ボタンも存在しない（権限境界の確認）。
    await login(page, EXISTING, 'tutor1@example.com', '/admin/users');
    await expect(page.locator('#csvHelpBtn')).toHaveCount(0);
  });
});
