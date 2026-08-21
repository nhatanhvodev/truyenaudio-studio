import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import VoiceBrowser from './VoiceBrowser';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('VoiceBrowser', () => {
  it('lists local voices and previews every preset with the common text', async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/voices?locale=vi-VN') {
        return jsonResponse({
          previewText: 'Day la doan nghe thu giong doc chung cho moi preset.',
          voices: [
            {
              id: 'vieneu-vi-int8',
              name: 'VieNeu Vietnamese',
              provider: 'vieneu',
              model: 'vieneu-vi-int8',
              locale: 'vi-VN',
              region: 'local',
              gender: 'neutral',
              license: 'verified local model',
              costTier: 'local',
              online: false,
              favorite: true,
              available: true,
              active: true,
              activationHint: 'Ready',
            },
            {
              id: 'piper-vais1000',
              name: 'Piper vais1000',
              provider: 'piper',
              model: 'vais1000',
              locale: 'vi-VN',
              region: 'local',
              gender: 'neutral',
              license: 'CC-BY-4.0',
              costTier: 'local',
              online: false,
              favorite: false,
              available: true,
              active: false,
              activationHint: 'Ready',
            },
          ],
        });
      }
      if (url === '/api/voices/preview') {
        expect(init?.method).toBe('POST');
        expect(JSON.parse(String(init?.body))).toEqual({
          presetId: 'piper-vais1000',
          text: 'Day la doan nghe thu giong doc chung cho moi preset.',
        });
        return jsonResponse({
          id: 'job-1',
          status: 'QUEUED',
          kind: 'PREVIEW_TTS',
          artifactKind: 'VOICE_PREVIEW',
          cacheKey: 'cache-123',
        });
      }
      throw new Error(`unexpected url ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<VoiceBrowser />);

    expect(await screen.findByText('VieNeu Vietnamese')).toBeVisible();
    expect(screen.getByText('Piper vais1000')).toBeVisible();
    expect(screen.getByText('vieneu · vieneu-vi-int8 · vi-VN')).toBeVisible();
    expect(screen.getByText('piper · vais1000 · vi-VN')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Preview Piper vais1000' }));

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Preview queued'));
  });

  it('shows missing model guidance without offering cloud fallback', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse({
          previewText: 'Day la doan nghe thu giong doc chung cho moi preset.',
          voices: [
            {
              id: 'piper-vais1000',
              name: 'Piper vais1000',
              provider: 'piper',
              model: 'vais1000',
              locale: 'vi-VN',
              region: 'local',
              gender: 'neutral',
              license: 'CC-BY-4.0',
              costTier: 'local',
              online: false,
              favorite: false,
              available: false,
              active: false,
              activationHint: 'Install local model snapshot, then verify the model and license.',
            },
          ],
        }),
      ),
    );

    render(<VoiceBrowser />);

    expect(await screen.findByText('Install local model snapshot, then verify the model and license.')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Preview Piper vais1000' })).toBeDisabled();
    expect(screen.queryByText(/cloud/i)).not.toBeInTheDocument();
  });
});

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  } as Response;
}
