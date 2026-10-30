"""Small OpenRouter transport. No SDK or credentials are imported by workflow."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import math
import os
import re
import socket
import urllib.error
import urllib.request

from src.shared.ai_config import get_model, get_backend, MODEL, MAX_OUTPUT_TOKENS, TIMEOUT_SECONDS, SEARCH_RESERVE_USD
from src.shared._workflow_store import WorkflowError

BASE_URL = "https://openrouter.ai/api/v1"


def redact(value):
    """Sanitize strings recursively before persistence or user-facing errors."""
    if isinstance(value, str):
        for name in ("OPENROUTER_API_KEY", "FPT_API_KEY"):
            key = os.environ.get(name, "")
            if key:
                value = value.replace(key, "[REDACTED]")
        return re.sub(r"sk-or-[A-Za-z0-9_-]+|Bearer\s+[^\s\"']+", "[REDACTED]", value)
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items() if k.lower() not in ("authorization", "api_key", "api-key", "fpt_api_key")}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


class HTTPFailure(WorkflowError):
    def __init__(self, status, message, retry_after=0):
        self.status = status
        self.retry_after = min(max(retry_after, 0), 10)
        codes = {400: "API_CONFIGURATION", 401: "API_AUTH", 402: "API_CREDITS", 403: "API_FORBIDDEN",
                 404: "MODEL_NOT_FOUND", 429: "API_RATE_LIMIT"}
        super().__init__(codes.get(status, "API_SERVICE"), redact(message))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_json(method, url, headers, payload=None):
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        # Never forward Authorization across redirects, even on provider errors.
        with urllib.request.build_opener(NoRedirect).open(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise WorkflowError("API_RESULT_UNKNOWN", "API response exceeded size limit; request may have been billed")
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            body = json.loads(exc.read(65536))
            error = body.get("error", {})
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                detail = ": " + redact(error["message"])[:500]
                metadata=error.get('metadata')
                if isinstance(metadata,dict):
                    provider=metadata.get('provider_name')
                    raw=metadata.get('raw')
                    if provider:detail+=' · '+redact(str(provider))[:100]
                    if raw:detail+=' · '+redact(str(raw))[:1000]
        except (ValueError, OSError, AttributeError):
            pass
        try:
            wait = float(exc.headers.get("Retry-After", 0))
        except ValueError:
            wait = 0
        raise HTTPFailure(exc.code, f"OpenRouter returned HTTP {exc.code}{detail}", wait) from None
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError, ValueError) as exc:
        raise WorkflowError("API_RESULT_UNKNOWN", f"No complete API result: {redact(str(exc))}; do not resend automatically") from None


def _price(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise WorkflowError("PRICING_UNAVAILABLE", "Provider pricing is missing or invalid") from None
    if not number.is_finite() or number < 0:
        raise WorkflowError("PRICING_UNAVAILABLE", "Provider pricing must be finite and nonnegative")
    return number


class OpenRouterClient:
    def __init__(self, *, transport=None):
        self._key = os.environ.get("OPENROUTER_API_KEY")
        self.model = os.environ.get("OPENROUTER_MODEL", "").strip() or MODEL
        self.transport = transport or http_json

    def _send(self, method, path, payload=None):
        if not self._key:
            raise WorkflowError("API_KEY_MISSING", "Set OPENROUTER_API_KEY in the execution environment")
        try:
            return self.transport(method, BASE_URL + path,
                                  {"Authorization": "Bearer " + self._key, "Content-Type": "application/json"}, payload)
        except WorkflowError as exc:
            if method == "GET" and exc.code == "API_RESULT_UNKNOWN":
                raise WorkflowError("API_METADATA_UNAVAILABLE", "Metadata lookup failed before any paid request: " + exc.message) from None
            raise

    def preflight(self, *, search=True):
        data = self._send("GET", "/models/" + self.model + "/endpoints").get("data", {})
        if data.get("id") != self.model:
            raise WorkflowError("MODEL_NOT_FOUND", f"Expected exact model {self.model}")
        candidates = []
        for endpoint in data.get("endpoints", []):
            # Do not pin the whole job to a provider reporting an outage merely
            # because its advertised token price is the lowest.
            uptime=next((endpoint[k] for k in ('uptime_last_5m','uptime_last_30m','uptime_last_1d') if endpoint.get(k) is not None),None)
            if endpoint.get('status',0) != 0 or uptime == 0:
                continue
            parameters = endpoint.get("supported_parameters", [])
            required = {"response_format", "structured_outputs"} | ({"tools"} if search else set())
            if not required.issubset(parameters):
                continue
            provider = endpoint.get("provider_name")
            if not isinstance(provider, str) or not provider:
                continue
            try:
                rates = endpoint["pricing"]
                prompt, completion = _price(rates["prompt"]), _price(rates["completion"])
                input_rate = prompt + max(_price(rates.get(k, 0)) for k in ("input_cache_write", "input_cache_write_1h"))
                tiers = [dict(min_prompt_tokens=int(tier["min_prompt_tokens"]),
                              prompt=str(_price(tier["prompt"])), completion=str(_price(tier["completion"])),
                              input_rate=str(_price(tier["prompt"]) + max(_price(tier.get(k, 0)) for k in ("input_cache_write", "input_cache_write_1h"))))
                         for tier in rates.get("overrides", [])]
            except (KeyError, TypeError, ValueError, WorkflowError):
                continue
            # Other mandatory per-request charges cannot be ignored in a bound.
            if any(_price(v) != 0 for k, v in rates.items()
                   if k not in ("prompt", "completion", "image", "audio", "web_search", "internal_reasoning",
                                "input_cache_read", "input_cache_write", "input_cache_write_1h", "discount", "overrides") and v is not None):
                continue
            if _price(rates.get("request", 0)) != 0:
                continue
            candidates.append(dict(provider=provider, prompt=str(prompt), completion=str(completion),
                                   input_rate=str(input_rate), tiers=tiers,
                                   token_parameter="max_completion_tokens" if "max_completion_tokens" in parameters else "max_tokens",
                                   low_effort="reasoning_effort" in parameters))
        if not candidates:
            raise WorkflowError("MODEL_UNSUPPORTED", "No priced endpoint supports both tools and structured JSON")
        selected = min(candidates, key=lambda x: Decimal(x["prompt"]) + Decimal(x["completion"]))
        return dict(model=self.model, **selected, checked_at=datetime.now(timezone.utc).isoformat(), **(self.search_pricing() if search else {}))

    def search_pricing(self):
        data = self._send("GET", "/tools/openrouter:web_search").get("data", {})
        if data.get("id") != "openrouter:web_search" or data.get("status") != "active":
            raise WorkflowError("RESEARCH_UNAVAILABLE", "OpenRouter web search tool is not active")
        engine = next((item for item in data.get("engines", []) if item.get("id") == "exa"), {})
        rows = [row for row in engine.get("pricing", []) if row.get("mode") in (None, "auto")]
        if not rows or engine.get("byok") == "required":
            raise WorkflowError("PRICING_UNAVAILABLE", "No server-side Exa auto price is available")
        total = Decimal(0)
        for row in rows:
            units = {"request": 2, "result": 10}.get(row.get("unit"))
            if units is None:
                raise WorkflowError("PRICING_UNAVAILABLE", "Unrecognized search billing unit; refusing to guess")
            total += _price(row.get("price")) * units
        return dict(search_reserve_usd=float(max(Decimal(str(SEARCH_RESERVE_USD)), total * Decimal("1.25"))),
                    search_price_reference="GET /api/v1/tools/openrouter:web_search", search_prices=rows,
                    search_price_checked_at=datetime.now(timezone.utc).isoformat())

    def payload(self, messages, pricing, *, schema=None, search=False):
        result = dict(model=pricing["model"], messages=messages, stream=False,
                      provider=dict(only=[pricing["provider"]], allow_fallbacks=False, require_parameters=True,
                                    max_price=dict(prompt=float(Decimal(pricing["prompt"]) * 1_000_000),
                                                   completion=float(Decimal(pricing["completion"]) * 1_000_000))))
        result[pricing.get("token_parameter", "max_completion_tokens")] = MAX_OUTPUT_TOKENS
        if pricing.get("low_effort"):
            result["reasoning_effort"] = "low"
        if schema is not None:
            result["response_format"] = dict(type="json_schema", json_schema=dict(name="script_result", strict=True, schema=schema))
        if search:
            result.update(tools=[dict(type="openrouter:web_search", parameters=dict(engine="exa", mode="auto",
                          max_uses=2, max_results=5, max_total_results=10, max_characters=1000))], max_tool_calls=2)
        return result

    def reserve(self, payload, pricing):
        # UTF-8 byte count plus protocol allowance is deliberately conservative;
        # search adds at most 10 excerpts and up to two internal tool turns.
        input_bound = len(json.dumps(payload, ensure_ascii=False).encode()) + 4096
        turns = 3 if "tools" in payload else 1
        if "tools" in payload:
            input_bound += 10 * 1000 * 4 + 8192 + 2 * MAX_OUTPUT_TOKENS
        rate = dict(prompt=pricing["prompt"], completion=pricing["completion"], input_rate=pricing.get("input_rate", pricing["prompt"]))
        for tier in sorted(pricing.get("tiers", []), key=lambda item: item["min_prompt_tokens"]):
            if input_bound >= tier["min_prompt_tokens"]:
                rate = tier
        cost = turns * (Decimal(input_bound) * Decimal(rate["input_rate"]) +
                        MAX_OUTPUT_TOKENS * Decimal(rate["completion"]))
        if "tools" in payload:
            cost += Decimal(str(pricing["search_reserve_usd"]))
        return float(cost)

    def complete(self, payload):
        return self._send("POST", "/chat/completions", payload)


def response_text(response, model=None):
    if model is not None:
        if response.get("model") != model:
            raise WorkflowError("MODEL_MISMATCH", "Response did not come from the configured model")
    elif os.environ.get("OPENROUTER_MODEL", "").strip():
        if response.get("model") != os.environ.get("OPENROUTER_MODEL", "").strip():
            raise WorkflowError("MODEL_MISMATCH", "Response did not come from the configured model")
    elif get_backend() == "openrouter":
        if response.get("model") != MODEL:
            raise WorkflowError("MODEL_MISMATCH", "Response did not come from the configured model")
    else:
        from src.shared.ai_config import get_allowed_models
        if response.get("model") not in get_allowed_models():
            raise WorkflowError("MODEL_MISMATCH", "Response did not come from the configured model")
    try:
        choice = response["choices"][0]
        if choice.get("finish_reason") not in ("stop", None):
            raise ValueError("Truncated or unfinished response")
        value = choice["message"]["content"]
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Empty response text")
        return value
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise WorkflowError("INVALID_MODEL_OUTPUT", str(exc)) from None
