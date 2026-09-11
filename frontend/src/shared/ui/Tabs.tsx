import { useId, type ReactNode } from 'react';

import styles from './Tabs.module.css';

export interface Tab {
  id: string;
  label: string;
  content: ReactNode;
}

export interface TabsProps {
  tabs: readonly Tab[];
  activeId: string;
  onChange: (id: string) => void;
  label: string;
}

export function Tabs({ tabs, activeId, onChange, label }: TabsProps) {
  const baseId = useId();
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
    <>
      <div role="tablist" aria-label={label} className={styles.tablist}>
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
              className={styles.tab}
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
        className={styles.panel}
      >
        {active?.content}
      </div>
    </>
  );
}
