"""Offline contract tests for the Hugging Face hosted adapter (plan X05).

Every case is hermetic: a stub HTTP client stands in for the network and no
socket is opened, so what is asserted is what the adapter would actually put on
the wire — the native text-generation body, the credential placement, the
reported model/usage, and the error/billing-state mapping.

The download-boundary test reads the adapter module itself (AST + allowlist of
import roots): the adapter must stay HTTP-only, importing no local model runtime
and calling no download helper, so a local LLM cannot be switched on through
this path.

NOT RUN here (and not claimed): no live Hugging Face account, token or region
was exercised; gated-model acceptance, per-model licence, plan/region,
inference-provider routing and price stay unverified.
"""

from __future__ import annotations

import ast
import inspect
import json
from dataclasses import fields, is_dataclass
from typing import Any

import httpx
import pytest

from app.contracts import OperationContext, TranslationRequest, UsageUnit
from app.modules.compliance.cloud import CloudCallBlocked, CloudCallDecision
from app.modules.execution.contracts import BillingState
from app.providers import huggingface
from app.providers.huggingface import (
    API_KIND,
    BILLING_UNKNOWN,
    ENDPOINT,
    ENDPOINT_INVALID,
    PROVIDER,
    SOURCE_HOSTED,
    SOURCE_REQUIRED,
    TASK_TEXT_GENERATION,
    TASK_UNSUPPORTED,
    HuggingFaceAdapter,
    ProviderBillingUnknown,
    build_endpoint,
    resolve_task,
)
from app.providers.transport import ProviderTransportError
from tests.providers.contract_suite import install_socket_tripwire


SECRET = "hf-token-do-not-leak-001"
REQUESTED_MODEL = "Qwen/Qwen3-4B"
ROUTED_MODEL = "meta-llama/Llama-3.2-1B-Instruct"
TRANSLATED = "Cô ấy mở cửa."
REQUEST_ID = "req-hf-001"


def _request(**overrides: object) -> TranslationRequest:
    payload: dict[str, object] = {
        "context": OperationContext(
            operation_id="op-hf-1",
            cache_key="cache-hf-1",
            timeout_seconds=17,
            estimated_units=42,
            budget_authorization_id="auth-1",
            cloud_consent_id="consent-1",
        ),
        "source_segment_id": "seg-1",
        "source_text": "她打开门。",
        "source_language": "zh-CN",
        "target_language": "vi-VN",
        "terms": (("门", "cửa"),),
        "tm_list": (),
        "domain_instruction": "natural",
        "story_memory": (),
    }
    payload.update(overrides)
    return TranslationRequest(**payload)  # type: ignore[arg-type]


@pytest.fixture
def translation_request() -> TranslationRequest:
    return _request()


def _list_body(**overrides: Any) -> list[dict[str, Any]]:
    item: dict[str, Any] = {
        "generated_text": TRANSLATED,
        "model": ROUTED_MODEL,
        "request_id": REQUEST_ID,
        "usage": {"input_tokens": 21, "output_tokens": 7},
    }
    item.update(overrides)
    return [item]


class Response:
    def __init__(self, body: Any, status_code: int = 200) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class Http:
    """Stub inference client that records the outgoing request."""

    def __init__(self, response: Response | None = None) -> None:
        self.response = response if response is not None else Response(_list_body())
        self.calls = 0
        self.last_url = ""
        self.last_json: dict[str, Any] = {}
        self.last_headers: dict[str, str] = {}
        self.last_timeout: int | None = None

    async def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: int) -> Response:
        self.calls += 1
        self.last_url = url
        self.last_json = json
        self.last_headers = headers
        self.last_timeout = timeout
        return self.response


class StatusHttp:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.calls = 0

    async def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: int) -> Response:
        self.calls += 1
        return Response({"error": f"upstream echoed secret={SECRET}"}, self.status_code)


class RaisingHttp:
    def __init__(self, exception: Exception) -> None:
        self.exception = exception
        self.calls = 0
        self.last_json: dict[str, Any] = {}

    async def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: int) -> Response:
        self.calls += 1
        self.last_json = json
        raise self.exception


