import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';

import { colors, font, radius, spacing, type UiTheme } from './tokens';

export interface ComboboxOption {
  value: string;
  label: string;
}

export interface ComboboxProps {
  label: string;
  options: readonly ComboboxOption[];
  value: string;
  onChange: (value: string) => void;
  theme?: UiTheme;
  placeholder?: string;
}

export function Combobox({
  label,
  options,
  value,
  onChange,
  theme = 'light',
  placeholder,
}: ComboboxProps) {
  const baseId = useId();
  const inputId = `${baseId}-input`;
  const listboxId = `${baseId}-listbox`;
  const c = colors[theme];
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [highlight, setHighlight] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const selected = options.find((option) => option.value === value);
  const filtered = query.trim()
    ? options.filter((option) =>
        option.label.toLocaleLowerCase('vi').includes(query.trim().toLocaleLowerCase('vi')),
      )
    : options;

  useEffect(() => {
    if (!open) {
      return;
    }
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false);
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [open]);

  function choose(option: ComboboxOption) {
    onChange(option.value);
    setOpen(false);
    setQuery('');
  }

  function onInputKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (!open && (event.key === 'ArrowDown' || event.key === 'Enter')) {
      setOpen(true);
      return;
    }
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setOpen(true);
      setHighlight((index) => Math.min(filtered.length - 1, index + 1));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setHighlight((index) => Math.max(0, index - 1));
    } else if (event.key === 'Enter' && open && filtered[highlight]) {
      event.preventDefault();
      choose(filtered[highlight]);
    } else if (event.key === 'Home') {
      setHighlight(0);
    } else if (event.key === 'End') {
      setHighlight(Math.max(0, filtered.length - 1));
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: spacing.xs }}>
      <label htmlFor={inputId} style={{ color: c.text, fontSize: font.sizeSm, fontWeight: 500 }}>
        {label}
      </label>
      <input
        ref={inputRef}
        id={inputId}
        role="combobox"
        aria-expanded={open}
        aria-controls={listboxId}
        aria-autocomplete="list"
        aria-activedescendant={
          open && filtered[highlight] ? `${listboxId}-${filtered[highlight].value}` : undefined
        }
        value={open ? query : selected?.label ?? ''}
        placeholder={placeholder}
        onChange={(event) => {
          setQuery(event.target.value);
          setOpen(true);
          setHighlight(0);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onInputKeyDown}
        onBlur={() => window.setTimeout(() => setOpen(false), 100)}
        style={{
          background: c.surface,
          color: c.text,
          border: `1px solid ${c.border}`,
          borderRadius: radius.sm,
          padding: `${spacing.sm}px ${spacing.md}px`,
          fontSize: font.sizeMd,
          fontFamily: 'inherit',
          outline: 'none',
        }}
      />
      {open ? (
        <ul
          id={listboxId}
          role="listbox"
          aria-label={label}
          style={{
            listStyle: 'none',
            margin: 0,
            padding: 0,
            background: c.surfaceRaised,
            border: `1px solid ${c.border}`,
            borderRadius: radius.sm,
            maxHeight: 220,
            overflowY: 'auto',
          }}
        >
          {filtered.length === 0 ? (
            <li style={{ padding: spacing.sm, color: c.textMuted, fontSize: font.sizeSm }}>
              Không có lựa chọn phù hợp.
            </li>
          ) : (
            filtered.map((option, index) => (
              <li
                key={option.value}
                role="option"
                id={`${listboxId}-${option.value}`}
                aria-selected={option.value === value}
                data-highlighted={index === highlight ? 'true' : 'false'}
                onMouseDown={(event) => {
                  event.preventDefault();
                  choose(option);
                }}
                onMouseEnter={() => setHighlight(index)}
                style={{
                  padding: `${spacing.sm}px ${spacing.md}px`,
                  cursor: 'pointer',
                  background: index === highlight ? c.primary : 'transparent',
                  color: index === highlight ? c.textOnPrimary : c.text,
                  fontSize: font.sizeMd,
                }}
              >
                {option.label}
              </li>
            ))
          )}
        </ul>
      ) : null}
    </div>
  );
}
