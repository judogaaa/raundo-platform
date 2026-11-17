"""Compact Apple-like startup cards for Telegram HTML (no Mini App)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape, unescape
from typing import Sequence

from raundo.domain import (
    CONTACT_POLICY_LABELS,
    PITCH_MAX,
    STARTUP_AUDIENCE_LABELS,
    STARTUP_FUNDRAISING_LABELS,
    STARTUP_INTENT_LABELS,
    STARTUP_MONETIZATION_LABELS,
    STARTUP_PROGRESS_LABELS,
    STARTUP_USE_OF_FUNDS_LABELS,
    format_ask_amount,
)
from raundo.models.startup import Startup, StartupMedia

# Readable compact card target; hard Telegram caption limit is 1024 with entities.
_CAPTION_SOFT_MAX = 750
_CAPTION_HARD_MAX = 1024
_META_SOFT_MAX = 78
_NOTE_COMPACT_MAX = 72


@dataclass(frozen=True)
class StartupCardView:
    """Resolved labels for rendering (no ORM I/O). Never invents metrics."""

    name: str
    pitch: str
    description: str
    sector_labels: tuple[str, ...]
    stage_label: str | None
    website: str | None
    no_link_reason: str | None
    ask_label: str | None
    ask_display: str | None
    intent_labels: tuple[str, ...]
    audience_compact: str | None
    audience_full: str | None
    progress_line: str | None
    monetization_compact: str | None
    monetization_full: str | None
    fundraising_line: str | None
    use_of_funds_line: str | None
    geography_line: str | None
    cover_file_id: str | None
    cover_width: int | None
    cover_height: int | None
    gallery_file_ids: tuple[str, ...]
    photo_count: int
    review_state: str
    founder_line: str | None
    contact_policy_label: str | None


def _labels_from_codes(codes: Sequence[str], mapping: dict[str, str]) -> list[str]:
    return [mapping[c] for c in codes if c in mapping]


def _audience_compact(codes: Sequence[str], note: str | None) -> str | None:
    labels = _labels_from_codes(codes, STARTUP_AUDIENCE_LABELS)
    if not labels and not (note or "").strip():
        return None
    text = " · ".join(labels) if labels else ""
    note_s = (note or "").strip()
    if note_s and "other" in codes:
        short = note_s if len(note_s) <= _NOTE_COMPACT_MAX else note_s[: _NOTE_COMPACT_MAX - 1] + "…"
        text = f"{text}: {short}" if text else short
    return text or None


def _audience_full(codes: Sequence[str], note: str | None) -> str | None:
    labels = _labels_from_codes(codes, STARTUP_AUDIENCE_LABELS)
    note_s = (note or "").strip()
    if not labels and not note_s:
        return None
    text = " · ".join(labels) if labels else ""
    if note_s:
        text = f"{text}\n{note_s}" if text else note_s
    return text or None


def _progress_line(codes: Sequence[str]) -> str | None:
    """Founder-declared signals only. Drop 'none_yet' if any stronger signal is present."""
    clean = [c for c in codes if c in STARTUP_PROGRESS_LABELS]
    if not clean:
        return None
    if "none_yet" in clean and any(c != "none_yet" for c in clean):
        clean = [c for c in clean if c != "none_yet"]
    labels = _labels_from_codes(clean, STARTUP_PROGRESS_LABELS)
    return ", ".join(labels) if labels else None


def _monetization_compact(status: str | None, note: str | None) -> str | None:
    """Short finished status for the skim card — never mid-sentence cut of long notes."""
    del note
    if not status or status not in STARTUP_MONETIZATION_LABELS:
        return None
    return STARTUP_MONETIZATION_LABELS[status]


def _monetization_full(status: str | None, note: str | None) -> str | None:
    """Status + full founder note (up to stored 240 chars). No invented metrics."""
    if not status or status not in STARTUP_MONETIZATION_LABELS:
        return None
    base = STARTUP_MONETIZATION_LABELS[status]
    note_s = (note or "").strip()
    if note_s:
        return f"{base}\n{note_s}"
    return base


def _use_of_funds_line(codes: Sequence[str], note: str | None) -> str | None:
    labels = _labels_from_codes(codes, STARTUP_USE_OF_FUNDS_LABELS)
    note_s = (note or "").strip()
    if not labels and not note_s:
        return None
    text = ", ".join(labels) if labels else ""
    if note_s:
        text = f"{text}: {note_s}" if text else note_s
    return text or None


def build_startup_card_view(
    startup: Startup,
    *,
    sector_labels: Sequence[str],
    stage_label: str | None,
    include_owner_private: bool = False,
) -> StartupCardView:
    media: list[StartupMedia] = list(startup.media or [])
    media_sorted = sorted(media, key=lambda m: m.position)
    cover = media_sorted[0] if media_sorted else None
    gallery = tuple(m.telegram_file_id for m in media_sorted if m.telegram_file_id)
    website = startup.website_url or startup.app_url or startup.demo_url
    no_link = (startup.no_link_reason or "").strip() or None
    founder_line = None
    if startup.founder_display_name or startup.founder_role:
        name = (startup.founder_display_name or "").strip() or "Команда"
        role = (startup.founder_role or "").strip() or "представитель"
        founder_line = f"{name} · {role}"
    raw_policy = startup.contact_policy if include_owner_private else None
    contact_label = None
    if raw_policy:
        contact_label = CONTACT_POLICY_LABELS.get(raw_policy, raw_policy)
    intents = tuple(
        STARTUP_INTENT_LABELS.get(code, code) for code in (startup.intents or [])
    )
    ask_label = format_ask_amount(startup.ask_amount_minor, startup.ask_currency)
    ask_display = f"Инвестиции: {ask_label}" if ask_label else None
    fund = startup.fundraising_stage
    fund_line = STARTUP_FUNDRAISING_LABELS.get(fund) if fund else None
    if ask_label and fund == "not_raising":
        fund_line = None
    geo = (startup.country or "").strip() or None
    aud_codes = list(startup.audience_codes or [])
    aud_note = getattr(startup, "audience_note", None)
    mon_status = getattr(startup, "monetization_status", None)
    mon_note = getattr(startup, "monetization_note", None)
    return StartupCardView(
        name=(startup.name or "").strip() or "Без названия",
        pitch=(startup.one_liner or "").strip(),
        description=(startup.full_description or "").strip(),
        sector_labels=tuple(label for label in sector_labels if label),
        stage_label=stage_label,
        website=website,
        no_link_reason=no_link if not website else None,
        ask_label=ask_label,
        ask_display=ask_display,
        intent_labels=intents,
        audience_compact=_audience_compact(aud_codes, aud_note),
        audience_full=_audience_full(aud_codes, aud_note),
        progress_line=_progress_line(list(startup.progress_codes or [])),
        monetization_compact=_monetization_compact(mon_status, mon_note),
        monetization_full=_monetization_full(mon_status, mon_note),
        fundraising_line=fund_line,
        use_of_funds_line=_use_of_funds_line(
            list(getattr(startup, "use_of_funds_codes", None) or []),
            getattr(startup, "use_of_funds_note", None),
        ),
        geography_line=geo,
        cover_file_id=cover.telegram_file_id if cover else None,
        cover_width=cover.width if cover else None,
        cover_height=cover.height if cover else None,
        gallery_file_ids=gallery,
        photo_count=len(media_sorted),
        review_state=startup.review_state,
        founder_line=founder_line,
        contact_policy_label=contact_label,
    )


def _safe_truncate_plain(text: str, *, limit: int) -> str:
    """Last-resort caption shrink: never cut mid-word or mid HTML (plain only)."""
    text = text.strip()
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n+", " ", text).strip()
    if len(text) <= limit:
        return text
    window = text[: limit + 1]
    best = -1
    for sep in (". ", "! ", "? "):
        idx = window.rfind(sep)
        if idx >= int(limit * 0.45):
            best = max(best, idx + 1)
    if best > 0:
        return text[:best].rstrip() + "…"
    space = window.rfind(" ")
    if space >= int(limit * 0.4):
        return text[:space].rstrip(" ,;:") + "…"
    return text[:limit].rstrip() + "…"


def _pitch_display(pitch: str) -> str:
    return pitch.strip()


def _sectors_meta_line(labels: Sequence[str], stage: str | None) -> str | None:
    names = [n.strip() for n in labels if n and n.strip()]
    stage_s = (stage or "").strip() or None

    def join(parts: list[str]) -> str:
        return " · ".join(parts)

    if not names and not stage_s:
        return None
    if not names:
        return stage_s
    for count in (3, 2, 1):
        parts = list(names[:count])
        if stage_s:
            parts.append(stage_s)
        line = join(parts)
        if len(line) <= _META_SOFT_MAX or count == 1:
            return line
    return join([names[0]] + ([stage_s] if stage_s else []))


def _site_display(url: str) -> str:
    site = url
    if site.startswith("https://"):
        site = site[len("https://") :]
    elif site.startswith("http://"):
        site = site[len("http://") :]
    return site.rstrip("/")


def _fact(label: str, value: str) -> str:
    return f"<b>{escape(label)}</b>\n{escape(value)}"


def _build_compact_body(
    *,
    name: str,
    meta: str | None,
    pitch: str,
    facts: list[str],
    tail: list[str],
) -> str:
    lines: list[str] = [f"<b>🚀 {escape(name)}</b>"]
    if meta:
        lines.append(escape(meta))
    if pitch:
        lines.append("")
        lines.append(escape(_pitch_display(pitch)))
    if facts:
        lines.append("")
        lines.extend(facts)
    if tail:
        lines.append("")
        lines.extend(tail)
    return "\n".join(lines)


def compact_startup_html(view: StartupCardView, *, owner_preview: bool = False) -> str:
    """Investor skim card. Omits empty fields. Never fabricates traction."""
    del owner_preview
    meta = _sectors_meta_line(view.sector_labels, view.stage_label)
    pitch = view.pitch

    facts: list[str] = []
    if view.audience_compact:
        facts.append(_fact("🎯 Аудитория", view.audience_compact))
    if view.progress_line:
        facts.append(_fact("📈 Прогресс", view.progress_line))
    if view.monetization_compact:
        facts.append(_fact("💼 Монетизация", view.monetization_compact))
    if view.intent_labels:
        facts.append(_fact("🤝 Цели", ", ".join(view.intent_labels)))

    tail: list[str] = []
    if view.ask_label:
        tail.append(escape(f"💰 Инвестиции\n{view.ask_label}"))
    if view.website:
        tail.append(escape(_site_display(view.website)))

    text = _build_compact_body(
        name=view.name, meta=meta, pitch=pitch, facts=facts, tail=tail
    )
    while len(text) > _CAPTION_SOFT_MAX and facts:
        facts.pop()
        text = _build_compact_body(
            name=view.name, meta=meta, pitch=pitch, facts=facts, tail=tail
        )
    if len(text) > _CAPTION_HARD_MAX:
        # Strip tags from already-escaped HTML, then unescape once before
        # re-escape — otherwise "&amp;" becomes "&amp;amp;" in Telegram.
        # Shrink plain text until escaped length fits Telegram's limit.
        plain = unescape(re.sub(r"<[^>]+>", "", text))
        limit = _CAPTION_HARD_MAX
        while True:
            candidate = escape(_safe_truncate_plain(plain, limit=limit))
            if len(candidate) <= _CAPTION_HARD_MAX or limit <= 16:
                text = candidate
                break
            limit = max(16, limit - max(8, len(candidate) - _CAPTION_HARD_MAX))
    elif len(text) > _CAPTION_SOFT_MAX and len(pitch) > PITCH_MAX:
        short_pitch = _safe_truncate_plain(pitch, limit=PITCH_MAX)
        text = _build_compact_body(
            name=view.name, meta=meta, pitch=short_pitch, facts=facts, tail=tail
        )
    return text


def details_startup_html(view: StartupCardView, *, owner_preview: bool = False) -> str:
    """Expanded investment brief with full founder text (no mid-cut notes)."""
    lines: list[str] = [f"<b>🚀 {escape(view.name)}</b>"]

    if view.sector_labels:
        lines.append(escape(" · ".join(view.sector_labels)))
    if view.stage_label:
        lines.append(escape(view.stage_label))

    lines.append("")
    lines.append("<b>Описание</b>")
    if view.description:
        lines.append(escape(view.description))
    elif view.pitch:
        lines.append(escape(view.pitch))
    else:
        lines.append("Описание пока не заполнено.")

    sections: list[tuple[str, str | None]] = [
        ("Аудитория", view.audience_full),
        ("Команда", view.founder_line),
        ("Прогресс", view.progress_line),
        ("Монетизация", view.monetization_full),
        ("Раунд", view.fundraising_line),
        ("Использование средств", view.use_of_funds_line),
        ("География", view.geography_line),
        ("Что ищет проект", ", ".join(view.intent_labels) if view.intent_labels else None),
        ("Инвестиционный запрос", view.ask_label),
    ]
    for title, value in sections:
        if not value:
            continue
        lines.append("")
        lines.append(f"<b>{escape(title)}</b>")
        lines.append(escape(value))

    lines.append("")
    lines.append("<b>Сайт</b>")
    if view.website:
        lines.append(escape(view.website))
    elif view.no_link_reason:
        lines.append("пока нет")
        lines.append(escape(f"Причина: {view.no_link_reason}"))
    else:
        lines.append("не указан")

    if view.contact_policy_label:
        lines.append("")
        lines.append(
            f"<b>Связь:</b> {escape(view.contact_policy_label)}"
        )

    if owner_preview:
        lines.append("")
        lines.append(
            "Показатели указаны основателем; RAUNDO их независимо не аудировал."
        )
        lines.append("После отправки проект проверяет администрация RAUNDO.")

    return "\n".join(lines).rstrip()


def cover_needs_letterbox(width: int | None, height: int | None) -> bool:
    """True for square/portrait covers that dominate vertical space on phones."""
    if not width or not height or height <= 0:
        return True
    return (width / height) < 1.45


def _pitch_compact(pitch: str, *, limit: int = PITCH_MAX) -> str:
    if len(pitch.strip()) <= limit:
        return pitch.strip()
    return _safe_truncate_plain(pitch, limit=limit)