class AllowingGuard:
    def evaluate(self, **kwargs: object) -> CloudCallDecision:
        return CloudCallDecision(
            allowed=True,
            cloud_consent_id=str(kwargs["cloud_consent_id"]),
            authorization_id=str(kwargs["budget_authorization_id"]),
            rate_card_ids=("rate-card-001",),
            remaining_quota=(),
            reasons=(),
        )


class DenyingGuard:
    def evaluate(self, **kwargs: object) -> CloudCallDecision:
        return CloudCallDecision(
            allowed=False,
            cloud_consent_id=None,
            authorization_id=None,
            rate_card_ids=(),
            remaining_quota=(),
            reasons=("CONSENT_NOT_GRANTED",),
        )


def _adapter(http: object, model: str = REQUESTED_MODEL, **kwargs: object) -> HuggingFaceAdapter:
    return HuggingFaceAdapter(
        http,
        model,
        SECRET,
        cloud_guard=AllowingGuard(),
        project_id="project-001",
        provider_profile_id="profile-001",
        **kwargs,  # type: ignore[arg-type]
    )


def _string_leaves(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if is_dataclass(value) and not isinstance(value, type):
        leaves: list[str] = []
        for field in fields(value):
            leaves.extend(_string_leaves(getattr(value, field.name)))
        return leaves
    if isinstance(value, (tuple, list)):
        leaves = []
        for item in value:
            leaves.extend(_string_leaves(item))
        return leaves
    return []


# --- translate: native hosted text-generation wire shape ----------------------


@pytest.mark.asyncio
async def test_huggingface_posts_the_native_text_generation_body(translation_request: TranslationRequest) -> None:
    http = Http()

    result = await _adapter(http).translate(translation_request)

    assert result.target_text == TRANSLATED
    assert result.provider == PROVIDER
    assert result.provider_version == "inference-api"
    assert http.calls == 1
    # The repository id is a path parameter: it must not be smuggled into a body
    # field, and the credential must never reach the URL.
    assert http.last_url == f"https://api-inference.huggingface.co/models/{REQUESTED_MODEL}"
    assert http.last_url == build_endpoint(REQUESTED_MODEL)
    assert SECRET not in http.last_url
    payload = http.last_json
    assert set(payload) == {"inputs", "parameters", "options"}
    assert isinstance(payload["inputs"], str)
    assert payload["inputs"].startswith("Translate faithfully")
    assert "她打开门" in payload["inputs"]
    assert "zh-CN" in payload["inputs"] and "vi-VN" in payload["inputs"]
    assert "门 -> cửa" in payload["inputs"]
    assert payload["parameters"] == {"return_full_text": False}
    # No silent cold-start wait: a loading model answers with an error that the
    # policy layer decides about, rather than holding the request open here.
    assert payload["options"] == {"wait_for_model": False}
    assert "model" not in payload
    assert http.last_headers["Authorization"] == f"Bearer {SECRET}"
    assert http.last_headers["Content-Type"] == "application/json"
    assert http.last_timeout == 17


@pytest.mark.asyncio
async def test_huggingface_maps_usage_tokens_and_request_id(translation_request: TranslationRequest) -> None:
    http = Http()

    result = await _adapter(http).translate(translation_request)

    assert result.usage[0].unit == UsageUnit.INPUT_TOKEN.value
    assert result.usage[0].measured_units == 21
    assert result.usage[1].unit == UsageUnit.OUTPUT_TOKEN.value
    assert result.usage[1].measured_units == 7
    assert result.usage[0].provider_request_id == REQUEST_ID


@pytest.mark.asyncio
async def test_huggingface_reports_the_provider_model_instead_of_the_requested_one(
    translation_request: TranslationRequest,
) -> None:
    http = Http(Response(_list_body(model=ROUTED_MODEL)))

    result = await _adapter(http).translate(translation_request)

    assert result.model == ROUTED_MODEL
    assert result.model != REQUESTED_MODEL
    # The requested repository stays in the path; the reported one is what a
    # reader sees, so a substitution can never be mistaken for the request.
    assert "model" not in http.last_json


@pytest.mark.asyncio
async def test_huggingface_accepts_the_document_object_response_shape(
    translation_request: TranslationRequest,
) -> None:
    http = Http(Response({"generated_text": f"  {TRANSLATED}  "}))

    result = await _adapter(http).translate(translation_request)

    assert result.target_text == TRANSLATED


@pytest.mark.asyncio
async def test_huggingface_keeps_requested_model_and_zero_usage_when_provider_reports_none(
    translation_request: TranslationRequest,
) -> None:
    http = Http(Response({"generated_text": TRANSLATED}))

    result = await _adapter(http).translate(translation_request)

    # Nothing is invented: an omitted model stays the requested one and omitted
    # token counts stay zero instead of being estimated.
    assert result.model == REQUESTED_MODEL
    assert result.usage[0].measured_units == 0
    assert result.usage[1].measured_units == 0
    assert result.usage[0].provider_request_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("usage", [{"input_tokens": "many"}, {"input_tokens": -3}, {"input_tokens": True}])
async def test_huggingface_never_reports_a_negative_or_non_integer_token_count(
    translation_request: TranslationRequest,
    usage: dict[str, Any],
) -> None:
    http = Http(Response(_list_body(usage=usage)))

    result = await _adapter(http).translate(translation_request)

    assert result.usage[0].measured_units == 0
    assert result.usage[1].measured_units == 0


# --- hosted-only capability surface ------------------------------------------


def test_huggingface_capabilities_declare_hosted_text_generation() -> None:
    adapter = _adapter(Http())

    capabilities = adapter.capabilities()

    assert capabilities["provider"] == PROVIDER
    assert capabilities["model"] == REQUESTED_MODEL
    assert capabilities["api_kind"] == API_KIND == TASK_TEXT_GENERATION == "text-generation"
    assert capabilities["task"] == TASK_TEXT_GENERATION
    # Hosted and explicit: nothing in this adapter implies a local runtime.
    assert capabilities["source"] == SOURCE_HOSTED == "HOSTED"
    assert capabilities["local_model_download"] is False
    assert capabilities["network"] is True
    assert capabilities == adapter.capabilities()
    assert SECRET not in json.dumps(capabilities)


@pytest.mark.asyncio
@pytest.mark.parametrize("task", ["text-to-image", "automatic-speech-recognition", "feature-extraction", "", None])
async def test_huggingface_refuses_an_unsupported_task_before_any_http(
    translation_request: TranslationRequest,
    task: object,
) -> None:
    http = Http()

    with pytest.raises(ValueError, match=TASK_UNSUPPORTED) as exc:
        _adapter(http, task=task).translate(translation_request)

    assert str(exc.value) == TASK_UNSUPPORTED
    assert http.calls == 0


@pytest.mark.asyncio
async def test_huggingface_serves_the_supported_task_when_asked_explicitly(
    translation_request: TranslationRequest,
) -> None:
    http = Http()

    result = await _adapter(http, task=TASK_TEXT_GENERATION).translate(translation_request)

    assert result.target_text == TRANSLATED
    assert http.calls == 1


def test_huggingface_resolves_the_task_from_model_metadata() -> None:
    # Routing/task come from the catalog metadata instead of a guess.
    assert resolve_task(None) == TASK_TEXT_GENERATION
    assert resolve_task({}) == TASK_TEXT_GENERATION
    assert resolve_task({"pipeline_tag": "text-generation"}) == TASK_TEXT_GENERATION
    assert resolve_task({"task": "text-generation"}) == TASK_TEXT_GENERATION
    assert resolve_task({"model_id": REQUESTED_MODEL}) == TASK_TEXT_GENERATION
    with pytest.raises(ValueError, match=TASK_UNSUPPORTED):
        resolve_task({"pipeline_tag": "text-to-image"})
    with pytest.raises(ValueError, match=TASK_UNSUPPORTED):
        resolve_task({"task": "automatic-speech-recognition"})


# --- guard before anything else ----------------------------------------------


@pytest.mark.asyncio
async def test_huggingface_guard_missing_blocks_before_any_http(translation_request: TranslationRequest) -> None:
    http = Http()
    adapter = HuggingFaceAdapter(http, REQUESTED_MODEL, SECRET)

    with pytest.raises(CloudCallBlocked) as exc:
        await adapter.translate(translation_request)

    assert exc.value.reasons == ("CLOUD_GUARD_REQUIRED",)
    assert http.calls == 0


@pytest.mark.asyncio
async def test_huggingface_guard_context_missing_blocks_before_any_http(
    translation_request: TranslationRequest,
) -> None:
    http = Http()
    adapter = HuggingFaceAdapter(http, REQUESTED_MODEL, SECRET, cloud_guard=AllowingGuard())

    with pytest.raises(CloudCallBlocked) as exc:
        await adapter.translate(translation_request)

    assert exc.value.reasons == ("CLOUD_GUARD_CONTEXT_REQUIRED",)
    assert http.calls == 0


@pytest.mark.asyncio
async def test_huggingface_denied_guard_blocks_before_any_http(translation_request: TranslationRequest) -> None:
    http = Http()
    adapter = HuggingFaceAdapter(
        http,
        REQUESTED_MODEL,
        SECRET,
        cloud_guard=DenyingGuard(),
        project_id="project-001",
        provider_profile_id="profile-001",
    )

    with pytest.raises(CloudCallBlocked) as exc:
        await adapter.translate(translation_request)

    assert exc.value.reasons == ("CONSENT_NOT_GRANTED",)
    assert http.calls == 0


@pytest.mark.asyncio
async def test_huggingface_guard_precedes_source_validation() -> None:
    http = Http()

    with pytest.raises(CloudCallBlocked):
        await HuggingFaceAdapter(http, REQUESTED_MODEL, SECRET).translate(_request(source_text="  "))

    assert http.calls == 0


@pytest.mark.asyncio
async def test_huggingface_rejects_empty_source_before_any_http(translation_request: TranslationRequest) -> None:
    http = Http()

    with pytest.raises(ValueError, match=SOURCE_REQUIRED):
        await _adapter(http).translate(_request(source_text="   "))

    assert http.calls == 0
    assert translation_request.source_text == "她打开门。"


@pytest.mark.asyncio
async def test_huggingface_missing_token_fails_closed_before_any_http(
    translation_request: TranslationRequest,
) -> None:
    http = Http()
    adapter = HuggingFaceAdapter(
        http,
        REQUESTED_MODEL,
        "",
        cloud_guard=AllowingGuard(),
        project_id="project-001",
        provider_profile_id="profile-001",
    )

    with pytest.raises(ProviderTransportError) as exc:
        await adapter.translate(translation_request)

    assert exc.value.code == "PROVIDER_AUTH_MISSING"
    assert exc.value.billing_state is BillingState.NOT_SENT
    assert http.calls == 0


# --- transport / billing state ------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "billing_state", "retryable"),
    [
        (400, BillingState.KNOWN, False),
        (401, BillingState.KNOWN, False),
        (403, BillingState.KNOWN, False),
        (404, BillingState.KNOWN, False),
        (422, BillingState.UNKNOWN, False),
        (429, BillingState.UNKNOWN, True),
        (500, BillingState.UNKNOWN, True),
        # A cold repository answers 503 while it loads: normalized, never waited on.
        (503, BillingState.UNKNOWN, True),
    ],
)
async def test_huggingface_http_error_matrix_maps_billing_state(
    translation_request: TranslationRequest,
    status_code: int,
    billing_state: BillingState,
    retryable: bool,
) -> None:
    http = StatusHttp(status_code)

    with pytest.raises(ProviderTransportError) as exc:
        await _adapter(http).translate(translation_request)

    assert exc.value.code == f"HTTP_{status_code}"
    assert exc.value.billing_state is billing_state
    assert exc.value.retryable is retryable
    assert http.calls == 1
    assert SECRET not in str(exc.value)


