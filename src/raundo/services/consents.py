"""Consent records: required acceptance, voluntary consents, revocation.

Each record keeps the document edition, the exact purpose/audience/fields of the
grant, the entry point and a revocation trail. Voluntary consents are never
pre-set and are recorded only after an explicit action.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.legal_edition import legal_edition
from raundo.models.user import UserConsent

CONSENT_TERMS = "terms"
CONSENT_PRIVACY = "privacy"
CONSENT_DATA = "data_processing"
CONSENT_PROFILE_PUBLICATION = "profile_publication"
CONSENT_MARKETING = "marketing"
CONSENT_IMAGE_ADVERTISING = "image_advertising"

# Without these the platform cannot be used at all.
REQUIRED_KINDS: tuple[str, ...] = (CONSENT_TERMS, CONSENT_PRIVACY, CONSENT_DATA)
# These are voluntary, off by default, and revocable at any time.
OPTIONAL_KINDS: tuple[str, ...] = (
    CONSENT_PROFILE_PUBLICATION,
    CONSENT_MARKETING,
    CONSENT_IMAGE_ADVERTISING,
)
ALL_KINDS: tuple[str, ...] = REQUIRED_KINDS + OPTIONAL_KINDS

# Default scope captured with each grant. Kept deliberately narrow.
DEFAULT_SCOPE: dict[str, dict[str, object]] = {
    CONSENT_TERMS: {"purpose": "platform_terms", "audience": "executor"},
    CONSENT_PRIVACY: {"purpose": "privacy_acknowledgement", "audience": "executor"},
    CONSENT_DATA: {"purpose": "platform_operation", "audience": "executor"},
    CONSENT_PROFILE_PUBLICATION: {
        "purpose": "profile_publication",
        "audience": "platform_users",
        "fields": [
            "display_name",
            "bio",
            "startup_card",
            "investor_profile",
            "organization_card",
        ],
    },
    CONSENT_MARKETING: {"purpose": "marketing_messages", "audience": "executor"},
    CONSENT_IMAGE_ADVERTISING: {
        "purpose": "image_in_advertising",
        "audience": "public",
        "fields": ["photo", "name"],
    },
}


class ConsentError(Exception):
    """Consent cannot be granted or revoked in this state."""


class PublicationConsentRequired(ConsentError):
    """Public distribution of profile data needs its own explicit consent."""


async def has_publication_consent(session: AsyncSession, user_id: uuid.UUID) -> bool:
    """Active consent for publishing profile data, current edition only."""
    return await ConsentService(session).has(user_id, CONSENT_PROFILE_PUBLICATION)


class ConsentService:
    def __init__(self, session: AsyncSession, *, policy_version: str | None = None) -> None:
        self._session = session
        self._policy_version = (policy_version or legal_edition()).strip()

    @property
    def policy_version(self) -> str:
        return self._policy_version

    async def _row(self, user_id: uuid.UUID, kind: str) -> UserConsent | None:
        return (
            await self._session.scalars(
                select(UserConsent).where(
                    UserConsent.user_id == user_id,
                    UserConsent.kind == kind,
                    UserConsent.policy_version == self._policy_version,
                )
            )
        ).first()

    async def has(self, user_id: uuid.UUID, kind: str) -> bool:
        row = await self._row(user_id, kind)
        return row is not None and row.revoked_at is None

    async def has_required_consents(self, user_id: uuid.UUID) -> bool:
        for kind in REQUIRED_KINDS:
            if not await self.has(user_id, kind):
                return False
        return True

    async def missing_required(self, user_id: uuid.UUID) -> list[str]:
        return [kind for kind in REQUIRED_KINDS if not await self.has(user_id, kind)]

    async def grant(
        self,
        user_id: uuid.UUID,
        kind: str,
        *,
        purpose: str | None = None,
        audience: str | None = None,
        fields: Iterable[str] | None = None,
        source: str = "bot",
    ) -> UserConsent:
        if kind not in ALL_KINDS:
            raise ConsentError(f"unknown consent kind: {kind}")
        scope = DEFAULT_SCOPE.get(kind, {})
        now = datetime.now(timezone.utc)
        row = await self._row(user_id, kind)
        if row is not None:
            row.revoked_at = None
            row.revoke_reason = None
            row.granted_at = now
            row.source = source
            row.purpose = purpose or str(scope.get("purpose") or "") or None
            row.audience = audience or str(scope.get("audience") or "") or None
            row.fields = list(fields) if fields is not None else scope.get("fields")  # type: ignore[assignment]
            await self._session.flush()
            return row
        row = UserConsent(
            user_id=user_id,
            kind=kind,
            policy_version=self._policy_version,
            granted_at=now,
            purpose=purpose or str(scope.get("purpose") or "") or None,
            audience=audience or str(scope.get("audience") or "") or None,
            fields=list(fields) if fields is not None else scope.get("fields"),  # type: ignore[arg-type]
            source=source,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def accept_required(self, user_id: uuid.UUID, *, source: str = "bot") -> None:
        for kind in REQUIRED_KINDS:
            await self.grant(user_id, kind, source=source)

    # Backwards-compatible name used by older call sites and tests.
    async def accept_all(self, user_id: uuid.UUID) -> None:
        await self.accept_required(user_id)

    async def revoke(
        self,
        user_id: uuid.UUID,
        kind: str,
        *,
        reason: str = "user_request",
    ) -> UserConsent:
        if kind in {CONSENT_TERMS, CONSENT_PRIVACY}:
            raise ConsentError(
                "terms and privacy acknowledgement cannot be revoked while using the platform"
            )
        return await self._revoke_row(user_id, kind, reason)

    async def _revoke_row(self, user_id: uuid.UUID, kind: str, reason: str) -> UserConsent:
        row = await self._row(user_id, kind)
        if row is None or row.revoked_at is not None:
            raise ConsentError("nothing to revoke")
        row.revoked_at = datetime.now(timezone.utc)
        row.revoke_reason = reason
        await self._session.flush()
        return row

    async def revoke_data_processing(
        self,
        user_id: uuid.UUID,
        *,
        reason: str = "user_request",
    ) -> dict[str, object]:
        """Withdraw the data-processing consent and everything derived from it.

        Processing stops immediately: the account loses access to gated features
        until a new consent is given, derived consents are withdrawn too, and the
        caller is expected to unpublish public profiles. Historical rows stay.
        """
        revoked: list[str] = []
        try:
            await self._revoke_row(user_id, CONSENT_DATA, reason)
            revoked.append(CONSENT_DATA)
        except ConsentError:
            pass
        for kind in (CONSENT_MARKETING, CONSENT_IMAGE_ADVERTISING, CONSENT_PROFILE_PUBLICATION):
            try:
                await self._revoke_row(user_id, kind, f"{reason}:data_processing_withdrawn")
                revoked.append(kind)
            except ConsentError:
                continue
        return {"revoked": revoked, "edition": self._policy_version}

    async def active(self, user_id: uuid.UUID) -> dict[str, UserConsent]:
        rows = (
            await self._session.scalars(
                select(UserConsent).where(
                    UserConsent.user_id == user_id,
                    UserConsent.policy_version == self._policy_version,
                )
            )
        ).all()
        return {row.kind: row for row in rows if row.revoked_at is None}

    async def history(self, user_id: uuid.UUID) -> list[UserConsent]:
        rows = (
            await self._session.scalars(
                select(UserConsent)
                .where(UserConsent.user_id == user_id)
                .order_by(UserConsent.granted_at.asc())
            )
        ).all()
        return list(rows)
