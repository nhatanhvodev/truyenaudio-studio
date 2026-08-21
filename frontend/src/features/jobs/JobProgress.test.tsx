import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { JobProgress } from './JobProgress';

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((message: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn();

  constructor(public readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  emit(payload: unknown) {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent);
  }
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  FakeEventSource.instances = [];
});

describe('JobProgress', () => {
  it('loads a snapshot once and keeps a stable EventSource after events', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url === '/api/jobs/snapshot') {
          return jsonResponse({
            events: [
              {
                sequenceId: '001',
                jobId: 'job-1',
                status: 'RUNNING',
                current: 1,
                total: 3,
                errorCode: null,
              },
            ],
          });
        }
        throw new Error(`unexpected url ${url}`);
      }),
    );
    vi.stubGlobal('EventSource', FakeEventSource);

    render(<JobProgress />);

    expect(await screen.findByText('job-1')).toBeVisible();
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    expect(FakeEventSource.instances[0].url).toBe('/api/jobs/events?after=001');

    act(() => {
      FakeEventSource.instances[0].emit({
        sequenceId: '002',
        jobId: 'job-2',
        status: 'SUCCEEDED',
        current: 3,
        total: 3,
        errorCode: null,
      });
    });

    expect(await screen.findByText('job-2')).toBeVisible();
    expect(FakeEventSource.instances).toHaveLength(1);
  });
});

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    text: async () => JSON.stringify(body),
    json: async () => body,
  } as Response;
}
