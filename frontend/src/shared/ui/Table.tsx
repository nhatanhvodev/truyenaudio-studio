import type { ReactNode } from 'react';

import { colors, font, spacing, type UiTheme } from './tokens';

export interface Column<Row> {
  key: string;
  header: string;
  render: (row: Row) => ReactNode;
}

export interface TableProps<Row> {
  rows: readonly Row[];
  columns: readonly Column<Row>[];
  caption?: string;
  theme?: UiTheme;
}

export function Table<Row extends { id: string }>({
  rows,
  columns,
  caption,
  theme = 'light',
}: TableProps<Row>) {
  const c = colors[theme];
  return (
    <table
      style={{
        width: '100%',
        borderCollapse: 'collapse',
        color: c.text,
        fontFamily: 'inherit',
        fontSize: font.sizeMd,
      }}
    >
      {caption ? <caption style={{ textAlign: 'left', padding: spacing.sm }}>{caption}</caption> : null}
      <thead>
        <tr style={{ borderBottom: `2px solid ${c.border}`, textAlign: 'left' }}>
          {columns.map((column) => (
            <th key={column.key} scope="col" style={{ padding: spacing.sm, fontWeight: 600 }}>
              {column.header}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.id} style={{ borderBottom: `1px solid ${c.border}` }}>
            {columns.map((column) => (
              <td key={column.key} style={{ padding: spacing.sm }}>
                {column.render(row)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
