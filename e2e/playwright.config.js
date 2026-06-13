// @ts-check
const { defineConfig, devices } = require('@playwright/test');

// 既存システム=http://localhost:8000、新システム=http://localhost:8001。
// baseURL はテストごとに page.goto で絶対URLを使うため設定しない。
module.exports = defineConfig({
  testDir: './tests',
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    headless: true,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
});
