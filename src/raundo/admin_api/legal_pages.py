"""Public legal pages (terms, privacy, offer, consents, moderation) and /premium.

The content lives in the repository `legal/` directory as a versioned edition
with a manifest. Pages are rendered to plain HTML with a tiny, safe Markdown
subset; no raw HTML from the files is ever emitted. Nothing here writes to the
database: `/premium` only reads the active catalog.
"""

from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.config import Settings, get_settings
from raundo.db.session import get_session_factory
from raundo.models.billing import PremiumPlanOffer
from raundo.payments_catalog import PLAN_AUDIENCES, PLAN_BENEFITS, PLAN_TITLES
from raundo.premium_catalog import DURATION_TITLES, PLAN_CODES, format_price

router = APIRouter()

LEGAL_DIR = Path(
    os.environ.get("LEGAL_DIST")
    or (Path(__file__).resolve().parents[3] / "legal")
)

DOC_HREF = {
    "terms": "/legal/terms",
    "privacy": "/legal/privacy",
    "offer": "/legal/offer",
    "requisites": "/legal/requisites",
    "payments": "/legal/payments",
    "data-consent": "/legal/data-consent",
    "profile-publication": "/legal/profile-publication",
    "marketing-consent": "/legal/marketing-consent",
    "image-advertising-consent": "/legal/image-advertising-consent",
    "moderation": "/legal/moderation",
    "minors": "/legal/minors",
}

_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def _manifest() -> dict:
    path = LEGAL_DIR / "manifest.json"
    if not path.exists():
        raise HTTPException(status_code=503, detail="Legal documents are not installed.")
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_url(url: str) -> str | None:
    target = url.strip()
    if target.startswith("/") or target.startswith("https://") or target.startswith("http://"):
        if target.startswith("//"):
            return None
        return target
    return None


def _inline(text: str) -> str:
    """Escape, then apply the supported inline Markdown subset."""
    escaped = html.escape(text, quote=False)
    escaped = _INLINE_CODE.sub(lambda m: f"<code>{m.group(1)}</code>", escaped)
    escaped = _BOLD.sub(lambda m: f"<strong>{m.group(1)}</strong>", escaped)
    escaped = _ITALIC.sub(lambda m: f"<em>{m.group(1)}</em>", escaped)
    escaped = _LINK.sub(
        lambda m: (
            f'<a href="{html.escape(_safe_url(m.group(2)) or "#", quote=True)}" rel="noopener">'
            f"{m.group(1)}</a>"
            if _safe_url(m.group(2))
            else m.group(1)
        ),
        escaped,
    )
    return escaped


def render_markdown(text: str) -> str:
    """Render the Markdown subset used by the legal edition."""
    out: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    ordered_items: list[str] = []
    table_rows: list[list[str]] = []

    def flush_paragraph() -> None:
        if paragraph:
            out.append(f"<p>{_inline(' '.join(paragraph))}</p>")
            paragraph.clear()

    def flush_list() -> None:
        if list_items:
            out.append("<ul>" + "".join(f"<li>{_inline(i)}</li>" for i in list_items) + "</ul>")
            list_items.clear()
        if ordered_items:
            out.append("<ol>" + "".join(f"<li>{_inline(i)}</li>" for i in ordered_items) + "</ol>")
            ordered_items.clear()

    def flush_table() -> None:
        if not table_rows:
            return
        rows = [r for r in table_rows if not all(re.fullmatch(r":?-{2,}:?", c.strip() or "-") for c in r)]
        table_rows.clear()
        if not rows:
            return
        head, *body = rows
        out.append("<table><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr></thead><tbody>")
        for row in body:
            out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>")
        out.append("</tbody></table>")

    def flush_all() -> None:
        flush_paragraph()
        flush_list()
        flush_table()

    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            flush_all()
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            flush_paragraph()
            flush_list()
            table_rows.append([c.strip() for c in stripped.strip("|").split("|")])
            continue
        flush_table()
        if stripped.startswith("### "):
            flush_all()
            out.append(f"<h3>{_inline(stripped[4:])}</h3>")
        elif stripped.startswith("## "):
            flush_all()
            out.append(f"<h2>{_inline(stripped[3:])}</h2>")
        elif stripped.startswith("# "):
            flush_all()
            out.append(f"<h1>{_inline(stripped[2:])}</h1>")
        elif stripped in {"---", "***", "___"}:
            flush_all()
            out.append("<hr>")
        elif stripped.startswith("- ") or stripped.startswith("* "):
            flush_paragraph()
            list_items.append(stripped[2:])
        elif re.match(r"^\d+\. ", stripped):
            flush_paragraph()
            ordered_items.append(re.sub(r"^\d+\. ", "", stripped))
        elif stripped.startswith(">"):
            flush_all()
            out.append(f"<blockquote>{_inline(stripped.lstrip('> ').strip())}</blockquote>")
        else:
            flush_list()
            paragraph.append(stripped)
    flush_all()
    return "\n".join(out)


