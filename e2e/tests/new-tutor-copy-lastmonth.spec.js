// @ts-check
// 新システム(EMPS) 講師「先月の記入分をコピー」e2e。
// 先月の下書きをAPIで用意（メール送信なし）→ UIでコピー → 日付が「同じ第N曜日」で当月へ
// 変換されることを検証する。当月フォームは保存しない（クリック無し）＝サーバ状態を変えない。
// 後片付けで用意した先月の下書きをAPI削除する。
// 明細の列は契約管理の列定義（無ければ既定列）で環境ごとに異なるため、実フォームの
// 列定義を API から解決し、存在する列だけで下書き作成・検証を行う（reports.html の
// activeColumnDefinition / timeMinuteKeys と同じ解決ルール）。
const { test, expect } = require('@playwright/test');
const { login, NEW, PASSWORD } = require('./helpers');

const TUTOR = 'qa.tutor.new@example.com';
const EDITABLE = new Set(['draft', 'returned_to_tutor', 'returned_to_sales']);
// reports.html の DEFAULT_FORM_DEFINITION.columns と同じ既定列（契約に列定義が無いとき適用）
const DEFAULT_COLUMNS = [
  { key: 'date', type: 'date' },
  { key: 'start', type: 'time' },
  { key: 'end', type: 'time' },
  { key: 'subject_period', type: 'number' },
  { key: 'teach_minutes', type: 'number' },
  { key: 'break_minutes', type: 'number' },
  { key: 'commute_fee', type: 'number' },
  { key: 'note', type: 'text' },
];

