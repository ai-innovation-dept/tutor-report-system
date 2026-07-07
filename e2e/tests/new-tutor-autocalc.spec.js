// @ts-check
// 新システム(EMPS) 講師 報告書入力の自動計算 e2e（UI操作のみ・保存/提出なし＝メール送信・DB書込ゼロ）。
// ①担当時限1コマ=50分を右隣の担当業務（分）へ自動入力
// ②休憩時間（分）＝（コマ数−1）×10分（3コマ→150分・休憩20分）
// ③業務開始〜終了時間は自動計算（開始8:40固定＋担当時限より右の分数合計）・手動入力不可
// ④分数は1分単位で手動修正でき、業務開始〜終了時間が連動する
const { test, expect } = require('@playwright/test');
const { login, NEW, PASSWORD } = require('./helpers');

const TUTOR = 'qa.tutor.new@example.com';

test('N-CALC 新: 担当時限→分数→業務開始〜終了時間の自動計算', async ({ page, request }) => {
  // 講師アカウント（qa.tutor.new は flow-new と同じ seed 前提）が無い環境ではスキップ
  const probe = await request.post(`${NEW}/api/auth/login`, { data: { username: TUTOR, password: PASSWORD } });
  test.skip(!probe.ok(), `講師 ${TUTOR} が未seedのためスキップ (${probe.status()})`);

  await login(page, NEW, TUTOR, '/tutor/reports');
  // loadTutor 完了（明細行の初回描画）を待ってから、新規フォーム＝デフォルト列（数学科指導（分）等）で検証する
  await page.waitForSelector('#lineRows tr[data-index="0"]');
  await page.click('#newReportBtn');

  const row = page.locator('#lineRows tr[data-index="0"]');
  const timeDisplay = row.locator('[data-time-display]');
  await expect(row.locator('[data-period-btn]')).toBeEnabled();
  // ③ 時間欄は自動計算表示（入力コントロールなし）。初期はプレースホルダ
  await expect(timeDisplay).toHaveText('自動計算');

  // ①② 担当時限を3コマ（1・2・3）選択 → 担当業務150分・休憩20分
  await row.locator('[data-period-btn]').click();
  const popover = page.locator('.period-popover');
  for (const n of ['1', '2', '3']) await popover.locator(`[data-period-toggle="${n}"]`).click();
  await popover.locator('[data-period-close]').click();
  await expect(row.locator('[data-field="teach_minutes"]')).toHaveValue('150');
  await expect(row.locator('[data-field="break_minutes"]')).toHaveValue('20');
  // ③ 開始8:40固定＋(150+20)分 → 08:40〜11:30（hidden の保存値も同じ）
  await expect(timeDisplay).toHaveText('08:40〜11:30');
  await expect(row.locator('input[data-field="start"]')).toHaveValue('08:40');
  await expect(row.locator('input[data-field="end"]')).toHaveValue('11:30');

  // ④ 分数を1分単位で手動修正（150→151）→ 終了時間が連動（171分 → 11:31）
  await row.locator('[data-field="teach_minutes"]').fill('151');
  await expect(timeDisplay).toHaveText('08:40〜11:31');
  await expect(row.locator('input[data-field="end"]')).toHaveValue('11:31');

  // 休憩も1分単位で修正可（20→5 → 合計156分 → 11:16）
  await row.locator('[data-field="break_minutes"]').fill('5');
  await expect(timeDisplay).toHaveText('08:40〜11:16');

  // レイアウト確認用スクリーンショット（値が入った状態。保存はしない）
  await page.screenshot({ path: 'test-results/new-tutor-autocalc.png', fullPage: false });

  // 合計が同日23:59を超える場合は計算不可（保存はブロックされる仕様。保存は行わない）
  await row.locator('[data-field="teach_minutes"]').fill('2000');
  await expect(timeDisplay).toHaveText('計算不可');
  await expect(row.locator('input[data-field="start"]')).toHaveValue('');
  await expect(row.locator('input[data-field="end"]')).toHaveValue('');

  // 担当時限のクリア → 自動入力（担当業務・休憩）と時間もクリア
  await row.locator('[data-period-btn]').click();
  await popover.locator('[data-period-clear]').click();
  await popover.locator('[data-period-close]').click();
  await expect(row.locator('[data-field="teach_minutes"]')).toHaveValue('');
  await expect(row.locator('[data-field="break_minutes"]')).toHaveValue('');
  await expect(timeDisplay).toHaveText('自動計算');

  // 1コマ選択 → 50分・休憩0分 → 08:40〜09:30
  await row.locator('[data-period-btn]').click();
  await popover.locator('[data-period-toggle="1"]').click();
  await popover.locator('[data-period-close]').click();
  await expect(row.locator('[data-field="teach_minutes"]')).toHaveValue('50');
  await expect(row.locator('[data-field="break_minutes"]')).toHaveValue('0');
  await expect(timeDisplay).toHaveText('08:40〜09:30');

  // 種別を有給にすると時間・分数はクリアされ入力不可、勤務へ戻すと再入力できる
  await row.locator('[data-field="kind"]').selectOption('paid_leave');
  await expect(timeDisplay).toHaveText('自動計算');
  await expect(row.locator('[data-field="teach_minutes"]')).toHaveValue('');
  await expect(row.locator('[data-field="teach_minutes"]')).toBeDisabled();
  await row.locator('[data-field="kind"]').selectOption('');
  await expect(row.locator('[data-field="teach_minutes"]')).toBeEnabled();
});
