// @ts-check
// 新システム（業務連絡表 / port 8001）ユーザー管理(/admin/users)のCSVエクスポート/インポートUI検証（フェーズ①）。
//  - CSVエクスポート/インポートのボタンが表示される（office/sales 両ロール）。
//  - エクスポートが正しいCSV見出しで取得できる（セッションCookie付き）。
//  - インポートのブラウザ動線（隠しfile input → importCsv → fetch → 結果モーダル）が機能する。
//    成功時のDB更新ロジックは backend の単体テスト(test_user_csv.py)で網羅済みのため、
//    ここではNo空欄(=新規作成)行でエラーモーダルが出ること（=全動線が繋がっていること）を確認する。
const { test } = require('@playwright/test');
const { login, NEW, expect } = require('./helpers');

const IMPORT_HEADERS = 'No,メールアドレス,氏名,ロール(参考),状態(参考),学校承認スキップ(参考),登録日(参考)';

function csvBuffer(lines) {
  return Buffer.from('﻿' + [IMPORT_HEADERS, ...lines].join('\n') + '\n', 'utf-8');
}

test.describe('新システム ユーザー管理 CSV', () => {
  for (const role of [
    { name: '事務(office)', email: 'office1@example.com' },
    { name: '営業(sales)', email: 'sales1@example.com' },
  ]) {
    test(`${role.name}：CSVボタン表示・エクスポート・インポート動線`, async ({ page }) => {
      await login(page, NEW, role.email, '/admin/users');
      await page.waitForSelector('#csvImportBtn', { state: 'visible' });

      // (1) ボタン/リンクの存在
      await expect(page.locator('a[href="/api/w/users/export"]')).toBeVisible();
      await expect(page.locator('#csvImportBtn')).toBeVisible();

      // (2) エクスポート（ページのセッションCookieで取得）
      const res = await page.request.get(`${NEW}/api/w/users/export`);
      expect(res.status()).toBe(200);
      expect(res.headers()['content-type']).toContain('text/csv');
      const csv = (await res.text()).replace(/^﻿/, '');
      const lines = csv.split(/\r?\n/).filter(Boolean);
      expect(lines[0].split(',').slice(0, 3)).toEqual(['No', 'メールアドレス', '氏名']);
      // 自分自身（new所属ユーザー）がエクスポートに含まれる
      expect(csv).toContain(role.email);

      // (3) インポートのブラウザ動線：No空欄(=新規作成)行はエラーモーダルになる（フェーズ①）
      await page.setInputFiles('#csvFileInput', {
        name: 'users.csv',
        mimeType: 'text/csv',
        buffer: csvBuffer([',new@e2e.example.com,新規ユーザー,,,,']),
      });
      await expect(page.locator('#importOverlay')).toBeVisible();
      await expect(page.locator('#importResultBody')).toContainText('No');
    });
  }
});
