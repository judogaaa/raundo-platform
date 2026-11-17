"""FastAPI application for RAUNDO Admin. Not embedded in the Telegram poller."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response as BytesResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from raundo import ui
from raundo.admin_api.security import hash_password, hash_token, new_token, verify_password
from raundo.config import get_settings
from raundo.db.session import get_session_factory
from raundo.domain import REVIEW_APPROVED, REVIEW_SUBMITTED
from raundo.models.admin_panel import (
    AdminAccount,
    AdminAuditLog,
    AdminInvite,
    AdminSession,
    Complaint,
    OutboxDeliveryMeta,
    SupportMessage,
    SupportTicket,
)
from raundo.models.billing import PremiumSubscription
from raundo.models.investor import InvestorProfile, InvestorReview
from raundo.models.organization import FundingOrganization, OrganizationMembership
from raundo.models.startup import Startup, StartupReview
from raundo.models.system import OutboxEvent
from raundo.models.user import User
from raundo.services.admin_directory import (
    DirectoryError,
    restrict_user,
    unrestrict_user,
)
from raundo.services.admin_directory import (
    list_users as directory_users,
)
from raundo.services.admin_directory import (
    user_detail as directory_user_detail,
)
from raundo.services.complaints import (
    PRIORITIES,
    ComplaintError,
)
from raundo.services.complaints import (
    TARGET_TYPES as COMPLAINT_TARGET_TYPES,
)
from raundo.services.complaints import (
    apply_status as apply_complaint_status,
)
from raundo.services.complaints import (
    complaint_detail as complaint_detail_view,
)
from raundo.services.complaints import (
    list_complaints as complaints_list,
)
from raundo.services.media_proxy import (
    SupportMediaError,
    fetch_investor_photo,
    fetch_startup_photo,
    fetch_support_attachment,
)
from raundo.services.moderation_decisions import (
    approve_investor,
    approve_startup,
    list_open_investor_reviews,
    list_open_startup_reviews,
    reject_investor,
    reject_startup,
    request_investor_changes,
    request_startup_changes,
    revision_diff,
)
from raundo.services.moderation_views import investor_review_detail, startup_review_detail
from raundo.services.organization_review import (
    OrganizationReviewError,
    list_organization_queue,
    organization_detail,
    review_membership,
    review_organization,
)
from raundo.services.review_guards import ReviewDecisionError
from raundo.services.support_tickets import (
    ALLOWED_TRANSITIONS,
    CATEGORIES,
    REOPENABLE_STATUSES,
    REPLY_OUTCOMES,
    TICKET_STATUSES,
    SupportError,
    delivery_state,
    set_ticket_status,
    staff_reply,
)

COOKIE = "raundo_admin"
_LOGIN_FAILS: dict[str, list[float]] = {}
_MFA_FAILS: dict[str, list[float]] = {}
_FAILURE_WINDOW_SECONDS = 600
_MAX_FAILURE_BUCKETS = 2048
_MFA_EXEMPT_PATHS = {
    "/auth/me",
    "/auth/logout",
    "/auth/mfa/verify-login",
    "/health",
}
_PASSWORD_EXEMPT_PATHS = {
    "/auth/me",
    "/auth/logout",
    "/auth/password",
    "/health",
}
ROLES_MOD = {"owner", "administrator", "moderator"}


def _role_guard(account: AdminAccount, roles: set[str]) -> tuple[bool, ...]:
    """SQL-neutral role gate: false predicate for roles outside the set."""
    return (True,) if account.role in roles else (False,)
ROLES_SUPPORT = {"owner", "administrator", "support"}
ROLES_OWNER = {"owner"}

app = FastAPI(title="RAUNDO Admin", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:3000", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def persist_session(session: AsyncSession) -> None:
    """Commit before response so the next request sees writes (login, moderation, support)."""
    await session.flush()
    await session.commit()


async def db_session():
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def current_admin(request: Request, session: AsyncSession = Depends(db_session)) -> AdminAccount:
    raw = request.cookies.get(COOKIE)
    if not raw:
        raise HTTPException(status_code=401, detail="Требуется вход.")
    row = await session.execute(
        select(AdminSession, AdminAccount)
        .join(AdminAccount, AdminAccount.id == AdminSession.admin_id)
        .where(AdminSession.token_hash == hash_token(raw), AdminSession.revoked_at.is_(None))
    )
    found = row.first()
    if found is None:
        raise HTTPException(status_code=401, detail="Сессия недействительна.")
    sess, account = found
    if sess.expires_at < datetime.now(timezone.utc) or account.disabled_at is not None:
        raise HTTPException(status_code=401, detail="Сессия истекла.")
    if account.mfa_enabled and sess.mfa_verified_at is None:
        path = request.url.path
        if path not in _MFA_EXEMPT_PATHS:
            raise HTTPException(status_code=403, detail="Требуется подтверждение MFA.")
    if account.must_change_password and request.url.path not in _PASSWORD_EXEMPT_PATHS:
        raise HTTPException(status_code=403, detail="Сначала смените временный пароль.")
    csrf = request.headers.get("x-csrf-token")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and csrf != sess.csrf_token:
        raise HTTPException(status_code=403, detail="CSRF.")
    request.state.csrf = sess.csrf_token
    request.state.admin_session = sess
    return account


def require_roles(account: AdminAccount, allowed: set[str]) -> None:
    if account.role not in allowed:
        raise HTTPException(status_code=403, detail="Недостаточно прав.")


class LoginBody(BaseModel):
    login: str
    password: str


class ReasonBody(BaseModel):
    comment: str = Field(min_length=8, max_length=2000)


class ReplyBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    internal: bool = False
    # "answer" sends the answer and waits for the user's confirmation in
    # Telegram; "clarify" asks a specific question (waiting for the user's info).
    outcome: str | None = None


class InviteBody(BaseModel):
    login: str = Field(min_length=3, max_length=64)
    role: str


class AcceptInviteBody(BaseModel):
    token: str
    password: str = Field(min_length=10, max_length=128)


class StatusBody(BaseModel):
    status: str


class PasswordBody(BaseModel):
    password: str = Field(min_length=10, max_length=128)
    current_password: str | None = Field(default=None, max_length=128)


class MfaBody(BaseModel):
    code: str = Field(min_length=6, max_length=8)


class RoleBody(BaseModel):
    role: str


class OrgReviewBody(BaseModel):
    decision: str
    reason: str | None = Field(default=None, max_length=2000)
    official_name: str | None = Field(default=None, max_length=256)


class MembershipReviewBody(BaseModel):
    decision: str
    reason: str | None = Field(default=None, max_length=2000)
    mandate: bool | None = None


class ComplaintBody(BaseModel):
    status: str
    comment: str | None = Field(default=None, max_length=2000)
    restrict_target: bool = False


class RestrictionBody(BaseModel):
    reason: str = Field(min_length=8, max_length=2000)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


def _recent_failures(buckets: dict[str, list[float]], key: str) -> list[float]:
    """Bound short-lived in-memory login/MFA counters, even for bogus usernames.

    The limiter is intentionally local to this process. A distributed deployment
    still needs a shared rate limiter in the reverse proxy or Redis.
    """
    import time

    now = time.time()
    window = [t for t in buckets.get(key, []) if now - t < _FAILURE_WINDOW_SECONDS]
    if window:
        buckets[key] = window
    else:
        buckets.pop(key, None)

    if key not in buckets and len(buckets) >= _MAX_FAILURE_BUCKETS:
        for name, times in list(buckets.items()):
            if not times or times[-1] <= now - _FAILURE_WINDOW_SECONDS:
                buckets.pop(name, None)
        while len(buckets) >= _MAX_FAILURE_BUCKETS:
            buckets.pop(next(iter(buckets)))
    return window


def _rate_ok(login_name: str) -> bool:
    return len(_recent_failures(_LOGIN_FAILS, login_name)) < 8


@app.post("/auth/login")
async def login(body: LoginBody, response: Response, session: AsyncSession = Depends(db_session)):
    key = body.login.strip().lower()
    if not _rate_ok(key):
        raise HTTPException(status_code=429, detail="Слишком много попыток. Подождите и повторите.")
    result = await session.execute(select(AdminAccount).where(AdminAccount.login == key))
    account = result.scalar_one_or_none()
    if account is None or account.disabled_at is not None or not verify_password(body.password, account.password_hash):
        import time

        _LOGIN_FAILS.setdefault(key, []).append(time.time())
        raise HTTPException(status_code=401, detail="Неверный логин или пароль.")
    _LOGIN_FAILS.pop(key, None)
    now = datetime.now(timezone.utc)
    open_sessions = await session.execute(
        select(AdminSession).where(
            AdminSession.admin_id == account.id,
            AdminSession.revoked_at.is_(None),
        )
    )
    for old in open_sessions.scalars():
        old.revoked_at = now
    token = new_token()
    csrf = new_token()
    mfa_verified = now if not account.mfa_enabled else None
    session.add(
        AdminSession(
            id=uuid.uuid4(),
            admin_id=account.id,
            token_hash=hash_token(token),
            csrf_token=csrf,
            expires_at=now + timedelta(hours=12),
            mfa_verified_at=mfa_verified,
        )
    )
    await persist_session(session)
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        samesite="lax",
        path="/",
        secure=bool(get_settings().admin_cookie_secure),
        max_age=12 * 3600,
    )
    return {
        "role": account.role,
        "login": account.login,
        "csrf": csrf,
        "must_change_password": bool(getattr(account, "must_change_password", False)),
        "mfa_required": bool(getattr(account, "mfa_enabled", False)),
    }


@app.post("/auth/logout")
async def logout(
    request: Request,
    response: Response,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    raw = request.cookies.get(COOKIE)
    if raw:
        row = await session.execute(select(AdminSession).where(AdminSession.token_hash == hash_token(raw)))
        sess = row.scalar_one_or_none()
        if sess is not None:
            sess.revoked_at = datetime.now(timezone.utc)
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True, "login": account.login}


@app.get("/auth/me")
async def me(request: Request, account: AdminAccount = Depends(current_admin)):
    csrf = getattr(request.state, "csrf", None)
    sess: AdminSession | None = getattr(request.state, "admin_session", None)
    mfa_pending = bool(account.mfa_enabled and sess is not None and sess.mfa_verified_at is None)
    return {
        "login": account.login,
        "role": account.role,
        "csrf": csrf,
        "must_change_password": account.must_change_password,
        "mfa_enabled": account.mfa_enabled,
        "mfa_pending": mfa_pending,
        "mfa_setup_pending": bool(account.totp_secret and not account.mfa_enabled),
    }


def _mfa_rate_ok(session_id: str) -> bool:
    return len(_recent_failures(_MFA_FAILS, session_id)) < 10


def _mfa_note_failure(session_id: str) -> None:
    import time

    _MFA_FAILS.setdefault(session_id, []).append(time.time())


@app.post("/auth/mfa/verify-login")
async def mfa_verify_login(
    body: MfaBody,
    request: Request,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    sess: AdminSession = request.state.admin_session
    if not account.mfa_enabled or sess.mfa_verified_at is not None:
        raise HTTPException(status_code=400, detail="MFA не требуется.")
    key = str(sess.id)
    if not _mfa_rate_ok(key):
        raise HTTPException(status_code=429, detail="Слишком много попыток MFA.")
    from raundo.services.admin_auth import verify_mfa_code

    if not verify_mfa_code(account, body.code):
        import time

        _MFA_FAILS.setdefault(key, []).append(time.time())
        raise HTTPException(status_code=401, detail="Неверный код.")
    _MFA_FAILS.pop(key, None)
    sess.mfa_verified_at = datetime.now(timezone.utc)
    await persist_session(session)
    return {"ok": True}


@app.get("/auth/sessions")
async def list_sessions(
    request: Request,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    current: AdminSession = request.state.admin_session
    rows = await session.execute(
        select(AdminSession)
        .where(
            AdminSession.admin_id == account.id,
            AdminSession.revoked_at.is_(None),
            AdminSession.expires_at > datetime.now(timezone.utc),
        )
        .order_by(AdminSession.expires_at.desc())
    )
    return {
        "items": [
            {
                "id": str(s.id),
                "expires_at": s.expires_at.isoformat(),
                "current": s.id == current.id,
                "mfa_verified": s.mfa_verified_at is not None,
            }
            for s in rows.scalars()
        ]
    }


@app.post("/auth/sessions/revoke-all")
async def revoke_all_sessions(
    request: Request,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    current: AdminSession = request.state.admin_session
    now = datetime.now(timezone.utc)
    rows = await session.execute(
        select(AdminSession).where(
            AdminSession.admin_id == account.id,
            AdminSession.revoked_at.is_(None),
            AdminSession.id != current.id,
        )
    )
    count = 0
    for s in rows.scalars():
        s.revoked_at = now
        count += 1
    await session.flush()
    await session.commit()
    return {"ok": True, "revoked": count}


@app.post("/auth/sessions/{session_id}/revoke")
async def revoke_session(
    session_id: uuid.UUID,
    request: Request,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    current: AdminSession = request.state.admin_session
    target = await session.get(AdminSession, session_id)
    if target is None or target.admin_id != account.id:
        raise HTTPException(status_code=404, detail="Сессия не найдена.")
    if target.id == current.id:
        raise HTTPException(
            status_code=400, detail="Текущую сессию завершите через выход из панели."
        )
    if target.revoked_at is None:
        target.revoked_at = datetime.now(timezone.utc)
    await persist_session(session)
    return {"ok": True}


@app.get("/media/startups/{startup_id}/photos/{position}")
async def startup_photo(
    startup_id: uuid.UUID,
    position: int,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    require_roles(account, ROLES_MOD)
    try:
        data, content_type = await fetch_startup_photo(
            session, startup_id, position, allow_deleted=True
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Медиа не найдено.") from None
    return BytesResponse(content=data, media_type=content_type)


@app.get("/media/investors/{investor_id}/photo")
async def investor_photo(
    investor_id: uuid.UUID,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    require_roles(account, ROLES_MOD)
    try:
        data, content_type = await fetch_investor_photo(session, investor_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Медиа не найдено.") from None
    return BytesResponse(content=data, media_type=content_type)


@app.get("/support/messages/{message_id}/attachment")
async def support_attachment(
    message_id: uuid.UUID,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    """Staff-only attachment bytes; Telegram file_id never leaves the server."""
    require_roles(account, ROLES_SUPPORT)
    try:
        data, content_type = await fetch_support_attachment(session, message_id)
    except SupportMediaError as exc:
        if exc.code == "missing":
            raise HTTPException(
                status_code=404, detail="Вложение не сохранено или удалено."
            ) from None
        if exc.code == "no_token":
            raise HTTPException(
                status_code=503, detail="Хранилище вложений временно недоступно."
            ) from None
        raise HTTPException(
            status_code=502, detail="Telegram не отдал файл. Повторите позже."
        ) from None
    return BytesResponse(content=data, media_type=content_type)


@app.get("/overview")
async def overview(account: AdminAccount = Depends(current_admin), session: AsyncSession = Depends(db_session)):
    users = await session.scalar(select(func.count()).select_from(User))
    startups = await session.scalar(
        select(func.count()).select_from(Startup).where(Startup.review_state == REVIEW_APPROVED, Startup.deleted_at.is_(None))
    )
    investors = await session.scalar(
        select(func.count()).select_from(InvestorProfile).where(
            InvestorProfile.review_status == REVIEW_APPROVED, InvestorProfile.deleted_at.is_(None)
        )
    )
    pending = await session.scalar(
        select(func.count()).select_from(Startup).where(Startup.review_state == REVIEW_SUBMITTED)
    )
    pending_investors = await session.scalar(
        select(func.count()).select_from(InvestorProfile).where(
            InvestorProfile.review_status == REVIEW_SUBMITTED, InvestorProfile.deleted_at.is_(None)
        )
    )
    pending_orgs = await session.scalar(
        select(func.count()).select_from(FundingOrganization).where(
            FundingOrganization.review_status.in_(["draft", "submitted", "under_review"])
        )
    )
    pending_representatives = await session.scalar(
        select(func.count()).select_from(OrganizationMembership).where(
            OrganizationMembership.status.in_(["claimed", "under_review"])
        )
    )
    open_tickets = await session.scalar(
        select(func.count())
        .select_from(SupportTicket)
        .where(
            SupportTicket.status.in_(
                ["new", "in_progress", "waiting_user", "awaiting_confirmation", "needs_help"]
            )
        )
    )
    tickets_awaiting_confirmation = await session.scalar(
        select(func.count())
        .select_from(SupportTicket)
        .where(SupportTicket.status == "awaiting_confirmation")
    )
    tickets_needs_help = await session.scalar(
        select(func.count())
        .select_from(SupportTicket)
        .where(SupportTicket.status == "needs_help")
    )
    tickets_awaiting_staff = await session.scalar(
        select(func.count())
        .select_from(SupportTicket)
        .where(SupportTicket.status.in_(["new", "in_progress"]))
    )
    tickets_awaiting_user = await session.scalar(
        select(func.count())
        .select_from(SupportTicket)
        .where(SupportTicket.status == "waiting_user")
    )
    tickets_resolved = await session.scalar(
        select(func.count())
        .select_from(SupportTicket)
        .where(SupportTicket.status == "resolved")
    )
    complaints = await session.scalar(
        select(func.count()).select_from(Complaint).where(Complaint.status == "open")
    )
    premium = await session.scalar(
        select(func.count()).select_from(PremiumSubscription).where(PremiumSubscription.status == "active")
    )
    return {
        "users": int(users or 0),
        "published_startups": int(startups or 0),
        "verified_investors": int(investors or 0),
        "pending_startups": int(pending or 0),
        "pending_investors": int(pending_investors or 0),
        "pending_organizations": int(pending_orgs or 0),
        "pending_representatives": int(pending_representatives or 0),
        "open_tickets": int(open_tickets or 0),
        "tickets_awaiting_staff": int(tickets_awaiting_staff or 0),
        "tickets_awaiting_user": int(tickets_awaiting_user or 0),
        "tickets_awaiting_confirmation": int(tickets_awaiting_confirmation or 0),
        "tickets_needs_help": int(tickets_needs_help or 0),
        "tickets_resolved": int(tickets_resolved or 0),
        "open_complaints": int(complaints or 0),
        "active_premium": int(premium or 0),
        "role": account.role,
    }


@app.get("/search")
async def search(
    q: str,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    needle = q.strip()
    if len(needle) < 2:
        return {"items": []}
    like = f"%{needle}%"
    items: list[dict] = []
    open_states = (REVIEW_SUBMITTED, "under_review")
    # Only moderation roles may see review hits: support would land on a
    # section the shell refuses to open.
    startups = await session.execute(
        select(Startup, StartupReview)
        .join(StartupReview, StartupReview.startup_id == Startup.id)
        .where(
            Startup.name.ilike(like),
            Startup.deleted_at.is_(None),
            StartupReview.state.in_(open_states),
            *_role_guard(account, ROLES_MOD),
        )
        .limit(6)
    )
    for row, review in startups.all():
        items.append(
            {
                "type": "startup",
                "id": str(review.id),
                "title": row.name,
                "subtitle": f"Стартап · {ui.status(row.review_state)} · версия {review.revision}",
                "section": "moderation",
            }
        )
    investors = await session.execute(
        select(InvestorProfile, InvestorReview)
        .join(InvestorReview, InvestorReview.investor_id == InvestorProfile.id)
        .where(
            InvestorProfile.display_name.ilike(like),
            InvestorProfile.deleted_at.is_(None),
            InvestorReview.state.in_(open_states),
            *_role_guard(account, ROLES_MOD),
        )
        .limit(6)
    )
    for row, review in investors.all():
        items.append(
            {
                "type": "investor",
                "id": str(review.id),
                "title": row.display_name or "Инвестор",
                "subtitle": f"Инвестор · {ui.status(row.review_status)} · версия {review.revision}",
                "section": "moderation",
            }
        )
    users = await session.execute(
        select(User).where(or_(User.username.ilike(like), User.first_name.ilike(like))).limit(6)
    )
    for row in users.scalars():
        items.append(
            {
                "type": "user",
                "id": str(row.id),
                "title": row.username or row.first_name or str(row.telegram_id),
                "subtitle": f"Пользователь · {row.telegram_id}",
                "section": "users",
            }
        )
    orgs = await session.execute(
        select(FundingOrganization).where(FundingOrganization.name.ilike(like)).limit(6)
    )
    for row in orgs.scalars():
        items.append(
            {
                "type": "organization",
                "id": str(row.id),
                "title": row.name,
                "subtitle": f"Организация · {ui.status(row.review_status)}",
                "section": "orgs",
            }
        )
    if account.role in ROLES_SUPPORT:
        tickets = await session.execute(
            select(SupportTicket)
            .where(or_(SupportTicket.public_number.ilike(like), SupportTicket.subject.ilike(like)))
            .limit(6)
        )
        for row in tickets.scalars():
            items.append(
                {
                    "type": "ticket",
                    "id": str(row.id),
                    "title": row.public_number,
                    "subtitle": f"Обращение · {row.subject}",
                    "section": "support",
                }
            )
    return {"items": items}


@app.get("/moderation/queue")
async def moderation_queue(
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    require_roles(account, ROLES_MOD)
    startups = await list_open_startup_reviews(session)
    investors = await list_open_investor_reviews(session)
    return {"items": startups + investors}


@app.post("/moderation/startups/{review_id}/approve")
async def moderation_approve(
    review_id: uuid.UUID,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    require_roles(account, ROLES_MOD)
    try:
        startup = await approve_startup(session, review_id=review_id, actor_id=account.id)
    except ReviewDecisionError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    await persist_session(session)
    return {"ok": True, "startup_id": str(startup.id), "state": startup.review_state}


@app.post("/moderation/startups/{review_id}/changes")
async def moderation_changes(
    review_id: uuid.UUID,
    body: ReasonBody,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    require_roles(account, ROLES_MOD)
    try:
        startup = await request_startup_changes(
            session, review_id=review_id, actor_id=account.id, comment=body.comment
        )
    except ReviewDecisionError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    await persist_session(session)
    return {"ok": True, "state": startup.review_state}


@app.post("/moderation/startups/{review_id}/reject")
async def moderation_reject(
    review_id: uuid.UUID,
    body: ReasonBody,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    require_roles(account, ROLES_MOD)
    try:
        startup = await reject_startup(session, review_id=review_id, actor_id=account.id, reason=body.comment)
    except ReviewDecisionError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    await persist_session(session)
    return {"ok": True, "state": startup.review_state}


@app.post("/moderation/investors/{review_id}/approve")
async def moderation_investor_approve(
    review_id: uuid.UUID,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    require_roles(account, ROLES_MOD)
    try:
        profile = await approve_investor(session, review_id=review_id, actor_id=account.id)
    except ReviewDecisionError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    await persist_session(session)
    return {"ok": True, "state": profile.review_status}


@app.post("/moderation/investors/{review_id}/reject")
async def moderation_investor_reject(
    review_id: uuid.UUID,
    body: ReasonBody,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    require_roles(account, ROLES_MOD)
    try:
        profile = await reject_investor(
            session, review_id=review_id, actor_id=account.id, reason=body.comment
        )
    except ReviewDecisionError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    await persist_session(session)
    return {"ok": True, "state": profile.review_status}


@app.post("/moderation/investors/{review_id}/changes")
async def moderation_investor_changes(
    review_id: uuid.UUID,
    body: ReasonBody,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    require_roles(account, ROLES_MOD)
    try:
        profile = await request_investor_changes(
            session, review_id=review_id, actor_id=account.id, comment=body.comment
        )
    except ReviewDecisionError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    await persist_session(session)
    return {"ok": True, "state": profile.review_status}


@app.get("/moderation/startups/reviews/{review_id}")
async def startup_review_view(
    review_id: uuid.UUID,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    require_roles(account, ROLES_MOD)
    try:
        return await startup_review_detail(session, review_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Заявка не найдена.") from None


@app.get("/moderation/investors/reviews/{review_id}")
async def investor_review_view(
    review_id: uuid.UUID,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    require_roles(account, ROLES_MOD)
    try:
        return await investor_review_detail(session, review_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Заявка не найдена.") from None


@app.get("/moderation/startups/{startup_id}/diff")
async def startup_diff(
    startup_id: uuid.UUID,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    require_roles(account, ROLES_MOD)
    startup = await session.get(Startup, startup_id)
    if startup is None:
        raise HTTPException(status_code=404, detail="Анкета не найдена.")
    current = {"name": startup.name, "pitch": startup.one_liner, "state": startup.review_state}
    return {"changes": revision_diff(startup.published_snapshot, current)}


@app.get("/support/tickets")
async def tickets(
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
    status: str | None = None,
):
    require_roles(account, ROLES_SUPPORT)
    stmt = select(SupportTicket).order_by(SupportTicket.updated_at.desc()).limit(100)
    if status:
        if status not in TICKET_STATUSES:
            raise HTTPException(status_code=400, detail="Недопустимый статус.")
        stmt = stmt.where(SupportTicket.status == status)
    rows = await session.execute(stmt)
    return {
        "items": [
            {
                "id": str(t.id),
                "number": t.public_number,
                "subject": t.subject,
                "status": t.status,
                "category": t.category,
                "category_label": CATEGORIES.get(t.category or "", None),
                "priority": t.priority,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
                "resolved_at": t.resolved_at.isoformat() if t.resolved_at else None,
                "confirmed_at": t.confirmed_at.isoformat() if t.confirmed_at else None,
                "disputed_at": t.disputed_at.isoformat() if t.disputed_at else None,
            }
            for t in rows.scalars()
        ],
        "categories": CATEGORIES,
    }


_SUPPORT_ATTACHMENT_SUFFIX = re.compile(r"\n\[вложение: [^\]\n]{1,255}\]$")


def _support_display_body(message: SupportMessage) -> str:
    """Hide the system-generated attachment marker; never alter stored text."""
    body = message.body or ""
    if message.attachment_kind:
        body = _SUPPORT_ATTACHMENT_SUFFIX.sub("", body)
    return body


@app.get("/support/tickets/{ticket_id}")
async def ticket_detail(
    ticket_id: uuid.UUID,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    require_roles(account, ROLES_SUPPORT)
    ticket = await session.get(SupportTicket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Обращение не найдено.")
    msgs = await session.execute(
        select(SupportMessage)
        .where(SupportMessage.ticket_id == ticket.id)
        .order_by(SupportMessage.created_at)
    )
    messages = list(msgs.scalars())
    event_ids = [m.outbox_event_id for m in messages if m.outbox_event_id]
    events: dict[uuid.UUID, OutboxEvent] = {}
    if event_ids:
        rows = await session.execute(select(OutboxEvent).where(OutboxEvent.id.in_(event_ids)))
        events = {e.id: e for e in rows.scalars()}
    staff_ids = {m.staff_id for m in messages if m.staff_id}
    staff_logins: dict[uuid.UUID, str] = {}
    if staff_ids:
        rows = await session.execute(
            select(AdminAccount.id, AdminAccount.login).where(AdminAccount.id.in_(staff_ids))
        )
        staff_logins = {row[0]: row[1] for row in rows.all()}
    resolved_by = None
    if ticket.resolved_by_id:
        actor = await session.get(AdminAccount, ticket.resolved_by_id)
        resolved_by = actor.login if actor else None
    confirmed_by = None
    if ticket.confirmed_by_user_id:
        confirmer = await session.get(User, ticket.confirmed_by_user_id)
        if confirmer is not None:
            name = " ".join(
                part for part in (confirmer.first_name, confirmer.last_name) if part
            )
            confirmed_by = f"{name} (@{confirmer.username})" if confirmer.username else name
    return {
        "id": str(ticket.id),
        "number": ticket.public_number,
        "subject": ticket.subject,
        "status": ticket.status,
        "category": ticket.category,
        "category_label": CATEGORIES.get(ticket.category or "", None),
        "priority": ticket.priority,
        "created_at": ticket.created_at.isoformat() if ticket.created_at else None,
        "updated_at": ticket.updated_at.isoformat() if ticket.updated_at else None,
        "resolved_at": ticket.resolved_at.isoformat() if ticket.resolved_at else None,
        "resolved_by": resolved_by,
        "confirmed_at": ticket.confirmed_at.isoformat() if ticket.confirmed_at else None,
        "confirmed_by": confirmed_by,
        "disputed_at": ticket.disputed_at.isoformat() if ticket.disputed_at else None,
        "confirmation_round": int(ticket.confirmation_round or 0),
        "reopenable": ticket.status in REOPENABLE_STATUSES,
        "allowed_statuses": sorted(ALLOWED_TRANSITIONS.get(ticket.status, frozenset())),
        "messages": [
            {
                "id": str(m.id),
                "kind": m.author_kind,
                "author": staff_logins.get(m.staff_id) if m.staff_id else None,
                "body": _support_display_body(m),
                "internal": m.author_kind == "note",
                "attachment": {
                    "kind": m.attachment_kind,
                    "name": m.attachment_name,
                    "size": m.attachment_size,
                    "available": bool(m.attachment_file_id),
                    "url": f"/api/support/messages/{m.id}/attachment"
                    if m.attachment_file_id
                    else None,
                }
                if m.attachment_kind
                else None,
                "delivery": delivery_state(
                    m,
                    events.get(m.outbox_event_id) if m.outbox_event_id else None,
                ),
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ],
    }


@app.post("/support/tickets/{ticket_id}/reply")
async def ticket_reply(
    ticket_id: uuid.UUID,
    body: ReplyBody,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    require_roles(account, ROLES_SUPPORT)
    # Serialized per ticket: two staff members cannot interleave answers.
    ticket = await session.get(SupportTicket, ticket_id, with_for_update=True)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Обращение не найдено.")
    if body.internal:
        outcome = "answer"  # ignored by the service for notes
    else:
        outcome = body.outcome or "answer"
        if outcome in {"resolve", "waiting_user"}:
            outcome = {"resolve": "answer", "waiting_user": "clarify"}[outcome]
        if outcome not in REPLY_OUTCOMES:
            raise HTTPException(status_code=400, detail="Неизвестный итог ответа.")
    before = ticket.status
    try:
        message, event = await staff_reply(
            session,
            ticket=ticket,
            staff_id=account.id,
            body=body.text,
            internal=body.internal,
            outcome=outcome,
        )
    except SupportError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    session.add(
        AdminAuditLog(
            id=uuid.uuid4(),
            actor_id=account.id,
            action="ticket_reply",
            resource_type="ticket",
            resource_id=str(ticket.id),
            before_state={"status": before},
            after_state={
                "status": ticket.status,
                "internal": body.internal,
                "delivery": delivery_state(message, event),
            },
            reason=None if body.internal else outcome,
        )
    )
    await persist_session(session)
    resolved_by = None
    if ticket.resolved_by_id:
        actor = await session.get(AdminAccount, ticket.resolved_by_id)
        resolved_by = actor.login if actor else None
    return {
        "ok": True,
        "internal": body.internal,
        "message_id": str(message.id),
        "delivery": delivery_state(message, event),
        "status": ticket.status,
        "confirmation_round": int(ticket.confirmation_round or 0),
        "resolved_at": ticket.resolved_at.isoformat() if ticket.resolved_at else None,
        "resolved_by": resolved_by,
        "confirmed_at": ticket.confirmed_at.isoformat() if ticket.confirmed_at else None,
    }


@app.patch("/support/tickets/{ticket_id}/status")
async def ticket_status(
    ticket_id: uuid.UUID,
    body: StatusBody,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    require_roles(account, ROLES_SUPPORT)
    ticket = await session.get(SupportTicket, ticket_id, with_for_update=True)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Обращение не найдено.")
    before = ticket.status
    try:
        set_ticket_status(ticket, body.status, actor_id=account.id)
    except SupportError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    session.add(
        AdminAuditLog(
            id=uuid.uuid4(),
            actor_id=account.id,
            action="ticket_status",
            resource_type="ticket",
            resource_id=str(ticket.id),
            before_state={"status": before},
            after_state={"status": ticket.status},
            reason=None,
        )
    )
    await persist_session(session)
    resolved_by = None
    if ticket.resolved_by_id:
        actor = await session.get(AdminAccount, ticket.resolved_by_id)
        resolved_by = actor.login if actor else None
    return {
        "ok": True,
        "status": ticket.status,
        "resolved_at": ticket.resolved_at.isoformat() if ticket.resolved_at else None,
        "resolved_by": resolved_by,
    }


@app.get("/analytics")
async def analytics(account: AdminAccount = Depends(current_admin), session: AsyncSession = Depends(db_session)):
    users = await session.scalar(select(func.count()).select_from(User))
    startups_total = await session.scalar(select(func.count()).select_from(Startup))
    approved = await session.scalar(
        select(func.count()).select_from(Startup).where(Startup.review_state == REVIEW_APPROVED)
    )
    pending = await session.scalar(
        select(func.count()).select_from(Startup).where(Startup.review_state == REVIEW_SUBMITTED)
    )
    changes = await session.scalar(
        select(func.count()).select_from(Startup).where(Startup.review_state == "changes_requested")
    )
    rejected = await session.scalar(
        select(func.count()).select_from(Startup).where(Startup.review_state == "rejected")
    )
    investors_approved = await session.scalar(
        select(func.count()).select_from(InvestorProfile).where(
            InvestorProfile.review_status == REVIEW_APPROVED,
            InvestorProfile.deleted_at.is_(None),
        )
    )
    investors_pending = await session.scalar(
        select(func.count()).select_from(InvestorProfile).where(
            InvestorProfile.review_status == REVIEW_SUBMITTED,
            InvestorProfile.deleted_at.is_(None),
        )
    )
    orgs_total = await session.scalar(select(func.count()).select_from(FundingOrganization))
    orgs_approved = await session.scalar(
        select(func.count()).select_from(FundingOrganization).where(
            FundingOrganization.review_status == REVIEW_APPROVED
        )
    )
    tickets = await session.scalar(
        select(func.count())
        .select_from(SupportTicket)
        .where(
            SupportTicket.status.in_(
                ["new", "in_progress", "waiting_user", "awaiting_confirmation", "needs_help"]
            )
        )
    )
    tickets_awaiting_confirmation = await session.scalar(
        select(func.count())
        .select_from(SupportTicket)
        .where(SupportTicket.status == "awaiting_confirmation")
    )
    tickets_awaiting_staff = await session.scalar(
        select(func.count())
        .select_from(SupportTicket)
        .where(SupportTicket.status.in_(["new", "in_progress"]))
    )
    tickets_resolved = await session.scalar(
        select(func.count()).select_from(SupportTicket).where(SupportTicket.status == "resolved")
    )
    tickets_closed = await session.scalar(
        select(func.count()).select_from(SupportTicket).where(SupportTicket.status == "closed")
    )
    premium_active = await session.scalar(
        select(func.count()).select_from(PremiumSubscription).where(PremiumSubscription.status == "active")
    )
    complaints_open = await session.scalar(
        select(func.count()).select_from(Complaint).where(Complaint.status == "open")
    )
    u = int(users or 0)
    s = int(startups_total or 0)
    series = [
        {"label": "Пользователи", "value": u},
        {"label": "Стартапы", "value": s},
        {"label": "На проверке", "value": int(pending or 0)},
        {"label": "Обращения", "value": int(tickets or 0)},
    ]
    return {
        "users_total": u,
        "startups_total": s,
        "startups_by_state": [
            {"label": "Опубликованы", "value": int(approved or 0)},
            {"label": "На проверке", "value": int(pending or 0)},
            {"label": "Нужны изменения", "value": int(changes or 0)},
            {"label": "Отклонены", "value": int(rejected or 0)},
        ],
        "investors_verified": int(investors_approved or 0),
        "investors_pending": int(investors_pending or 0),
        "organizations_total": int(orgs_total or 0),
        "organizations_verified": int(orgs_approved or 0),
        "pending_reviews": int(pending or 0),
        "open_tickets": int(tickets or 0),
        "awaiting_confirmation_tickets": int(tickets_awaiting_confirmation or 0),
        "awaiting_staff_tickets": int(tickets_awaiting_staff or 0),
        "resolved_tickets": int(tickets_resolved or 0),
        "closed_tickets": int(tickets_closed or 0),
        "active_premium": int(premium_active or 0),
        "open_complaints": int(complaints_open or 0),
        "series": series,
    }


@app.get("/staff")
async def staff_list(account: AdminAccount = Depends(current_admin), session: AsyncSession = Depends(db_session)):
    require_roles(account, ROLES_OWNER)
    rows = await session.execute(select(AdminAccount).order_by(AdminAccount.created_at))
    invites = await session.execute(
        select(AdminInvite)
        .where(AdminInvite.consumed_at.is_(None), AdminInvite.expires_at > datetime.now(timezone.utc))
        .order_by(AdminInvite.expires_at)
    )
    return {
        "items": [
            {
                "id": str(a.id),
                "login": a.login,
                "role": a.role,
                "disabled": a.disabled_at is not None,
                "mfa": a.mfa_enabled,
                "must_change_password": a.must_change_password,
            }
            for a in rows.scalars()
        ],
        "invites": [
            {
                "id": str(i.id),
                "login": i.login,
                "role": i.role,
                "expires_at": i.expires_at.isoformat(),
            }
            for i in invites.scalars()
        ],
        "roles": ["owner", "administrator", "moderator", "support"],
    }


@app.post("/staff/{staff_id}/role")
async def staff_role(
    staff_id: uuid.UUID,
    body: RoleBody,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    require_roles(account, ROLES_OWNER)
    if body.role not in {"administrator", "moderator", "support"}:
        raise HTTPException(status_code=400, detail="Недопустимая роль.")
    target = await session.get(AdminAccount, staff_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Сотрудник не найден.")
    if target.role == "owner":
        raise HTTPException(status_code=403, detail="Роль владельца не меняется.")
    before = target.role
    target.role = body.role
    session.add(
        AdminAuditLog(
            id=uuid.uuid4(),
            actor_id=account.id,
            action="staff_role",
            resource_type="admin_account",
            resource_id=str(target.id),
            before_state={"role": before},
            after_state={"role": target.role},
            reason=None,
        )
    )
    await persist_session(session)
    return {"ok": True, "role": target.role}


@app.post("/staff/{staff_id}/enable")
async def staff_enable(
    staff_id: uuid.UUID,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    require_roles(account, ROLES_OWNER)
    target = await session.get(AdminAccount, staff_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Сотрудник не найден.")
    target.disabled_at = None
    session.add(
        AdminAuditLog(
            id=uuid.uuid4(),
            actor_id=account.id,
            action="staff_enable",
            resource_type="admin_account",
            resource_id=str(target.id),
            before_state={"disabled": True},
            after_state={"disabled": False},
            reason=None,
        )
    )
    await persist_session(session)
    return {"ok": True}


@app.get("/staff/invites")
async def staff_invites(
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    require_roles(account, ROLES_OWNER)
    rows = await session.execute(
        select(AdminInvite)
        .where(AdminInvite.consumed_at.is_(None), AdminInvite.expires_at > datetime.now(timezone.utc))
        .order_by(AdminInvite.expires_at)
    )
    return {
        "items": [
            {
                "id": str(i.id),
                "login": i.login,
                "role": i.role,
                "expires_at": i.expires_at.isoformat(),
            }
            for i in rows.scalars()
        ]
    }


@app.post("/staff/invites/{invite_id}/revoke")
async def staff_invite_revoke(
    invite_id: uuid.UUID,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    require_roles(account, ROLES_OWNER)
    invite = await session.get(AdminInvite, invite_id)
    if invite is None:
        raise HTTPException(status_code=404, detail="Приглашение не найдено.")
    invite.consumed_at = datetime.now(timezone.utc)
    await persist_session(session)
    return {"ok": True}


@app.post("/auth/password")
async def change_password(
    body: PasswordBody,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    from raundo.services.admin_auth import change_password as apply_password

    if not account.must_change_password:
        if not body.current_password or not verify_password(
            body.current_password, account.password_hash
        ):
            raise HTTPException(status_code=400, detail="Текущий пароль неверен.")
    try:
        apply_password(account, body.password)
    except ValueError:
        raise HTTPException(status_code=400, detail="Пароль слишком короткий.") from None
    await persist_session(session)
    return {"ok": True}


@app.get("/users")
async def users(
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
    q: str | None = None,
    status: str | None = None,
    profile_type: str | None = None,
    sort: str = "created_at",
    direction: str = "desc",
    page: int = 1,
    page_size: int = 25,
):
    require_roles(account, ROLES_MOD | ROLES_SUPPORT)
    try:
        return await directory_users(
            session,
            q=q,
            status=status,
            profile_type=profile_type,
            sort=sort,
            direction=direction,
            page=page,
            page_size=page_size,
        )
    except DirectoryError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc


@app.get("/users/{user_id}")
async def user_detail(
    user_id: uuid.UUID,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    require_roles(account, ROLES_MOD | ROLES_SUPPORT)
    try:
        detail = await directory_user_detail(session, user_id)
    except DirectoryError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    detail["can_restrict"] = account.role in ROLES_MOD
    detail["links"] = {
        "tickets": "support" if account.role in ROLES_SUPPORT else None,
        "complaints": "complaints",
        "moderation": "moderation" if account.role in ROLES_MOD else None,
    }
    return detail


@app.post("/users/{user_id}/restrict")
async def user_restrict(
    user_id: uuid.UUID,
    body: RestrictionBody,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    require_roles(account, ROLES_MOD)
    try:
        restriction = await restrict_user(
            session, user_id=user_id, actor_id=account.id, reason=body.reason
        )
    except DirectoryError as exc:
        status_code = 404 if exc.code == "missing" else 409
        raise HTTPException(status_code=status_code, detail=exc.message) from exc
    await persist_session(session)
    return {
        "ok": True,
        "restriction_id": str(restriction.id),
        "status": "restricted",
    }


@app.post("/users/{user_id}/unrestrict")
async def user_unrestrict(
    user_id: uuid.UUID,
    body: RestrictionBody,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    require_roles(account, ROLES_MOD)
    try:
        released = await unrestrict_user(
            session, user_id=user_id, actor_id=account.id, reason=body.reason
        )
    except DirectoryError as exc:
        status_code = 404 if exc.code == "missing" else 409
        raise HTTPException(status_code=status_code, detail=exc.message) from exc
    await persist_session(session)
    return {"ok": True, "released": released, "status": "active"}


@app.get("/organizations")
async def organizations(account: AdminAccount = Depends(current_admin), session: AsyncSession = Depends(db_session)):
    require_roles(account, ROLES_MOD | ROLES_SUPPORT)
    return {
        "items": await list_organization_queue(session),
        "can_review": account.role in ROLES_MOD,
    }


@app.get("/organizations/{org_id}")
async def organization_view(
    org_id: uuid.UUID,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    require_roles(account, ROLES_MOD | ROLES_SUPPORT)
    try:
        detail = await organization_detail(session, org_id)
    except OrganizationReviewError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    return detail


@app.post("/organizations/{org_id}/review")
async def organization_review(
    org_id: uuid.UUID,
    body: OrgReviewBody,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    require_roles(account, ROLES_MOD)
    try:
        org = await review_organization(
            session,
            org_id=org_id,
            actor_id=account.id,
            decision=body.decision,
            reason=body.reason,
            official_name=body.official_name,
        )
    except OrganizationReviewError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    await persist_session(session)
    return {"ok": True, "review_status": org.review_status}


@app.post("/organizations/memberships/{membership_id}/review")
async def organization_membership_review(
    membership_id: uuid.UUID,
    body: MembershipReviewBody,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    require_roles(account, ROLES_MOD)
    try:
        membership = await review_membership(
            session,
            membership_id=membership_id,
            actor_id=account.id,
            decision=body.decision,
            reason=body.reason,
            mandate=body.mandate,
        )
    except OrganizationReviewError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    await persist_session(session)
    return {
        "ok": True,
        "status": membership.status,
        "mandate": bool(membership.claims_direct_investment_mandate),
    }


@app.get("/complaints")
async def complaints(
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
    status: str | None = None,
    target_type: str | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 25,
):
    require_roles(account, ROLES_MOD | ROLES_SUPPORT)
    try:
        payload = await complaints_list(
            session,
            status=status,
            target_type=target_type,
            q=q,
            page=page,
            page_size=page_size,
        )
    except ComplaintError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    payload["can_decide"] = account.role in ROLES_MOD
    payload["priorities"] = list(PRIORITIES)
    payload["status_labels"] = {
        "open": "Новая",
        "in_review": "В работе",
        "resolved": "Рассмотрена",
        "dismissed": "Закрыта",
    }
    return payload


@app.get("/complaints/{complaint_id}")
async def complaint_view(
    complaint_id: uuid.UUID,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    require_roles(account, ROLES_MOD | ROLES_SUPPORT)
    try:
        detail = await complaint_detail_view(session, complaint_id)
    except ComplaintError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    detail["can_decide"] = account.role in ROLES_MOD
    detail["status_labels"] = {
        "open": "Новая",
        "in_review": "В работе",
        "resolved": "Рассмотрена",
        "dismissed": "Закрыта",
    }
    detail["target_types"] = COMPLAINT_TARGET_TYPES
    return detail


@app.patch("/complaints/{complaint_id}")
async def complaint_update(
    complaint_id: uuid.UUID,
    body: ComplaintBody,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    require_roles(account, ROLES_MOD)
    complaint = await session.get(Complaint, complaint_id, with_for_update=True)
    if complaint is None:
        raise HTTPException(status_code=404, detail="Жалоба не найдена.")
    try:
        result = await apply_complaint_status(
            session,
            complaint=complaint,
            status=body.status,
            comment=body.comment,
            actor_id=account.id,
            restrict_target=body.restrict_target,
        )
    except ComplaintError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    await persist_session(session)
    return {"ok": True, **result}


@app.get("/premium")
async def premium(account: AdminAccount = Depends(current_admin), session: AsyncSession = Depends(db_session)):
    require_roles(account, {"owner", "administrator"})
    rows = await session.execute(
        select(PremiumSubscription).order_by(PremiumSubscription.expires_at.desc()).limit(100)
    )
    subs = list(rows.scalars())
    user_ids = {s.user_id for s in subs if s.user_id}
    startup_ids = {s.startup_id for s in subs if s.startup_id}
    org_ids = {s.organization_id for s in subs if s.organization_id}
    users: dict[uuid.UUID, User] = {}
    if user_ids:
        found = await session.execute(select(User).where(User.id.in_(user_ids)))
        users = {u.id: u for u in found.scalars()}
    startups: dict[uuid.UUID, str] = {}
    if startup_ids:
        found = await session.execute(
            select(Startup.id, Startup.name).where(Startup.id.in_(startup_ids))
        )
        startups = {row[0]: row[1] for row in found.all()}
    orgs: dict[uuid.UUID, str] = {}
    if org_ids:
        found = await session.execute(
            select(FundingOrganization.id, FundingOrganization.name).where(
                FundingOrganization.id.in_(org_ids)
            )
        )
        orgs = {row[0]: row[1] for row in found.all()}

    def owner_label(sub: PremiumSubscription) -> str:
        if sub.user_id and sub.user_id in users:
            user = users[sub.user_id]
            name = " ".join(part for part in (user.first_name, user.last_name) if part)
            return f"{name} (@{user.username})" if user.username else (name or str(user.telegram_id))
        if sub.startup_id and sub.startup_id in startups:
            return startups[sub.startup_id] or "Стартап"
        if sub.organization_id and sub.organization_id in orgs:
            return orgs[sub.organization_id] or "Организация"
        return "—"

    return {
        "items": [
            {
                "id": str(s.id),
                "plan": s.plan_code,
                "owner_type": s.owner_type,
                "owner": owner_label(s),
                "status": s.status,
                "source": s.source,
                "is_test": s.is_test,
                "starts_at": s.starts_at.isoformat() if s.starts_at else None,
                "expires_at": s.expires_at.isoformat() if s.expires_at else None,
            }
            for s in subs
        ],
        "payments": "disabled",
    }


@app.get("/payments")
async def payments_list(
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    require_roles(account, {"owner", "administrator"})
    from raundo.models.billing import PremiumSubscription
    from raundo.models.payments import PaymentOrder
    from raundo.premium_catalog import PLAN_TITLES, format_price

    rows = await session.execute(
        select(PaymentOrder, User, PremiumSubscription)
        .outerjoin(User, User.id == PaymentOrder.user_id)
        .outerjoin(
            PremiumSubscription,
            PremiumSubscription.id == PaymentOrder.premium_subscription_id,
        )
        .order_by(PaymentOrder.created_at.desc())
        .limit(100)
    )
    items = []
    for order, user, sub in rows.all():
        name = " ".join(part for part in (user.first_name, user.last_name) if part) if user else ""
        label = f"{name} (@{user.username})" if user and user.username else (name or "—")
        items.append(
            {
                "id": str(order.id),
                "number": order.order_number,
                "user": label,
                "plan": PLAN_TITLES.get(order.plan_code, order.plan_code),
                "period": order.duration_code,
                "amount": (
                    f"{order.amount_minor} ⭐ (XTR)"
                    if order.currency == "XTR"
                    else format_price(order.amount_minor)
                ),
                "amount_minor": order.amount_minor,
                "currency": order.currency,
                "provider": order.payment_provider,
                "status": order.status,
                "created_at": order.created_at.isoformat() if order.created_at else None,
                "paid_at": order.paid_at.isoformat() if order.paid_at else None,
                "premium_until": sub.expires_at.isoformat() if sub else None,
                "is_test": order.is_test,
            }
        )
    return {"items": items}


@app.get("/payments/{order_id}")
async def payment_detail(
    order_id: uuid.UUID,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    require_roles(account, {"owner", "administrator"})
    from raundo.models.payments import PaymentOrder, PaymentTransaction

    order = await session.get(PaymentOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Заказ не найден.")
    user = await session.get(User, order.user_id)
    tx_rows = await session.execute(
        select(PaymentTransaction)
        .where(PaymentTransaction.order_id == order.id)
        .order_by(PaymentTransaction.created_at)
    )
    return {
        "id": str(order.id),
        "number": order.order_number,
        "status": order.status,
        "plan_code": order.plan_code,
        "duration": order.duration_code,
        "amount_minor": order.amount_minor,
        "currency": order.currency,
        "provider": order.payment_provider,
        "user": user.username or user.first_name if user else None,
        "consent_version": order.consent_version,
        "consent_accepted_at": order.consent_accepted_at.isoformat()
        if order.consent_accepted_at
        else None,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "paid_at": order.paid_at.isoformat() if order.paid_at else None,
        "is_test": order.is_test,
        "transactions": [
            {
                "provider": tx.provider,
                "provider_payment_id": tx.provider_payment_id,
                "provider_charge_id": tx.provider_charge_id,
                "provider_status": tx.provider_status,
                "amount_minor": tx.amount_minor,
                "currency": tx.currency,
                "refund_status": tx.refund_status,
                "processed_at": tx.processed_at.isoformat() if tx.processed_at else None,
            }
            for tx in tx_rows.scalars()
        ],
    }


@app.get("/tax-receipts")
async def tax_receipts_list(
    status: str | None = None,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    """Self-employed receipt ledger: paid orders that still need a real receipt."""
    require_roles(account, {"owner", "administrator"})
    from raundo.models.tax import TaxReceipt

    stmt = select(TaxReceipt).order_by(TaxReceipt.created_at)
    if status:
        stmt = stmt.where(TaxReceipt.status == status)
    rows = (await session.scalars(stmt.limit(200))).all()
    counts = {
        str(state): int(count)
        for state, count in (
            await session.execute(
                select(TaxReceipt.status, func.count()).group_by(TaxReceipt.status)
            )
        ).all()
    }
    return {
        "counts": counts,
        "items": [
            {
                "id": str(row.id),
                "order_id": str(row.order_id),
                "order_number": row.order_number,
                "amount_minor": row.amount_minor,
                "currency": row.currency,
                "status": row.status,
                "provider": row.provider,
                "fiscal_receipt_id": row.fiscal_receipt_id,
                "receipt_url": row.receipt_url,
                "paid_at": row.paid_at.isoformat() if row.paid_at else None,
                "registered_at": row.registered_at.isoformat() if row.registered_at else None,
                "sent_to_user": row.sent_to_user,
                "error": row.error,
            }
            for row in rows
        ],
    }


class ReceiptRegisteredBody(BaseModel):
    provider: str = Field(default="my_tax", max_length=64)
    fiscal_receipt_id: str = Field(min_length=1, max_length=128)
    receipt_url: str | None = Field(default=None, max_length=512)
    sent_to_user: bool = False


@app.post("/tax-receipts/{receipt_id}/registered")
async def tax_receipt_registered(
    receipt_id: uuid.UUID,
    body: ReceiptRegisteredBody,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    """Record a real receipt issued in «Мой налог» (never fabricates one)."""
    require_roles(account, {"owner", "administrator"})
    from raundo.services import tax_receipts

    try:
        receipt = await tax_receipts.mark_registered(
            session,
            receipt_id,
            provider=body.provider,
            fiscal_receipt_id=body.fiscal_receipt_id,
            receipt_url=body.receipt_url,
            sent_to_user=body.sent_to_user,
        )
    except tax_receipts.ReceiptError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.add(
        AdminAuditLog(
            id=uuid.uuid4(),
            actor_id=account.id,
            action="tax_receipt_registered",
            resource_type="tax_receipt",
            resource_id=str(receipt.id),
            before_state={"status": "pending"},
            after_state={
                "status": receipt.status,
                "provider": receipt.provider,
                "fiscal_receipt_id": receipt.fiscal_receipt_id,
            },
            reason=None,
        )
    )
    await session.commit()
    return {"ok": True, "status": receipt.status}


class ReceiptFailedBody(BaseModel):
    error: str = Field(min_length=1, max_length=500)


@app.post("/tax-receipts/{receipt_id}/failed")
async def tax_receipt_failed(
    receipt_id: uuid.UUID,
    body: ReceiptFailedBody,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    require_roles(account, {"owner", "administrator"})
    from raundo.services import tax_receipts

    try:
        receipt = await tax_receipts.mark_failed(session, receipt_id, error=body.error)
    except tax_receipts.ReceiptError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    session.add(
        AdminAuditLog(
            id=uuid.uuid4(),
            actor_id=account.id,
            action="tax_receipt_failed",
            resource_type="tax_receipt",
            resource_id=str(receipt.id),
            before_state={"status": "pending"},
            after_state={"status": receipt.status},
            reason=body.error[:200],
        )
    )
    await session.commit()
    return {"ok": True, "status": receipt.status}




# ---------------------------------------------------------------- catalog
def _catalog_iso(value):
    return value.isoformat() if value is not None else None


def _catalog_dump(obj):
    from sqlalchemy import inspect as sa_inspect

    data = {}
    for column in sa_inspect(obj).mapper.column_attrs:
        value = getattr(obj, column.key)
        if isinstance(value, (uuid.UUID,)):
            value = str(value)
        elif hasattr(value, "isoformat"):
            value = value.isoformat()
        data[column.key] = value
    return data


def _catalog_status(obj) -> str:
    if getattr(obj, "deleted_at", None) is not None:
        return "archived"
    return str(getattr(obj, "review_state", None) or getattr(obj, "review_status", "draft"))


def _catalog_is_fund(org) -> bool:
    value = (getattr(org, "participant_type", "") or "").lower()
    return any(word in value for word in ("fund", "vc", "family", "syndicate", "angel"))


@app.get("/catalog")
async def catalog_list(
    q: str | None = None,
    profile_type: str | None = None,
    status: str | None = None,
    premium: bool | None = None,
    sort: str = "updated",
    page: int = 1,
    page_size: int = 25,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    """Full registry of every profile: drafts, pending, approved, rejected, archived."""
    require_roles(account, {"owner", "administrator", "moderator", "support"})
    from raundo.models.billing import PremiumSubscription
    from raundo.models.investor import InvestorProfile
    from raundo.models.organization import FundingOrganization
    from raundo.models.startup import Startup

    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    like = f"%{(q or '').strip()}%" if (q or "").strip() else None
    search_uuid = None
    if (q or "").strip():
        try:
            search_uuid = uuid.UUID(str(q).strip())
        except ValueError:
            search_uuid = None

    rows: list[dict] = []
    owners: dict[uuid.UUID, User] = {}

    async def add(model, kind, title_attr, subtitle_attr, owner_attr):
        stmt = select(model, User).outerjoin(User, User.id == getattr(model, owner_attr))
        clauses = []
        if like:
            field = getattr(model, title_attr)
            clauses.append(field.ilike(like))
            clauses.append(User.username.ilike(f"%{str(q).strip().lstrip('@')}%"))
            clauses.append(User.first_name.ilike(like))
            if search_uuid:
                clauses.append(model.id == search_uuid)
            if str(q).strip().isdigit() and int(q) <= 2**63 - 1:
                clauses.append(User.telegram_id == int(q))
            stmt = stmt.where(or_(*clauses))
        elif search_uuid:
            stmt = stmt.where(model.id == search_uuid)
        result = await session.execute(stmt)
        for obj, owner in result.all():
            st = _catalog_status(obj)
            if status and st != status:
                continue
            if profile_type == "fund" and (kind != "organization" or not _catalog_is_fund(obj)):
                continue
            if profile_type == "organization" and (kind != "organization" or _catalog_is_fund(obj)):
                continue
            if profile_type and profile_type not in ("fund", "organization") and profile_type != kind:
                continue
            if owner is not None:
                owners[owner.id] = owner
            rows.append(
                {
                    "kind": kind,
                    "id": str(obj.id),
                    "title": str(getattr(obj, title_attr) or "Без названия"),
                    "subtitle": str(getattr(obj, subtitle_attr) or "")[:180],
                    "status": st,
                    "created_at": _catalog_iso(getattr(obj, "created_at", None)),
                    "updated_at": _catalog_iso(getattr(obj, "updated_at", None)),
                    "owner": (
                        " ".join(p for p in (owner.first_name, owner.last_name) if p) or owner.username
                        if owner
                        else "-"
                    ),
                    "username": owner.username if owner else None,
                    "telegram_id": owner.telegram_id if owner else None,
                    "premium": False,
                    "_owner_id": owner.id if owner else None,
                    "_obj": obj,
                    "_catalog_fund": kind == "organization" and _catalog_is_fund(obj),
                }
            )

    await add(Startup, "startup", "name", "one_liner", "owner_user_id")
    await add(InvestorProfile, "investor", "display_name", "strategy_description", "user_id")
    await add(FundingOrganization, "organization", "name", "description", "created_by_user_id")

    for row in rows:
        obj = row.pop("_obj", None)
        if obj is None:
            continue
        if row["kind"] == "startup":
            from raundo.models.startup import StartupMedia

            media = await session.scalar(
                select(StartupMedia)
                .where(StartupMedia.startup_id == obj.id)
                .order_by(StartupMedia.position)
                .limit(1)
            )
            if media is not None:
                row["cover_url"] = f"/api/media/startups/{obj.id}/photos/{media.position}"
            if getattr(obj, "ask_amount_minor", None):
                currency = getattr(obj, "ask_currency", None) or ""
                row["amount_label"] = f"{obj.ask_amount_minor / 100:,.0f} {currency}".strip()
        elif row["kind"] == "investor":
            if getattr(obj, "telegram_photo_file_id", None):
                row["cover_url"] = f"/api/media/investors/{obj.id}/photo"
            minimum = getattr(obj, "min_check_minor", None)
            maximum = getattr(obj, "max_check_minor", None)
            currency = getattr(obj, "currency", None) or ""
            if minimum or maximum:
                low = f"{minimum / 100:,.0f}" if minimum else "?"
                high = f"{maximum / 100:,.0f}" if maximum else "?"
                row["amount_label"] = f"{low}–{high} {currency}".strip()

    premium_owners: set[uuid.UUID] = set()
    if owners:
        now = datetime.now(timezone.utc)
        subs = await session.scalars(
            select(PremiumSubscription).where(
                PremiumSubscription.user_id.in_(list(owners)),
                PremiumSubscription.status == "active",
                PremiumSubscription.expires_at > now,
            )
        )
        premium_owners = {sub.user_id for sub in subs.all()}
    for row in rows:
        row["premium"] = row["_owner_id"] in premium_owners
        row.pop("_owner_id", None)

    if premium is not None:
        rows = [row for row in rows if row["premium"] is premium]
    sort_field = {"updated": "updated_at", "created": "created_at"}.get(sort, sort)
    if sort_field not in ("created_at", "updated_at"):
        sort_field = "updated_at"
    rows.sort(key=lambda row: row.get(sort_field) or "", reverse=True)

    counts = {
        "startup": {"total": 0, "statuses": {}},
        "investor": {"total": 0, "statuses": {}},
        "fund": {"total": 0, "statuses": {}},
        "organization": {"total": 0, "statuses": {}},
    }
    for row in rows:
        is_fund = row.pop("_catalog_fund")
        key = "fund" if is_fund else row["kind"]
        counts[key]["total"] += 1
        counts[key]["statuses"][row["status"]] = counts[key]["statuses"].get(row["status"], 0) + 1

    total = len(rows)
    start = (page - 1) * page_size
    return {"items": rows[start : start + page_size], "total": total, "page": page, "page_size": page_size, "counts": counts}


@app.get("/catalog/{kind}/{profile_id}")
async def catalog_detail(
    kind: str,
    profile_id: uuid.UUID,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    require_roles(account, {"owner", "administrator", "moderator", "support"})
    from raundo.models.investor import InvestorProfile
    from raundo.models.organization import FundingOrganization
    from raundo.models.startup import Startup

    models = {"startup": Startup, "investor": InvestorProfile, "organization": FundingOrganization}
    model = models.get(kind)
    if model is None:
        raise HTTPException(status_code=404, detail="Неизвестный тип анкеты.")
    obj = await session.get(model, profile_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Анкета не найдена.")
    owner_id = getattr(obj, "owner_user_id", None) or getattr(obj, "user_id", None) or getattr(obj, "created_by_user_id", None)
    owner = await session.get(User, owner_id) if owner_id else None
    from raundo.models.billing import PremiumSubscription

    subs = []
    if owner is not None:
        active = await session.scalars(
            select(PremiumSubscription).where(
                PremiumSubscription.user_id == owner.id,
                PremiumSubscription.status == "active",
                PremiumSubscription.expires_at > datetime.now(timezone.utc),
            )
        )
        subs = [
            {"plan": s.plan_code, "owner_type": s.owner_type, "expires_at": _catalog_iso(s.expires_at)}
            for s in active.all()
        ]
    photos = []
    reviews = []
    if kind == "startup":
        from raundo.models.startup import StartupMedia, StartupReview

        media = (
            await session.scalars(
                select(StartupMedia)
                .where(StartupMedia.startup_id == obj.id)
                .order_by(StartupMedia.position)
            )
        ).all()
        photos = [
            {
                "position": m.position,
                "kind": m.kind,
                "url": f"/api/media/startups/{obj.id}/photos/{m.position}",
            }
            for m in media
        ]
        review_rows = (
            await session.scalars(
                select(StartupReview)
                .where(StartupReview.startup_id == obj.id)
                .order_by(StartupReview.submitted_at.desc())
            )
        ).all()
    elif kind == "investor":
        from raundo.models.investor import InvestorReview

        if obj.telegram_photo_file_id:
            photos = [{"position": 0, "kind": "photo", "url": f"/api/media/investors/{obj.id}/photo"}]
        review_rows = (
            await session.scalars(
                select(InvestorReview)
                .where(InvestorReview.investor_id == obj.id)
                .order_by(InvestorReview.submitted_at.desc())
            )
        ).all()
    else:
        review_rows = []
    reviews = [_catalog_dump(row) for row in review_rows]
    review_id = str(review_rows[0].id) if review_rows else None
    return {
        "kind": kind,
        "status": _catalog_status(obj),
        "item": _catalog_dump(obj),
        "owner": _catalog_dump(owner) if owner is not None else None,
        "premium": subs,
        "reviews": reviews,
        "photos": photos,
        "review_id": review_id,
    }


@app.get("/audit")
async def audit(account: AdminAccount = Depends(current_admin), session: AsyncSession = Depends(db_session)):
    require_roles(account, {"owner", "administrator"})
    rows = await session.execute(
        select(AdminAuditLog, AdminAccount)
        .outerjoin(AdminAccount, AdminAccount.id == AdminAuditLog.actor_id)
        .order_by(AdminAuditLog.created_at.desc())
        .limit(100)
    )
    return {
        "items": [
            {
                "action": a.action,
                "resource_type": a.resource_type,
                "resource_id": a.resource_id,
                "reason": a.reason,
                "actor": actor.login if actor else None,
                "before_state": a.before_state,
                "after_state": a.after_state,
                "created_at": a.created_at.isoformat(),
            }
            for a, actor in rows.all()
        ]
    }


@app.get("/outbox")
async def outbox(account: AdminAccount = Depends(current_admin), session: AsyncSession = Depends(db_session)):
    require_roles(account, {"owner", "administrator"})
    rows = await session.execute(
        select(OutboxEvent, OutboxDeliveryMeta)
        .outerjoin(OutboxDeliveryMeta, OutboxDeliveryMeta.outbox_id == OutboxEvent.id)
        .order_by(OutboxEvent.created_at.desc())
        .limit(50)
    )
    return {
        "items": [
            {
                "id": str(e.id),
                "kind": e.kind,
                "state": e.state,
                "attempts": meta.attempt_count if meta else 0,
                "last_error": meta.last_error if meta else None,
                "delivered_at": meta.delivered_at.isoformat()
                if meta and meta.delivered_at
                else None,
                "next_attempt_at": e.next_attempt_at.isoformat() if e.next_attempt_at else None,
                "created_at": e.created_at.isoformat(),
            }
            for e, meta in rows.all()
        ]
    }


@app.post("/outbox/{event_id}/retry")
async def outbox_retry(
    event_id: uuid.UUID,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    require_roles(account, ROLES_OWNER)
    event = await session.get(OutboxEvent, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Событие не найдено.")
    if event.state not in {"failed", "undeliverable", "pending", "retry_scheduled"}:
        raise HTTPException(status_code=409, detail="Повтор недоступен для этого состояния.")
    event.state = "pending"
    event.next_attempt_at = datetime.now(timezone.utc)
    await persist_session(session)
    return {"ok": True, "state": event.state}


@app.post("/staff/{staff_id}/disable")
async def staff_disable(
    staff_id: uuid.UUID,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    require_roles(account, ROLES_OWNER)
    target = await session.get(AdminAccount, staff_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Сотрудник не найден.")
    if target.role == "owner":
        raise HTTPException(status_code=403, detail="Нельзя отключить владельца.")
    target.disabled_at = datetime.now(timezone.utc)
    sessions = await session.execute(
        select(AdminSession).where(AdminSession.admin_id == target.id, AdminSession.revoked_at.is_(None))
    )
    now = datetime.now(timezone.utc)
    for sess in sessions.scalars():
        sess.revoked_at = now
    session.add(
        AdminAuditLog(
            id=uuid.uuid4(),
            actor_id=account.id,
            action="staff_disable",
            resource_type="admin_account",
            resource_id=str(target.id),
            before_state={"disabled": False},
            after_state={"disabled": True},
            reason=None,
        )
    )
    await persist_session(session)
    return {"ok": True}


@app.post("/auth/mfa/setup")
async def mfa_setup(account: AdminAccount = Depends(current_admin), session: AsyncSession = Depends(db_session)):
    """Start or resume TOTP enrolment; never silently disables active MFA."""
    import pyotp

    from raundo.services.admin_auth import generate_totp_secret

    if account.mfa_enabled:
        raise HTTPException(
            status_code=409,
            detail="MFA уже включена. Сначала отключите её, если нужно новое устройство.",
        )
    if account.totp_secret:
        # Resume pending enrolment without rotating the secret (QR stays valid).
        secret = account.totp_secret
    else:
        secret = generate_totp_secret()
        account.totp_secret = secret
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name=account.login, issuer_name="RAUNDO Admin")
    await persist_session(session)
    return {
        "otpauth_uri": uri,
        "secret": secret,
        "issuer": "RAUNDO Admin",
        "account": account.login,
        "pending": True,
    }


@app.post("/auth/mfa/enable")
async def mfa_enable(
    body: MfaBody,
    request: Request,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    from raundo.services.admin_auth import generate_backup_codes, verify_totp

    if not account.totp_secret:
        raise HTTPException(status_code=400, detail="Сначала сгенерируйте секрет.")
    if account.mfa_enabled:
        raise HTTPException(status_code=409, detail="MFA уже включена.")
    current: AdminSession = request.state.admin_session
    if not _mfa_rate_ok(str(current.id)):
        raise HTTPException(status_code=429, detail="Слишком много попыток. Повторите позже.")
    if not verify_totp(account, body.code):
        _mfa_note_failure(str(current.id))
        raise HTTPException(status_code=400, detail="Неверный код. Проверьте время на устройстве.")
    _MFA_FAILS.pop(str(current.id), None)
    codes, digest = generate_backup_codes()
    account.mfa_enabled = True
    account.backup_codes_hash = digest
    current.mfa_verified_at = datetime.now(timezone.utc)
    session.add(
        AdminAuditLog(
            id=uuid.uuid4(),
            actor_id=account.id,
            action="mfa_enable",
            resource_type="admin_account",
            resource_id=str(account.id),
            before_state={"mfa_enabled": False},
            after_state={"mfa_enabled": True},
            reason=None,
        )
    )
    await persist_session(session)
    return {"ok": True, "backup_codes": codes}


@app.post("/auth/mfa/disable")
async def mfa_disable(
    body: MfaBody,
    request: Request,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    from raundo.services.admin_auth import verify_mfa_code

    if not account.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA не включена.")
    current: AdminSession = request.state.admin_session
    if not _mfa_rate_ok(str(current.id)):
        raise HTTPException(status_code=429, detail="Слишком много попыток. Повторите позже.")
    if not verify_mfa_code(account, body.code):
        _mfa_note_failure(str(current.id))
        raise HTTPException(status_code=400, detail="Неверный код.")
    _MFA_FAILS.pop(str(current.id), None)
    account.mfa_enabled = False
    account.totp_secret = None
    account.backup_codes_hash = None
    session.add(
        AdminAuditLog(
            id=uuid.uuid4(),
            actor_id=account.id,
            action="mfa_disable",
            resource_type="admin_account",
            resource_id=str(account.id),
            before_state={"mfa_enabled": True},
            after_state={"mfa_enabled": False},
            reason=None,
        )
    )
    await persist_session(session)
    return {"ok": True}


@app.post("/staff/invites")
async def create_invite(
    body: InviteBody,
    account: AdminAccount = Depends(current_admin),
    session: AsyncSession = Depends(db_session),
):
    require_roles(account, ROLES_OWNER)
    if body.role not in {"administrator", "moderator", "support"}:
        raise HTTPException(status_code=400, detail="Недопустимая роль.")
    token = new_token()
    session.add(
        AdminInvite(
            id=uuid.uuid4(),
            login=body.login.strip().lower(),
            role=body.role,
            token_hash=hash_token(token),
            expires_at=datetime.now(timezone.utc) + timedelta(days=2),
        )
    )
    await persist_session(session)
    return {"invite_token": token, "login": body.login}


@app.post("/staff/accept")
async def accept_invite(body: AcceptInviteBody, session: AsyncSession = Depends(db_session)):
    row = await session.execute(select(AdminInvite).where(AdminInvite.token_hash == hash_token(body.token)))
    invite = row.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if invite is None or invite.consumed_at is not None or invite.expires_at < now:
        raise HTTPException(status_code=400, detail="Приглашение недействительно.")
    exists = await session.execute(select(AdminAccount).where(AdminAccount.login == invite.login))
    if exists.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Логин уже занят.")
    session.add(
        AdminAccount(
            id=uuid.uuid4(),
            login=invite.login,
            password_hash=hash_password(body.password),
            role=invite.role,
            # The invitee chose this password themselves: no forced rotation.
            must_change_password=False,
        )
    )
    invite.consumed_at = now
    await persist_session(session)
    return {"ok": True, "login": invite.login}


# ------------------------------------------------------------------ public checkout
# Mounted after admin routes; the public router contains no admin handlers.
from raundo.admin_api import public_payments  # noqa: E402

app.include_router(public_payments.router)

# Public legal edition and the Premium catalog page live on the checkout host.
from raundo.admin_api import legal_pages  # noqa: E402

app.include_router(legal_pages.router)

# Self-service purchase on the website (disabled by SITE_CHECKOUT_ENABLED).
from raundo.admin_api import site_checkout  # noqa: E402

app.include_router(site_checkout.router)
if (public_payments.CHECKOUT_DIST / "assets").exists():
    from fastapi.staticfiles import StaticFiles  # noqa: PLC0415

    app.mount(
        "/assets",
        StaticFiles(directory=public_payments.CHECKOUT_DIST / "assets"),
        name="checkout-assets",
    )


def create_app() -> FastAPI:
    return app
