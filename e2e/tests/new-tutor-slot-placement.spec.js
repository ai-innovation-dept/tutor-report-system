// @ts-check
// 新システム(EMPS) コマ設定契約の「副担当の位置」（コマ後/コマ間）e2e（UI操作のみ・保存/提出なし＝メール送信・DB書込ゼロ）。
// 改修依頼 202607161853: 副担当業務等（分）の既定を「最終コマの後に実施」（休憩＝コマ間の隙間のまま・終了が延びる）へ変更し、
// 従来の「コマ間で実施」（休憩＝隙間−副担当）は行ごとの「副担当の位置」セレクトで選べるようにした。
// 前提seed: 講師 qa.tutor.slots@example.com ＋ コマ設定契約（①8:30〜9:20 ②9:30〜10:20 ③10:30〜11:20 ④11:30〜12:20・
// 担当業務=task_minutes_1・副担当業務=sub_minutes_1）。未seed環境ではスキップ。
const { test, expect } = require('@playwright/test');
const { login, NEW, PASSWORD } = require('./helpers');

const TUTOR = 'qa.tutor.slots@example.com';

test('N-SLOT-PLACE 新: 副担当の位置（コマ後＝既定/コマ間）の休憩自動計算', async ({ page, request }) => {
  const probe = await request.post(`${NEW}/api/auth/login`, { data: { username: TUTOR, password: PASSWORD } });
  test.skip(!probe.ok(), `講師 ${TUTOR} が未seedのためスキップ (${probe.status()})`);

  const pageErrors = [];
  page.on('pageerror', err => pageErrors.push(String(err)));

  await login(page, NEW, TUTOR, '/tutor/reports');
  await page.waitForSelector('#lineRows tr[data-index="0"]');
  await page.click('#newReportBtn');
  // 新規作成は学校未選択で始まる（デフォルト列）。学校を選ぶと契約の列定義＋コマ設定が適用される
  await page.selectOption('#dispatchPlaceSchool', { label: 'コマ検証学園' });
  await page.waitForSelector('#lineRows [data-field="task_minutes_1"]');

  // コマ設定契約では休憩時間の右隣に「副担当の位置」列（コマ後＝既定/コマ間）が表示される
  await expect(page.locator('#lineHead')).toContainText('副担当の位置');
  const row = page.locator('#lineRows tr[data-index="0"]');
  const breakInput = row.locator('[data-field="break_minutes"]');
  const placement = row.locator('select[data-field="secondary_placement"]');
  const timeDisplay = row.locator('[data-time-display]');
  await expect(placement).toBeVisible();
  await expect(placement).toHaveValue('');

  // ①③④コマを選択 → 担当業務150分・休憩＝コマ間の隙間80分（70+10）・8:30〜12:20
  await row.locator('[data-period-btn]').click();
  const popover = page.locator('.period-popover');
  for (const n of ['1', '3', '4']) await popover.locator(`[data-period-toggle="${n}"]`).click();
  await popover.locator('[data-period-close]').click();
  await expect(row.locator('[data-field="task_minutes_1"]')).toHaveValue('150');
  await expect(breakInput).toHaveValue('80');
  await expect(timeDisplay).toHaveText('08:30〜12:20');

  // 副担当業務50分（既定＝コマ後）: 休憩は80分のまま・終了は④コマ(12:20)の直後+50分=13:10
  await row.locator('[data-field="sub_minutes_1"]').fill('50');
  await expect(breakInput).toHaveValue('80');
  await expect(timeDisplay).toHaveText('08:30〜13:10');

  // 「コマ間」へ切替: 休憩＝隙間80−副担当50=30分・終了は④コマの終了のまま12:20（従来の既定）
  await placement.selectOption('gap');
  await expect(breakInput).toHaveValue('30');
  await expect(timeDisplay).toHaveText('08:30〜12:20');

  // 「コマ後」へ戻すと休憩80分・13:10へ再計算される
  await placement.selectOption('');
  await expect(breakInput).toHaveValue('80');
  await expect(timeDisplay).toHaveText('08:30〜13:10');

  // レイアウト確認用スクリーンショット（保存はしない）
  await page.screenshot({ path: 'test-results/new-tutor-slot-placement.png', fullPage: false });

  // 有給にすると位置セレクトもクリア・無効化され、勤務へ戻すと再入力できる
  await row.locator('[data-field="kind"]').selectOption('paid_leave');
  await expect(placement).toBeDisabled();
  await expect(placement).toHaveValue('');
  await row.locator('[data-field="kind"]').selectOption('');
  await expect(placement).toBeEnabled();

  expect(pageErrors, `ページJSエラー: ${pageErrors.join(' / ')}`).toHaveLength(0);
});

