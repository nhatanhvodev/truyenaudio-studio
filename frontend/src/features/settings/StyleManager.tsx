import { useCallback, useEffect, useState } from 'react';

import { apiJson } from '../../shared/api';
import { Button } from '../../shared/ui';

import styles from './StyleManager.module.css';

export interface StylePresetView {
  key: string;
  name: string;
  genre: string;
  tone: string;
  user_instruction: string;
}

export interface StyleView {
  id: string;
  revision_no: number;
  name: string;
  genre: string;
  tone: string;
  source_language: string;
  target_language: string;
  user_instruction: string;
  sha256: string;
}

export interface StyleManagerProps {
  projectId: string;
}

export function StyleManager({ projectId }: StyleManagerProps) {
  const [styleRows, setStyleRows] = useState<StyleView[]>([]);
  const [presets, setPresets] = useState<StylePresetView[]>([]);
  const [presetKey, setPresetKey] = useState('');
  const [draft, setDraft] = useState({ name: '', genre: 'general', tone: 'natural', userInstruction: '' });
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [stylePayload, presetPayload] = await Promise.all([
        apiJson<{ styles: StyleView[] }>(`/api/projects/${projectId}/styles`),
        apiJson<{ presets: StylePresetView[] }>(`/api/projects/${projectId}/styles/presets`),
      ]);
      setStyleRows(stylePayload.styles ?? []);
      setPresets(presetPayload.presets ?? []);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Không tải được style');
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  function applyPreset(key: string) {
    setPresetKey(key);
    const preset = presets.find((item) => item.key === key);
    if (!preset) {
      return;
    }
    setDraft({
      name: preset.name,
      genre: preset.genre,
      tone: preset.tone,
      userInstruction: preset.user_instruction,
    });
  }

  async function save() {
    if (!draft.name.trim()) {
      setError('Cần đặt tên style trước khi lưu.');
      return;
    }
    setSaving(true);
    setError('');
    setStatus('');
    try {
      const payload = await apiJson<{ style: StyleView; sha256: string }>(
        `/api/projects/${projectId}/styles`,
        {
          method: 'POST',
          body: JSON.stringify({
            name: draft.name.trim(),
            genre: draft.genre,
            tone: draft.tone,
            source_language: 'zh-CN',
            target_language: 'vi-VN',
            user_instruction: draft.userInstruction,
          }),
        },
      );
      setStatus(
        `Đã lưu "${payload.style.name}" revision ${payload.style.revision_no} (hash ${payload.sha256.slice(0, 8)}…).`,
      );
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Không lưu được style');
    } finally {
      setSaving(false);
    }
  }

  return (
    <section aria-labelledby="style-manager-heading" className={styles.shell}>
      <h2 id="style-manager-heading" className={styles.heading}>
        Translation style
      </h2>
      <p className={styles.note}>
        Preset là cấu hình có phiên bản, không phải nhãn chất lượng. Sửa preset cho project sẽ tạo revision mới.
      </p>

      {error ? (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      ) : null}
      {status ? (
        <p role="status" className={styles.success}>
          {status}
        </p>
      ) : null}

      <label className={styles.field}>
        Preset có sẵn
        <select
          className={styles.control}
          value={presetKey}
          onChange={(event) => applyPreset(event.target.value)}
        >
          <option value="">— chọn preset —</option>
          {presets.map((preset) => (
            <option key={preset.key} value={preset.key}>
              {preset.name} ({preset.genre}/{preset.tone})
            </option>
          ))}
        </select>
      </label>

      <div className={styles.row}>
        <label className={styles.field}>
          Tên style
          <input
            className={styles.control}
            value={draft.name}
            onChange={(event) => setDraft({ ...draft, name: event.target.value })}
          />
        </label>
        <label className={styles.field}>
          Thể loại
          <select
            className={styles.control}
            value={draft.genre}
            onChange={(event) => setDraft({ ...draft, genre: event.target.value })}
          >
            <option value="general">general</option>
            <option value="webnovel">webnovel</option>
            <option value="lightnovel">lightnovel</option>
            <option value="ancient">ancient</option>
            <option value="modern">modern</option>
            <option value="fantasy">fantasy</option>
            <option value="wuxia">wuxia</option>
            <option value="xianxia">xianxia</option>
          </select>
        </label>
        <label className={styles.field}>
          Tone
          <select
            className={styles.control}
            value={draft.tone}
            onChange={(event) => setDraft({ ...draft, tone: event.target.value })}
          >
            <option value="faithful">faithful</option>
            <option value="natural">natural</option>
            <option value="literary">literary</option>
          </select>
        </label>
      </div>
      <label className={`${styles.field} ${styles.wideField}`}>
        Custom instruction (gửi kèm prompt; không override glossary khóa)
        <textarea
          className={styles.textarea}
          value={draft.userInstruction}
          onChange={(event) => setDraft({ ...draft, userInstruction: event.target.value })}
          rows={3}
        />
      </label>
      <div className={styles.actions}>
        <Button variant="primary" disabled={saving} onClick={() => void save()}>
          {saving ? 'Đang lưu…' : 'Lưu style'}
        </Button>
      </div>

      <h3 className={styles.subheading}>Style đang dùng</h3>
      {loading ? <p className={styles.meta}>Đang tải…</p> : null}
      {!loading && styleRows.length === 0 ? <p className={styles.meta}>Chưa có style nào cho project này.</p> : null}
      {styleRows.length > 0 ? (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <caption className={styles.caption}>Style active theo revision</caption>
            <thead>
              <tr>
                <th scope="col" className={styles.th}>Tên</th>
                <th scope="col" className={styles.th}>Thể loại</th>
                <th scope="col" className={styles.th}>Tone</th>
                <th scope="col" className={styles.th}>Revision</th>
                <th scope="col" className={styles.th}>Hash</th>
              </tr>
            </thead>
            <tbody>
              {styleRows.map((style) => (
                <tr key={style.id}>
                  <td className={styles.td}>{style.name}</td>
                  <td className={styles.td}>{style.genre}</td>
                  <td className={styles.td}>{style.tone}</td>
                  <td className={styles.td}>{style.revision_no}</td>
                  <td className={styles.td}>{style.sha256.slice(0, 8)}…</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
