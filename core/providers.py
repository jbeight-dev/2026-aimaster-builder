"""Swappable LLM/Embedder interfaces (architectural invariant #4 -- no vendor
strings in builder/*). Two implementations ship: Azure OpenAI (real calls) and
a deterministic Fake (offline tests, no network/keys).

complete_structured() always takes a `context: dict` -- a plain-Python "safe
answer" the caller assembles without any LLM call (e.g. joining paragraph
blocks into a naive summary). FakeLLMProvider validates `context` directly and
returns it unchanged (the whole point of a deterministic fake). AzureLLMProvider
tries a real call (+1 retry on validation failure) and only falls back to
`context` if both attempts fail -- so a broken/unavailable gateway degrades to
something reasonable instead of crashing the pipeline, with a review flag
attached either way.
"""
from __future__ import annotations

import json
import typing
from abc import ABC, abstractmethod
from typing import Any, TypeVar, get_args, get_origin

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class LLMProvider(ABC):
    @abstractmethod
    def complete(self, system: str, user: str, **kw: Any) -> str: ...

    @abstractmethod
    def complete_structured(
        self,
        system: str,
        user: str,
        schema: type[T],
        context: dict[str, Any],
        max_retries: int = 1,
    ) -> tuple[T, list[str]]:
        """Returns (validated_object, review_flags)."""
        ...


class Embedder(ABC):
    dimension: int

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _empty_value_for_annotation(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin is typing.Union:
        args = [a for a in get_args(annotation) if a is not type(None)]
        return _empty_value_for_annotation(args[0]) if args else None
    if origin in (list, typing.List):
        return []
    if origin in (dict, typing.Dict):
        return {}
    if annotation is str:
        return ""
    if annotation is int:
        return 0
    if annotation is float:
        return 0.0
    if annotation is bool:
        return False
    if get_origin(annotation) is typing.Literal:
        return get_args(annotation)[0]
    return None


def fallback_instance(schema: type[T], context: dict[str, Any]) -> T:
    """Best-effort construction of `schema` from `context`, filling any
    missing required fields with type-appropriate empty values so this never
    raises (used as the last-resort degrade path).
    """
    values = dict(context)
    for name, field in schema.model_fields.items():
        if name in values:
            continue
        if not field.is_required():
            continue  # pydantic will fill in the default during model_validate
        values[name] = _empty_value_for_annotation(field.annotation)
    return schema.model_validate(values)


class FakeLLMProvider(LLMProvider):
    """Deterministic stand-in: no network, no keys. See module docstring."""

    def complete(self, system: str, user: str, **kw: Any) -> str:
        return user

    def complete_structured(
        self,
        system: str,
        user: str,
        schema: type[T],
        context: dict[str, Any],
        max_retries: int = 1,
    ) -> tuple[T, list[str]]:
        try:
            return schema.model_validate(context), []
        except ValidationError:
            return fallback_instance(schema, context), [
                "fake_provider: context incomplete for schema, used defaults"
            ]


class FakeEmbedder(Embedder):
    """Deterministic hash-based vectors -- same text always yields the same
    vector, different texts (almost always) yield different vectors.
    """

    def __init__(self, dimension: int = 16):
        self.dimension = dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def _vector(self, text: str) -> list[float]:
        import hashlib

        vec: list[float] = []
        seed = text.encode("utf-8")
        for i in range(self.dimension):
            h = hashlib.sha256(seed + str(i).encode()).digest()
            val = int.from_bytes(h[:4], "big") / 2**32  # in [0, 1)
            vec.append(val * 2 - 1)  # in [-1, 1)
        return vec


class AzureLLMProvider(LLMProvider):
    def __init__(self, client: Any, deployment: str, max_retries: int = 1):
        self.client = client
        self.deployment = deployment
        self.max_retries = max_retries

    def complete(self, system: str, user: str, **kw: Any) -> str:
        resp = self.client.chat.completions.create(
            model=self.deployment,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            **kw,
        )
        return resp.choices[0].message.content or ""

    def complete_structured(
        self,
        system: str,
        user: str,
        schema: type[T],
        context: dict[str, Any],
        max_retries: int | None = None,
    ) -> tuple[T, list[str]]:
        retries = self.max_retries if max_retries is None else max_retries
        schema_hint = json.dumps(schema.model_json_schema())
        prompt = (
            f"{user}\n\n"
            "Respond with a single JSON object only (no markdown fences, no commentary) "
            f"matching this JSON Schema:\n{schema_hint}"
        )

        last_error: str | None = None
        for attempt in range(retries + 1):
            if last_error:
                prompt = f"{prompt}\n\nYour previous output was invalid: {last_error}\nReturn corrected JSON only."
            try:
                raw = self.complete(system, prompt, response_format={"type": "json_object"})
                data = json.loads(raw)
                return schema.model_validate(data), []
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = str(exc)
            except Exception as exc:  # gateway/network errors -- degrade, don't crash
                last_error = str(exc)

        return fallback_instance(schema, context), [
            f"azure_provider: structured output failed after {retries + 1} attempt(s) "
            f"({last_error}); used deterministic fallback"
        ]


class AzureEmbedder(Embedder):
    def __init__(self, client: Any, deployment: str, dimension: int = 1536):
        self.client = client
        self.deployment = deployment
        self.dimension = dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = self.client.embeddings.create(model=self.deployment, input=texts)
        return [d.embedding for d in resp.data]