@pytest.mark.asyncio
async def test_huggingface_timeout_after_send_is_billing_unknown_without_retry(
    translation_request: TranslationRequest,
) -> None:
    http = RaisingHttp(TimeoutError("provider timed out after dispatch"))

    with pytest.raises(ProviderBillingUnknown) as exc:
        await _adapter(http).translate(translation_request)

    assert str(exc.value) == BILLING_UNKNOWN
    # Exactly one attempt: retry belongs to the J02 policy layer, not here.
    assert http.calls == 1
    assert "她打开门" in http.last_json["inputs"]
    assert SECRET not in repr(http.last_json)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exception",
    [httpx.ReadTimeout("read timed out"), httpx.ConnectError("connection died after dispatch")],
)
async def test_huggingface_transport_error_after_send_is_billing_unknown(
    translation_request: TranslationRequest,
    exception: Exception,
) -> None:
    http = RaisingHttp(exception)

    with pytest.raises(ProviderBillingUnknown) as exc:
        await _adapter(http).translate(translation_request)

    assert str(exc.value) == BILLING_UNKNOWN
    assert http.calls == 1


@pytest.mark.asyncio
async def test_huggingface_json_decode_failure_fails_closed_as_unknown(
    translation_request: TranslationRequest,
) -> None:
    http = Http(Response(ValueError(f"secret={SECRET} is not json")))

    with pytest.raises(ProviderTransportError) as exc:
        await _adapter(http).translate(translation_request)

    assert exc.value.code == "MALFORMED_RESPONSE"
    assert exc.value.billing_state is BillingState.UNKNOWN
    assert SECRET not in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["not-an-object", 12, 4.5, True, None])
