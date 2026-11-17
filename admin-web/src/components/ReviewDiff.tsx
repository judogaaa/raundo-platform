import { ExpandableText } from "./ExpandableText";

/** Human-readable labels for diff field keys received from the backend. */
const FIELD_LABELS: Record<string, string> = {
  name: "Название",
  pitch: "Питч",
  description: "Описание",
  strategy: "Стратегия",
  type: "Тип",
  display_name: "Имя",
  state: "Статус анкеты",
};

const NOT_SET = "Не указано";

export function diffFieldLabel(key: string): string {
  return FIELD_LABELS[key] ?? key;
}

function DiffValue({ value, fallback }: { value: unknown; fallback: string }) {
  const text = value === null || value === undefined ? "" : String(value).trim();
  return (
    <div className="review-diff-value">
      <span className="muted field-key">{fallback}</span>
      {text ? <ExpandableText value={text} limit={220} /> : <p className="review-text muted">{NOT_SET}</p>}
    </div>
  );
}

/**
 * "Было → Стало" renderer for the moderation diff. Never shows raw field
 * keys; every change gets a human label and, for state, a status label.
 */
export function ReviewDiff({
  diff,
  diffView,
  describe,
}: {
  diff: { field: string; published: unknown; pending: unknown }[];
  diffView?: { label: string; before: string; after: string }[];
  describe?: (field: string, value: unknown) => string;
}) {
  if (diffView) {
    if (diffView.length === 0) {
      return (
        <p className="review-text muted">
          Изменений относительно опубликованной версии нет.
        </p>
      );
    }
    return (
      <div className="stack">
        {diffView.map((row) => (
          <div key={row.label} className="review-diff-row">
            <strong>{row.label}</strong>
            <div className="review-diff-cols">
              <DiffValue value={row.before} fallback="Не было" />
              <DiffValue value={row.after} fallback="Стало" />
            </div>
          </div>
        ))}
      </div>
    );
  }
  const rows = diff.filter(
    (d) => (d.published ?? "") !== (d.pending ?? "") && diffFieldLabel(d.field) !== d.field,
  );
  if (rows.length === 0) {
    return (
      <p className="review-text muted">
        Изменений относительно опубликованной версии нет.
      </p>
    );
  }
  return (
    <div className="stack">
      {rows.map((d) => (
        <div key={d.field} className="review-diff-row">
          <strong>{diffFieldLabel(d.field)}</strong>
          <div className="review-diff-cols">
            <DiffValue value={describe ? describe(d.field, d.published) : d.published} fallback="Не было" />
            <DiffValue value={describe ? describe(d.field, d.pending) : d.pending} fallback="Стало" />
          </div>
        </div>
      ))}
    </div>
  );
}
