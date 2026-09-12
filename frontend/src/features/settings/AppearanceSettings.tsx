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
import { notifyPreferencesChanged, UI_PREFERENCES_STORAGE_KEY } from './themeRuntime';

import { Button } from '../../shared/ui';

import styles from './AppearanceSettings.module.css';

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
    const raw = window.localStorage.getItem(UI_PREFERENCES_STORAGE_KEY);
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
    window.localStorage.setItem(UI_PREFERENCES_STORAGE_KEY, raw);
    setStoredPreview(raw);
    setMessage('Đã lưu tùy chọn hiển thị (chỉ gồm theme/mật độ/cỡ chữ).');
    notifyPreferencesChanged();
  }

  function reset() {
    window.localStorage.removeItem(UI_PREFERENCES_STORAGE_KEY);
    setPreferences(defaultPreferences());
    setRejectedKeys([]);
    setMigrated(false);
    setStoredPreview('');
    setMessage('Đã đưa tùy chọn hiển thị về mặc định.');
    notifyPreferencesChanged();
  }

  return (
    <section aria-label="Appearance" className={styles.shell}>
      <h2 className={styles.title}>Appearance</h2>
      <p className={styles.note}>
        Chỉ lưu giá trị hiển thị vô hại trong trình duyệt. Nội dung truyện, style guide, glossary và mọi
        credential luôn nằm ở server/keyring — không bao giờ được lưu qua màn này.
      </p>

      <label className={styles.field}>
        Theme
        <select
          aria-label="Theme"
          value={preferences.theme}
          onChange={(event) => setPreferences({ ...preferences, theme: event.target.value as UiPreferences['theme'] })}
          className={styles.control}
        >
          {THEMES.map((theme) => (
            <option key={theme} value={theme}>
              {theme}
            </option>
          ))}
        </select>
      </label>

      <label className={styles.field}>
        Mật độ hiển thị
        <select
          aria-label="Mật độ hiển thị"
          value={preferences.density}
          onChange={(event) =>
            setPreferences({ ...preferences, density: event.target.value as UiPreferences['density'] })
          }
          className={styles.control}
        >
          {DENSITIES.map((density) => (
            <option key={density} value={density}>
              {density}
            </option>
          ))}
        </select>
      </label>

      <label className={styles.field}>
        Cỡ chữ
        <select
          aria-label="Cỡ chữ"
          value={preferences.fontScale}
          onChange={(event) =>
            setPreferences({ ...preferences, fontScale: event.target.value as UiPreferences['fontScale'] })
          }
          className={styles.control}
        >
          {FONT_SCALES.map((scale) => (
            <option key={scale} value={scale}>
              {scale}
            </option>
          ))}
        </select>
      </label>

      <label className={styles.inline}>
        <input
          type="checkbox"
          aria-label="Giảm chuyển động"
          checked={preferences.reduceMotion}
          onChange={(event) => setPreferences({ ...preferences, reduceMotion: event.target.checked })}
        />
        Giảm chuyển động
      </label>

      <div className={styles.actions}>
        <Button variant="primary" onClick={save}>
          Lưu tùy chọn hiển thị
        </Button>
        <Button variant="secondary" onClick={reset}>
          Về mặc định
        </Button>
      </div>

      {message ? <p role="status" className={styles.success}>{message}</p> : null}
      {error ? <p role="alert" className={styles.error}>{error}</p> : null}
      {rejectedKeys.length > 0 ? (
        <p role="status" className={styles.warning}>
          Đã bỏ các khóa không hợp lệ khi khôi phục: {rejectedKeys.length} khóa.
        </p>
      ) : null}
      {migrated ? <p role="status" className={styles.warning}>Tùy chọn cũ đã được chuyển sang phiên bản hiện tại.</p> : null}

      <details className={styles.details}>
        <summary className={styles.summary}>Giá trị sẽ lưu trong trình duyệt (đã lọc)</summary>
        <pre aria-label="Giá trị đang lưu" className={styles.pre}>
          {storedPreview || '(trống)'}
        </pre>
      </details>
    </section>
  );
}
