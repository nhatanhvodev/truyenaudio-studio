import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';

import styles from './Combobox.module.css';

export interface ComboboxOption {
  value: string;
  label: string;
}

export interface ComboboxProps {
  label: string;
  options: readonly ComboboxOption[];
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}

export function Combobox({
  label,
  options,
  value,
  onChange,
  placeholder,
}: ComboboxProps) {
  const baseId = useId();
  const inputId = `${baseId}-input`;
  const listboxId = `${baseId}-listbox`;
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
    <>
      <label htmlFor={inputId} className={styles.label}>
        {label}
      </label>
      <div className={styles.wrapper}>
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
          className={styles.input}
        />
        {open ? (
          <ul id={listboxId} role="listbox" aria-label={label} className={styles.list}>
            {filtered.length === 0 ? (
              <li className={styles.empty}>Không có lựa chọn phù hợp.</li>
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
                  className={styles.option}
                >
                  {option.label}
                </li>
              ))
            )}
          </ul>
        ) : null}
      </div>
    </>
  );
}
