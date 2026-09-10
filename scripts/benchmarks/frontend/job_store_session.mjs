/**
 * V02 — driver Node replay trace frame SSE thật vào CHÍNH store của frontend (U07).
 *
 * Input: file JSON { frames: [{ sequenceId, jobId, status, current, total, tMs }] } do
 * fixture SSE30MIN ghi lại từ route `/api/jobs/events` + `/api/jobs/snapshot` thật.
 * Driver bơm đúng các frame đó vào `frontend/src/features/jobs/jobStore.ts` (module
 * thật) và báo lại: sức chứa store, cờ truncated, frame/s/job lớn nhất và heap sau GC.
 *
 * Chạy bằng `node --expose-gc` để đo heap sau GC; heap của Node KHÔNG phải heap
 * browser — con số này chỉ là bằng chứng bổ sung cho bounded store.
 */

import { register } from 'node:module';
import { readFileSync } from 'node:fs';

register('./ts-resolve-hooks.mjs', import.meta.url);

const { JobEventStore, MAX_EVENTS } = await import('../../../frontend/src/features/jobs/jobStore.ts');

const inputPath = process.argv[2];
if (!inputPath) {
  console.error('usage: node job_store_session.mjs <frames.json>');
  process.exit(2);
}
const payload = JSON.parse(readFileSync(inputPath, 'utf8'));
const capturedFrames = Array.isArray(payload.frames) ? payload.frames : [];
const repeat = Math.max(1, Math.floor(Number(payload.repeat ?? 1)));
// Trace thật của phiên đo được lặp lại `repeat` lần (kèm dịch sequenceId) để mô phỏng
// KHỐI LƯỢNG frame của phiên 30 phút khi phiên thật bị rút ngắn — store thật phải vẫn bounded.
const frames = [];
for (let pass = 0; pass < repeat; pass += 1) {
  for (const frame of capturedFrames) {
    frames.push({
      ...frame,
      sequenceId: Number(frame.sequenceId ?? 0) + pass * 1_000_000,
      tMs: Number(frame.tMs ?? 0) + pass * 3_600_000,
    });
  }
}

function heapMb() {
  if (typeof global.gc === 'function') {
    global.gc();
  }
  return process.memoryUsage().heapUsed / (1024 * 1024);
}

const listeners = [];
const scheduled = [];
const store = new JobEventStore({
  fetchSnapshot: async () => [],
  openStream: () => ({
    addEventListener: (name, callback) => listeners.push({ name, callback }),
    close: () => {},
    onopen: null,
    onerror: null,
  }),
  schedule: (callback) => {
    scheduled.push(callback);
    return scheduled.length;
  },
  cancelScheduled: () => {},
});

const snapshotSizes = [];
let emitted = 0;
const unsubscribe = store.subscribe((snapshot) => snapshotSizes.push(snapshot.events.length));
await new Promise((resolve) => setTimeout(resolve, 0));
const streamListener = listeners.find((entry) => entry.name === 'job')?.callback;

const heapBeforeMb = heapMb();
const perJobTimes = new Map();
for (const frame of frames) {
  streamListener?.({ data: JSON.stringify(frame) });
  emitted += 1;
  for (const jobId of new Set([frame.jobId])) {
    const times = perJobTimes.get(jobId) ?? [];
    times.push(Number(frame.tMs ?? 0));
    perJobTimes.set(jobId, times);
  }
}
const snapshot = store.snapshot();
const heapAfterMb = heapMb();
unsubscribe();

// Frame/s/job: số frame lớn nhất trong một cửa sổ 1 giây cho cùng một job.
let maxPerJobPerSecond = 0;
let windowSeconds = 0;
const first = frames[0]?.tMs ?? 0;
const last = frames[frames.length - 1]?.tMs ?? 0;
windowSeconds = Math.max(0, (last - first) / 1000);
for (const times of perJobTimes.values()) {
  const ordered = [...times].sort((left, right) => left - right);
  let start = 0;
  for (let index = 0; index < ordered.length; index += 1) {
    while (ordered[index] - ordered[start] > 1000) {
      start += 1;
    }
    maxPerJobPerSecond = Math.max(maxPerJobPerSecond, index - start + 1);
  }
}

console.log(
  JSON.stringify({
    nodeVersion: process.version,
    maxEvents: MAX_EVENTS,
    framesReplayed: emitted,
    capturedFrames: capturedFrames.length,
    repeat,
    storedEventsAfterReplay: snapshot.events.length,
    maxStoredDuringReplay: Math.max(0, ...snapshotSizes),
    truncated: snapshot.truncated,
    cursor: snapshot.cursor,
    distinctJobs: perJobTimes.size,
    maxFramesPerJobPerSecond: maxPerJobPerSecond,
    capturedWindowSeconds: Number(windowSeconds.toFixed(3)),
    capturedFramesPerSecond: windowSeconds > 0 ? Number((capturedFrames.length / windowSeconds).toFixed(3)) : 0,
    heapBeforeMb: Number(heapBeforeMb.toFixed(2)),
    heapAfterMb: Number(heapAfterMb.toFixed(2)),
    heapDeltaMb: Number((heapAfterMb - heapBeforeMb).toFixed(2)),
    gcAvailable: typeof global.gc === 'function',
  }),
);