async def test_huggingface_non_json_document_shapes_are_malformed(
    translation_request: TranslationRequest,
    body: object,
) -> None:
    http = Http(Response(body))

    with pytest.raises(ProviderTransportError) as exc:
        await _adapter(http).translate(translation_request)

    assert exc.value.code == "MALFORMED_RESPONSE"
    assert exc.value.billing_state is BillingState.UNKNOWN


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ([], "EMPTY_RESPONSE"),
        ([{}], "EMPTY_RESPONSE"),
        ([{"error": f"model is currently loading secret={SECRET}"}], "EMPTY_RESPONSE"),
        ([{"error": "boom", "generated_text": TRANSLATED}], "EMPTY_RESPONSE"),
        ([{"generated_text": "   "}], "EMPTY_RESPONSE"),
        ([{"generated_text": 42}], "EMPTY_RESPONSE"),
        ([{"generated_text": None}], "EMPTY_RESPONSE"),
        ([{"output_text": TRANSLATED}], "EMPTY_RESPONSE"),
        ([["nested"]], "EMPTY_RESPONSE"),
        ({"error": {"message": "gated repository"}}, "EMPTY_RESPONSE"),
        ({"foo": "bar"}, "EMPTY_RESPONSE"),
        # A bare string is not one of the two documented shapes at all.
        ("a-string-body", "MALFORMED_RESPONSE"),
    ],
)
async def test_huggingface_missing_or_error_generated_text_is_empty_response(
    translation_request: TranslationRequest,
    body: object,
    expected: str,
) -> None:
    http = Http(Response(body))

    with pytest.raises(ProviderTransportError) as exc:
        await _adapter(http).translate(translation_request)

    assert exc.value.code == expected
    # An error envelope is never echoed back as the translation.
    assert exc.value.billing_state is BillingState.UNKNOWN
    assert SECRET not in str(exc.value)
    assert TRANSLATED not in str(exc.value)


