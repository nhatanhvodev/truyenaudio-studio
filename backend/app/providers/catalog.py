"""Immutable provider/model descriptors used by registry and discovery APIs."""

from __future__ import annotations

from dataclasses import dataclass, field
import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Callable, Iterable

from app.modules.execution.contracts import CapabilityState, ModelSnapshot, PricingClass


@dataclass(frozen=True)
class CatalogFilter:
    provider_id: str | None = None
    language: str | None = None
    pricing: PricingClass | None = None
    stream: CapabilityState | None = None
    structured: CapabilityState | None = None


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

    def list_models(self, filters: CatalogFilter | None = None) -> list[ModelSnapshot]:
        filters = filters or CatalogFilter()
        result: list[ModelSnapshot] = []
        for descriptor in self._descriptors.values():
            if not descriptor.enabled or (filters.provider_id and descriptor.provider_id != filters.provider_id):
                continue
            for model in descriptor.models:
                if filters.language and filters.language not in model.languages:
                    continue
                if filters.pricing is not None and model.pricing.pricing_class != filters.pricing:
                    continue
                if filters.stream is not None and model.capabilities.stream != filters.stream:
                    continue
                if filters.structured is not None and model.capabilities.structured != filters.structured:
                    continue
                result.append(model)
        return sorted(result, key=lambda model: (model.provider_id, model.model_id, model.id))

    def page_models(
        self,
        filters: CatalogFilter | None = None,
        *,
        cursor: str | None = None,
        limit: int = 25,
    ) -> tuple[list[ModelSnapshot], str | None]:
        if not 1 <= limit <= 100:
            raise ValueError("LIMIT_OUT_OF_RANGE")
        models = self.list_models(filters)
        offset = _decode_cursor(cursor) if cursor else 0
        if offset > len(models):
            raise ValueError("CURSOR_INVALID")
        page = models[offset : offset + limit]
        next_cursor = _encode_cursor(offset + limit) if offset + limit < len(models) else None
        return page, next_cursor


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
        return DiscoveryState(state.items, state.etag, state.fetched_at, instant - state.fetched_at > self.ttl, state.error)

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
                state = DiscoveryState(previous.items, previous.etag, instant, False, None)
            else:
                state = DiscoveryState(tuple(response.items), response.etag, instant, False, None)
        except Exception:
            if previous is None:
                raise
            state = DiscoveryState(previous.items, previous.etag, previous.fetched_at, True, "DISCOVERY_UNAVAILABLE")
        self._states[provider_id] = state
        return state
