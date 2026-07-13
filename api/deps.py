"""FastAPI dependency providers for config/paths/vendor clients. Mirrors what
`cli.py::_load_context` + `core/factory.py` build* functions do, split into
composable `Depends()` so tests can override individual pieces (e.g. swap in
FakeLLMProvider/FakeEmbedder + tmp_path, the same fixtures
`tests/integration/test_pipeline_fake.py` already uses) without touching
config/settings.yaml or hitting Azure/Qdrant.

Every provider is `@lru_cache`d into a process-lifetime singleton rather than
rebuilt per request (unlike cli.py, which happily rebuilds everything on each
short-lived invocation). This matters beyond just avoiding wasted Azure client
construction: `factory.build_vector_store` opens `QdrantClient(path=...)` in
local/embedded mode, which takes an on-disk lock for as long as the instance
is alive. Building a fresh one per request would race that lock against
whichever previous instance the garbage collector hasn't reclaimed yet.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from core import factory


@lru_cache
def get_config() -> dict[str, Any]:
    load_dotenv()
    config_path = os.environ.get("WIKI_CONFIG_PATH")
    return factory.load_config(Path(config_path) if config_path else None)


@lru_cache
def get_paths() -> dict[str, Path]:
    paths = factory.resolve_paths(get_config())
    for p in paths.values():
        p.mkdir(parents=True, exist_ok=True)
    return paths


@lru_cache
def get_llm():
    return factory.build_llm_provider(get_config())


@lru_cache
def get_embedder():
    return factory.build_embedder(get_config())


@lru_cache
def get_vector_store():
    return factory.build_vector_store(get_config())


@lru_cache
def get_namespace():
    return factory.resolve_namespace(get_config())


@lru_cache
def get_entity_types() -> list[str]:
    return factory.resolve_entity_types(get_config())


@lru_cache
def get_relation_types() -> list[str]:
    return factory.resolve_relation_types(get_config())
