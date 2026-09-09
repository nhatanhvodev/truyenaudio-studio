import { useId, type ReactNode } from 'react';

import { colors, font, spacing, type UiTheme } from './tokens';

export interface Tab {
  id: string;
  label: string;
  content: ReactNode;
}

export interface TabsProps {
  tabs: readonly Tab[];
  activeId: string;
  onChange: (id: string) => void;
  theme?: UiTheme;
  label: string;
}

export function Tabs({ tabs, activeId, onChange, theme = 'light', label }: TabsProps) {
  const baseId = useId();
  const c = colors[theme];
  const active = tabs.find((tab) => tab.id === activeId) ?? tabs[0];

  function activate(index: number) {
    const next = tabs[index];
    if (next) {
      onChange(next.id);
    }
  }

  function onKeyDown(event: React.KeyboardEvent, index: number) {
    if (event.key === 'ArrowRight') {
      event.preventDefault();
      activate((index + 1) % tabs.length);
    } else if (event.key === 'ArrowLeft') {
      event.preventDefault();
      activate((index - 1 + tabs.length) % tabs.length);
    } else if (event.key === 'Home') {
      event.preventDefault();
      activate(0);
    } else if (event.key === 'End') {
      event.preventDefault();
      activate(tabs.length - 1);
    }
  }

  return (
    <div>
      <div
        role="tablist"
        aria-label={label}
        style={{ display: 'flex', gap: spacing.xs, borderBottom: `1px solid ${c.border}` }}
      >
        {tabs.map((tab, index) => {
          const selected = tab.id === active?.id;
          return (
            <button
              key={tab.id}
              role="tab"
              id={`${baseId}-tab-${tab.id}`}
              aria-selected={selected}
              aria-controls={`${baseId}-panel-${tab.id}`}
              tabIndex={selected ? 0 : -1}
              onClick={() => onChange(tab.id)}
              onKeyDown={(event) => onKeyDown(event, index)}
              style={{
                background: 'transparent',
                border: 'none',
                color: selected ? c.primary : c.textMuted,
                fontWeight: selected ? font.weightSemibold : font.weightNormal,
                padding: `${spacing.sm}px ${spacing.md}px`,
                borderBottom: selected ? `2px solid ${c.primary}` : 'none',
                cursor: 'pointer',
                fontFamily: 'inherit',
                fontSize: font.sizeMd,
                outline: 'none',
              }}
            >
              {tab.label}
            </button>
          );
        })}
      </div>
      <div
        key={active?.id}
        role="tabpanel"
        id={`${baseId}-panel-${active?.id}`}
        aria-labelledby={`${baseId}-tab-${active?.id}`}
        style={{ paddingTop: spacing.lg }}
      >
        {active?.content}
      </div>
    </div>
  );
}
