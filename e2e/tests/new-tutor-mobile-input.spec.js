// @ts-check
// 新システム(EMPS) 講師 報告書入力のスマホUI e2e（UI操作のみ・保存/提出なし＝メール送信・DB書込ゼロ）。
// ①md未満では明細テーブルが隠れ、明細リスト（日付・開始・終了・交通費・事由）が表示される（画面①）
// ②「＋ 日付を追加」で明細詳細シート（画面②）が開き、担当時限トグルで分数・業務開始〜終了時間が自動入力される
// ③シートの保存でリストへ反映され、シートが閉じて画面①へ戻る
// ④行タップでシートを再度開くと入力値が保持されている／種別（有給）で業務入力が無効化され事由に表示される
const { test, expect } = require('@playwright/test');
const { login, NEW, PASSWORD } = require('./helpers');

const TUTOR = 'qa.tutor.new@example.com';

test.use({ viewport: { width: 390, height: 844 } });

test('N-MOBILE 新: スマホの明細リスト＋詳細シートで入力できる', async ({ page, request }) => {
  // 講師アカウント（qa.tutor.new は flow-new と同じ seed 前提）が無い環境ではスキップ
  const probe = await request.post(`${NEW}/api/auth/login`, { data: { username: TUTOR, password: PASSWORD } });
  test.skip(!probe.ok(), `講師 ${TUTOR} が未seedのためスキップ (${probe.status()})`);

  await login(page, NEW, TUTOR, '/tutor/reports');
  // 明細行はスマホでは非表示（attached のみ待つ）。新規フォーム＝デフォルト列で検証する
  await page.waitForSelector('#lineRows tr[data-index="0"]', { state: 'attached' });
  await page.click('#newReportBtn');

  // ① テーブルは非表示・明細リスト表示（未入力なので空メッセージ＋追加ボタン）
  await expect(page.locator('#lineRows')).toBeHidden();
  await expect(page.locator('#mobileLineList')).toBeVisible();
  await expect(page.locator('#mobileLineList')).toContainText('まだ入力がありません');
  await page.screenshot({ path: 'test-results/new-tutor-mobile-list-empty.png' });

  // ② 「＋ 日付を追加」→ 詳細シートが開く
  await page.click('[data-line-add]');
  const sheet = page.locator('#lineSheetOverlay');
  await expect(sheet).toBeVisible();
  await expect(page.locator('#lineSheetTitle')).toHaveText('1回目の明細');
  await page.screenshot({ path: 'test-results/new-tutor-mobile-sheet-top.png' });

  // 日付を入力（曜日がボックス内に併記される）
  await sheet.locator('[data-sheet-field="date"]').fill('2026-07-01');
  await expect(sheet.locator('[data-sheet-weekday]')).toHaveText('(水)');

  // 担当時限①②③ → 担当業務150分・休憩20分・業務開始〜終了 08:40〜11:30 が自動入力
  for (const n of ['1', '2', '3']) await sheet.locator(`[data-sheet-period="${n}"]`).click();
  await expect(sheet.locator('[data-sheet-field="teach_minutes"]')).toHaveValue('150');
  await expect(sheet.locator('[data-sheet-field="break_minutes"]')).toHaveValue('20');
  await expect(sheet.locator('[data-sheet-time]')).toHaveText('08:40〜11:30');

  // 分数の1分単位の手動修正にも時間が連動する（150→151 → 11:31）
  await sheet.locator('[data-sheet-field="teach_minutes"]').fill('151');
  await expect(sheet.locator('[data-sheet-time]')).toHaveText('08:40〜11:31');
  await sheet.locator('[data-sheet-field="teach_minutes"]').fill('150');

  // 交通費・内容を入力
  await sheet.locator('[data-sheet-field="commute_fee"]').fill('1000');
  await sheet.locator('[data-sheet-field="note"]').fill('スマホ入力テスト');
  await page.screenshot({ path: 'test-results/new-tutor-mobile-sheet.png' });

  // ③ シートの保存 → 閉じて画面①へ戻り、リストに反映される
  await page.click('#lineSheetSave');
  await expect(sheet).toBeHidden();
  const row0 = page.locator('#mobileLineList [data-line-open="0"]');
  await expect(row0).toContainText('07/01(水)');
  await expect(row0).toContainText('08:40');
  await expect(row0).toContainText('11:30');
  await expect(row0).toContainText('1,000');
  // 通常勤務の行は事由欄を空欄にする（種別を選んだ行だけ有給などを表示）
  await expect(row0).not.toContainText('勤務');
  await page.screenshot({ path: 'test-results/new-tutor-mobile-list-filled.png' });

  // ④ 行タップで再度開くと入力値が保持されている
  await row0.click();
  await expect(sheet).toBeVisible();
  await expect(sheet.locator('[data-sheet-field="teach_minutes"]')).toHaveValue('150');
  await expect(sheet.locator('[data-sheet-field="note"]')).toHaveValue('スマホ入力テスト');

  // 種別＝有給 → 業務入力が無効化・クリアされ、保存後は事由欄に「有給」を表示
  await sheet.locator('[data-sheet-field="kind"]').selectOption('paid_leave');
  await expect(sheet.locator('[data-sheet-field="teach_minutes"]')).toBeDisabled();
  await expect(sheet.locator('[data-sheet-time]')).toHaveText('自動計算');
  await page.click('#lineSheetSave');
  await expect(row0).toContainText('有給');
  await page.screenshot({ path: 'test-results/new-tutor-mobile-list-kind.png' });

  // 同日重複ガード: 2回目の明細に同じ日付を入れると保存できずエラー表示
  await page.click('[data-line-add]');
  await expect(page.locator('#lineSheetTitle')).toHaveText('2回目の明細');
  await sheet.locator('[data-sheet-field="date"]').fill('2026-07-01');
  await page.click('#lineSheetSave');
  await expect(page.locator('#lineSheetError')).toContainText('同じ日付の行がすでにあります');
  await page.click('#lineSheetCancel');
  await expect(sheet).toBeHidden();

  // 保存・提出は行わない（サーバ状態を変えない＝メール送信ゼロ）
});
