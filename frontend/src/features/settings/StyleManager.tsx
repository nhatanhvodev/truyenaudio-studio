import { useCallback, useEffect, useState } from 'react';

import { apiJson } from '../../shared/api';

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
  const [styles, setStyles] = useState<StyleView[]>([]);
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
      setStyles(stylePayload.styles ?? []);
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
    <section aria-labelledby="style-manager-heading">
      <h2 id="style-manager-heading" style={{ margin: '0 0 8px', fontSize: 15 }}>
        Translation style
      </h2>
      <p style={{ margin: '0 0 8px', color: '#4b5563' }}>
        Preset là cấu hình có phiên bản, không phải nhãn chất lượng. Sửa preset cho project sẽ tạo revision mới.
      </p>

      {error ? (
        <p role="alert" style={{ color: '#b91c1c' }}>
          {error}
        </p>
      ) : null}
      {status ? (
        <p role="status" style={{ color: '#166534' }}>
          {status}
        </p>
      ) : null}

      <label>
        Preset có sẵn
        <select value={presetKey} onChange={(event) => applyPreset(event.target.value)}>
          <option value="">— chọn preset —</option>
          {presets.map((preset) => (
            <option key={preset.key} value={preset.key}>
              {preset.name} ({preset.genre}/{preset.tone})
            </option>
          ))}
        </select>
      </label>

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
        <label>
          Tên style
          <input value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
        </label>
        <label>
          Thể loại
          <select value={draft.genre} onChange={(event) => setDraft({ ...draft, genre: event.target.value })}>
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
        <label>
          Tone
          <select value={draft.tone} onChange={(event) => setDraft({ ...draft, tone: event.target.value })}>
            <option value="faithful">faithful</option>
            <option value="natural">natural</option>
            <option value="literary">literary</option>
          </select>
        </label>
      </div>
      <label style={{ display: 'block', marginTop: 8 }}>
        Custom instruction (gửi kèm prompt; không override glossary khóa)
        <textarea
          value={draft.userInstruction}
          onChange={(event) => setDraft({ ...draft, userInstruction: event.target.value })}
          rows={3}
          style={{ width: '100%' }}
        />
      </label>
      <button type="button" disabled={saving} onClick={() => void save()}>
        {saving ? 'Đang lưu…' : 'Lưu style'}
      </button>

      <h3 style={{ fontSize: 14, margin: '16px 0 4px' }}>Style đang dùng</h3>
      {loading ? <p>Đang tải…</p> : null}
      {!loading && styles.length === 0 ? <p>Chưa có style nào cho project này.</p> : null}
      {styles.length > 0 ? (
        <table>
          <caption>Style active theo revision</caption>
          <thead>
            <tr>
              <th scope="col">Tên</th>
              <th scope="col">Thể loại</th>
              <th scope="col">Tone</th>
              <th scope="col">Revision</th>
              <th scope="col">Hash</th>
            </tr>
          </thead>
          <tbody>
            {styles.map((style) => (
              <tr key={style.id}>
                <td>{style.name}</td>
                <td>{style.genre}</td>
                <td>{style.tone}</td>
                <td>{style.revision_no}</td>
                <td>{style.sha256.slice(0, 8)}…</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </section>
  );
}
