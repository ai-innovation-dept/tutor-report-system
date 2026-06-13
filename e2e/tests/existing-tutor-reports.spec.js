// @ts-check
// 既存システム（指導実績報告システム / port 8000）講師「報告書一覧」のレイアウト改修①②③の検証。
const { test } = require('@playwright/test');
const { login, EXISTING, expect } = require('./helpers');

test.describe('既存システム 講師 報告書一覧 レイアウト改修', () => {
  test.beforeEach(async ({ page }) => {
    await login(page, EXISTING, 'tutor1@example.com', '/tutor/reports');
  });

  test('① 生徒ラベルは左・プルダウンは半分幅', async ({ page }) => {
    const label = page.locator('label', { hasText: '生徒：' }).first();
    await expect(label).toBeVisible();
    const select = page.locator('#assignment');
    await expect(select).toBeVisible();
    // 半分幅クラス
    await expect(select).toHaveClass(/w-1\/2/);
    // ラベルとプルダウンが同じ行（縦位置がほぼ一致＝横並び）
    const labelText = label.locator('span', { hasText: '生徒：' }).first();
    const tBox = await labelText.boundingBox();
    const sBox = await select.boundingBox();
    expect(tBox).not.toBeNull();
    expect(sBox).not.toBeNull();
    // ラベルはプルダウンの左側
    expect(tBox.x).toBeLessThan(sBox.x);
    // 同一行（中央Yの差が20px以内）
    expect(Math.abs((tBox.y + tBox.height / 2) - (sBox.y + sBox.height / 2))).toBeLessThan(20);
  });

  test('② 表示月プルダウンは簡易作成パネル内・生徒バッジは非表示', async ({ page }) => {
    // 表示月プルダウンが簡易作成(formPanel)の中にある
    const monthInPanel = page.locator('#formPanel #monthFilter');
    await expect(monthInPanel).toBeVisible();
    // 簡易作成見出しと同じ行（縦位置がほぼ一致）
    const title = page.locator('#formTitle');
    const titleBox = await title.boundingBox();
    const monthBox = await monthInPanel.boundingBox();
    expect(titleBox).not.toBeNull();
    expect(monthBox).not.toBeNull();
    expect(Math.abs((titleBox.y + titleBox.height / 2) - (monthBox.y + monthBox.height / 2))).toBeLessThan(24);
    // 表示月は簡易作成の右側
    expect(monthBox.x).toBeGreaterThan(titleBox.x);
    // 生徒バッジ「生徒：すべて」は非表示
    await expect(page.locator('#studentBadge')).toBeHidden();
  });

  test('③ 不要な案内文が表示されない', async ({ page }) => {
    // 「※生徒を選択してください」はDOMから除去済み
    await expect(page.getByText('※生徒を選択してください')).toHaveCount(0);
    // 生徒未選択（すべての生徒）時、コピー案内文「生徒を選択するとコピーできます」は出ない
    await page.selectOption('#assignment', '');
    await expect(page.locator('#copyLastHint')).toHaveText('');
    await expect(page.getByText('生徒を選択するとコピーできます')).toHaveCount(0);
  });
});
