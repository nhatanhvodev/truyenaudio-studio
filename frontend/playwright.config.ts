import { defineConfig } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontendRoot = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(frontendRoot, '..');

export default defineConfig({
  testDir: './e2e',
  use: {
    baseURL: 'http://127.0.0.1:8765',
    trace: 'retain-on-failure',
  },
  webServer: {
    command:
      'powershell -NoProfile -ExecutionPolicy Bypass -File scripts\\e2e-server.ps1',
    cwd: repoRoot,
    url: 'http://127.0.0.1:8765/',
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
