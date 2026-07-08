// @ts-check
// 新システム(EMPS) 講師 報告書入力の種別（勤怠区分）e2e（UI操作のみ・保存/提出なし＝メール送信・DB書込ゼロ）。
// ①日付ボックス内に曜日「(火)」を併記（例: [2026/07/07 (火)]）
// ②種別プルダウンに「自己都合」「学校行事」がある
// ③自己都合・学校行事を選ぶと担当時限＝選択不可・担当業務（分）＝0固定、その他（休憩・交通費等）は手動入力可
//   （勤務へ戻すと0はクリアされ再入力できる。集計は自己都合/学校行事を回数として表示）
const { test, expect } = require('@playwright/test');
const { login, NEW, PASSWORD } = require('./helpers');

const TUTOR = 'qa.tutor.new@example.com';

test('N-KIND 新: 種別 自己都合/学校行事の追加と担当時限・担当業務ロック', async ({ page, request }) => {
  const probe = await request.post(`${NEW}/api/auth/login`, { data: { username: TUTOR, password: PASSWORD } });
  test.skip(!probe.ok(), `講師 ${TUTOR} が未seedのためスキップ (${probe.status()})`);

  await login(page, NEW, TUTOR, '/tutor/reports');
  await page.waitForSelector('#lineRows tr[data-index="0"]');
  await page.click('#newReportBtn');

  const row = page.locator('#lineRows tr[data-index="0"]');
  const kindSelect = row.locator('select[data-field="kind"]');

  // ② 種別プルダウン: 勤務/有給/欠勤/自己都合/学校行事
  await expect(kindSelect.locator('option')).toHaveText(['勤務', '有給', '欠勤', '自己都合', '学校行事']);

  // ① 日付ボックス内に曜日を併記（2026/07/07 は火曜）
  await row.locator('input[data-field="date"]').fill('2026-07-07');
  await expect(row.locator('[data-weekday]')).toHaveText('(火)');

  // ③ 自己都合: 担当時限=選択不可・担当業務（分）=0固定。休憩・交通費は手動入力可
  await kindSelect.selectOption('personal_reason');
  await expect(row.locator('[data-period-btn]')).toBeDisabled();
  await expect(row.locator('[data-field="teach_minutes"]')).toHaveValue('0');
  await expect(row.locator('[data-field="teach_minutes"]')).toBeDisabled();
  await expect(row.locator('[data-field="break_minutes"]')).toBeEnabled();
  await expect(row.locator('[data-field="commute_fee"]')).toBeEnabled();
  await expect(row).toHaveClass(/bg-violet-50/);
  // その他の分は自動計算（開始8:40＋30分）と集計（自己都合1回・勤務日数0日）に反映される
  await row.locator('[data-field="break_minutes"]').fill('30');
  await expect(row.locator('[data-time-display]')).toHaveText('08:40〜09:10');
  await expect(page.locator('#summaryArea')).toContainText('自己都合：1回');
  await expect(page.locator('#summaryArea')).toContainText('勤務日数：0日');

  // ③ 学校行事も同じロック＋行背景sky
  await kindSelect.selectOption('school_event');
  await expect(row.locator('[data-period-btn]')).toBeDisabled();
  await expect(row.locator('[data-field="teach_minutes"]')).toHaveValue('0');
  await expect(row).toHaveClass(/bg-sky-50/);
  await expect(page.locator('#summaryArea')).toContainText('学校行事：1回');

  // レイアウト確認用スクリーンショット（保存はしない）
  await page.screenshot({ path: 'test-results/new-tutor-kind.png', fullPage: false });

  // 勤務へ戻すと0固定は解除され（値クリア）、担当時限も選択可能に戻る
  await kindSelect.selectOption('');
  await expect(row.locator('[data-field="teach_minutes"]')).toHaveValue('');
  await expect(row.locator('[data-field="teach_minutes"]')).toBeEnabled();
  await expect(row.locator('[data-period-btn]')).toBeEnabled();
});
