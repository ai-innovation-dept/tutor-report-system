// @ts-check
// 新システム（業務連絡表 / port 8001）事務・営業の進捗パイプライン編集モーダル：
// 上部にも横スクロールバーを追加し、下部の表とスクロール位置が双方向同期することを検証する。
// office/queue.html と sales/queue.html は同一実装のため、両ロールで同じ検証を行う。
// シードデータに依存しないよう、列の多い合成レポートを allReports へ注入して編集モーダルを開く。
const { test } = require('@playwright/test');
const { login, NEW, expect } = require('./helpers');

// 列が確実にモーダル幅を超える（横スクロールが発火する）よう、数値列を多めに持つ列定義を作る。
function wideReport() {
  const columnDefinition = [
    { key: 'date', label: '日付', type: 'date' },
    { key: 'start', label: '開始', type: 'time' },
    { key: 'end', label: '終了', type: 'time' },
    { key: 'note', label: '指導内容', type: 'text' },
  ];
  for (let i = 1; i <= 10; i++) {
    columnDefinition.push({ key: `num${i}`, label: `数値${i}`, type: 'number' });
  }
  return {
    id: 'topscroll-test-report',
    status: 'awaiting_office', // OFFICE_EDIT_STATUSES に含まれる＝編集ボタン対象
    target_month: '2026-06',
    form_data: {
      meta: { column_definition: columnDefinition },
      lines: [
        { date: '2026-06-01', start: '10:00', end: '11:00', note: 'テスト行1' },
        { date: '2026-06-02', start: '13:00', end: '14:30', note: 'テスト行2' },
      ],
    },
  };
}

async function verifyTopScrollSync(page) {
  // 合成レポートを注入して編集モーダルを開く（実テンプレートの openOfficeEditModal を使用）。
  // allReports は `let` グローバルで window に乗らず page.evaluate の関数スコープから見えないため、
  // 全 <script> と同じグローバル字句環境で解決される indirect eval 経由で参照する。
  await page.evaluate((reportJson) => {
    const report = JSON.parse(reportJson);
    const g = (0, eval); // indirect eval → グローバルスコープで実行
    g('allReports').push(report);
    g('openOfficeEditModal')(report.id);
  }, JSON.stringify(wideReport()));

  const modal = page.locator('#officeEditModal');
  await expect(modal).toBeVisible();

  // 上部スクロールバー・本体・スペーサの状態を計測
  const metrics = await page.evaluate(() => {
    const top = document.getElementById('officeEditScrollTop');
    const body = document.getElementById('officeEditScrollBody');
    const spacer = document.getElementById('officeEditScrollTopSpacer');
    return {
      hasTop: !!top,
      hasBody: !!body,
      hasSpacer: !!spacer,
      // 本体は横にあふれている（スクロール可能）か
      bodyOverflow: body ? body.scrollWidth - body.clientWidth : 0,
      // 上部バーもあふれている（＝横スクロールバーが出る）か
      topOverflow: top ? top.scrollWidth - top.clientWidth : 0,
      spacerWidth: spacer ? Math.round(spacer.getBoundingClientRect().width) : 0,
      bodyScrollWidth: body ? body.scrollWidth : 0,
    };
  });
  expect(metrics.hasTop, '上部スクロールバー要素が存在する').toBeTruthy();
  expect(metrics.hasSpacer, 'スペーサ要素が存在する').toBeTruthy();
  expect(metrics.bodyOverflow, '表本体が横にあふれている（横スクロール可能）').toBeGreaterThan(0);
  expect(metrics.topOverflow, '上部バーも横にあふれている（上部に横スクロールバーが出る）').toBeGreaterThan(0);
  // スペーサ幅は本体の描画幅に一致（数pxの誤差を許容）
  expect(Math.abs(metrics.spacerWidth - metrics.bodyScrollWidth)).toBeLessThanOrEqual(2);

  // 上部バー → 本体 への同期
  const topToBody = await page.evaluate(() => {
    const top = document.getElementById('officeEditScrollTop');
    const body = document.getElementById('officeEditScrollBody');
    top.scrollLeft = 120;
    top.dispatchEvent(new Event('scroll'));
    return { topLeft: top.scrollLeft, bodyLeft: body.scrollLeft };
  });
  expect(topToBody.bodyLeft, '上部バーを動かすと本体も同じ位置へ').toBe(topToBody.topLeft);

  // 本体 → 上部バー への同期
  const bodyToTop = await page.evaluate(() => {
    const top = document.getElementById('officeEditScrollTop');
    const body = document.getElementById('officeEditScrollBody');
    body.scrollLeft = 60;
    body.dispatchEvent(new Event('scroll'));
    return { topLeft: top.scrollLeft, bodyLeft: body.scrollLeft };
  });
  expect(bodyToTop.topLeft, '本体を動かすと上部バーも同じ位置へ').toBe(bodyToTop.bodyLeft);
}

test.describe('新システム 事務/営業 進捗パイプライン編集 上部横スクロール', () => {
  test('事務(office)：上部スクロールバーが本体と双方向同期', async ({ page }) => {
    await login(page, NEW, 'office1@example.com', '/office/queue');
    await verifyTopScrollSync(page);
  });

  test('営業(sales)：上部スクロールバーが本体と双方向同期', async ({ page }) => {
    await login(page, NEW, 'sales1@example.com', '/sales/queue');
    await verifyTopScrollSync(page);
  });
});
