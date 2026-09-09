import { colors, font, spacing, type ColorTokens, type UiTheme } from './tokens';

export interface ToastProps {
  message: string;
  tone?: 'info' | 'success' | 'danger';
  theme?: UiTheme;
}

const TONE_TEXT: Record<NonNullable<ToastProps['tone']>, keyof ColorTokens> = {
  info: 'text',
  success: 'successText',
  danger: 'danger',
};

export function Toast({ message, tone = 'info', theme = 'light' }: ToastProps) {
  const c = colors[theme];
  const textColor = c[TONE_TEXT[tone]];
  return (
    <div
      role="status"
      aria-live="polite"
      style={{
        background: c.surfaceRaised,
        color: textColor,
        border: `1px solid ${c.border}`,
        borderRadius: 6,
        padding: `${spacing.sm}px ${spacing.lg}px`,
        fontSize: font.sizeMd,
        boxShadow: '0 2px 6px rgba(0,0,0,0.18)',
      }}
    >
      {message}
    </div>
  );
}
