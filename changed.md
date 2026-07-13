# 변경 사항 (clone 이후)

기준: `ba583b5 Initial commit: llm_wiki_builder project` 이후 작업 트리의 변경분.
아직 커밋되지 않은 상태(unstaged + untracked).

## 1. REST API 추가 (`api/`)

`cli.py`가 호출하는 것과 동일한 `builder/*`, `core/*` 함수를 그대로 감싸는 FastAPI 앱을 새로 추가.

- **`api/app.py`** — FastAPI 라우터. 라우팅만 담당하고 실제 로직은 기존 모듈에 위임.
  - `POST /ingest` (multipart 업로드)
  - `GET /drafts`
  - `POST /documents/{doc_id}/approve`
  - `POST /documents/{doc_id}/reindex` (`dry_run` 지원)
  - `GET /index-status`
  - `POST /documents/{doc_id}/verify`
  - `POST /relink`
  - RAG 검색/질의 API는 범위 밖(§9와 동일하게 미포함).
- **`api/deps.py`** — config/paths/LLM/Embedder/VectorStore 등을 `@lru_cache`로 프로세스 수명 동안 싱글턴으로 제공하는 `Depends()` 프로바이더 모음. `QdrantClient(path=...)`가 온디스크 락을 잡기 때문에 요청마다 새로 만들지 않도록 캐싱.
- **`api/schemas.py`** — 요청/응답 pydantic 모델. 가능한 곳은 `core/schemas.py`의 `WikiFrontmatter`, `VerificationReport`를 재사용.
- 실행: `uvicorn api.app:app --reload`
- 사용할 설정 파일은 `WIKI_CONFIG_PATH` 환경변수로 지정 가능(기본 `config/settings.yaml`).

## 2. CLI 리팩터링 → 공용 오케스트레이션 계층 분리 (`builder/ops.py`)

`cli.py`의 `cmd_verify`/`cmd_relink`에 있던 로직을 `builder/ops.py`의 `run_verify()` / `run_relink()`로 추출해서 API와 CLI가 동일한 코드 경로를 공유하도록 함.

- `run_verify(doc_id, paths, llm, relation_types, neighbor_top_k)` → `(WikiFrontmatter, VerificationReport)`
- `run_relink(target_ids, paths, llm, relation_types, neighbor_top_k, apply)` → `list[RelinkResult]` (dataclass)
- `cli.py`는 이제 이 함수들을 호출하고 결과를 출력만 함 (93줄 → 대폭 축소, diff상 -94/+... 정리).
- `cli.py`의 `_load_context`도 `core/factory.py::load_context()`로 이동해 `api/deps.py`와 공유.

## 3. Qdrant local/cloud 모드 지원

- **`builder/indexing/qdrant_writer.py`**: 기존 `QdrantLocalStore` 하나였던 것을, 공통 로직을 담은 `_QdrantStore` 베이스 클래스로 리팩터링하고
  - `QdrantLocalStore` (embedded, `path=`, 서버 불필요 — 기존 동작 유지)
  - `QdrantCloudStore` (`url=` + `api_key=`, 실제 Qdrant 서버/Cloud 연결) 신설
  두 클래스로 분리. 컬렉션/포인트 로직은 `self.client`만 사용하므로 완전히 공유됨.
- **`core/factory.py::build_vector_store`**: `config/settings.yaml`의 `qdrant.mode` (`local`|`cloud`)에 따라 두 스토어 중 하나를 생성. `.env`의 `QDRANT_MODE`/`QDRANT_URL`/`QDRANT_API_KEY`로 오버라이드 가능. `cloud` 모드인데 URL/API 키가 없으면 명확한 `RuntimeError` 발생.
- **`config/settings.yaml`**: `qdrant` 섹션에 `url`, `api_key_env` 필드 추가 + 주석으로 오버라이드 방법 설명.
- **`.env.example`** (신규): Azure OpenAI / Qdrant / API용 환경변수 템플릿.

## 4. 의존성 / 패키징

- **`pyproject.toml`**: `fastapi>=0.110`, `uvicorn[standard]>=0.29`, `python-multipart>=0.0.9` 의존성 추가. `[tool.setuptools] packages`에 `api` 패키지 추가.

## 5. 문서 업데이트 (`README.md`)

- Qdrant `local`/`cloud` 모드 선택 방법 설명 추가.
- 새 "## API" 섹션 추가: 실행 방법, 엔드포인트 목록, `WIKI_CONFIG_PATH` 설명.

## 6. 테스트 추가/보강

