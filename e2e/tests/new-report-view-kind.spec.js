// @ts-check
// 新システム(EMPS) 参照ビュー（report_view）の種別表示 e2e。
// 下書き作成/削除はAPIで行い、提出・承認等の遷移は一切実行しない＝通知メール送信ゼロ。
// 検証: ①種別バッジ（自己都合=violet/学校行事=sky）②勤務日数に含めず回数として集計
//       ③自己都合・学校行事の行の休憩等の分は合計に含める
const { test, expect } = require('@playwright/test');
const { login, NEW, PASSWORD } = require('./helpers');

const TUTOR = 'qa.tutor.new@example.com';
const SCHOOL = 'school1@example.com';
const MONTH = '2031-02'; // 他specと衝突しない未来月

test('N-VIEW 新: 参照ビューの自己都合/学校行事バッジと集計', async ({ page, request }) => {
  const probe = await request.post(`${NEW}/api/auth/login`, { data: { username: TUTOR, password: PASSWORD } });
  test.skip(!probe.ok(), `講師 ${TUTOR} が未seedのためスキップ (${probe.status()})`);
  const auth = { Authorization: `Bearer ${(await probe.json()).access_token}` };

  const schoolProbe = await request.post(`${NEW}/api/auth/login`, { data: { username: SCHOOL, password: PASSWORD } });
  test.skip(!schoolProbe.ok(), `学校 ${SCHOOL} が未seedのためスキップ`);
  const schoolMe = await request.get(`${NEW}/api/w/users/me`, {
    headers: { Authorization: `Bearer ${(await schoolProbe.json()).access_token}` },
  });
  const schoolId = (await schoolMe.json()).id;

  const asg = await request.post(`${NEW}/api/w/assignments/for-school`, { headers: auth, data: { school_id: schoolId } });
  expect(asg.ok(), `for-school (${asg.status()})`).toBeTruthy();
  const assignmentId = (await asg.json()).id;
  const formData = {
    meta: {},
    lines: [
      { date: `${MONTH}-02`, kind: '', start: '08:40', end: '10:20', subject_period: '1・2', teach_minutes: 100, break_minutes: 0, commute_fee: 500, note: '' },
      { date: `${MONTH}-03`, kind: 'paid_leave' },
      { date: `${MONTH}-04`, kind: 'personal_reason', teach_minutes: 0, break_minutes: 0, commute_fee: 0, note: '私用のため' },
      { date: `${MONTH}-05`, kind: 'school_event', start: '08:40', end: '09:10', teach_minutes: 0, break_minutes: 30, commute_fee: 0, note: '' },
    ],
  };
  let reportId = null;
  const create = await request.post(`${NEW}/api/w/reports`, {
    headers: auth,
    data: { assignment_id: assignmentId, target_month: MONTH, form_type: 'monthly_dispatch', form_data: formData },
  });
  if (create.ok()) {
    reportId = (await create.json()).id;
  } else {
    const list = await request.get(`${NEW}/api/w/reports?target_month=${MONTH}`, { headers: auth });
    const existing = (await list.json()).find(r => r.assignment_id === assignmentId && r.target_month === MONTH);
    test.skip(!existing || !['draft', 'returned_to_tutor'].includes(existing.status),
      `対象月の報告が編集不可状態のためスキップ (${create.status()})`);
    reportId = existing.id;
    const patch = await request.patch(`${NEW}/api/w/reports/${reportId}`, { headers: auth, data: { form_data: formData } });
    expect(patch.ok(), `draft patch (${patch.status()})`).toBeTruthy();
  }

  try {
    await login(page, NEW, TUTOR, `/reports/${reportId}/view`);
    await page.waitForSelector('#content:not(.hidden)');
    // ① 種別バッジ
    await expect(page.locator('span.bg-violet-100', { hasText: '自己都合' })).toBeVisible();
    await expect(page.locator('span.bg-sky-100', { hasText: '学校行事' })).toBeVisible();
    // ② 勤務日数は勤務行のみ・種別は回数集計
    await expect(page.locator('#content')).toContainText('勤務日数：1日');
    await expect(page.locator('#content')).toContainText('有給休暇：1回');
    await expect(page.locator('#content')).toContainText('自己都合：1回');
    await expect(page.locator('#content')).toContainText('学校行事：1回');
    // ③ 学校行事の行の休憩30分は合計に含める（0+0+30）
    await expect(page.locator('#content')).toContainText('休憩時間（分）：30');
    await page.screenshot({ path: 'test-results/new-report-view-kind.png', fullPage: false });
  } finally {
    // 後始末: 下書きを削除（通知なし）。失敗しても他テストに影響しないため黙認
    await request.delete(`${NEW}/api/w/reports/${reportId}`, { headers: auth }).catch(() => {});
  }
});
