import type { ReactNode } from 'react';

import styles from './Table.module.css';

export interface Column<Row> {
  key: string;
  header: string;
  render: (row: Row) => ReactNode;
}

export interface TableProps<Row> {
  rows: readonly Row[];
  columns: readonly Column<Row>[];
  caption?: string;
}

export function Table<Row extends { id: string }>({ rows, columns, caption }: TableProps<Row>) {
  return (
    <table className={styles.table}>
      {caption ? <caption className={styles.caption}>{caption}</caption> : null}
      <thead>
        <tr>
          {columns.map((column) => (
            <th key={column.key} scope="col" className={styles.th}>
              {column.header}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.id}>
            {columns.map((column) => (
              <td key={column.key} className={styles.td}>
                {column.render(row)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
