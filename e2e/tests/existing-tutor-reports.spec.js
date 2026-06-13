// @ts-check
// 既存システム（指導実績報告システム / port 8000）講師「報告書一覧」のレイアウト検証。
// 「簡易作成」見出しの下に、生徒・表示月を同じ高さ・同じ幅(各50%)の2プルダウンとして左右整列する。
const { test } = require('@playwright/test');
const { login, EXISTING, expect } = require('./helpers');

test.describe('既存システム 講師 報告書一覧 レイアウト', () => {
  test.beforeEach(async ({ page }) => {
    await login(page, EXISTING, 'tutor1@example.com', '/tutor/reports');
  });

  test('① 生徒・表示月が見出し下で同じ高さ・同じ幅で左右整列', async ({ page }) => {
    const title = page.locator('#formTitle');
    const student = page.locator('#assignment');
    const month = page.locator('#monthFilter');
    await expect(title).toBeVisible();
    await expect(student).toBeVisible();
    await expect(month).toBeVisible();

    const tBox = await title.boundingBox();
    const sBox = await student.boundingBox();
    const mBox = await month.boundingBox();
    expect(tBox).not.toBeNull();
    expect(sBox).not.toBeNull();
    expect(mBox).not.toBeNull();

    // 見出しは2プルダウンより上
    expect(tBox.y).toBeLessThan(sBox.y);
    expect(tBox.y).toBeLessThan(mBox.y);
    // 生徒は表示月の左
    expect(sBox.x).toBeLessThan(mBox.x);
    // 同じ行（中央Yの差が8px以内）
    expect(Math.abs((sBox.y + sBox.height / 2) - (mBox.y + mBox.height / 2))).toBeLessThan(8);
    // 同じ高さ（差4px以内）
    expect(Math.abs(sBox.height - mBox.height)).toBeLessThanOrEqual(4);
    // 同じ幅（各50%、差12px以内）
    expect(Math.abs(sBox.width - mBox.width)).toBeLessThanOrEqual(12);
  });

  test('② 生徒バッジは非表示・生徒/表示月はパネル内かつフォーム外', async ({ page }) => {
    await expect(page.locator('#studentBadge')).toBeHidden();
    await expect(page.locator('#formPanel #assignment')).toBeVisible();
    await expect(page.locator('#formPanel #monthFilter')).toBeVisible();
    // 生徒・表示月は見出し下のセレクター行（フォーム外）に移設済み。
    // 生徒(assignment)は保存時に formPayload() で assignment_id を明示付与するため、フォーム外でも送信される。
    await expect(page.locator('#reportForm #assignment')).toHaveCount(0);
    await expect(page.locator('#reportForm #monthFilter')).toHaveCount(0);
  });

  test('③ 不要な案内文が表示されない', async ({ page }) => {
    await expect(page.getByText('※生徒を選択してください')).toHaveCount(0);
    await page.selectOption('#assignment', '');
    await expect(page.locator('#copyLastHint')).toHaveText('');
    await expect(page.getByText('生徒を選択するとコピーできます')).toHaveCount(0);
  });

  test('④ 生徒プルダウンの値が assignment_id として送信される', async ({ page }) => {
    // 生徒プルダウンをフォーム外へ移したため、保存時に assignment_id が送信経路へ正しく載るかを検証する。
    // 実DBを汚さないよう POST /api/reports を横取り（モック）し、送信ボディだけを確認する。
    const optionValues = [];
    for (const opt of await page.locator('#assignment option').all()) {
      const v = await opt.getAttribute('value');
      if (v) optionValues.push(v);
    }
    test.skip(optionValues.length === 0, '紐付け生徒が無い環境のためスキップ');

    // フォームが有効（=当月に未作成で作成可能）な生徒を探す。進行中/承認済みの生徒は入力欄が無効化される。
    let value = '';
    for (const v of optionValues) {
      await page.selectOption('#assignment', v);
      if (await page.locator('#lessonDate').isEnabled()) { value = v; break; }
    }
    test.skip(!value, '当月に作成可能な生徒がいない環境のためスキップ');
    await page.selectOption('#assignment', value);

    // 重複しない指導日を当月内から探す（重複日はフロント検証で送信前にブロックされるため）
    const ym = new Date().toISOString().slice(0, 7);
    let freeDate = '';
    for (let d = 1; d <= 28; d++) {
      const candidate = `${ym}-${String(d).padStart(2, '0')}`;
      await page.fill('#lessonDate', candidate);
      if (!(await page.locator('#lessonDateError').isVisible())) { freeDate = candidate; break; }
    }
    expect(freeDate).not.toBe('');

    await page.selectOption('#startHour', '10');
    await page.selectOption('#startMin', '00');
    await page.selectOption('#endHour', '11');
    await page.selectOption('#endMin', '30');
    await page.fill('#subject', 'E2E確認');
    await page.fill('#content', 'E2E自動テストによる送信確認');

    let captured = null;
    await page.route('**/api/reports', async route => {
      if (route.request().method() === 'POST') {
        captured = route.request().postData();
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ id: 'e2e-mock' }) });
      } else {
        await route.continue();
      }
    });
    await page.click('#saveButton');
    await expect.poll(() => captured).not.toBeNull();
    expect(JSON.parse(captured).assignment_id).toBe(value);
  });
});
