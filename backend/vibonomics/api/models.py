"""Model table data and per-model details."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..catalog.modelsdev import ModelsDevCatalog, load_catalog
from ..db import get_session, session_scope
from ..http import FetchError, public_url
from ..orm import EvalResult, Model, PriceHistory, ProviderConfig
from ..provider_service import resolve
from ..providers.base import FetchContext
from ..providers.registry import get_adapter_class
from ..scoring import ModelScores, compute_scores, own_test_scores, score_key
from ..settings_store import AppSettings, UsageProfile, load_settings
from ..snippets import Routing, SnippetContext, generate

router = APIRouter(prefix="/api/models", tags=["models"])


class PriceOut(BaseModel):
    input: float | None = None
    output: float | None = None
    cache_read: float | None = None
    cache_write: float | None = None


class ScoresOut(BaseModel):
    intelligence: float | None = None
    coding: float | None = None
    agentic: float | None = None
    composite: float | None = None
    own_test: float | None = None
    source_count: int = 0


class TestSummaryOut(BaseModel):
    score: float | None = None
    tasks: int = 0
    runs: int = 0
    last_run_at: datetime | None = None
    tokens_per_task: int | None = None
    cost_per_task: float | None = None


class ModelRow(BaseModel):
    id: int
    provider_id: int
    provider_type: str
    provider_name: str
    model_id: str
    name: str
    creator: str | None
    canonical_id: str | None
    family: str | None
    context_length: int | None
    max_output: int | None
    input_modalities: list[str]
    reasoning: bool | None
    tool_call: bool | None
    open_weights: bool | None
    release_date: str | None
    price: PriceOut
    list_price: PriceOut | None
    discount: float | None
    discount_note: str | None
    discount_until: str | None
    is_free: bool
    pricing_source: str
    first_seen: datetime
    is_new: bool
    new_discount: bool
    price_drop: bool
    active: bool
    testable: bool
    scores: ScoresOut
    reported_cost_per_task: float | None
    test: TestSummaryOut | None
    client_restricted: str | None = None
    """The only client that may call this model (``opencode`` for the OpenCode Console's free tier), else None."""


class ModelsResponse(BaseModel):
    generated_at: datetime
    models: list[ModelRow]
    profiles: list[UsageProfile]
    default_profile: str
    provider_count: int


def _row(
    m: Model,
    provider: ProviderConfig,
    sc: ModelScores | None,
    own: dict[str, Any] | None,
    settings: AppSettings,
    testable: bool,
    now: datetime,
) -> ModelRow:
    new_cut = now - timedelta(days=settings.highlight.new_model_days)
    disc_cut = now - timedelta(days=settings.highlight.new_discount_days)
    released_recently = bool(m.release_date and m.release_date >= new_cut.date().isoformat())
    is_new = (not m.baseline and m.first_seen >= new_cut) or released_recently
    return ModelRow(
        id=m.id,
        provider_id=provider.id,
        provider_type=provider.type,
        provider_name=provider.name,
        model_id=m.model_id,
        name=m.name,
        creator=m.creator,
        canonical_id=m.canonical_id,
        family=m.family,
        context_length=m.context_length,
        max_output=m.max_output,
        input_modalities=list(m.input_modalities or []),
        reasoning=m.reasoning,
        tool_call=m.tool_call,
        open_weights=m.open_weights,
        release_date=m.release_date,
        price=PriceOut(
            input=m.price_input, output=m.price_output, cache_read=m.price_cache_read, cache_write=m.price_cache_write
        ),
        list_price=PriceOut(input=m.list_input, output=m.list_output) if m.discount else None,
        discount=m.discount,
        discount_note=m.discount_note,
        discount_until=m.discount_until,
        is_free=m.is_free,
        pricing_source=m.pricing_source,
        first_seen=m.first_seen,
        is_new=is_new,
        new_discount=bool(m.discount and m.discount_since and m.discount_since >= disc_cut),
        price_drop=bool(m.price_drop_since and m.price_drop_since >= disc_cut),
        active=m.active,
        testable=testable,
        scores=ScoresOut(**sc.summary()) if sc else ScoresOut(),
        reported_cost_per_task=sc.reported_cost_per_task if sc else None,
        test=TestSummaryOut(**{k: v for k, v in own.items() if k != "canonical_id"}) if own else None,
        client_restricted=(m.extra or {}).get("client_restricted") or None,
    )


def _testable(provider: ProviderConfig) -> bool:
    cls = get_adapter_class(provider.type)
    if cls is None:
        return False
    return bool(resolve(provider).api_key) or cls.spec.custom


@router.get("", response_model=ModelsResponse)
def list_models(session: Session = Depends(get_session)) -> ModelsResponse:
    settings = load_settings(session)
    providers = {p.id: p for p in session.scalars(select(ProviderConfig).where(ProviderConfig.enabled.is_(True)))}
    query = select(Model).where(Model.provider_config_id.in_(providers.keys()))
    if settings.general.hide_inactive_models:
        query = query.where(Model.active.is_(True))
    own = own_test_scores(session)
    scores = compute_scores(session, settings, own)
    testable = {pid: _testable(p) for pid, p in providers.items()}
    now = datetime.now(UTC)
    rows = [
        _row(
            m,
            providers[m.provider_config_id],
            scores.get(score_key(m.canonical_id, m.provider_config_id, m.model_id)),
            own.get((m.provider_config_id, m.model_id)),
            settings,
            testable[m.provider_config_id] and not (providers[m.provider_config_id].type == "opencode" and m.is_free),
            now,
        )
        for m in session.scalars(query)
    ]
    return ModelsResponse(
        generated_at=now,
        models=rows,
        profiles=settings.scoring.profiles,
        default_profile=settings.scoring.default_profile,
        provider_count=len(providers),
    )


class MetricOut(BaseModel):
    source: str
    source_name: str
    metric: str
    label: str
    dimension: str
    value: float
    percentile: float | None
    effort: str
    as_of: str | None
    url: str | None
    cost_per_task: float | None


class PricePointOut(BaseModel):
    observed_at: datetime
    price_input: float | None
    price_output: float | None
    price_cache_read: float | None
    discount: float | None


class OtherOfferOut(BaseModel):
    id: int
    provider_name: str
    model_id: str
    price: PriceOut
    discount: float | None
    is_free: bool


class TestResultOut(BaseModel):
    run_id: int
    task_id: str
    category: str
    status: str
    score: float
    judge_notes: str | None
    created_at: datetime


class SnippetOut(BaseModel):
    id: str
    harness: str
    title: str
    language: str
    content: str
    filename: str | None = None
    notes: str | None = None
    docs_url: str | None = None


class ModelDetail(BaseModel):
    row: ModelRow
    description: str | None
    data_use: str | None = None
    """What the provider says it does with your prompts on this model, when it says (free OpenCode models)."""
    metrics: list[MetricOut]
    reported_cost_sources: list[str]
    price_history: list[PricePointOut]
    other_offers: list[OtherOfferOut]
    endpoints: list[dict[str, Any]]
    tests: list[TestResultOut]
    snippets: list[SnippetOut]
    attributions: list[str]


def build_snippets(m: Model, provider: ProviderConfig, routing: Routing | None = None) -> list[SnippetOut]:
    cls = get_adapter_class(provider.type)
    if cls is None:
        return []
    spec = cls.spec
    # The adapter knows where this account's key works (Moonshot's China platform, Z.ai's coding endpoint) and how
    # OpenCode serves one model; it needs nothing from the catalogue to say so.
    adapter = cls(resolve(provider), FetchContext(catalog=ModelsDevCatalog({}), settings=AppSettings()))
    extra = m.extra or {}
    # Snippets reach visitors of an open-read instance, so a custom URL goes out without its credentials or query.
    base_url = public_url(adapter.base_url)
    anthropic_url = adapter.anthropic_base_url
    route = adapter.model_route(extra)
    md_provider = extra.get("modelsdev_provider") or (spec.modelsdev_ids[0] if spec.modelsdev_ids else None)
    env_key = spec.type.upper().replace("-", "_") + "_API_KEY"
    ctx = SnippetContext(
        spec=spec,
        provider_name=provider.name,
        base_url=base_url,
        anthropic_base_url=public_url(anthropic_url) if anthropic_url else None,
        model_id=m.model_id,
        model_name=m.name,
        context_length=m.context_length,
        max_output=m.max_output,
        tool_call=m.tool_call,
        reasoning=m.reasoning,
        vision="image" in (m.input_modalities or []),
        modelsdev_provider=md_provider,
        env_key=env_key,
        price_input=m.price_input,
        price_output=m.price_output,
        routing=routing,
        client_restricted=extra.get("client_restricted") or None,
        route=route,
    )
    return [SnippetOut(**s.to_dict()) for s in generate(ctx)]


@router.get("/{model_pk}/snippets", response_model=list[SnippetOut])
def model_snippets(
    model_pk: int,
    variant: Literal["floor", "nitro"] | None = None,
    provider: str | None = None,
    fallbacks: bool = False,
    session: Session = Depends(get_session),
) -> list[SnippetOut]:
    """The config snippets for an OpenRouter model routed to its cheapest or fastest provider, or pinned to one."""
    m = session.get(Model, model_pk)
    if m is None:
        raise HTTPException(404, "Model not found")
    if m.provider.type != "openrouter":
        raise HTTPException(422, "Routing applies to OpenRouter models only")
    if variant and provider:
        raise HTTPException(422, "Choose a variant or a provider, not both")
    if variant and ":" in m.model_id:
        raise HTTPException(422, "This model id already carries a variant")
    if provider and provider not in {e.get("tag") for e in (m.extra or {}).get("endpoints") or []}:
        raise HTTPException(422, "Unknown provider endpoint for this model")
    return build_snippets(m, m.provider, Routing(variant=variant, provider=provider, allow_fallbacks=fallbacks))


@router.get("/{model_pk}", response_model=ModelDetail)
async def model_detail(model_pk: int) -> ModelDetail:
    try:
        catalog = await load_catalog()
    except FetchError:
        catalog = None  # it only supplies a fallback description; the details are worth showing without one
    # The rest is queries and scoring over every benchmark row: off the loop, as list_models is.
    return await run_in_threadpool(_model_detail, model_pk, catalog)


def _model_detail(model_pk: int, catalog: ModelsDevCatalog | None) -> ModelDetail:
    with session_scope() as session:
        m = session.get(Model, model_pk)
        if m is None:
            raise HTTPException(404, "Model not found")
        provider = m.provider
        settings = load_settings(session)
        own = own_test_scores(session)
        scores = compute_scores(session, settings, own)
        key = score_key(m.canonical_id, m.provider_config_id, m.model_id)
        sc = scores.get(key)
        row = _row(
            m,
            provider,
            sc,
            own.get((m.provider_config_id, m.model_id)),
            settings,
            _testable(provider) and not (provider.type == "opencode" and m.is_free),
            datetime.now(UTC),
        )
        history = session.scalars(
            select(PriceHistory).where(PriceHistory.model_pk == m.id).order_by(PriceHistory.observed_at)
        ).all()
        others: list[OtherOfferOut] = []
        if m.canonical_id:
            for o in session.scalars(
                select(Model).where(Model.canonical_id == m.canonical_id, Model.id != m.id, Model.active.is_(True))
            ):
                if not o.provider.enabled:
                    continue
                others.append(
                    OtherOfferOut(
                        id=o.id,
                        provider_name=o.provider.name,
                        model_id=o.model_id,
                        price=PriceOut(input=o.price_input, output=o.price_output, cache_read=o.price_cache_read),
                        discount=o.discount,
                        is_free=o.is_free,
                    )
                )
        tests = session.scalars(
            select(EvalResult)
            .where(EvalResult.provider_config_id == m.provider_config_id, EvalResult.model_id == m.model_id)
            .order_by(EvalResult.created_at.desc())
            .limit(100)
        ).all()
        from ..benchmarks.registry import SOURCES

        metrics = [
            MetricOut(**{k: getattr(mv, k) for k in MetricOut.model_fields}) for mv in (sc.metrics if sc else [])
        ]
        attributions = sorted({SOURCES[mv.source].spec.attribution for mv in metrics if mv.source in SOURCES})
        description = (m.extra or {}).get("description")
        if not description and m.canonical_id and catalog is not None:
            info = catalog.canonical_index().get(m.canonical_id)
            description = info.name if info else None
        return ModelDetail(
            row=row,
            description=description,
            data_use=(m.extra or {}).get("data_use") or None,
            metrics=metrics,
            reported_cost_sources=sc.reported_cost_sources if sc else [],
            price_history=[PricePointOut.model_validate(h, from_attributes=True) for h in history],
            # An offer with no known price is not the cheapest: it goes last.
            other_offers=sorted(
                others,
                key=lambda o: (
                    o.price.input is None or o.price.output is None,
                    (o.price.input or 0) + (o.price.output or 0),
                ),
            ),
            endpoints=list((m.extra or {}).get("endpoints") or []),
            tests=[
                TestResultOut(
                    run_id=t.run_id,
                    task_id=t.task_id,
                    category=t.category,
                    status=t.status,
                    score=t.score,
                    judge_notes=t.judge_notes,
                    created_at=t.created_at,
                )
                for t in tests
            ],
            snippets=build_snippets(m, provider),
            attributions=attributions,
        )