test('N-SLOT-PLACE 新: スマホ詳細シートの副担当の位置（明細行と同一ルール）', async ({ page, request }) => {
  const probe = await request.post(`${NEW}/api/auth/login`, { data: { username: TUTOR, password: PASSWORD } });
  test.skip(!probe.ok(), `講師 ${TUTOR} が未seedのためスキップ (${probe.status()})`);

  await page.setViewportSize({ width: 390, height: 844 });
  const pageErrors = [];
  page.on('pageerror', err => pageErrors.push(String(err)));

  await login(page, NEW, TUTOR, '/tutor/reports');
  await page.waitForSelector('#lineRows tr[data-index="0"]', { state: 'attached' });
  await page.click('#newReportBtn');
  // 学校を選ぶと契約の列定義＋コマ設定が適用される（PCと同じ）
  await page.selectOption('#dispatchPlaceSchool', { label: 'コマ検証学園' });
  await page.waitForSelector('#lineRows [data-field="task_minutes_1"]', { state: 'attached' });

  // 明細リストの「＋ 日付を追加」で先頭の空行のシートを開く
  await page.click('[data-line-add]');
  const sheet = page.locator('#lineSheetBody');
  const sheetPlacement = sheet.locator('select[data-sheet-field="secondary_placement"]');
  await expect(sheetPlacement).toBeVisible();

  // ①③④コマ＋副担当50分（既定＝コマ後）→ 休憩80分のまま
  for (const n of ['1', '3', '4']) await sheet.locator(`[data-sheet-period="${n}"]`).click();
  await expect(sheet.locator('[data-sheet-field="task_minutes_1"]')).toHaveValue('150');
  await expect(sheet.locator('[data-sheet-field="break_minutes"]')).toHaveValue('80');
  await sheet.locator('[data-sheet-field="sub_minutes_1"]').fill('50');
  await expect(sheet.locator('[data-sheet-field="break_minutes"]')).toHaveValue('80');
  await expect(sheet.locator('[data-sheet-time]')).toHaveText('08:30〜13:10');

  // 「コマ間」へ切替 → 休憩30分・12:20（明細行と同一ルール）
  await sheetPlacement.selectOption('gap');
  await expect(sheet.locator('[data-sheet-field="break_minutes"]')).toHaveValue('30');
  await expect(sheet.locator('[data-sheet-time]')).toHaveText('08:30〜12:20');

  await page.screenshot({ path: 'test-results/new-tutor-slot-placement-mobile.png', fullPage: false });
  expect(pageErrors, `ページJSエラー: ${pageErrors.join(' / ')}`).toHaveLength(0);
});

test('N-SLOT-PLACE 新: コマ設定なし契約には副担当の位置列を表示しない', async ({ page, request }) => {
  const probe = await request.post(`${NEW}/api/auth/login`, { data: { username: 'qa.tutor.new@example.com', password: PASSWORD } });
  test.skip(!probe.ok(), 'qa.tutor.new が未seedのためスキップ');

  await login(page, NEW, 'qa.tutor.new@example.com', '/tutor/reports');
  await page.waitForSelector('#lineRows tr[data-index="0"]');
  await page.click('#newReportBtn');
  // 学校を選択して契約列を適用しても、コマ設定が無い契約には位置列を表示しない
  const schoolSel = page.locator('#dispatchPlaceSchool');
  const optionCount = await schoolSel.locator('option:not([value=""])').count();
  if (optionCount > 0) {
    await schoolSel.selectOption({ index: 1 });
  }
  await expect(page.locator('#lineHead')).not.toContainText('副担当の位置');
  await expect(page.locator('#lineRows select[data-field="secondary_placement"]')).toHaveCount(0);
});
