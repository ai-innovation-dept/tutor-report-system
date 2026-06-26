// @ts-check
// 新システム（業務連絡表 / port 8001）事務・営業の進捗パイプライン編集モーダルの表検証。
//  (1) 上部にも横スクロールバーがあり、下部の表とスクロール位置が双方向同期する。
//  (2) 縦スクロールで見出し行(thead)が固定される。
//  (3) 横スクロールで先頭=日付列が固定される。
// 編集モーダル(office-edit)を持つのは事務(office)のみ。営業(sales)は最終承認（承認/差戻し）専用で
// 編集モーダルを持たない（sales/queue.html に #officeEditHead は無い＝office-edit は撤回済み）。
// そのため本「編集モーダルの表スクロール」検証は office のみを対象とする。
// シードに依存しないよう、列も行も多い合成レポートを allReports へ注入して編集モーダルを開く。
const { test } = require('@playwright/test');
const { login, NEW, expect } = require('./helpers');

// 共有 login()（認証レスポンス受領後に遷移）でログインし、queue.html の静的要素 #officeEditHead を
// 待って到達を確証する。
async function robustLogin(page, email, path) {
  await login(page, NEW, email, path);
  await page.waitForSelector('#officeEditHead', { state: 'attached', timeout: 15000 });
}

// 列が確実にモーダル幅を、行が確実に表領域の高さを超えるよう、多列・多行の合成レポートを作る。
function wideReport() {
  const columnDefinition = [
    { key: 'date', label: '日付', type: 'date' },
    { key: 'start', label: '業務開始時間', type: 'time' },
    { key: 'end', label: '業務終了時間', type: 'time' },
    { key: 'note', label: '指導内容', type: 'text' },
  ];
  for (let i = 1; i <= 10; i++) {
    columnDefinition.push({ key: `num${i}`, label: `数値${i}`, type: 'number' });
  }
  const lines = [];
  for (let d = 1; d <= 40; d++) {
    lines.push({ date: `2026-06-${String(((d - 1) % 28) + 1).padStart(2, '0')}`, start: '10:00', end: '11:00', note: `行${d}` });
  }
  return {
    id: 'topscroll-test-report',
    status: 'awaiting_office', // OFFICE_EDIT_STATUSES に含まれる＝編集対象
    target_month: '2026-06',
    form_data: { meta: { column_definition: columnDefinition }, lines },
  };
}

async function openWideEditModal(page) {
  // allReports は `let` グローバルで window に乗らず page.evaluate の関数スコープから見えないため、
  // 全 <script> と同じグローバル字句環境で解決される indirect eval 経由で参照する。
  await page.evaluate((reportJson) => {
    const report = JSON.parse(reportJson);
    const g = (0, eval); // indirect eval → グローバルスコープで実行
    g('allReports').push(report);
    g('openOfficeEditModal')(report.id);
  }, JSON.stringify(wideReport()));
  await expect(page.locator('#officeEditModal')).toBeVisible();
}

