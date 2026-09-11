import { useEffect, type ReactNode } from 'react';

import { applyPreferences, readStoredPreferences, subscribePreferences } from './themeRuntime';

/**
 * Owns every LATER change. The boot script in index.html already resolved
 * data-theme before this bundle ran, so the provider never resolves a second
 * time — it re-applies the stored document and then reacts to changes.
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  useEffect(() => {
    // Idempotent at startup: the boot script wrote these from the raw
    // localStorage value. parsePreferences sanitizes that value, so this is
    // also what repairs a malformed or tampered stored document.
    applyPreferences(readStoredPreferences());
    return subscribePreferences((preferences) => {
      applyPreferences(preferences);
    });
  }, []);

  // System colour-scheme changes while the preference is "system".
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const query = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = () => applyPreferences(readStoredPreferences());
    query.addEventListener('change', onChange);
    return () => query.removeEventListener('change', onChange);
  }, []);

  return <>{children}</>;
}
