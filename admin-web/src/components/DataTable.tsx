import type { ReactNode } from "react";

export type Column = {
  key: string;
  label: string;
  render?: (row: Row) => ReactNode;
  /** Secondary columns collapse on narrow screens so the list stays readable. */
  hideOnMobile?: boolean;
};

export type Row = Record<string, unknown>;

type Props = {
  columns: Column[];
  rows: Row[];
  onRowClick?: (row: Row) => void;
  actions?: (row: Row) => ReactNode;
  emptyText?: string;
  rowKey?: (row: Row) => string;
};

export function DataTable({ columns, rows, onRowClick, actions, emptyText, rowKey }: Props) {
  if (!rows.length) {
    return <div className="empty">{emptyText || "Пока пусто."}</div>;
  }
  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} className={c.hideOnMobile ? "col-hide-sm" : undefined}>
                {c.label}
              </th>
            ))}
            {actions ? <th aria-label="Действия" /> : null}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr
              key={rowKey ? rowKey(row) : String(row.id ?? row.review_id ?? row.number ?? i)}
              className={onRowClick ? "clickable" : ""}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              tabIndex={onRowClick ? 0 : undefined}
              onKeyDown={
                onRowClick
                  ? (e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onRowClick(row);
                      }
                    }
                  : undefined
              }
            >
              {columns.map((c) => (
                <td key={c.key} className={c.hideOnMobile ? "col-hide-sm" : undefined}>
                  {c.render ? c.render(row) : String(row[c.key] ?? "—")}
                </td>
              ))}
              {actions ? (
                <td onClick={(e) => e.stopPropagation()}>
                  <div className="table-actions">{actions(row)}</div>
                </td>
              ) : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
