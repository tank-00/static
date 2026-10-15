import { existsSync } from 'node:fs'
import { defineConfig, devices } from '@playwright/test'

// Use the preinstalled Chromium in cloud sessions when available (avoids `playwright install`).
const preinstalled = process.env.PLAYWRIGHT_CHROMIUM_PATH ?? '/opt/pw-browsers/chromium'
const executablePath = existsSync(preinstalled) ? preinstalled : undefined

const baseURL = process.env.E2E_BASE_URL ?? 'http://127.0.0.1:8765'

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  outputDir: 'test-results',
  use: {
    baseURL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    launchOptions: executablePath ? { executablePath } : {},
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
