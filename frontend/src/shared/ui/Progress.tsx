import { colors, radius, type UiTheme } from './tokens';

export interface ProgressProps {
  value: number;
  max?: number;
  label?: string;
  theme?: UiTheme;
}

export function Progress({ value, max = 100, label = 'Tiến độ', theme = 'light' }: ProgressProps) {
  const c = colors[theme];
  const bounded = Math.max(0, Math.min(max, value));
  const percent = max > 0 ? Math.round((bounded / max) * 100) : 0;
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={bounded}
      style={{
        background: c.surfaceRaised,
        border: `1px solid ${c.border}`,
        borderRadius: radius.sm,
        height: 10,
        width: '100%',
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          background: c.primary,
          height: '100%',
          width: `${percent}%`,
          transition: 'width 120ms ease',
        }}
      />
    </div>
  );
}
