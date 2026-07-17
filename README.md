# LLM WIKI Builder

pdf·csv·txt·md·sqlite 원천을 하나의 통일된 파이프라인(S0-S8)으로 처리하여,
사람이 검수 가능한 Wiki Markdown(`wiki/approved/*.md`, 단일 진실 원천)과
그로부터 파생된 Qdrant 벡터 인덱스를 생성한다. 별도의 레지스트리 DB는 없다:
문서/엔티티/관계 조회는 실행 시점에 approved frontmatter를 메모리로 로드해서
처리한다(`core/wiki_io.py::load_index`).



## 파이프라인 단계

| 단계 | 이름 | 모듈 | LLM 호출 |
|---|---|---|---|
| S0 | Intake | `builder/intake.py` | - |
| S1 | Extraction (pdf/csv/txt/md/sqlite) | `builder/extract/*` | - |
| S2 | Structuring (정규화) | `builder/structuring.py` | O |
| S2.5 | Translation (영→한, 조건부) | `builder/translate.py` | O (영문일 때만) |
| S3 | Enrichment (요약/엔티티/키워드) | `builder/enrichment.py` | O |
| S4 | Metadata Assembly (frontmatter 조립, 엔티티 정규화) | `builder/metadata.py` | - |
| S5 | Relation Mapping (FK/see_also) | `builder/relations.py` | - |
| S5.5 | Verification & Relation Curation (확장) | `builder/verify_curate.py` | O |
| S6 | Review (HITL, draft 파일) | `builder/review.py` | - |
| S7 | Chunking + Embedding | `builder/indexing/*` | O (embedding) |
| S8 | Finalize (approved 승격 + Qdrant upsert) | `builder/finalize.py` | - |

`builder/pipeline.py::run_ingest`가 S0-S6을 순서대로 실행하는 실제 오케스트레이터이고,
`graphs/ingestion_graph.py`는 동일한 함수들을 LangGraph 노드로 감싼 얇은 래퍼다
(비즈니스 로직은 전부 `builder/*.py`에만 있음).

## 설치

```bash
pip install -e .                 # 또는: pip install -r requirements.txt
cp .env.example .env             # 실제 Azure OpenAI 호출을 위해 AOAI_API_KEY를 채워 넣는다
```

`config/`에는 두 가지 설정 파일이 있다:
- `settings.yaml` - 실제 Azure OpenAI(`aitl-prd-gpt-5.4` / `aitl-prd-text-embedding-3-small`,
  엔드포인트 `https://skax.ai-talentlab.com`)를 사용한다. 환경변수 `AOAI_API_KEY`가 필요하다.
- `settings.fake.yaml` - 결정론적(deterministic) 오프라인 Fake LLM/Embedder를 사용한다.
  네트워크나 키가 필요 없다(`scripts/demo.sh`의 기본 설정).

Qdrant는 `qdrant.mode: local | cloud`로 선택한다(`config/settings.yaml`).
`local`은 서버 없이 `qdrant.path`에 파일로 저장하는 임베디드 모드(기본값,
데모/테스트가 쓰는 방식), `cloud`는 실제 Qdrant 서버/Qdrant Cloud에
`qdrant.url` + API 키로 접속한다. `.env`의 `QDRANT_MODE`/`QDRANT_URL`/
`QDRANT_API_KEY`가 설정되어 있으면 yaml 값을 덮어쓴다(`core/factory.py::build_vector_store`).
`AOAI_API_ENDPOINT`/`AOAI_API_VERSION`/`AOAI_MODEL_DEPLOYMENT`/
`AOAI_EMBEDDING_MODEL_DEPLOYMENT`도 같은 방식으로 `.env`가 yaml보다 우선한다.

## API

```bash
uvicorn api.app:app --port 8002 --reload
```

`cli.py`의 각 명령을 그대로 REST로 감싼 것이다(같은 `builder/*.py`,
`core/*.py` 함수를 호출한다). 모든 엔드포인트는 `/builderapi/v1` 아래에 있다:

- `POST /builderapi/v1/ingest` (multipart 업로드, `force` 쿼리 파라미터) -
  S0-S6 진행 상황을 **Server-Sent Events(SSE)**로 스트리밍한다. 응답은
  `text/event-stream`이며 각 단계가 끝날 때마다 `{"event": "start"|"finish", "step": ..., "detail": ...}`
  이벤트가, 마지막에 `{"event": "result", "doc_ids": [...]}` 또는
  `{"event": "error", "detail": ...}`가 온다. SSE 특성상 HTTP status는 스트림
  시작 이후 항상 200이므로, 성공/실패 판단은 status가 아니라 마지막 이벤트의
  `event` 값으로 해야 한다.
