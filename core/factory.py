"""Builds concrete LLMProvider/Embedder/VectorStore instances from
config/settings.yaml. This is the ONLY place vendor strings (Azure, Qdrant)
are allowed to appear outside their own implementation modules -- builder/*
only ever sees the ABCs from core/providers.py.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "settings.yaml"


def load_config(path: Path | None = None) -> dict[str, Any]:
    with open(path or CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_paths(config: dict[str, Any]) -> dict[str, Path]:
    """Every path in config/settings.yaml is relative to the project root, not
    to whatever directory `wiki` happens to be invoked from.
    """
    p = config["paths"]
    return {
        "raw": PROJECT_ROOT / p["raw"],
        "staging": PROJECT_ROOT / p["staging"],
        "wiki_draft": PROJECT_ROOT / p["wiki_draft"],
        "wiki_approved": PROJECT_ROOT / p["wiki_approved"],
    }


def load_context(config_path: Path | None = None) -> tuple[dict[str, Any], dict[str, Path]]:
    """Shared by `cli.py` and `api/deps.py`: load .env, parse config, resolve
    paths, and make sure every resolved directory exists.
    """
    from dotenv import load_dotenv

    load_dotenv()
    config = load_config(config_path)
    paths = resolve_paths(config)
    for p in paths.values():
        p.mkdir(parents=True, exist_ok=True)
    return config, paths


def resolve_entity_types(config: dict[str, Any]) -> list[str]:
    path = PROJECT_ROOT / config["entity_types_file"]
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data.get("entity_types", [])


def resolve_relation_types(config: dict[str, Any]) -> list[str]:
    """S5.5 relation curation (extension, additive): the allowed-types list
    fed to builder/verify_curate.py so 'add' suggestions can be validated.
    """
    path = PROJECT_ROOT / config["relation_types_file"]
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data.get("relation_types", [])


def _env_override(env_name: str, fallback: str) -> str:
    """.env wins over config/settings.yaml when both are set -- lets deployment
    name/endpoint/api_version be swapped per-machine without editing yaml.
    """
    return os.environ.get(env_name) or fallback


def _azure_client(section: dict[str, Any]):
    from openai import AzureOpenAI

    api_key = os.environ.get(section["api_key_env"])
    if not api_key:
        raise RuntimeError(
            f"Environment variable {section['api_key_env']!r} is not set. "
            "Copy .env.example to .env and fill in the real key."
        )
    return AzureOpenAI(
        azure_endpoint=_env_override("AOAI_API_ENDPOINT", section["azure_endpoint"]),
        api_key=api_key,
        api_version=_env_override("AOAI_API_VERSION", section["api_version"]),
    )


def build_llm_provider(config: dict[str, Any]):
    from core.providers import AzureLLMProvider, FakeLLMProvider

    section = config["llm"]
    if section["provider"] == "fake":
        return FakeLLMProvider()
    if section["provider"] == "azure":
        client = _azure_client(section)
        deployment = _env_override("AOAI_MODEL_DEPLOYMENT", section["deployment"])
        return AzureLLMProvider(client, deployment, max_retries=section.get("max_retries", 1))
    raise ValueError(f"Unknown llm.provider: {section['provider']!r}")


def build_embedder(config: dict[str, Any]):
    from core.providers import AzureEmbedder, FakeEmbedder

    section = config["embedding"]
    if section["provider"] == "fake":
        return FakeEmbedder(dimension=section.get("dimension", 16))
    if section["provider"] == "azure":
        client = _azure_client(section)
        deployment = _env_override("AOAI_EMBEDDING_MODEL_DEPLOYMENT", section["deployment"])
        return AzureEmbedder(client, deployment, dimension=section["dimension"])
    raise ValueError(f"Unknown embedding.provider: {section['provider']!r}")


def build_vector_store(config: dict[str, Any]):
    from builder.indexing.qdrant_writer import QdrantCloudStore, QdrantLocalStore

    section = config["qdrant"]
    namespace = uuid.uuid5(uuid.NAMESPACE_URL, section["uuid_namespace"])
    # QDRANT_MODE lets a deployment flip local <-> cloud from .env without
    # editing config/settings.yaml, same pattern as the AOAI_* overrides above.
    mode = _env_override("QDRANT_MODE", section.get("mode", "local"))

    if mode == "local":
        return QdrantLocalStore(
            path=PROJECT_ROOT / section["path"],
            namespace=namespace,
            collection_summary=section["collection_summary"],
            collection_chunk=section["collection_chunk"],
        )
    if mode == "cloud":
        url = _env_override("QDRANT_URL", section.get("url", ""))
        if not url:
            raise RuntimeError(
                "qdrant.mode=cloud requires a URL. Set QDRANT_URL in .env "
                "(or qdrant.url in the config file)."
            )
        api_key_env = section.get("api_key_env", "QDRANT_API_KEY")
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise RuntimeError(
                f"Environment variable {api_key_env!r} is not set. "
                "Copy .env.example to .env and fill in your Qdrant Cloud API key."
            )
        return QdrantCloudStore(
            url=url,
            api_key=api_key,
            namespace=namespace,
            collection_summary=section["collection_summary"],
            collection_chunk=section["collection_chunk"],
        )
    raise ValueError(f"Unknown qdrant.mode: {mode!r}")


def resolve_namespace(config: dict[str, Any]) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, config["qdrant"]["uuid_namespace"])
