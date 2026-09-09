// Design tokens for the Truyện Audio Studio UI (task U01 part 1).
// Light/dark semantic colors meet WCAG: body text >= 4.5:1, UI/focus >= 3:1.

export type UiTheme = 'light' | 'dark';

export interface ColorTokens {
  surface: string;
  surfaceRaised: string;
  text: string;
  textMuted: string;
  textOnPrimary: string;
  primary: string;
  primaryHover: string;
  danger: string;
  dangerHover: string;
  border: string;
  focusRing: string;
  successText: string;
}

export const colors: Record<UiTheme, ColorTokens> = {
  light: {
    surface: '#ffffff',
    surfaceRaised: '#f9fafb',
    text: '#111827',
    textMuted: '#4b5563',
    textOnPrimary: '#ffffff',
    primary: '#1d4ed8',
    primaryHover: '#1e40af',
    danger: '#b91c1c',
    dangerHover: '#991b1b',
    border: '#d1d5db',
    focusRing: '#2563eb',
    successText: '#166534',
  },
  dark: {
    surface: '#111827',
    surfaceRaised: '#1f2937',
    text: '#f9fafb',
    textMuted: '#d1d5db',
    textOnPrimary: '#ffffff',
    primary: '#2563eb',
    primaryHover: '#1d4ed8',
    danger: '#dc2626',
    dangerHover: '#b91c1c',
    border: '#4b5563',
    focusRing: '#60a5fa',
    successText: '#4ade80',
  },
};

export const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
} as const;

export const radius = { sm: 6, md: 8 } as const;

export const font = {
  sizeSm: 13,
  sizeMd: 14,
  sizeLg: 16,
  weightNormal: 400,
  weightMedium: 500,
  weightSemibold: 600,
} as const;