- `POST /builderapi/v1/analyze` - `/ingest`와 완전히 동일한 엔드포인트의
  별칭. 다른 백엔드 서비스가 사용자가 업로드한 파일을 이 파이프라인
  (S0-S5.5)에 직접 넣을 수 있도록 추가되었다.
- `GET /builderapi/v1/drafts` - 검수 대기 중인 draft 목록.
- `GET /builderapi/v1/documents/{doc_id}` - draft/approved 여부와 무관하게
  해당 문서의 md 원문(frontmatter+본문)을 디스크에서 그대로 읽어 반환하는
  조회 전용 엔드포인트(별도 DB 없이 파일이 곧 source of truth).
- `POST /builderapi/v1/documents/{doc_id}/approve` - S7-S8 실행, approved로 승격.
- `POST /builderapi/v1/documents/{doc_id}/reindex` (`dry_run` 지원) - 기존
  Qdrant 포인트를 delete-then-upsert.
- `GET /builderapi/v1/index-status` - approved/draft 문서 수 + Qdrant 컬렉션별 포인트 수.
- `POST /builderapi/v1/documents/{doc_id}/verify` - S5.5(검증+관계 큐레이션) 단독 재실행.
- `POST /builderapi/v1/relink` (`doc_id` 또는 `all`, `apply`) - approved 문서들의
  관계 배치 재큐레이션.
- RAG 검색/질의 API는 범위 밖(기획서 §9와 동일하게 미포함).

사용할 설정 파일은 `WIKI_CONFIG_PATH` 환경변수로 고른다(기본
`config/settings.yaml`, 오프라인 데모는 `WIKI_CONFIG_PATH=config/settings.fake.yaml`).
Swagger UI는 `/docs`에서 그대로 제공된다(프리픽스와 무관).

`api/deps.py`는 config/paths/LLM/Embedder/VectorStore를 `@lru_cache`로
프로세스 수명 동안 싱글턴으로 제공한다 - `QdrantClient(path=...)`(local 모드)가
온디스크 락을 잡기 때문에 요청마다 새로 만들지 않기 위함이다.

## CLI

```bash
python cli.py [--config config/settings.yaml] ingest <path> [--force]
python cli.py review list
python cli.py approve <doc_id>
python cli.py reindex <doc_id> [--dry-run]
python cli.py index-status
python cli.py verify <doc_id>
python cli.py relink [--all | <doc_id>] [--apply]
```

`ingest`는 S0-S6을 실행하고 `wiki/draft/{doc_id}.md`를 생성하며, 실행 중
각 단계(intake/extract/structure/translate/enrich/metadata/relations/verify/draft)의
시작·종료를 `rich` 기반 컬러 로그로 실시간 출력한다(`core/rich_reporter.py`).
사람이 draft를 검수하고(파일을 열어 직접 수정하거나 `<!-- REVIEW: ... -->`
주석을 확인), `approve`를 실행하면 청킹·임베딩·인덱싱 후 `wiki/approved/`로
승격된다. `approve`/`reindex`는 디스크에서 바로 읽어오므로 재개할 "살아있는
프로세스"가 필요 없다 -- 완전히 새로운 프로세스에서 실행해도 된다(결정 B).

### 진행 상황 리포팅 (`core/progress.py`)

`StageReporter` 프로토콜(`start`/`finish`)을 파이프라인 함수들이 선택적으로
받는다 - 기본값은 아무 동작도 하지 않는 `NULL_REPORTER`이므로 테스트나
`graphs/ingestion_graph.py`처럼 진행 상황이 필요 없는 호출자는 그냥 생략하면 된다.
구현체는 두 가지다:

- `RichReporter`(`core/rich_reporter.py`) - CLI(`wiki ingest`)가 콘솔에
  단계별 소요 시간을 출력할 때 사용.
- `QueueReporter`(`core/queue_reporter.py`) - API의 `/ingest`가 파이프라인을
  백그라운드 스레드에서 돌리면서, 각 단계 이벤트를 스레드 세이프 큐에 쌓고
  이를 제너레이터가 비워가며 SSE로 클라이언트에 전달할 때 사용.
