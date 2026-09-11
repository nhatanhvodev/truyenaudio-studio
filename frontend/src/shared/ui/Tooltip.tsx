import {
  Children,
  cloneElement,
  useId,
  useRef,
  useState,
  type ReactElement,
} from 'react';

import styles from './Tooltip.module.css';

export interface TooltipProps {
  content: string;
  children: ReactElement;
}

export function Tooltip({ content, children }: TooltipProps) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const timeoutRef = useRef<number | null>(null);
  const trigger = Children.only(children);

  function show() {
    if (timeoutRef.current !== null) {
      window.clearTimeout(timeoutRef.current);
    }
    timeoutRef.current = window.setTimeout(() => setOpen(true), 120);
  }

  function hide() {
    if (timeoutRef.current !== null) {
      window.clearTimeout(timeoutRef.current);
    }
    setOpen(false);
  }

  const wrapped = cloneElement(trigger as ReactElement<Record<string, unknown>>, {
    'aria-describedby': id,
    onMouseEnter: show,
    onMouseLeave: hide,
    onFocus: show,
    onBlur: hide,
  });

  return (
    <>
      {wrapped}
      {open ? (
        <span
          id={id}
          role="tooltip"
          className={styles.tip}
        >
          {content}
        </span>
      ) : null}
    </>
  );
}
