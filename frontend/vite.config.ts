import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    exclude: ['node_modules/**', 'dist/**', 'e2e/**'],
    // Vitest's 5000ms default is measured against an IDLE machine; this suite
    // runs its files in parallel, so every duration is inflated by CPU
    // contention. The worst case is App.test.tsx's first test, which imports the
    // whole App module graph: 1357ms alone, 2516ms when the full suite runs
    // (verified with --reporter=verbose). That left barely 2x headroom against a
    // 5s budget, so on a busier machine it died as "Test timed out in 5000ms"
    // while passing 14/14 on its own - the reported flake.
    //
    // Reproduced rather than guessed: with six busy-loop node processes running,
    // the full suite failed at the 5s budget on exactly that test, and passed
    // 347/347 with the same load under this one.
    //
    // Sized at the SUITE level on purpose, not with vi.setConfig in that one
    // file: the inflation is a property of running 49 files in parallel, not of
    // App.test.tsx, and JobsList already has a test at 2137ms under the same
    // load. Patching the file that flaked first would leave the identical
    // failure armed in the next one to cross the line.
    //
    // The cost is honest and bounded: a genuinely hung test now fails after 15s
    // instead of 5s. It still fails - this only moves when the alarm goes off.
    testTimeout: 15000,
  },
});