- `CompositeReporter` - 여러 리포터에 동시에 이벤트를 팬아웃한다. API는
  서버 콘솔 로그(`RichReporter`)와 클라이언트 스트리밍(`QueueReporter`)을
  동시에 원하므로 `CompositeReporter([RichReporter(), QueueReporter()])`로 합성해 사용.

이 모듈은 의도적으로 의존성이 없다(`builder/*.py`, `graphs/*.py`가 렌더링
라이브러리를 몰라도 되게 하기 위함) - `rich`를 실제로 import하는 건
`core/rich_reporter.py` 뿐이다.

## 데모

```bash
bash scripts/demo.sh                         # settings.fake.yaml 사용, 키 불필요
bash scripts/demo.sh config/settings.yaml    # 동일한 흐름을 실제 Azure OpenAI로 실행
```

pdf/sqlite 샘플 픽스처를 재생성하고(`scripts/generate_samples.py`), 5개 포맷을
모두 ingest한 뒤, 모든 draft를 approve하고, 인덱스 상태를 출력하고, 한 원천을
재수집해 멱등성을 보여주고, 문서 하나를 reindex해서 delete-then-upsert 방식의
덮어쓰기를 보여준다. `samples/`, `tests/`는 `.gitignore`에 포함되어 있어 각자
로컬에서 준비해야 한다(`scripts/generate_samples.py`가 pdf/sqlite 픽스처를 만들어줌).

## 테스트

```bash
pytest tests/unit tests/integration -q
```

`FakeLLMProvider`/`FakeEmbedder`(`core/providers.py`)를 통해 완전히 오프라인으로
실행된다 -- 네트워크나 `AOAI_API_KEY` 없이 동작한다.

## S5.5 - 검증 & 관계 큐레이션 (확장)

`LLM_WIKI_Builder_확장_검증_및_관계큐레이션.md`에 정의된 확장. S5(관계 매핑)와
S6(검수) 사이에서 `wiki ingest` 실행 시 항상 자동으로 돈다(`builder/verify_curate.py`):

- **6-A 근거 검증**: S2 본문을 S1 원본 블록과 대조해 미근거 주장(faithfulness)과
  누락된 내용(completeness)을 찾는다. `verdict`(pass/regenerate/review)와 `score`는
  LLM이 자체 판정하지 않고 코드가 findings로부터 결정론적으로 계산한다
  (`compute_verdict`/`compute_score`).
- **재생성 루프**: verdict가 `regenerate`면 `config: verification.max_regen`
  (기본 2회) 한도 내에서 S2/S3를 재실행한다. 완전성 문제면 S2+S3를, 근거성
  문제만 있으면 S3만 재실행한다.
- **6-B 관계 큐레이션**: S5의 결정론적 관계를 `core/wiki_io.py::neighbor_candidates`
  (공유 엔티티/태그 기반 후보)를 참고해 LLM이 keep/prune/add로 큐레이션한다.
  검증이 통과하면 큐레이션 결과가 즉시 `frontmatter.relations`에 반영된다(별도
  수동 승인 단계 없음 -- 프로젝트 결정 사항).
- 각 시도의 리포트는 `staging/{source_id}/06_verification/{doc_id}.json`에
  남고, 문제가 있으면 draft 본문 상단에 `<!-- S5.5 ... -->` 주석으로 표시된다
  (검토용 -- 청킹/임베딩 전에는 `<!-- REVIEW: ... -->`와 함께 자동으로 제거됨,
  `builder/review.py::strip_review_comments`).
- `wiki verify <doc_id>`: 현재 draft/approved 문서에 대해 S5.5를 단독 재실행
  (사람이 draft를 직접 수정한 뒤 다시 확인할 때 유용).
- `wiki relink [--all|<doc_id>] [--apply]`: 이미 approved된 문서들의 관계를
  현재 코퍼스 기준으로 재큐레이션하는 배치 작업(예: 나중에 approve된 문서로의
  역방향 링크를 뒤늦게 보충). `--apply` 전에는 dry-run으로 변경 예정 사항만
  출력한다. 관계는 임베딩되는 콘텐츠가 아니므로 reindex는 필요 없다.
- **Fake 모드에서는 완전히 무동작(no-op)이다**: `FakeLLMProvider`의 폴백은
  "문제 없음"(근거 검증)과 "전부 유지"(관계 큐레이션)이므로, 오프라인
  테스트에서는 이 단계가 실제로 실행되면서도 기존 산출물(본문·relations)을
  전혀 바꾸지 않는다.

