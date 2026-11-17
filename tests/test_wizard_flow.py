"""Resume / back step derivation for profile wizards."""

from types import SimpleNamespace

from raundo.state.investor import InvestorWizard
from raundo.state.startup import StartupWizard
from raundo.wizard_flow import (
    next_investor_step,
    next_startup_step,
    previous_investor_step,
    previous_startup_step,
)


def _startup_ns(**kwargs):  # noqa: ANN003
    base = dict(
        name=None,
        one_liner=None,
        full_description=None,
        primary_sector_id=None,
        stage_id=None,
        website_url=None,
        app_url=None,
        demo_url=None,
        no_link_reason=None,
        intents=None,
        audience_codes=[],
        audience_note=None,
        progress_codes=[],
        monetization_status=None,
        monetization_note=None,
        fundraising_stage=None,
        use_of_funds_codes=[],
        ask_amount_minor=None,
        country=None,
        founder_display_name=None,
        founder_role=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_startup_resume_lands_on_first_missing() -> None:
    empty = _startup_ns()
    assert next_startup_step(empty, photo_count=0) == StartupWizard.name.state

    partial = _startup_ns(
        name="A",
        one_liner="x" * 50,
        full_description="y" * 100,
        primary_sector_id="sec",
        stage_id=None,
    )
    assert next_startup_step(partial, photo_count=0) == StartupWizard.secondary_sectors.state


def test_startup_resume_new_investor_fields_for_legacy_draft() -> None:
    """IMVERA-like draft: core filled, new fields empty → land on audience."""
    legacy = _startup_ns(
        name="IMVERA",
        one_liner="x" * 80,
        full_description="y" * 120,
        primary_sector_id="sec",
        stage_id="stg",
        website_url="https://imvera.ru",
        intents=["investment"],
        founder_display_name="Ivan",
        founder_role="CEO",
    )
    assert next_startup_step(legacy, photo_count=1) == StartupWizard.audience.state

    with_audience = _startup_ns(
        name="IMVERA",
        one_liner="x" * 80,
        full_description="y" * 120,
        primary_sector_id="sec",
        stage_id="stg",
        website_url="https://imvera.ru",
        intents=["investment", "pilots"],
        audience_codes=["b2c"],
        progress_codes=["mvp"],
        monetization_status="planned",
        monetization_note="Подписка B2C",
        founder_display_name="Ivan",
        founder_role="CEO",
    )
    assert next_startup_step(with_audience, photo_count=1) == StartupWizard.fundraising.state


def test_startup_back_chain() -> None:
    assert previous_startup_step(StartupWizard.pitch.state) == StartupWizard.name.state
    assert previous_startup_step(StartupWizard.name.state) == StartupWizard.hub.state
    assert previous_startup_step(StartupWizard.progress.state) == StartupWizard.audience.state
    assert previous_startup_step(StartupWizard.fundraising.state) == (
        StartupWizard.investment_ask.state
    )


def test_investor_resume_skips_cheque_for_accelerator() -> None:
    profile = SimpleNamespace(
        display_name="Acc",
        investor_type="accelerator",
        support_modes=["program"],
        organization_name="Accel X",
        strategy_description="s" * 100,
        check_discuss_individually=False,
        min_check_minor=None,
        max_check_minor=None,
        geography={"scope": "ru"},
        public_reference_url="https://example.com",
        contact_policy="relay",
    )
    step = next_investor_step(profile, sector_codes={"ai_ml"}, stage_codes={"mvp"})
    assert step == InvestorWizard.preview.state


def test_investor_back_skips_org_for_angel() -> None:
    profile = SimpleNamespace(investor_type="angel", support_modes=["direct_equity"])
    prev = previous_investor_step(InvestorWizard.position.state, profile)
    assert prev == InvestorWizard.support_modes.state


def test_investor_resume_after_org_skips_reasking_position() -> None:
    profile = SimpleNamespace(
        display_name="Rep",
        investor_type="vc_fund",
        support_modes=["direct_equity"],
        organization_name="Fund X",
        position="Partner",
        strategy_description=None,
        check_discuss_individually=False,
        min_check_minor=None,
        max_check_minor=None,
        geography={},
        public_reference_url=None,
    )
    step = next_investor_step(profile, sector_codes=set(), stage_codes=set())
    assert step == InvestorWizard.strategy.state
