import { useState, type CSSProperties } from 'react';

import styles from './Tree.module.css';

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
}

export function Tree({ nodes, selectedId, onSelect, label }: TreeProps) {
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());

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
          className={onSelect ? styles.item : `${styles.item} ${styles.unselectable}`}
          style={{ '--tree-depth': depth } as CSSProperties}
        >
          {hasChildren ? (
            <button
              type="button"
              aria-label={isCollapsed ? `Mở ${node.label}` : `Đóng ${node.label}`}
              onClick={(event) => {
                event.stopPropagation();
                toggle(node.id);
              }}
              className={styles.chevron}
            >
              {isCollapsed ? '▸' : '▾'}
            </button>
          ) : (
            <span className={styles.spacer} />
          )}
          {node.label}
        </span>
        {hasChildren && !isCollapsed ? (
          <ul role="group" className={styles.group}>
            {node.children!.map((child) => renderNode(child, depth + 1))}
          </ul>
        ) : null}
      </li>
    );
  }

  return (
    <ul role="tree" aria-label={label} className={styles.tree}>
      {nodes.map((node) => renderNode(node, 0))}
    </ul>
  );
}
