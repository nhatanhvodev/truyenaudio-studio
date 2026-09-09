import { useEffect, useRef, type ReactNode } from 'react';

import { colors, font, radius, spacing, type UiTheme } from './tokens';

export interface DrawerProps {
  open: boolean;
  title: string;
  onClose: () => void;
  theme?: UiTheme;
  children: ReactNode;
}

export function Drawer({ open, title, onClose, theme = 'light', children }: DrawerProps) {
  const c = colors[theme];
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    const previousActive = document.activeElement as HTMLElement | null;
    panelRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      previousActive?.focus();
    };
  }, [open, onClose]);

  if (!open) {
    return null;
  }

  return (
    <div
      role="presentation"
      style={{ position: 'fixed', inset: 0, display: 'flex', justifyContent: 'flex-end', background: 'rgba(0,0,0,0.45)' }}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          onClose();
        }
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        style={{
          background: c.surface,
          color: c.text,
          borderLeft: `1px solid ${c.border}`,
          width: 360,
          maxWidth: 'calc(100vw - 32px)',
          height: '100%',
          padding: spacing.xl,
          overflowY: 'auto',
          outline: 'none',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
          <h2 style={{ margin: 0, fontSize: font.sizeLg }}>{title}</h2>
          <button
            type="button"
            aria-label="Đóng"
            onClick={onClose}
            style={{ background: 'transparent', border: 'none', color: c.textMuted, cursor: 'pointer', fontSize: font.sizeLg }}
          >
            ✕
          </button>
        </div>
        <div style={{ marginTop: spacing.lg }}>{children}</div>
      </div>
    </div>
  );
}
