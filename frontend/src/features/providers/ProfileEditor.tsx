import { useCallback, useEffect, useState } from 'react';

import { apiJson } from '../../shared/api';

export interface ProviderProfileView {
  id: string;
  providerKind: string;
  adapterName: string;
  displayName: string;
  model: string | null;
  region: string | null;
  revision: number;
  secretConfigured: boolean;
  config: Record<string, unknown>;
  enabled: boolean;
  status: string;
}

interface ProfilesResponse {
  profiles: ProviderProfileView[];
}

const KIND_OPTIONS = ['TRANSLATOR', 'TTS', 'REVIEWER'] as const;

export function ProfileEditor() {
  const [profiles, setProfiles] = useState<ProviderProfileView[]>([]);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState({
    providerKind: 'TRANSLATOR',
    adapterName: 'qwen-mt',
    displayName: '',
    model: '',
    region: '',
  });

  const refresh = useCallback(async () => {
    try {
      const payload = await apiJson<ProfilesResponse>('/api/cloud-profiles');
      setProfiles(payload.profiles ?? []);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Không tải được profile');
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function createProfile() {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await apiJson('/api/cloud-profiles', {
        method: 'POST',
        body: JSON.stringify({
          providerKind: draft.providerKind,
          adapterName: draft.adapterName.trim(),
          displayName: draft.displayName.trim(),
          model: draft.model.trim() || null,
          region: draft.region.trim() || null,
        }),
      });
      setNotice('Đã tạo profile. Nhập credential để profile sẵn sàng.');
      setDraft({ ...draft, displayName: '', model: '', region: '' });
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Không tạo được profile');
    } finally {
      setBusy(false);
    }
  }

  async function saveSecret(profileId: string) {
    const secret = secrets[profileId] ?? '';
    if (!secret.trim()) {
      setError('Cần nhập credential trước khi lưu.');
      return;
    }
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await apiJson(`/api/cloud-profiles/${profileId}/credential`, {
        method: 'PUT',
        body: JSON.stringify({ secret }),
      });
      // The secret only ever lived in this request body; drop it immediately.
      setSecrets((previous) => ({ ...previous, [profileId]: '' }));
      setNotice('Đã lưu credential vào keyring hệ thống (không lưu trong trình duyệt).');
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Không lưu được credential');
    } finally {
      setBusy(false);
    }
  }

  async function deleteSecret(profileId: string) {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await apiJson(`/api/cloud-profiles/${profileId}/credential`, { method: 'DELETE' });
      setNotice('Đã xoá credential khỏi keyring.');
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Không xoá được credential');
    } finally {
      setBusy(false);
    }
  }

  async function validate(profileId: string) {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const payload = await apiJson<{ status?: string; detail?: string }>(
        `/api/cloud-profiles/${profileId}/validate`,
        { method: 'POST' },
      );
      setNotice(`Kiểm tra credential: ${payload.status ?? 'không rõ'}${payload.detail ? ` — ${payload.detail}` : ''}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Không kiểm tra được credential');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="provider-profile-heading">
      <h2 id="provider-profile-heading" style={{ margin: '0 0 8px', fontSize: 15 }}>
        AI Providers
      </h2>
      {error ? (
        <p role="alert" style={{ color: '#b91c1c' }}>
          {error}
        </p>
      ) : null}
      {notice ? (
        <p role="status" style={{ color: '#166534' }}>
          {notice}
        </p>
      ) : null}

      <table>
        <caption>Profile hiện có (credential chỉ hiển thị trạng thái)</caption>
        <thead>
          <tr>
            <th scope="col">Profile</th>
            <th scope="col">Adapter</th>
            <th scope="col">Model</th>
            <th scope="col">Credential</th>
            <th scope="col">Trạng thái</th>
            <th scope="col">Thao tác</th>
          </tr>
        </thead>
        <tbody>
          {profiles.map((profile) => (
            <tr key={profile.id}>
              <td>{profile.displayName}</td>
              <td>{profile.adapterName}</td>
              <td>{profile.model ?? '—'}</td>
              <td>{profile.secretConfigured ? 'Đã cấu hình' : 'Chưa có'}</td>
              <td>{profile.status}</td>
              <td>
                <label>
                  <span className="visually-hidden">Credential cho {profile.displayName}</span>
                  <input
                    type="password"
                    autoComplete="off"
                    aria-label={`Credential cho ${profile.displayName}`}
                    value={secrets[profile.id] ?? ''}
                    onChange={(event) =>
                      setSecrets((previous) => ({ ...previous, [profile.id]: event.target.value }))
                    }
                  />
                </label>
                <button type="button" disabled={busy} onClick={() => void saveSecret(profile.id)}>
                  Lưu key
                </button>
                <button type="button" disabled={busy} onClick={() => void deleteSecret(profile.id)}>
                  Xoá key
                </button>
                <button type="button" disabled={busy} onClick={() => void validate(profile.id)}>
                  Kiểm tra
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {profiles.length === 0 ? <p>Chưa có profile nào.</p> : null}

      <h3 style={{ fontSize: 14, marginBottom: 4 }}>Thêm profile</h3>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <label>
          Loại
          <select
            value={draft.providerKind}
            onChange={(event) => setDraft({ ...draft, providerKind: event.target.value })}
          >
            {KIND_OPTIONS.map((kind) => (
              <option key={kind} value={kind}>
                {kind}
              </option>
            ))}
          </select>
        </label>
        <label>
          Adapter
          <input
            value={draft.adapterName}
            onChange={(event) => setDraft({ ...draft, adapterName: event.target.value })}
          />
        </label>
        <label>
          Tên hiển thị
          <input
            value={draft.displayName}
            onChange={(event) => setDraft({ ...draft, displayName: event.target.value })}
          />
        </label>
        <label>
          Model
          <input value={draft.model} onChange={(event) => setDraft({ ...draft, model: event.target.value })} />
        </label>
        <label>
          Region
          <input value={draft.region} onChange={(event) => setDraft({ ...draft, region: event.target.value })} />
        </label>
        <button
          type="button"
          disabled={busy || !draft.displayName.trim() || !draft.adapterName.trim()}
          onClick={() => void createProfile()}
        >
          Tạo profile
        </button>
      </div>
    </section>
  );
}