# --- routing / credential boundaries -----------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint",
    ["https://attacker.example/collect", "https://api-inference.huggingface.co/models/other", ENDPOINT + "/extra", 5],
)
async def test_huggingface_rejects_an_arbitrary_endpoint_before_any_bearer_request(
    translation_request: TranslationRequest,
    endpoint: object,
) -> None:
    http = Http()

    with pytest.raises(ValueError, match=ENDPOINT_INVALID):
        _adapter(http, endpoint=endpoint).translate(translation_request)

    assert http.calls == 0


def test_huggingface_accepts_the_canonical_endpoint_forms() -> None:
    adapter = _adapter(Http(), endpoint=ENDPOINT)

    assert adapter.endpoint == f"https://api-inference.huggingface.co/models/{REQUESTED_MODEL}"
    assert build_endpoint(REQUESTED_MODEL) == adapter.endpoint


def test_huggingface_accepts_a_prefilled_canonical_endpoint() -> None:
    http = Http()
    prefilled = f"https://api-inference.huggingface.co/models/{REQUESTED_MODEL}"

    adapter = _adapter(http, endpoint=prefilled)

    assert adapter.endpoint == prefilled


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model",
    ["Qwen/Qwen3-4B?token=payload", "../etc/passwd", "org/model name", "", "org//model", "org/model#frag"],
)
async def test_huggingface_rejects_an_unsafe_model_before_any_http(
    translation_request: TranslationRequest,
    model: str,
) -> None:
    http = Http()

    with pytest.raises(ValueError, match="MODEL_IDENTIFIER_INVALID"):
        _adapter(http, model=model).translate(translation_request)

    assert http.calls == 0


