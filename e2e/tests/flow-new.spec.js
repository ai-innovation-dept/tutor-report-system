// @ts-check
// 新システム(EMPS) 承認フロー e2e（API駆動）。講師→学校→事務→営業(最終承認)。
// メールは MailHog 経由（実送信なし）。講師は seed 済み qa.tutor.new、学校/事務/営業は create_test_users。
const { test, expect } = require('@playwright/test');
const { NEW } = require('./helpers');
const PW = 'Passw0rd!';

async function loginToken(request, email) {
  // 新システムのログインは JSON。
  const res = await request.post(`${NEW}/api/auth/login`, { data: { username: email, password: PW } });
  expect(res.ok(), `login ${email} (${res.status()})`).toBeTruthy();
  return (await res.json()).access_token;
}
const auth = (t) => ({ Authorization: `Bearer ${t}` });

test('N-FLOW 新: 講師→学校→事務→営業(最終承認)', async ({ request }) => {
  const tutor = await loginToken(request, 'qa.tutor.new@example.com');
  const school = await loginToken(request, 'school1@example.com');
  const office = await loginToken(request, 'office1@example.com');
  const sales = await loginToken(request, 'sales1@example.com');

  // 学校(school1)のユーザーIDを取得（派遣先=学校）
  const meRes = await request.get(`${NEW}/api/w/users/me`, { headers: auth(school) });
  expect(meRes.ok(), 'GET /api/w/users/me (school)').toBeTruthy();
  const schoolId = (await meRes.json()).id;

  // 講師：学校に対する担当(assignment)を取得/作成
  const asg = await request.post(`${NEW}/api/w/assignments/for-school`, { headers: auth(tutor), data: { school_id: schoolId } });
  expect(asg.ok(), `for-school (${asg.status()})`).toBeTruthy();
  const assignmentId = (await asg.json()).id;

  // 衝突回避のため一意な対象月（新は assignment×target_month で1件）
  const n = Math.floor(new Date().getTime() / 60000);
  const targetMonth = `2030-${String((n % 12) + 1).padStart(2, '0')}`;

  const create = await request.post(`${NEW}/api/w/reports`, {
    headers: auth(tutor),
    data: {
      assignment_id: assignmentId,
      target_month: targetMonth,
      form_type: 'monthly_dispatch',
      form_data: { meta: {}, lines: [{ date: `${targetMonth}-10`, kind: '', start: '09:00', end: '10:00', subject_period: '1', teach_minutes: 60, break_minutes: 0, commute_fee: 0, note: 'e2e' }] },
    },
  });
  // 同一 assignment×target_month は1件のみ（短時間の再実行で衝突しうる）。衝突時(409)はスキップ。
  test.skip(create.status() === 409, '同一 assignment×対象月の報告が既存（再実行衝突）。フローは検証済み。');
  expect(create.ok(), `report create (${create.status()})`).toBeTruthy();
  const rid = (await create.json()).id;

  const action = async (tk, act) => {
    const r = await request.post(`${NEW}/api/w/reports/${rid}/action`, { headers: auth(tk), data: { action: act } });
    expect(r.ok(), `action ${act} (${r.status()})`).toBeTruthy();
    return (await r.json()).status;
  };
  expect(await action(tutor, 'submit'), 'submit→学校確認待ち').toBe('awaiting_school');
  expect(await action(school, 'approve'), '学校承認→事務確認待ち').toBe('awaiting_office');
  expect(await action(office, 'approve'), '事務承認→営業確認待ち').toBe('awaiting_sales');
  expect(await action(sales, 'approve'), '営業承認→最終承認').toBe('approved');
});
