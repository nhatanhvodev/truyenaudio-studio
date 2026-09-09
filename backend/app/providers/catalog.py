"""Immutable provider/model descriptors used by registry and discovery APIs."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import json
from typing import Callable, Iterable

from app.modules.execution.contracts import (
    ApiKind,
    Availability,
    CapabilityState,
    ModelSnapshot,
    Pricing,
    PricingClass,
    ProviderCapabilities,
)


@dataclass(frozen=True)
class CatalogFilter:
    provider_id: str | None = None
    language: str | None = None
    region: str | None = None
    pricing: PricingClass | None = None
    stream: CapabilityState | None = None
    structured: CapabilityState | None = None
    translation: CapabilityState | None = None
    min_context_tokens: int | None = None
    benchmarked: bool | None = None


@dataclass(frozen=True)
class ProviderDescriptor:
    provider_id: str
    display_name: str
    adapter_name: str
    models: tuple[ModelSnapshot, ...] = field(default_factory=tuple)
    profile_ids: tuple[str, ...] = field(default_factory=tuple)
    enabled: bool = True
    factory: Callable[[object], object] | None = None

    def __init__(
        self,
        provider_id: str,
        display_name: str,
        adapter_name: str,
        *,
        models: Iterable[ModelSnapshot] = (),
        profile_ids: Iterable[str] = (),
        enabled: bool = True,
        factory: Callable[[object], object] | None = None,
    ) -> None:
        if not provider_id.strip() or not adapter_name.strip():
            raise ValueError("PROVIDER_DESCRIPTOR_INVALID")
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "display_name", display_name)
        object.__setattr__(self, "adapter_name", adapter_name)
        object.__setattr__(self, "models", tuple(models))
        object.__setattr__(self, "profile_ids", tuple(profile_ids))
        object.__setattr__(self, "enabled", enabled)
        object.__setattr__(self, "factory", factory)


class ProviderCatalog:
    def __init__(self, descriptors: Iterable[ProviderDescriptor] = ()) -> None:
        self._descriptors: dict[str, ProviderDescriptor] = {}
        for descriptor in descriptors:
            self.register(descriptor)

    def register(self, descriptor: ProviderDescriptor) -> None:
        if descriptor.provider_id in self._descriptors:
            raise ValueError("PROVIDER_ALREADY_REGISTERED")
        self._descriptors[descriptor.provider_id] = descriptor

    def get(self, provider_id: str) -> ProviderDescriptor:
        try:
            return self._descriptors[provider_id]
        except KeyError as exc:
            raise KeyError("PROVIDER_NOT_FOUND") from exc

    def descriptors(self) -> tuple[ProviderDescriptor, ...]:
        return tuple(self._descriptors.values())

    def provider_for_profile(self, profile_id: str) -> str | None:
        for descriptor in self._descriptors.values():
            if profile_id in descriptor.profile_ids:
                return descriptor.provider_id
        return None

    def model_descriptor(self, snapshot_id: str) -> tuple[ProviderDescriptor, ModelSnapshot] | None:
        for descriptor in self._descriptors.values():
            for model in descriptor.models:
                if model.id == snapshot_id:
                    return descriptor, model
        return None

    def list_models(self, filters: CatalogFilter | None = None) -> list[ModelSnapshot]:
        filters = filters or CatalogFilter()
        candidates: list[ModelSnapshot] = []
        for descriptor in self._descriptors.values():
            if not descriptor.enabled or (filters.provider_id and descriptor.provider_id != filters.provider_id):
                continue
            candidates.extend(descriptor.models)
        return filter_model_snapshots(candidates, filters)

    def page_models(
        self,
        filters: CatalogFilter | None = None,
        *,
        cursor: str | None = None,
        limit: int = 25,
    ) -> tuple[list[ModelSnapshot], str | None]:
        if not 1 <= limit <= 100:
            raise ValueError("LIMIT_OUT_OF_RANGE")
        return page_model_snapshots(self.list_models(filters), cursor=cursor, limit=limit)


def filter_model_snapshots(
    models: Iterable[ModelSnapshot],
    filters: CatalogFilter | None = None,
) -> list[ModelSnapshot]:
    filters = filters or CatalogFilter()
    result: list[ModelSnapshot] = []
    for model in models:
        if filters.provider_id and model.provider_id != filters.provider_id:
            continue
        if filters.language and filters.language not in model.languages:
            continue
        if filters.region and model.region != filters.region:
            continue
        if filters.pricing is not None and model.pricing.pricing_class != filters.pricing:
            continue
        if filters.stream is not None and model.capabilities.stream != filters.stream:
            continue
        if filters.structured is not None and model.capabilities.structured != filters.structured:
            continue
        if filters.translation is not None and model.capabilities.translation != filters.translation:
            continue
        if filters.min_context_tokens is not None:
            if model.context_tokens is None or model.context_tokens < filters.min_context_tokens:
                continue
        if filters.benchmarked is True and not model.benchmark_ref:
            continue
        if filters.benchmarked is False and model.benchmark_ref:
            continue
        result.append(model)
    return sorted(result, key=lambda item: (item.provider_id, item.model_id, item.id))


def page_model_snapshots(
    models: Iterable[ModelSnapshot],
    *,
    cursor: str | None = None,
    limit: int = 25,
) -> tuple[list[ModelSnapshot], str | None]:
    if not 1 <= limit <= 100:
        raise ValueError("LIMIT_OUT_OF_RANGE")
    sorted_models = sorted(models, key=lambda model: (model.provider_id, model.model_id, model.id))
    offset = _decode_cursor(cursor) if cursor else 0
    if offset > len(sorted_models):
        raise ValueError("CURSOR_INVALID")
    page = sorted_models[offset : offset + limit]
    next_cursor = _encode_cursor(offset + limit) if offset + limit < len(sorted_models) else None
    return page, next_cursor


def curated_provider_catalog(*, now: datetime | None = None) -> ProviderCatalog:
    fetched_at = now or datetime.now(UTC)
    expires_at = fetched_at + timedelta(hours=24)
    return ProviderCatalog(
        [
            ProviderDescriptor(
                "gemini",
                "Gemini",
                "gemini",
                models=[
                    _curated_snapshot(
                        "curated-gemini-2-5-flash",
                        provider_id="gemini",
                        model_id="gemini-2.5-flash",
                        api_kind=ApiKind.CHAT,
                        context_tokens=1_048_576,
                        max_output_tokens=65_536,
                        stream=CapabilityState.SUPPORTED,
                        structured=CapabilityState.SUPPORTED,
                        translation=CapabilityState.SUPPORTED,
                        source_url="docs/research/provider-research.md#L39",
                        fetched_at=fetched_at,
                        expires_at=expires_at,
                    )
                ],
            ),
            ProviderDescriptor(
                "qwen",
                "Qwen MT",
                "qwen-mt",
                models=[
                    _curated_snapshot(
                        "curated-qwen-mt-flash",
                        provider_id="qwen",
                        model_id="qwen-mt-flash",
                        api_kind=ApiKind.NATIVE_MT,
                        region="frankfurt",
                        context_tokens=8_192,
                        stream=CapabilityState.SUPPORTED,
                        structured=CapabilityState.UNSUPPORTED,
                        translation=CapabilityState.SUPPORTED,
                        source_url="docs/research/provider-research.md#L46",
                        fetched_at=fetched_at,
                        expires_at=expires_at,
                    )
                ],
            ),
            ProviderDescriptor(
                "openrouter",
                "OpenRouter",
                "openrouter",
                models=[
                    _curated_snapshot(
                        "curated-openrouter-router",
                        provider_id="openrouter",
                        model_id="openrouter/auto",
                        api_kind=ApiKind.CHAT,
                        stream=CapabilityState.SUPPORTED,
                        structured=CapabilityState.UNKNOWN,
                        translation=CapabilityState.UNKNOWN,
                        source_url="docs/research/provider-research.md#L38",
                        fetched_at=fetched_at,
                        expires_at=expires_at,
                    )
                ],
            ),
        ]
    )


def _curated_snapshot(
    snapshot_id: str,
    *,
    provider_id: str,
    model_id: str,
    api_kind: ApiKind,
    source_url: str,
    fetched_at: datetime,
    expires_at: datetime,
    region: str | None = None,
    context_tokens: int | None = None,
    max_output_tokens: int | None = None,
    stream: CapabilityState = CapabilityState.UNKNOWN,
    structured: CapabilityState = CapabilityState.UNKNOWN,
    translation: CapabilityState = CapabilityState.UNKNOWN,
) -> ModelSnapshot:
    return ModelSnapshot(
        id=snapshot_id,
        provider_id=provider_id,
        model_id=model_id,
        region=region,
        api_kind=api_kind,
        context_tokens=context_tokens,
        max_output_tokens=max_output_tokens,
        languages=["zh-CN", "vi-VN"],
        capabilities=ProviderCapabilities(stream=stream, structured=structured, translation=translation),
        availability=Availability.UNKNOWN,
        pricing=Pricing(pricing_class=PricingClass.UNKNOWN, currency=None),
        source_url=source_url,
        fetched_at=fetched_at,
        expires_at=expires_at,
        benchmark_ref=None,
    )


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"offset": offset}, separators=(",", ":")).encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> int:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        offset = payload["offset"]
        if type(offset) is not int or offset < 0:
            raise ValueError
        return offset
    except (ValueError, KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError, base64.binascii.Error) as exc:
        raise ValueError("CURSOR_INVALID") from exc


@dataclass(frozen=True)
class DiscoveryResponse:
    items: tuple[ModelSnapshot, ...] = ()
    etag: str | None = None
    not_modified: bool = False


@dataclass(frozen=True)
class DiscoveryState:
    items: tuple[ModelSnapshot, ...]
    etag: str | None
    fetched_at: datetime
    stale: bool
    error: str | None = None
    not_modified: bool = False


class DiscoveryCache:
    """Keep the last good catalog snapshot across provider outages."""

    def __init__(self, *, ttl: timedelta = timedelta(hours=24), cooldown: timedelta = timedelta(seconds=60)) -> None:
        self.ttl = ttl
        self.cooldown = cooldown
        self._states: dict[str, DiscoveryState] = {}
        self._last_refresh: dict[str, datetime] = {}

    def get(self, provider_id: str, *, now: datetime | None = None) -> DiscoveryState | None:
        state = self._states.get(provider_id)
        if state is None:
            return None
        instant = now or datetime.now(UTC)
        return DiscoveryState(
            state.items,
            state.etag,
            state.fetched_at,
            instant - state.fetched_at > self.ttl,
            state.error,
            state.not_modified,
        )

    def refresh(self, provider_id: str, fetcher: Callable[[str | None], DiscoveryResponse], *, now: datetime | None = None) -> DiscoveryState:
        instant = now or datetime.now(UTC)
        previous = self._states.get(provider_id)
        last = self._last_refresh.get(provider_id)
        if last is not None and instant - last < self.cooldown and previous is not None:
            return self.get(provider_id, now=instant) or previous
        self._last_refresh[provider_id] = instant
        try:
            response = fetcher(previous.etag if previous else None)
            if response.not_modified and previous is not None:
                state = DiscoveryState(previous.items, previous.etag, instant, False, None, True)
            elif response.not_modified:
                raise RuntimeError("DISCOVERY_NOT_MODIFIED_WITHOUT_SNAPSHOT")
            else:
                state = DiscoveryState(tuple(response.items), response.etag, instant, False, None, False)
        except Exception:
            if previous is None:
                raise
            state = DiscoveryState(previous.items, previous.etag, previous.fetched_at, True, "DISCOVERY_UNAVAILABLE", False)
        self._states[provider_id] = state
        return state