@pytest.mark.asyncio
async def test_huggingface_never_places_or_returns_the_api_key(translation_request: TranslationRequest) -> None:
    http = Http()

    result = await _adapter(http).translate(translation_request)

    assert SECRET not in json.dumps(http.last_json, ensure_ascii=False)
    assert SECRET not in http.last_url
    assert httpx.URL(http.last_url).params == httpx.QueryParams()
    assert http.last_headers["Authorization"] == f"Bearer {SECRET}"
    # The token appears in exactly one place on the wire: that header.
    assert [name for name, value in http.last_headers.items() if SECRET in value] == ["Authorization"]
    assert all(SECRET not in leaf for leaf in _string_leaves(result))
    assert SECRET not in repr(result)
    assert SECRET not in repr(_adapter(http))

    failing = StatusHttp(401)
    with pytest.raises(ProviderTransportError) as exc:
        await _adapter(failing).translate(translation_request)
    assert SECRET not in str(exc.value)
    assert SECRET not in repr(exc.value)


@pytest.mark.asyncio
async def test_huggingface_contract_runs_without_opening_a_socket(
    translation_request: TranslationRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network_calls = install_socket_tripwire(monkeypatch)
    http = Http()

    result = await _adapter(http).translate(translation_request)

    assert result.provider == PROVIDER
    assert http.calls == 1
    assert network_calls == []


# --- no local runtime, no download -------------------------------------------


_ALLOWED_IMPORT_ROOTS = (
    "__future__",
    "collections.abc",
    "inspect",
    "typing",
    "httpx",
    "app.contracts",
    "app.modules.compliance.cloud",
    "app.modules.execution.contracts",
    "app.modules.security.model_identifier",
    "app.providers.transport",
)
_FORBIDDEN_IMPORT_ROOTS = (
    "transformers",
    "torch",
    "huggingface_hub",
    "safetensors",
    "sentencepiece",
    "onnxruntime",
    "diffusers",
    "accelerate",
    "llama_cpp",
    "ctransformers",
    "mlx",
)
_FORBIDDEN_NAMES = ("from_pretrained", "snapshot_download", "hf_hub_download", "open", "urlretrieve")


def test_huggingface_module_is_http_only_and_never_downloads_a_model() -> None:
    source = inspect.getsource(huggingface)
    assert "class HuggingFaceAdapter" in source
    tree = ast.parse(source)

    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    assert imported, "the adapter module must be inspected, not assumed empty"
    for name in imported:
        assert name in _ALLOWED_IMPORT_ROOTS, f"unexpected import in the adapter: {name}"
        assert not name.startswith(_FORBIDDEN_IMPORT_ROOTS), f"local model runtime imported: {name}"

    called_names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            called_names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            called_names.add(node.func.attr)
    assert called_names & set(_FORBIDDEN_NAMES) == set(), f"download/file call found: {called_names}"

    # No file-target helper is even reachable: no pathlib/os/tempfile import.
    assert "pathlib" not in imported
    assert "os" not in imported
    assert "tempfile" not in imported


def test_huggingface_constants_describe_the_hosted_surface() -> None:
    assert PROVIDER == "huggingface"
    assert ENDPOINT == "https://api-inference.huggingface.co/models/{model}"
    assert API_KIND == "text-generation"
    assert SOURCE_HOSTED == "HOSTED"