## 아키텍처 노트 / 기획서 대비 범위를 좁힌 지점

- **chunk_context는 S3가 아니라 S7에서 조립된다** (결정 A). S3의 `Enrichment`는
  `section_summaries`만 생성하며, `builder/indexing/embedder.py`가 실제 임베딩
  입력을 조립할 때 청크의 `section_path`를 이 요약들과 매칭한다. 검수자가
  draft의 헤더를 수정해 경로가 더 이상 정확히 일치하지 않으면 `doc_summary`로
  폴백한다.
- **원천 식별은 콘텐츠 해시가 아니라 파일 경로 기반이다**: 같은 경로로 `ingest`를
  재실행하면 항상 같은 `doc_id`를 재사용한다. 파일 내용이 바뀌면 고아 문서를
  새로 만드는 대신 `version`을 올리고 재처리한다(`core/ids.py::make_source_id`,
  `core/manifest.py`). 재개(resume)는 `manifest.json`의 단계별 완료 상태만
  보고 판단하며, "raw 디렉터리가 존재하는가" 같은 곁가지 신호는 쓰지 않는다.
- **엔티티 정규화와 관계 매핑은 결정론적 로직만 구현했다.**
  `builder/metadata.py::match_entities`는 인메모리 인덱스와 매칭하고
  (선착순/first-seen-wins), 매칭되지 않은 이름은 사람 검토를 위해
  `WikiIndex.unresolved_entities`에 기록한다. `builder/relations.py`는 sqlite
  FK로부터 `foreign_key` 관계를, 공유된 엔티티 canonical로부터 `see_also`
  관계를 생성한다. LLM 기반 관계 판단(`config: relations.use_llm`)은 문서화된
  확장 지점일 뿐 S5 자체에는 구현되어 있지 않다(대신 S5.5 관계 큐레이션이
  이를 보완).
- **LangGraph는 얇은 래퍼다** (`graphs/ingestion_graph.py`). `builder/pipeline.py`가
  사용하고 직접 테스트되는 것과 동일한 순수 함수들 위에 얹혀 있다. S6에서
  reject 시 되돌아가는 라이브 엣지는 없는데, 결정 B에 따라 HITL 거부가
  프로세스 밖에서 처리되기 때문이다(draft를 직접 수정하거나 `ingest --force`를
  재실행) -- 그래프 실행을 중단시킨 채 대기하지 않는다. 체크포인터는 인트라런
  크래시 복구용으로 선택적으로 받을 수 있지만 기본은 off이며, 런 간 재개
  가능성의 근거는 어디까지나 `manifest.json`이다.
- **청크 단위 페이지 번호는 추적하지 않는다.** S2가 블록별 페이지 메타데이터를
  순수 Markdown 텍스트로 평탄화하기 때문에, 이 PoC에서 `Chunk.source_page`는
  항상 `None`이다. 더 거친 단위의 역추적은 여전히 가능하다:
  `frontmatter.source.locator`가 문서 전체 기준 페이지 목록(pdf)이나 테이블명
  (sqlite)을 기록하고, `frontmatter.source.raw_path`는 항상 원본 파일을
  가리킨다.
- **대용량/자주 바뀌는 정형 데이터는 알려진 PoC 한계다** (결정 I): CSV/sqlite
  테이블 전체가 wiki 본문에 하나의 Markdown 표로 렌더링된다(크면 `MAX_ROWS`로
  샘플링). 기획서 §9의 "라이브 DB 조회" 확장 지점은 의도적으로 미구현 상태로
  남겨두었다.
- **진행 상황 리포팅은 파이프라인 로직과 분리되어 있다** (`core/progress.py`).
  `StageReporter`는 옵션 인자이자 기본값이 no-op이므로, 이를 지정하지 않는
  기존 호출자(테스트, `graphs/ingestion_graph.py`)의 동작은 전혀 바뀌지 않는다.
- **검색/답변 Agent, 인증, UI는 범위 밖이다** -- 기획서 §9는 의도된 RAG 참조
  패턴(Qdrant 검색 결과 → 해당 `wiki/approved/*.md` 읽기 → 필요 시 라이브
  원천 DB 조회)을 설명하지만, 이 섹션의 내용은 여기서 구현되지 않았다.