def _shell(title: str, body: str, *, subtitle: str = "") -> str:
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#16171A">
<meta name="robots" content="noindex, nofollow">
<meta name="referrer" content="no-referrer">
<link rel="icon" type="image/png" href="/logo.png">
<title>{html.escape(title)} · RAUNDO</title>
<style>
:root{{--primary:#D7FA48;--bg:#16171A;--surface:#1D1F24;--border:#2D3037;--text:#F2F3F5;--muted:#A3A7B0}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--text);font:16px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;-webkit-font-smoothing:antialiased}}
a{{color:var(--primary);text-underline-offset:2px}}
.page{{max-width:820px;margin:0 auto;padding:28px 20px 72px}}
.top{{display:flex;align-items:center;gap:12px;padding:6px 0 22px}}
.top img{{width:40px;height:40px;border-radius:12px;object-fit:contain}}
.top .name{{font-weight:700;letter-spacing:.08em;font-size:15px}}
.top .back{{margin-left:auto;font-size:13px;color:var(--muted);text-decoration:none}}
.top .back:hover{{color:var(--primary)}}
h1{{font-size:28px;line-height:1.2;margin:8px 0 10px;letter-spacing:-.01em}}
h2{{font-size:20px;line-height:1.3;margin:30px 0 10px}}
h3{{font-size:17px;margin:22px 0 8px}}
p{{margin:10px 0;color:#DCDEE3}}
ul,ol{{margin:10px 0 14px;padding-left:22px;color:#DCDEE3}}
li{{margin:5px 0}}
hr{{border:0;border-top:1px solid var(--border);margin:26px 0}}
code{{background:#22252B;border:1px solid var(--border);border-radius:6px;padding:1px 5px;font-size:13.5px}}
blockquote{{margin:14px 0;padding:10px 14px;border-left:3px solid var(--primary);background:#1B1D22;color:#DCDEE3}}
table{{width:100%;border-collapse:collapse;margin:16px 0;font-size:14.5px}}
th,td{{border:1px solid var(--border);padding:9px 10px;text-align:left;vertical-align:top}}
th{{background:#20232A}}
.meta{{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 18px}}
.chip{{font-size:12.5px;border:1px solid var(--border);border-radius:999px;padding:4px 10px;color:var(--muted)}}
.chip.warn{{border-color:rgba(215,250,72,.45);color:var(--primary);background:rgba(215,250,72,.07)}}
.banner{{border:1px solid rgba(215,250,72,.35);background:rgba(215,250,72,.06);border-radius:14px;padding:13px 15px;color:#E7E9ED;font-size:14px;margin:0 0 22px}}
.card{{display:block;border:1px solid var(--border);background:var(--surface);border-radius:14px;padding:14px 16px;text-decoration:none;color:var(--text);margin:10px 0}}
.card:hover{{border-color:rgba(215,250,72,.5)}}
.card .t{{font-weight:600}}
.card .d{{color:var(--muted);font-size:13px;margin-top:2px}}
.footer{{margin-top:34px;padding-top:18px;border-top:1px solid var(--border);color:var(--muted);font-size:13px;display:flex;flex-wrap:wrap;gap:10px}}
.footer a{{color:var(--muted);text-decoration:none}}
.footer a:hover{{color:var(--primary)}}
.price{{white-space:nowrap;font-weight:600}}
.pending{{color:var(--muted)}}
.note{{font-size:13.5px;color:var(--muted)}}
@media (max-width:420px){{h1{{font-size:23px}}.page{{padding:22px 16px 60px}}}}
</style>
</head>
<body>
<div class="page">
<header class="top">
  <img src="/logo.png" alt="RAUNDO">
  <span class="name">RAUNDO</span>
  <a class="back" href="/legal">Все документы</a>
</header>
{body}
</div>
</body>
</html>"""


def _edition_chips(manifest: dict) -> str:
    status = manifest.get("status", "draft_published")
    status_label = "проект редакции" if status == "draft_published" else status
    chips = [
        f'<span class="chip">Редакция {html.escape(str(manifest.get("edition", "")))}</span>',
        f'<span class="chip">Дата: {html.escape(str(manifest.get("edition_date", "")))}</span>',
        f'<span class="chip warn">{html.escape(status_label)}</span>',
    ]
    if not manifest.get("lawyer_review"):
        chips.append('<span class="chip">Юридическая проверка: не завершена</span>')
    return '<div class="meta">' + "".join(chips) + "</div>"


@router.get("/legal", response_class=HTMLResponse)
async def legal_index() -> HTMLResponse:
    manifest = _manifest()
    docs = manifest.get("docs", [])
    documents = [d for d in docs if d.get("kind") != "consent"]
    consents = [d for d in docs if d.get("kind") == "consent"]

    def cards(items: list[dict]) -> str:
        return "".join(
            f'<a class="card" href="{html.escape(DOC_HREF.get(d["slug"], "/legal"))}">'
            f'<div class="t">{html.escape(d.get("title", d["slug"]))}</div>'
            f'<div class="d">Редакция {html.escape(str(manifest.get("edition", "")))}</div></a>'
            for d in items
        )

    body = (
        "<h1>Документы RAUNDO</h1>"
        + _edition_chips(manifest)
        + f'<div class="banner">{html.escape(manifest.get("status_note", ""))}</div>'
        + "<h2>Документы</h2>"
        + cards(documents)
        + "<h2>Согласия</h2>"
        + cards(consents)
        + '<h2>Тарифы</h2><a class="card" href="/premium"><div class="t">Premium: тарифы и функции</div>'
        + '<div class="d">Каталог планов, сроков и цен</div></a>'
        + '<div class="footer"><a href="/">На главную</a><a href="/legal/requisites">Реквизиты</a>'
        + '<a href="https://t.me/RaundoAppBot">Поддержка в Telegram</a></div>'
    )
    return HTMLResponse(_shell("Документы", body))


@router.get("/legal/{slug}", response_class=HTMLResponse)
async def legal_page(slug: str) -> Response:
    manifest = _manifest()
    doc = next((d for d in manifest.get("docs", []) if d.get("slug") == slug), None)
    if doc is None:
        raise HTTPException(status_code=404, detail="Документ не найден.")
    path = LEGAL_DIR / doc["file"]
    if not path.exists():
        raise HTTPException(status_code=503, detail="Документ не установлен.")
    content = render_markdown(path.read_text(encoding="utf-8"))
    body = (
        _edition_chips(manifest)
        + f'<div class="banner">{html.escape(manifest.get("status_note", ""))}</div>'
        + content
        + '<div class="footer"><a href="/legal">Все документы</a>'
        + '<a href="/legal/requisites">Реквизиты</a>'
        + '<a href="https://t.me/RaundoAppBot">Поддержка</a></div>'
    )
    return HTMLResponse(_shell(doc.get("title", slug), body))


async def _session():  # pragma: no cover - FastAPI dependency
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        finally:
            await session.close()


def _approved_prices() -> dict:
    path = LEGAL_DIR / "prices.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/api/public/premium-plans")
async def premium_plans_json(
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(_session),
) -> JSONResponse:
    """Machine-readable catalog for the landing page; approved prices only."""
    rows = (
        await session.scalars(
            select(PremiumPlanOffer)
            .where(PremiumPlanOffer.is_active.is_(True))
            .order_by(PremiumPlanOffer.plan_code, PremiumPlanOffer.duration_hours)
        )
    ).all()
    prices_doc = _approved_prices()
    approved = prices_doc.get("plans") or {}
    plan_order = {code: index for index, code in enumerate(PLAN_CODES)}
    rows = sorted(rows, key=lambda item: (plan_order.get(item.plan_code, 99), item.duration_hours))

    plans: dict[str, dict] = {}
    for offer in rows:
        item = plans.setdefault(
            offer.plan_code,
            {
                "code": offer.plan_code,
                "title": PLAN_TITLES.get(offer.plan_code, offer.plan_code),
                "audience": PLAN_AUDIENCES.get(offer.plan_code, ""),
                "benefits": list(PLAN_BENEFITS.get(offer.plan_code, ())),
                "durations": [],
            },
        )
        approved_minor = (approved.get(offer.plan_code) or {}).get(offer.duration_code)
        visible = bool(settings.premium_catalog_public and approved_minor == offer.price_minor)
        item["durations"].append(
            {
                "code": offer.duration_code,
                "title": DURATION_TITLES.get(offer.duration_code, offer.duration_code),
                "price_minor": offer.price_minor if visible else None,
                "currency": offer.currency,
                "approved": visible,
            }
        )

    payload = {
        "currency": "RUB",
        "payment_available": False,
        "payments_mode": settings.payments_mode,
        "plans": list(plans.values()),
    }
    response = JSONResponse(payload)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/premium-catalog", response_class=HTMLResponse)
async def premium_page(
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(_session),
) -> HTMLResponse:
    rows = (
        await session.scalars(
            select(PremiumPlanOffer)
            .where(PremiumPlanOffer.is_active.is_(True))
            .order_by(PremiumPlanOffer.plan_code, PremiumPlanOffer.duration_hours)
        )
    ).all()
    # Business order of plans (community, startup, investor, organization).
    plan_order = {code: index for index, code in enumerate(PLAN_CODES)}
    rows = sorted(rows, key=lambda item: (plan_order.get(item.plan_code, 99), item.duration_hours))

    prices_doc = _approved_prices()
    approved = prices_doc.get("plans") or {}
    approval_date = str(prices_doc.get("approved_at") or "")

    table_rows = []
    for offer in rows:
        title = PLAN_TITLES.get(offer.plan_code, offer.plan_code)
        duration = DURATION_TITLES.get(offer.duration_code, offer.duration_code)
        approved_minor = (approved.get(offer.plan_code) or {}).get(offer.duration_code)
        # Publish an amount only when the database offer matches the approved list;
        # an unapproved price change must never appear on the public page.
        if settings.premium_catalog_public and approved_minor == offer.price_minor:
            price = f'<span class="price">{html.escape(format_price(offer.price_minor))}</span>'
        else:
            price = '<span class="pending">на утверждении</span>'
        table_rows.append(
            "<tr>"
            f"<td>{html.escape(title)}</td>"
            f"<td>{html.escape(duration)}</td>"
            f"<td>{price}</td>"
            "</tr>"
        )

    offer_options: list[str] = []
    for offer in rows:
        offer_approved = (approved.get(offer.plan_code) or {}).get(offer.duration_code)
        if settings.premium_catalog_public and offer_approved == offer.price_minor:
            offer_options.append(
                '<option value="'
                + html.escape(f"{offer.plan_code}|{offer.duration_code}", quote=True)
                + '">'
                + html.escape(PLAN_TITLES.get(offer.plan_code, offer.plan_code))
                + " · "
                + html.escape(DURATION_TITLES.get(offer.duration_code, offer.duration_code))
                + " — "
                + html.escape(format_price(offer.price_minor))
                + "</option>"
            )

    benefits = []
    for plan_code, items in PLAN_BENEFITS.items():
        plan_title = PLAN_TITLES.get(plan_code, plan_code)
        lis = "".join(f"<li>{html.escape(item)}</li>" for item in items)
        benefits.append(f"<h3>{html.escape(plan_title)}</h3><ul>{lis}</ul>")

    if settings.premium_catalog_public:
        approved_note = (
            f"Цены утверждены владельцем {html.escape(approval_date)}. "
            if approval_date
            else ""
        )
        banner = (
            f'<div class="banner">{approved_note}Карта и СБП — на этом сайте. '
            "В Telegram Premium оплачивается через Telegram Stars. Доступ активируется "
            "автоматически после подтверждения платежа.</div>"
        )
    else:
        banner = (
            '<div class="banner">Каталог опубликован для согласования: суммы не показываются до '
            "утверждения владельцем. Реальные платежи отключены.</div>"
        )

    buy_js = """
<script>
let raundoAuthed = false;
function onTgAuth(user) {
  fetch('/api/public/site/session', {method:'POST', headers:{'Content-Type':'application/json'},
    credentials:'same-origin', body: JSON.stringify(user)})
    .then(function(r){ return r.json().then(function(d){ return {ok:r.ok, d:d}; }); })
    .then(function(x){
      if (!x.ok) { document.getElementById('auth-state').textContent = 'Не удалось войти: ' + (x.d.detail || 'ошибка'); return; }
      raundoAuthed = true;
      document.getElementById('auth-state').textContent = 'Вы вошли как ' + (x.d.display_name || 'участник RAUNDO');
      document.getElementById('pay-btn').disabled = false;
    })
    .catch(function(){ document.getElementById('auth-state').textContent = 'Не удалось войти через Telegram.'; });
}
document.getElementById('pay-btn').addEventListener('click', function(){
  var btn = this;
  var state = document.getElementById('buy-state');
  var parts = (document.getElementById('offer').value || '').split('|');
  if (!raundoAuthed || parts.length !== 2) { state.textContent = 'Сначала войдите через Telegram.'; return; }
  if (!document.getElementById('offer-check').checked) { state.textContent = 'Подтвердите оферту и условия оплаты.'; return; }
  btn.disabled = true;
  state.textContent = 'Создаём заказ…';
  fetch('/api/public/site/orders', {method:'POST', headers:{'Content-Type':'application/json'},
    credentials:'same-origin', body: JSON.stringify({plan_code: parts[0], duration_code: parts[1], accept_offer: true})})
    .then(function(r){ return r.json().then(function(d){ return {ok:r.ok, d:d}; }); })
    .then(function(x){
      if (!x.ok) { state.textContent = x.d.detail || 'Не удалось создать заказ.'; btn.disabled = false; return; }
      window.location.href = x.d.checkout_url;
    })
    .catch(function(){ state.textContent = 'Ошибка сети. Попробуйте ещё раз.'; btn.disabled = false; });
});
</script>
<script async src="https://telegram.org/js/telegram-widget.js?22"
  data-telegram-login="RaundoAppBot" data-size="large"
  data-onauth="onTgAuth(user)" data-request-access="write"></script>
"""

    buy_html = (
        "<h2>Оплата картой или через СБП</h2>"
        '<div class="buy">'
        '<p class="note">Войдите через Telegram, выберите тариф и срок. Оплата проходит на '
        "защищённой странице ЮKassa. Premium активируется для этого же Telegram-аккаунта "
        "автоматически после подтверждения платежа.</p>"
        '<div id="tg-login"></div>'
        '<p id="auth-state" class="note">Вход через Telegram…</p>'
        '<label for="offer">Тариф и срок</label>'
        '<select id="offer">' + "".join(offer_options) + "</select>"
        '<label class="check"><input type="checkbox" id="offer-check" /> '
        "Я принимаю оферту и условия оплаты</label>"
        '<button id="pay-btn" type="button" disabled>Перейти к оплате</button>'
        '<p id="buy-state" class="note"></p>'
        "</div>" + buy_js
    )

    body = (
        "<h1>RAUNDO Premium</h1>"
        '<p class="note">Плата за функциональность платформы. RAUNDO не принимает инвестиции '
        "и не является инвестиционным посредником.</p>"
        + banner
        + "<h2>Тарифы</h2>"
        + '<table><thead><tr><th>План</th><th>Срок</th><th>Стоимость</th></tr></thead><tbody>'
        + "".join(table_rows)
        + "</tbody></table>"
        + '<p class="note">Срок отсчитывается от активации. Автопродление не подключено: '
        "покупка разовая, продление — по желанию пользователя. Условия продления и возврата — "
        'в документе «Оплата и возвраты».</p>'
        + "<h2>Как получить Premium</h2>"
        + "<ol>"
        + "<li>Выберите тариф и срок в боте RAUNDO.</li>"
        + "<li>Подтвердите условия и оплатите доступным способом.</li>"
        + "<li>После подтверждённого платежа возможности активируются в вашем аккаунте.</li>"
        + "</ol>"
        + '<p class="note">В Telegram цифровые подписки продаются через Telegram Stars. '
        "Карта и СБП доступны на этом сайте как самостоятельный веб-канал.</p>"
        + "<h2>Что входит</h2>"
        + '<p class="note">Работающие сейчас возможности отмечены как действующие; то, что '
        "появится позже, прямо помечено в списке.</p>"
        + "".join(benefits)
        + buy_html
        + '<div class="footer"><a href="/legal/offer">Оферта</a>'
        + '<a href="/legal/payments">Оплата и возвраты</a>'
        + '<a href="/legal/terms">Соглашение</a>'
        + '<a href="https://t.me/RaundoAppBot">Открыть бота RAUNDO</a></div>'
    )
    return HTMLResponse(_shell("Premium", body))

