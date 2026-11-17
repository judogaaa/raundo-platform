import { useState } from "react";

const PREVIEW_LIMIT = 280;

/**
 * Long-text block: collapsed preview with an explicit reveal control.
 * Text is always strictly limited to its container (no horizontal overflow).
 */
export function ExpandableText({
  value,
  placeholder = "Не указано",
  limit = PREVIEW_LIMIT,
}: {
  value: string;
  placeholder?: string;
  limit?: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const text = (value || "").trim();
  if (!text) return <p className="review-text muted">{placeholder}</p>;
  if (text.length <= limit) {
    return <p className="review-text">{text}</p>;
  }
  return (
    <div className="stack">
      <p className="review-text">
        {expanded ? text : `${text.slice(0, limit).trimEnd()}…`}
      </p>
      <button
        type="button"
        className="ghost small"
        onClick={() => setExpanded((v) => !v)}
      >
        {expanded ? "Свернуть" : "Показать полностью"}
      </button>
    </div>
  );
}
