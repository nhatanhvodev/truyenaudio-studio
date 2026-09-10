import { defineConfig } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontendRoot = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(frontendRoot, '..');

/**
 * The repo does not ship / install Playwright browser binaries (`ms-playwright`
 * cache is absent on dev machines). Instead the E2E suite drives a system
 * browser. The docs said "no browser binary => E2E NOT_RUN"; that is no longer
 * true: system Chrome/Edge are installed and launch fine (probe verified
 * 2026-09-10), so `channel: 'chrome'` is the default and the suite is runnable
 * everywhere without a separate config flag.
 */
export default defineConfig({
  testDir: './e2e',
  workers: 1,
  use: {
    baseURL: 'http://127.0.0.1:8765',
    trace: 'retain-on-failure',
    channel: 'chrome',
  },
  webServer: {
    command:
      'powershell -NoProfile -ExecutionPolicy Bypass -File scripts\\e2e-server.ps1',
    cwd: repoRoot,
    url: 'http://127.0.0.1:8765/',
    reuseExistingServer: false,
    timeout: 120_000,
  },
});