test.describe('新システム 事務/営業 進捗パイプライン編集 表スクロール', () => {
  for (const role of [
    { name: '事務(office)', email: 'office1@example.com', path: '/office/queue' },
  ]) {
    test(`${role.name}：上部スクロールバーが本体と双方向同期`, async ({ page }) => {
      await page.setViewportSize({ width: 1280, height: 900 });
      await robustLogin(page, role.email, role.path);
      await openWideEditModal(page);

      const m = await page.evaluate(() => {
        const top = document.getElementById('officeEditScrollTop');
        const body = document.getElementById('officeEditScrollBody');
        const spacer = document.getElementById('officeEditScrollTopSpacer');
        return {
          bodyOverflow: body.scrollWidth - body.clientWidth,
          topOverflow: top.scrollWidth - top.clientWidth,
          // 上部バーと本体の最大スクロール量が一致する（縦スクロールバー幅を補正済み）
          topMax: top.scrollWidth - top.clientWidth,
          bodyMax: body.scrollWidth - body.clientWidth,
          hasSpacer: !!spacer,
        };
      });
      expect(m.hasSpacer, 'スペーサ要素が存在').toBeTruthy();
      expect(m.bodyOverflow, '表本体が横にあふれている').toBeGreaterThan(0);
      expect(m.topOverflow, '上部バーも横にあふれている').toBeGreaterThan(0);
      expect(Math.abs(m.topMax - m.bodyMax), '上部バーと本体の最大横スクロール量が一致').toBeLessThanOrEqual(2);

      const topToBody = await page.evaluate(() => {
        const top = document.getElementById('officeEditScrollTop');
        const body = document.getElementById('officeEditScrollBody');
        top.scrollLeft = 120; top.dispatchEvent(new Event('scroll'));
        return { topLeft: top.scrollLeft, bodyLeft: body.scrollLeft };
      });
      expect(topToBody.bodyLeft, '上部バー→本体 同期').toBe(topToBody.topLeft);

      const bodyToTop = await page.evaluate(() => {
        const top = document.getElementById('officeEditScrollTop');
        const body = document.getElementById('officeEditScrollBody');
        body.scrollLeft = 60; body.dispatchEvent(new Event('scroll'));
        return { topLeft: top.scrollLeft, bodyLeft: body.scrollLeft };
      });
      expect(bodyToTop.topLeft, '本体→上部バー 同期').toBe(bodyToTop.bodyLeft);
    });

    test(`${role.name}：縦スクロールで見出し行が固定`, async ({ page }) => {
      await page.setViewportSize({ width: 1280, height: 900 });
      await robustLogin(page, role.email, role.path);
      await openWideEditModal(page);

      const r = await page.evaluate(() => {
        const body = document.getElementById('officeEditScrollBody');
        const th = document.querySelector('#officeEditHead th');
        const pos = getComputedStyle(th).position;
        const before = th.getBoundingClientRect().top - body.getBoundingClientRect().top;
        body.scrollTop = 300; // 縦に大きくスクロール
        const after = th.getBoundingClientRect().top - body.getBoundingClientRect().top;
        return { pos, before, after, vScroll: body.scrollTop, vOverflow: body.scrollHeight - body.clientHeight };
      });
      expect(r.vOverflow, '表本体が縦にあふれている（縦スクロール可能）').toBeGreaterThan(0);
      expect(r.vScroll, '実際に縦スクロールした').toBeGreaterThan(0);
      expect(r.pos, '見出しセルが position:sticky').toBe('sticky');
      // 見出し行は縦スクロールしても表領域の上端に留まる（移動量はごく僅か）
      expect(Math.abs(r.after - r.before), '見出し行が縦スクロールで上端に固定').toBeLessThanOrEqual(2);
    });

    test(`${role.name}：横スクロールで日付列が固定`, async ({ page }) => {
      await page.setViewportSize({ width: 1280, height: 900 });
      await robustLogin(page, role.email, role.path);
      await openWideEditModal(page);

      const r = await page.evaluate(() => {
        const body = document.getElementById('officeEditScrollBody');
        const firstHeadCell = document.querySelector('#officeEditHead th');           // 日付見出し
        const firstBodyCell = document.querySelector('#officeEditBody tr td');         // 日付セル
        const secondBodyCell = document.querySelector('#officeEditBody tr td:nth-child(2)'); // 種別セル（非固定）
        const bx = body.getBoundingClientRect().left;
        const headPos = getComputedStyle(firstHeadCell).position;
        const bodyPos = getComputedStyle(firstBodyCell).position;
        const dateBefore = firstBodyCell.getBoundingClientRect().left - bx;
        const kindBefore = secondBodyCell.getBoundingClientRect().left - bx;
        body.scrollLeft = 300; // 横に大きくスクロール
        const dateAfter = firstBodyCell.getBoundingClientRect().left - bx;
        const kindAfter = secondBodyCell.getBoundingClientRect().left - bx;
        return { headPos, bodyPos, dateBefore, dateAfter, kindBefore, kindAfter, hScroll: body.scrollLeft };
      });
      expect(r.hScroll, '実際に横スクロールした').toBeGreaterThan(0);
      expect(r.headPos, '日付見出しが position:sticky').toBe('sticky');
      expect(r.bodyPos, '日付セルが position:sticky').toBe('sticky');
      // 日付列は横スクロールしても表領域の左端に留まる
      expect(Math.abs(r.dateAfter - r.dateBefore), '日付列が横スクロールで左端に固定').toBeLessThanOrEqual(2);
      // 非固定の種別列は横スクロールで左へ移動する（固定されていないことの対照確認）
      expect(r.kindBefore - r.kindAfter, '非固定列(種別)は横スクロールで移動する').toBeGreaterThan(50);
    });
  }
});