function monthOf(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
}
// 対象月の第N曜日（YYYY-MM-DD）。存在しない場合は null（実装と独立に定義から算出する）
function nthWeekdayOf(month, weekday, nth) {
  const [y, m] = month.split('-').map(Number);
  const first = new Date(y, m - 1, 1).getDay();
  const day = 1 + ((weekday - first + 7) % 7) + (nth - 1) * 7;
  if (day > new Date(y, m, 0).getDate()) return null;
  return `${y}-${String(m).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}
// 業務開始〜終了の合計対象＝担当時限より右の「分」列（交通費と採点の回数は除外）
function timeMinuteKeys(columns) {
  const periodIndex = columns.findIndex(column => column.key === 'subject_period');
  const keys = [];
  columns.slice(periodIndex + 1).forEach(column => {
    if (column.type === 'count_minutes') keys.push(column.minutes_key);
    else if (column.type === 'number' && column.key !== 'commute_fee') keys.push(column.key);
  });
  return keys;
}

test('N-COPYLM 新: 先月の記入分を第N曜日変換でコピーできる', async ({ page, request }) => {
  const probe = await request.post(`${NEW}/api/auth/login`, { data: { username: TUTOR, password: PASSWORD } });
  test.skip(!probe.ok(), `講師 ${TUTOR} が未seedのためスキップ (${probe.status()})`);
  const token = (await probe.json()).access_token;
  const auth = { Authorization: `Bearer ${token}` };

  const now = new Date();
  const curMonth = monthOf(now);
  const prevMonth = monthOf(new Date(now.getFullYear(), now.getMonth() - 1, 1));

  // ページは最初の紐付け（学校）を自動選択するため、同じ先頭の紐付けを対象にする
  const asgRes = await request.get(`${NEW}/api/w/assignments`, { headers: auth });
  expect(asgRes.ok()).toBeTruthy();
  const assignments = await asgRes.json();
  test.skip(!assignments.length, '紐付けが無いためスキップ');
  const assignmentId = assignments[0].id;

  // 実フォームの列定義を解決（契約の column_definition ＞ 既定列。休憩非表示フラグにも追従）
  const contractsRes = await request.get(`${NEW}/api/w/contracts/for-tutor`, { headers: auth });
  const contracts = contractsRes.ok() ? await contractsRes.json() : [];
  const contract = contracts.find(c => String(c.school_id) === String(assignments[0].parent_id)) || null;
  let columns = (contract && Array.isArray(contract.column_definition) && contract.column_definition.length)
    ? contract.column_definition : DEFAULT_COLUMNS;
  if (contract && contract.show_break_minutes === false) columns = columns.filter(c => c.key !== 'break_minutes');
  const lineKeys = new Set(columns.flatMap(c => (c.type === 'count_minutes' ? [c.count_key, c.minutes_key] : [c.key])));

  // 当月の報告が編集不可（提出済み等）だとコピーできないため、その場合はスキップ
  const curRes = await request.get(`${NEW}/api/w/reports?target_month=${curMonth}`, { headers: auth });
  const curReport = (await curRes.json()).find(r => r.assignment_id === assignmentId);
  test.skip(Boolean(curReport && !EDITABLE.has(curReport.status)), `当月報告が編集不可(${curReport?.status})のためスキップ`);

  // 先月分のコピー元下書きを用意（既存があれば下書きに限り削除して作り直す）
  const prevRes = await request.get(`${NEW}/api/w/reports?target_month=${prevMonth}`, { headers: auth });
  const prevReport = (await prevRes.json()).find(r => r.assignment_id === assignmentId);
  if (prevReport) {
    test.skip(prevReport.status !== 'draft', `先月報告が下書き以外(${prevReport.status})のためスキップ`);
    await request.delete(`${NEW}/api/w/reports/${prevReport.id}`, { headers: auth });
  }
  const srcWed1 = nthWeekdayOf(prevMonth, 3, 1); // 先月の第1水曜
  const srcWed2 = nthWeekdayOf(prevMonth, 3, 2); // 先月の第2水曜
  const wantWed1 = nthWeekdayOf(curMonth, 3, 1); // 当月の第1水曜
  const wantWed2 = nthWeekdayOf(curMonth, 3, 2); // 当月の第2水曜
  // 実フォームに存在する列だけで1行目の値を組み立てる（キーが無い列は保存もされないため）
  const candidateValues = { subject_period: '1・2', teach_minutes: 100, break_minutes: 10, commute_fee: 500, note: '先月分e2e' };
  const line1 = { date: srcWed1 };
  Object.entries(candidateValues).forEach(([key, value]) => { if (lineKeys.has(key)) line1[key] = value; });
  const create = await request.post(`${NEW}/api/w/reports`, {
    headers: auth,
    data: {
      assignment_id: assignmentId,
      target_month: prevMonth,
      form_type: 'monthly_dispatch',
      form_data: {
        meta: {},
        lines: [line1, { date: srcWed2, kind: 'paid_leave' }],
      },
    },
  });
  expect(create.ok(), `先月下書き作成 (${create.status()})`).toBeTruthy();
  const sourceId = (await create.json()).id;

  try {
    await login(page, NEW, TUTOR, '/tutor/reports');
    await page.waitForSelector('#lineRows tr[data-index="0"]', { state: 'attached' });

    // 「先月の記入分をコピー」を押す（既入力がある場合は確認ポップアップ→コピーする）
    await page.click('#copyLastMonthBtn');
    const confirmBtn = page.getByRole('button', { name: 'コピーする' });
    if (await confirmBtn.isVisible({ timeout: 1500 }).catch(() => false)) {
      await confirmBtn.click();
    }

    // 日付が「同じ第N曜日」で当月へ変換される（第1水曜→第1水曜・第2水曜→第2水曜、日付昇順）
    const row0 = page.locator('#lineRows tr[data-index="0"]');
    const row1 = page.locator('#lineRows tr[data-index="1"]');
    await expect(row0.locator('[data-field="date"]')).toHaveValue(wantWed1 ?? '');
    await expect(row1.locator('[data-field="date"]')).toHaveValue(wantWed2 ?? '');
    // 分数・交通費・内容・担当時限など、実フォームに存在する列は先月と同じ値がコピーされる
    for (const [key, value] of Object.entries(line1)) {
      if (key === 'date') continue;
      await expect(row0.locator(`[data-field="${key}"]`), `列 ${key} のコピー`).toHaveValue(String(value));
    }
    // 業務開始〜終了時間は分数（担当時限より右の分列合計）から再計算される
    const totalMinutes = timeMinuteKeys(columns).reduce((sum, key) => sum + (Number(line1[key]) || 0), 0);
    if (totalMinutes > 0) {
      const endMinutes = 8 * 60 + 40 + totalMinutes;
      const endLabel = `${String(Math.floor(endMinutes / 60)).padStart(2, '0')}:${String(endMinutes % 60).padStart(2, '0')}`;
      await expect(row0.locator('[data-time-display]')).toHaveText(`08:40〜${endLabel}`);
    }
    // 種別（有給）もコピーされ、業務入力（日付・種別・内容以外）は無効化される
    await expect(row1.locator('[data-field="kind"]')).toHaveValue('paid_leave');
    const workKey = ['teach_minutes', 'break_minutes', 'commute_fee'].find(key => lineKeys.has(key));
    if (workKey) await expect(row1.locator(`[data-field="${workKey}"]`)).toBeDisabled();
    await page.screenshot({ path: 'test-results/new-tutor-copy-lastmonth.png' });
    // 保存はしない（当月のサーバ状態を変えない＝メール送信ゼロ）
  } finally {
    // 後片付け: 用意した先月の下書きを削除
    await request.delete(`${NEW}/api/w/reports/${sourceId}`, { headers: auth });
  }
});
