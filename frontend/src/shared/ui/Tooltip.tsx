import {
  Children,
  cloneElement,
  useId,
  useRef,
  useState,
  type ReactElement,
} from 'react';

import { colors, font, radius, spacing, type UiTheme } from './tokens';

export interface TooltipProps {
  content: string;
  children: ReactElement;
  theme?: UiTheme;
}

export function Tooltip({ content, children, theme = 'light' }: TooltipProps) {
  const id = useId();
  const c = colors[theme];
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
          style={{
            position: 'absolute',
            zIndex: 40,
            background: c.surfaceRaised,
            color: c.text,
            border: `1px solid ${c.border}`,
            borderRadius: radius.sm,
            padding: `${spacing.xs}px ${spacing.sm}px`,
            fontSize: font.sizeSm,
            maxWidth: 280,
          }}
        >
          {content}
        </span>
      ) : null}
    </>
  );
}
