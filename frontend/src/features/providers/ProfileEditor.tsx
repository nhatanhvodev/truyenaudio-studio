import { useCallback, useEffect, useState } from 'react';

import { apiJson } from '../../shared/api';
import { Button } from '../../shared/ui';

import styles from './ProfileEditor.module.css';

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
    <section aria-labelledby="provider-profile-heading" className={styles.shell}>
      <h2 id="provider-profile-heading" className={styles.heading}>
        AI Providers
      </h2>
      {error ? (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      ) : null}
      {notice ? (
        <p role="status" className={styles.success}>
          {notice}
        </p>
      ) : null}

      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <caption className={styles.caption}>Profile hiện có (credential chỉ hiển thị trạng thái)</caption>
          <thead>
            <tr>
              <th scope="col" className={styles.th}>Profile</th>
              <th scope="col" className={styles.th}>Adapter</th>
              <th scope="col" className={styles.th}>Model</th>
              <th scope="col" className={styles.th}>Credential</th>
              <th scope="col" className={styles.th}>Trạng thái</th>
              <th scope="col" className={styles.th}>Thao tác</th>
            </tr>
          </thead>
          <tbody>
            {profiles.map((profile) => (
              <tr key={profile.id}>
                <td className={styles.td}>{profile.displayName}</td>
                <td className={styles.td}>{profile.adapterName}</td>
                <td className={styles.td}>{profile.model ?? '—'}</td>
                <td className={styles.td}>{profile.secretConfigured ? 'Đã cấu hình' : 'Chưa có'}</td>
                <td className={styles.td}>{profile.status}</td>
                <td className={styles.td}>
                  <div className={styles.cellActions}>
                    <label>
                      <span className="visually-hidden">Credential cho {profile.displayName}</span>
                      <input
                        className={styles.control}
                        type="password"
                        autoComplete="off"
                        aria-label={`Credential cho ${profile.displayName}`}
                        value={secrets[profile.id] ?? ''}
                        onChange={(event) =>
                          setSecrets((previous) => ({ ...previous, [profile.id]: event.target.value }))
                        }
                      />
                    </label>
                    <Button variant="secondary" disabled={busy} onClick={() => void saveSecret(profile.id)}>
                      Lưu key
                    </Button>
                    <Button variant="secondary" disabled={busy} onClick={() => void deleteSecret(profile.id)}>
                      Xoá key
                    </Button>
                    <Button variant="secondary" disabled={busy} onClick={() => void validate(profile.id)}>
                      Kiểm tra
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {profiles.length === 0 ? <p className={styles.meta}>Chưa có profile nào.</p> : null}

      <h3 className={styles.subheading}>Thêm profile</h3>
      <div className={styles.row}>
        <label className={styles.field}>
          Loại
          <select
            className={styles.control}
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
        <label className={styles.field}>
          Adapter
          <input
            className={styles.control}
            value={draft.adapterName}
            onChange={(event) => setDraft({ ...draft, adapterName: event.target.value })}
          />
        </label>
        <label className={styles.field}>
          Tên hiển thị
          <input
            className={styles.control}
            value={draft.displayName}
            onChange={(event) => setDraft({ ...draft, displayName: event.target.value })}
          />
        </label>
        <label className={styles.field}>
          Model
          <input
            className={styles.control}
            value={draft.model}
            onChange={(event) => setDraft({ ...draft, model: event.target.value })}
          />
        </label>
        <label className={styles.field}>
          Region
          <input
            className={styles.control}
            value={draft.region}
            onChange={(event) => setDraft({ ...draft, region: event.target.value })}
          />
        </label>
        <div className={styles.actions}>
          <Button
            variant="primary"
            disabled={busy || !draft.displayName.trim() || !draft.adapterName.trim()}
            onClick={() => void createProfile()}
          >
            Tạo profile
          </Button>
        </div>
      </div>
    </section>
  );
}
