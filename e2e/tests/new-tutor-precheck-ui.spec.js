// @ts-check
// 新システム(EMPS) 講師 承認管理の事前確認フロー表示 e2e。
// 下書き作成/削除はAPIで行うが、提出・承認等の遷移は一切実行しない（ポップアップはキャンセルで中断）
// ＝通知メール送信ゼロ・ワークフロー状態変更ゼロ。
// 検証: ①1〜9分手入力の報告書は4ステップ表示（運営へ依頼→学校へ依頼→学校承認→運営承認）
//       ②ボタンが「提出」表記 ③押下でワークフロー変更のポップアップ→キャンセルで提出されない
const { test, expect } = require('@playwright/test');
const { login, NEW, PASSWORD } = require('./helpers');

const TUTOR = 'qa.tutor.new@example.com';
const SCHOOL = 'school1@example.com';
const MONTH = '2031-01';

test('N-PRECHECK 新: 1〜9分手入力の報告書は事前確認フロー表示＋提出前ポップアップ', async ({ page, request }) => {
  // 前提ユーザー（flow-new と同じ seed）が無い環境ではスキップ
  const probe = await request.post(`${NEW}/api/auth/login`, { data: { username: TUTOR, password: PASSWORD } });
  test.skip(!probe.ok(), `講師 ${TUTOR} が未seedのためスキップ (${probe.status()})`);
  const tutorToken = (await probe.json()).access_token;
  const auth = { Authorization: `Bearer ${tutorToken}` };

  const schoolProbe = await request.post(`${NEW}/api/auth/login`, { data: { username: SCHOOL, password: PASSWORD } });
  test.skip(!schoolProbe.ok(), `学校 ${SCHOOL} が未seedのためスキップ`);
  const schoolMe = await request.get(`${NEW}/api/w/users/me`, {
    headers: { Authorization: `Bearer ${(await schoolProbe.json()).access_token}` },
  });
  const schoolId = (await schoolMe.json()).id;

  // 講師×学校の紐付けを取得/作成し、151分（1の位=1）の下書きを用意（下書き作成は通知なし）
  const asg = await request.post(`${NEW}/api/w/assignments/for-school`, { headers: auth, data: { school_id: schoolId } });
  expect(asg.ok(), `for-school (${asg.status()})`).toBeTruthy();
  const assignmentId = (await asg.json()).id;
  const formData = {
    meta: {},
    lines: [{ date: `${MONTH}-10`, kind: '', start: '08:40', end: '11:31', subject_period: '1・2・3', teach_minutes: 151, break_minutes: 20, commute_fee: 0, note: 'e2e precheck' }],
  };
  let reportId = null;
  const create = await request.post(`${NEW}/api/w/reports`, {
    headers: auth,
    data: { assignment_id: assignmentId, target_month: MONTH, form_type: 'monthly_dispatch', form_data: formData },
  });
  if (create.ok()) {
    reportId = (await create.json()).id;
  } else {
    // 既存（前回実行の残り等）を再利用。下書き/差戻し以外ならスキップ
    const list = await request.get(`${NEW}/api/w/reports?target_month=${MONTH}`, { headers: auth });
    const existing = (await list.json()).find(r => r.assignment_id === assignmentId && r.target_month === MONTH);
    test.skip(!existing || !['draft', 'returned_to_tutor'].includes(existing.status),
      `対象月の報告が編集不可状態のためスキップ (${create.status()})`);
    reportId = existing.id;
    const patch = await request.patch(`${NEW}/api/w/reports/${reportId}`, { headers: auth, data: { form_data: formData } });
    expect(patch.ok(), `draft patch (${patch.status()})`).toBeTruthy();
  }

  try {
    await login(page, NEW, TUTOR, '/tutor/approval');
    await page.waitForSelector('#approvalMonths details, #approvalMonths div');
    await page.selectOption('#exportMonth', MONTH);

    const card = page.locator('#approvalMonths details').first();
    await expect(card).toBeVisible();
    // ③ 4ステップ表示（運営へ依頼→学校へ依頼→学校承認→運営承認）と日時4欄
    await expect(card.getByText('運営へ依頼', { exact: true })).toBeVisible();
    await expect(card.getByText('運営へ依頼日時')).toBeVisible();
    await expect(card.getByText('学校へ依頼日時')).toBeVisible();
    await expect(card.getByText('学校承認日時')).toBeVisible();
    await expect(card.getByText('運営承認日時')).toBeVisible();
    // 事前確認フローの事前案内（amber）とボタン表記「提出」
    await expect(card.getByText('運営（事務）の事前確認を経由します', { exact: false })).toBeVisible();
    const submitBtn = card.getByRole('button', { name: '提出', exact: true });
    await expect(submitBtn).toBeVisible();

    // ② 押下でワークフロー変更ポップアップ → キャンセルで提出しない
    await submitBtn.click();
    const modal = page.getByText('承認ワークフローが変更されます');
    await expect(modal).toBeVisible();
    await expect(page.getByText('講師 → 事務（事前確認） → 学校 → 事務 → 営業')).toBeVisible();
    await page.screenshot({ path: 'test-results/new-tutor-precheck-ui.png', fullPage: false });
    await page.getByRole('button', { name: 'キャンセル' }).click();
    await expect(modal).not.toBeVisible();

    // キャンセルしたので下書きのまま（提出＝メール送信は発生していない）
    const after = await request.get(`${NEW}/api/w/reports/${reportId}`, { headers: auth });
    expect((await after.json()).status).toBe('draft');
  } finally {
    // 後始末: 下書きを削除（通知なし）。失敗しても他テストに影響しないため黙認
    await request.delete(`${NEW}/api/w/reports/${reportId}`, { headers: auth }).catch(() => {});
  }
});
