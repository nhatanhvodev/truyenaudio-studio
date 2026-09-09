import { useState } from 'react';

import { colors, font, spacing, type UiTheme } from './tokens';

export interface TreeNode {
  id: string;
  label: string;
  children?: readonly TreeNode[];
}

export interface TreeProps {
  nodes: readonly TreeNode[];
  selectedId?: string | null;
  onSelect?: (id: string) => void;
  label: string;
  theme?: UiTheme;
}

export function Tree({ nodes, selectedId, onSelect, label, theme = 'light' }: TreeProps) {
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());
  const c = colors[theme];

  function toggle(id: string) {
    setCollapsed((previous) => {
      const next = new Set(previous);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }

  function renderNode(node: TreeNode, depth: number) {
    const hasChildren = Boolean(node.children?.length);
    const isCollapsed = collapsed.has(node.id);
    return (
      <li key={node.id} role="treeitem" aria-expanded={hasChildren ? !isCollapsed : undefined}>
        <span
          role={onSelect ? 'button' : undefined}
          tabIndex={onSelect ? 0 : -1}
          aria-selected={onSelect ? selectedId === node.id : undefined}
          onClick={() => onSelect?.(node.id)}
          onKeyDown={(event) => {
            if (onSelect && (event.key === 'Enter' || event.key === ' ')) {
              event.preventDefault();
              onSelect(node.id);
            }
          }}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: spacing.xs,
            paddingLeft: depth * spacing.lg,
            cursor: onSelect ? 'pointer' : 'default',
            color: selectedId === node.id ? c.primary : c.text,
            fontWeight: selectedId === node.id ? 600 : 400,
            fontSize: font.sizeMd,
          }}
        >
          {hasChildren ? (
            <button
              type="button"
              aria-label={isCollapsed ? `Mở ${node.label}` : `Đóng ${node.label}`}
              onClick={(event) => {
                event.stopPropagation();
                toggle(node.id);
              }}
              style={{
                background: 'transparent',
                border: 'none',
                color: c.textMuted,
                cursor: 'pointer',
                width: 18,
                padding: 0,
              }}
            >
              {isCollapsed ? '▸' : '▾'}
            </button>
          ) : (
            <span style={{ width: 18, display: 'inline-block' }} />
          )}
          {node.label}
        </span>
        {hasChildren && !isCollapsed ? (
          <ul role="group" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {node.children!.map((child) => renderNode(child, depth + 1))}
          </ul>
        ) : null}
      </li>
    );
  }

  return (
    <ul role="tree" aria-label={label} style={{ listStyle: 'none', margin: 0, padding: 0 }}>
      {nodes.map((node) => renderNode(node, 0))}
    </ul>
  );
}
