import { useEffect, useState } from "react";

/** Scan/delivery states shown to humans, never raw codes. */
const SCAN_LABELS: Record<string, string> = {
  ok: "Готово",
  clean: "Готово",
  approved: "Готово",
  accepted: "Готово",
  pending: "Ожидает обработки",
  processing: "Ожидает обработки",
  queued: "Ожидает обработки",
  failed: "Ошибка обработки",
  rejected: "Отклонено сканом",
};

export function mediaScanLabel(state: string | undefined): string {
  return SCAN_LABELS[state ?? ""] ?? "Ожидает обработки";
}

type Media = {
  position: number;
  kind: string;
  scan_state: string;
  preview_url?: string;
};

/**
 * Photo gallery for review cards: real previews through the media proxy,
 * a calm placeholder when there is nothing to show, and a lightbox with
 * prev/next + counter for a closer look.
 */
export function ReviewMedia({
  media,
  imageClass = "media-img",
}: {
  media?: Media[];
  imageClass?: string;
}) {
  const photos = (media ?? []).filter((m) => (m.preview_url ?? "").length > 0);
  const [active, setActive] = useState<number | null>(null);

  useEffect(() => {
    if (active === null) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setActive(null);
      if (e.key === "ArrowRight") setActive((i) => (i === null ? i : (i + 1) % photos.length));
      if (e.key === "ArrowLeft")
        setActive((i) => (i === null ? i : (i - 1 + photos.length) % photos.length));
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, photos.length]);

  if (photos.length === 0) {
    return (
      <div className="review-media-empty muted">
        <span className="review-media-icon" aria-hidden>
          ▢
        </span>
        Фото отсутствует
      </div>
    );
  }

  return (
    <>
      <div className="media-grid">
        {photos.map((m, index) => (
          <button
            key={m.position}
            type="button"
            className="review-media-thumb"
            onClick={() => setActive(index)}
            aria-label={`Открыть фото ${m.position}`}
          >
            <img
              className={`${imageClass} review-media-img`}
              src={m.preview_url}
              alt={`Фото ${m.position}`}
              loading="lazy"
            />
          </button>
        ))}
      </div>
      {photos.length > 1 && (
        <p className="muted small review-media-count">
          Фото: {photos.length} · нажмите для просмотра
        </p>
      )}
      {active !== null && (
        <div
          className="lightbox"
          role="dialog"
          aria-modal="true"
          aria-label={`Фото ${active + 1} из ${photos.length}`}
          onClick={() => setActive(null)}
        >
          <div className="lightbox-inner" onClick={(e) => e.stopPropagation()}>
            <img
              className="lightbox-img"
              src={photos[active].preview_url}
              alt={`Фото ${photos[active].position}`}
            />
            <div className="lightbox-bar">
              <span className="muted">
                {active + 1} / {photos.length}
              </span>
              <div className="lightbox-actions">
                <button
                  type="button"
                  className="ghost small"
                  onClick={() => setActive((active - 1 + photos.length) % photos.length)}
                  aria-label="Предыдущее фото"
                >
                  ←
                </button>
                <button
                  type="button"
                  className="ghost small"
                  onClick={() => setActive((active + 1) % photos.length)}
                  aria-label="Следующее фото"
                >
                  →
                </button>
                <button
                  type="button"
                  className="ghost small"
                  onClick={() => setActive(null)}
                  aria-label="Закрыть"
                >
                  ✕
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