- **`tests/unit/test_factory.py`**: `build_vector_store`의 local/cloud 분기, env override, URL/API 키 누락 시 에러, 알 수 없는 mode 에러 등 5개 케이스 추가.
- **`tests/unit/test_qdrant_writer.py`**: `QdrantCloudStore`가 `QdrantLocalStore`와 동일한 컬렉션 로직을 공유하는지 확인하는 테스트 추가.
- **`tests/unit/test_api.py`** (신규, 127줄): FastAPI 엔드포인트 테스트.

## 7. 기타 신규/미추적 파일

- `samples/pdf/manual.pdf`, `samples/sqlite/sample.db` — API/파이프라인 수동 테스트용 샘플 입력 파일로 추정.
- `builder/ops.py`, `api/` 디렉토리는 위에서 설명한 신규 모듈.

---

**요약**: 이번 작업의 핵심은 (1) 기존 CLI 파이프라인을 그대로 노출하는 FastAPI REST API 추가, (2) 그 과정에서 verify/relink 로직을 `builder/ops.py`로 추출해 CLI·API가 공유하도록 리팩터링, (3) Qdrant를 로컬 임베디드 모드뿐 아니라 원격 서버/Cloud에도 연결할 수 있도록 확장한 것.

## 8. (이 문서 작성 이후 추가 작업)

### 8-1. `.env`에 Azure OpenAI 오버라이드 값 명시

- `.env`의 `AOAI_API_ENDPOINT`/`AOAI_API_VERSION`/`AOAI_MODEL_DEPLOYMENT`/`AOAI_EMBEDDING_MODEL_DEPLOYMENT` 주석을 해제하고 실제 값 채움. 코드(`core/factory.py`의 `_env_override`)는 이미 이 값들을 `config/settings.yaml`보다 우선해서 읽도록 구현돼 있었으므로 코드 변경 없이 `.env` 값만 채운 것. `.env`는 `.gitignore`에 포함되어 있어 커밋되지 않음.

### 8-2. `requirements.txt` 신규 작성

- `pyproject.toml`의 `dependencies` 목록을 그대로 옮겨 작성 (`pydantic`, `langgraph`, `qdrant-client`, `pandas`, `pymupdf`, `pdfplumber`, `markdown-it-py`, `chardet`, `python-dotenv`, `pyyaml`, `openai`, `fastapi`, `uvicorn[standard]`, `python-multipart`). `pip install -e .` 대신 `pip install -r requirements.txt`로도 설치 가능하도록.

### 8-3. API 실행 포트를 `.env`로 지정 가능하게 시도 → 최종적으로 `--port` CLI 플래그 방식으로 정리

- 처음에는 `api/app.py`에 `if __name__ == "__main__":` 진입점을 추가해 `python -m api.app` 실행 시 `.env`의 `API_PORT`/`API_HOST`를 읽도록 구현 (`uvicorn api.app:app --reload` CLI 방식은 `__main__` 블록을 안 타므로 `.env` 값을 못 읽는다는 한계 있음을 확인).
- 이후 사용자가 직접 `api/app.py`를 편집해 `uvicorn.run()`에서 `host=`/`port=` 인자를 제거하고 `reload=True`만 남김. 최종적으로는 README에 `uvicorn api.app:app --port 8002 --reload`처럼 **CLI에서 `--port`를 직접 지정하는 방식**으로 정리됨(`.env` 기반 자동 포트 지정은 사용하지 않기로 함).
- `.env.example`/`.env`에는 `API_PORT`/`API_HOST` 설명이 남아 있으나 현재 실행 경로(`uvicorn ... --port 8002`)에서는 참조되지 않음 — 필요 시 `python -m api.app` 실행으로 되돌리면 다시 유효해짐.

### 8-4. 모든 API 엔드포인트에 `/builderapi/v1` 프리픽스 적용

- **`api/app.py`**: `APIRouter(prefix="/builderapi/v1")`를 만들어 기존 `@app.get/@app.post` 라우트를 전부 `@router.get/@router.post`로 옮기고, 마지막에 `app.include_router(router)`로 마운트. 실제 서비스 경로가 `POST /ingest` → `POST /builderapi/v1/ingest` 식으로 전부 바뀜(`/drafts`, `/documents/{doc_id}/approve`, `/documents/{doc_id}/reindex`, `/index-status`, `/documents/{doc_id}/verify`, `/relink` 전부 동일하게 프리픽스 적용).
- **`tests/unit/test_api.py`**: 모든 `client.get(...)`/`client.post(...)` 호출 경로를 `/builderapi/v1/...`로 갱신. `pytest tests/unit/test_api.py`로 6개 테스트 통과 확인.
- **`README.md`**: API 섹션의 엔드포인트 설명을 새 경로(`/builderapi/v1/...`)로 갱신, 최하단에 "builder API 실행 방법" 섹션 추가(`uvicorn api.app:app --port 8002 --reload`).
- Swagger UI(`/docs`)는 프리픽스와 무관하게 그대로 루트에서 제공됨.
