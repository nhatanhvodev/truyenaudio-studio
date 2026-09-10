import { useEffect, useState } from 'react';

import {
  DENSITIES,
  FONT_SCALES,
  THEMES,
  defaultPreferences,
  parsePreferences,
  preferencesAreSafe,
  serializePreferences,
  type UiPreferences,
} from './uiPreferences';

const STORAGE_KEY = 'studio.ui-preferences';

/**
 * U10: Appearance settings backed by the harmless UI preference document.
 *
 * The screen only ever stores presentation values: the document is validated
 * (`preferencesAreSafe`) and re-serialized through `serializePreferences` before
 * it is written, so pasted text or a credential can never reach the browser
 * storage through this form. Project data is untouched — it lives on the server.
 */
export function AppearanceSettings() {
  const [preferences, setPreferences] = useState<UiPreferences>(() => defaultPreferences());
  const [rejectedKeys, setRejectedKeys] = useState<string[]>([]);
  const [migrated, setMigrated] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [storedPreview, setStoredPreview] = useState('');

  useEffect(() => {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed = parsePreferences(raw);
    setPreferences(parsed.preferences);
    setRejectedKeys(parsed.rejectedKeys);
    setMigrated(parsed.migrated);
    // Only the sanitized document is ever displayed: a rejected key (which can
    // contain pasted text or a credential) is never echoed into the DOM.
    setStoredPreview(raw === null ? '' : serializePreferences(parsed.preferences));
  }, []);

  function save() {
    setMessage('');
    setError('');
    const raw = serializePreferences(preferences);
    if (!preferencesAreSafe(raw)) {
      setError('UI_PREFERENCE_UNSAFE');
      return;
    }
    window.localStorage.setItem(STORAGE_KEY, raw);
    setStoredPreview(raw);
    setMessage('Đã lưu tùy chọn hiển thị (chỉ gồm theme/mật độ/cỡ chữ).');
  }

  function reset() {
    window.localStorage.removeItem(STORAGE_KEY);
    setPreferences(defaultPreferences());
    setRejectedKeys([]);
    setMigrated(false);
    setStoredPreview('');
    setMessage('Đã đưa tùy chọn hiển thị về mặc định.');
  }

  return (
    <section aria-label="Appearance" style={styles.shell}>
      <h2 style={styles.title}>Appearance</h2>
      <p style={styles.note}>
        Chỉ lưu giá trị hiển thị vô hại trong trình duyệt. Nội dung truyện, style guide, glossary và mọi
        credential luôn nằm ở server/keyring — không bao giờ được lưu qua màn này.
      </p>

      <label style={styles.label}>
        Theme
        <select
          aria-label="Theme"
          value={preferences.theme}
          onChange={(event) => setPreferences({ ...preferences, theme: event.target.value as UiPreferences['theme'] })}
          style={styles.control}
        >
          {THEMES.map((theme) => (
            <option key={theme} value={theme}>
              {theme}
            </option>
          ))}
        </select>
      </label>

      <label style={styles.label}>
        Mật độ hiển thị
        <select
          aria-label="Mật độ hiển thị"
          value={preferences.density}
          onChange={(event) =>
            setPreferences({ ...preferences, density: event.target.value as UiPreferences['density'] })
          }
          style={styles.control}
        >
          {DENSITIES.map((density) => (
            <option key={density} value={density}>
              {density}
            </option>
          ))}
        </select>
      </label>

      <label style={styles.label}>
        Cỡ chữ
        <select
          aria-label="Cỡ chữ"
          value={preferences.fontScale}
          onChange={(event) =>
            setPreferences({ ...preferences, fontScale: event.target.value as UiPreferences['fontScale'] })
          }
          style={styles.control}
        >
          {FONT_SCALES.map((scale) => (
            <option key={scale} value={scale}>
              {scale}
            </option>
          ))}
        </select>
      </label>

      <label style={styles.inline}>
        <input
          type="checkbox"
          aria-label="Giảm chuyển động"
          checked={preferences.reduceMotion}
          onChange={(event) => setPreferences({ ...preferences, reduceMotion: event.target.checked })}
        />
        Giảm chuyển động
      </label>

      <div style={styles.actions}>
        <button type="button" onClick={save} style={styles.primary}>
          Lưu tùy chọn hiển thị
        </button>
        <button type="button" onClick={reset} style={styles.secondary}>
          Về mặc định
        </button>
      </div>

      {message ? <p role="status" style={styles.success}>{message}</p> : null}
      {error ? <p role="alert" style={styles.error}>{error}</p> : null}
      {rejectedKeys.length > 0 ? (
        <p role="status" style={styles.warning}>
          Đã bỏ các khóa không hợp lệ khi khôi phục: {rejectedKeys.join(', ')}.
        </p>
      ) : null}
      {migrated ? <p role="status" style={styles.warning}>Tùy chọn cũ đã được chuyển sang phiên bản hiện tại.</p> : null}

      <details style={styles.details}>
        <summary style={styles.summary}>Giá trị sẽ lưu trong trình duyệt (đã lọc)</summary>
        <pre aria-label="Giá trị đang lưu" style={styles.pre}>
          {storedPreview || '(trống)'}
        </pre>
      </details>
    </section>
  );
}

const styles: Record<string, React.CSSProperties> = {
  shell: { display: 'grid', gap: 10, maxWidth: 560 },
  title: { margin: 0, fontSize: 15 },
  note: { margin: 0, color: '#4b5563', fontSize: 13, lineHeight: 1.5 },
  label: { display: 'grid', gap: 6, fontWeight: 700, fontSize: 13 },
  control: { padding: '8px 10px', border: '1px solid #c8d1dc', borderRadius: 6, background: '#ffffff' },
  inline: { display: 'flex', alignItems: 'center', gap: 8, fontWeight: 700, fontSize: 13 },
  actions: { display: 'flex', gap: 8, flexWrap: 'wrap' },
  primary: { padding: '8px 14px', border: 0, borderRadius: 6, background: '#155eef', color: '#ffffff', fontWeight: 700 },
  secondary: { padding: '8px 14px', border: '1px solid #c8d1dc', borderRadius: 6, background: '#ffffff', fontWeight: 700 },
  success: { margin: 0, color: '#166534', fontWeight: 700 },
  error: { margin: 0, color: '#9a3412', fontWeight: 700 },
  warning: { margin: 0, color: '#92400e', fontWeight: 700, fontSize: 13 },
  details: { fontSize: 13 },
  summary: { cursor: 'pointer', fontWeight: 700 },
  pre: { margin: '8px 0 0', padding: 10, background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: 6, overflowX: 'auto' },
};
