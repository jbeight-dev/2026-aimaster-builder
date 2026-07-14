# LLM WIKI Builder

pdf·csv·txt·md·sqlite 원천을 하나의 통일된 파이프라인(S0-S8)으로 처리하여,
사람이 검수 가능한 Wiki Markdown(`wiki/approved/*.md`, 단일 진실 원천)과
그로부터 파생된 Qdrant 벡터 인덱스를 생성한다. 별도의 레지스트리 DB는 없다:
문서/엔티티/관계 조회는 실행 시점에 approved frontmatter를 메모리로 로드해서
처리한다(`core/wiki_io.py::load_index`).

전체 기획서: `LLM_WIKI_Builder_기획서_v2.md`. 이 README는 실제로 구현된 내용과,
기획서와 달라지거나 범위를 좁힌 지점을 정리한다.

## 설치

```bash
pip install -e .
cp .env.example .env   # 실제 Azure OpenAI 호출을 위해 AOAI_API_KEY를 채워 넣는다
```

`config/`에는 두 가지 설정 파일이 있다:
- `settings.yaml` - 실제 Azure OpenAI(`aitl-prd-gpt-5.4` / `aitl-prd-text-embedding-3-small`,
  엔드포인트 `https://skax.ai-talentlab.com`)를 사용한다. 환경변수 `AOAI_API_KEY`가 필요하다.
- `settings.fake.yaml` - 결정론적(deterministic) 오프라인 Fake LLM/Embedder를 사용한다.
  네트워크나 키가 필요 없다.

Qdrant는 `qdrant.mode: local | cloud`로 선택한다(`config/settings.yaml`).
`local`은 서버 없이 `qdrant.path`에 파일로 저장하는 임베디드 모드(기본값,
데모/테스트가 쓰는 방식), `cloud`는 실제 Qdrant 서버/Qdrant Cloud에
`qdrant.url` + API 키로 접속한다. `.env`의 `QDRANT_MODE`/`QDRANT_URL`/
`QDRANT_API_KEY`가 설정되어 있으면 yaml 값을 덮어쓴다(`core/factory.py::build_vector_store`).

## API

```bash
uvicorn api.app:app --reload              # 기본 포트 8000, --port로 직접 지정 가능
# 또는 .env의 API_PORT/API_HOST를 사용:
python -m api.app
```
uvicorn api.app:app --reload --port 8002

`cli.py`의 각 명령을 그대로 REST로 감싼 것이다(같은 `builder/*.py`,
`core/*.py` 함수를 호출한다). 모든 엔드포인트는 `/builderapi/v1` 아래에
있다: `POST /builderapi/v1/ingest`(multipart 업로드),
`GET /builderapi/v1/drafts`, `GET /builderapi/v1/documents/{doc_id}`,
`POST /builderapi/v1/documents/{doc_id}/approve`,
`POST /builderapi/v1/documents/{doc_id}/reindex`, `GET /builderapi/v1/index-status`,
`POST /builderapi/v1/documents/{doc_id}/verify`, `POST /builderapi/v1/relink`.
`POST /builderapi/v1/analyze`는 `/ingest`와 완전히 동일한 엔드포인트의 별칭으로,
다른 백엔드 서비스가 사용자가 업로드한 파일을 이 파이프라인(S0~S5.5)에
직접 넣을 수 있도록 추가되었다. `GET /builderapi/v1/documents/{doc_id}`는
draft/approved 여부와 무관하게 해당 문서의 md 원문(frontmatter+본문)을
디스크에서 그대로 읽어 반환하는 조회 전용 엔드포인트다(별도 DB 없이 파일이
곧 source of truth).
사용할 설정 파일은 `WIKI_CONFIG_PATH` 환경변수로 고른다(기본
`config/settings.yaml`). RAG 검색/질의 API는 포함하지 않는다(§9와 동일하게
범위 밖).

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

`ingest`는 S0~S6을 실행하고 `wiki/draft/{doc_id}.md`를 생성한다. 사람이 이를
검수하고(파일을 열어 직접 수정하거나 `<!-- REVIEW: ... -->` 주석을 확인),
`approve`를 실행하면 청킹·임베딩·인덱싱 후 `wiki/approved/`로 승격된다.
`approve`/`reindex`는 디스크에서 바로 읽어오므로 재개할 "살아있는 프로세스"가
필요 없다 -- 완전히 새로운 프로세스에서 실행해도 된다(결정 B).

## 데모

```bash
bash scripts/demo.sh                         # settings.fake.yaml 사용, 키 불필요
bash scripts/demo.sh config/settings.yaml    # 동일한 흐름을 실제 Azure OpenAI로 실행
```

pdf/sqlite 샘플 픽스처를 재생성하고, 5개 포맷을 모두 ingest한 뒤, 모든 draft를
approve하고, 인덱스 상태를 출력하고, 한 원천을 재수집해 멱등성을 보여주고,
문서 하나를 reindex해서 delete-then-upsert 방식의 덮어쓰기를 보여준다.

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
  LLM이 자체 판정하지 않고 코드가 findings로부터 결정론적으로 계산한다.
- **재생성 루프**: verdict가 `regenerate`면 `config: verification.max_regen`
  (기본 2회) 한도 내에서 S2/S3를 재실행한다. 완전성 문제면 S2+S3를, 근거성
  문제만 있으면 S3만 재실행한다.
- **6-B 관계 큐레이션**: S5의 결정론적 관계를 `core/wiki_io.py::neighbor_candidates`
  (공유 엔티티/태그 기반 후보)를 참고해 LLM이 keep/prune/add로 큐레이션한다.
  검증이 통과하면 큐레이션 결과가 즉시 `frontmatter.relations`에 반영된다(별도
  수동 승인 단계 없음 -- 프로젝트 결정 사항).
- 각 시도의 리포트는 `staging/{source_id}/06_verification/{doc_id}.json`에
  남고, 문제가 있으면 draft 본문 상단에 `<!-- S5.5 ... -->` 주석으로 표시된다
  (검토용 -- 청킹/임베딩 전에는 `<!-- REVIEW: ... -->`와 함께 자동으로 제거됨).
- `wiki verify <doc_id>`: 현재 draft/approved 문서에 대해 S5.5를 단독 재실행
  (사람이 draft를 직접 수정한 뒤 다시 확인할 때 유용).
- `wiki relink [--all|<doc_id>] [--apply]`: 이미 approved된 문서들의 관계를
  현재 코퍼스 기준으로 재큐레이션하는 배치 작업(예: 나중에 approve된 문서로의
  역방향 링크를 뒤늦게 보충). `--apply` 전에는 dry-run으로 변경 예정 사항만
  출력한다. 관계는 임베딩되는 콘텐츠가 아니므로 reindex는 필요 없다.
- **Fake 모드에서는 완전히 무동작(no-op)이다**: `FakeLLMProvider`의 폴백은
  "문제 없음"(근거 검증)과 "전부 유지"(관계 큐레이션)이므로, 오프라인
  테스트에서는 이 단계가 실제로 실행되면서도 기존 산출물(본문·relations)을
  전혀 바꾸지 않는다 -- 기존 68개 테스트가 수정 없이 그대로 통과하는 이유다.

## 아키텍처 노트 / 기획서 대비 범위를 좁힌 지점

- **chunk_context는 S3가 아니라 S7에서 조립된다** (결정 A). S3의 `Enrichment`는
  `section_summaries`만 생성하며, `builder/indexing/embedder.py`가 실제 임베딩
  입력을 조립할 때 청크의 `section_path`를 이 요약들과 매칭한다. 검수자가
  draft의 헤더를 수정해 경로가 더 이상 정확히 일치하지 않으면 `doc_summary`로
  폴백한다.
- **원천 식별은 콘텐츠 해시가 아니라 파일 경로 기반이다**: 같은 경로로 `ingest`를
  재실행하면 항상 같은 `doc_id`를 재사용한다. 파일 내용이 바뀌면 고아 문서를
  새로 만드는 대신 `version`을 올리고 재처리한다(`core/ids.py::make_source_id`,
  `core/manifest.py`).
- **엔티티 정규화와 관계 매핑은 결정론적 로직만 구현했다.**
  `builder/metadata.py::match_entities`는 인메모리 인덱스와 매칭하고
  (선착순/first-seen-wins), 매칭되지 않은 이름은 사람 검토를 위해
  `WikiIndex.unresolved_entities`에 기록한다. `builder/relations.py`는 sqlite
  FK로부터 `foreign_key` 관계를, 공유된 엔티티 canonical로부터 `see_also`
  관계를 생성한다. LLM 기반 관계 판단(`config: relations.use_llm`)은 문서화된
  확장 지점일 뿐 구현되어 있지 않다.
- **LangGraph는 얇은 래퍼다** (`graphs/ingestion_graph.py`). `builder/pipeline.py`가
  사용하고 직접 테스트되는 것과 동일한 순수 함수들 위에 얹혀 있다. S6에서
  reject 시 되돌아가는 라이브 엣지는 없는데, 결정 B에 따라 HITL 거부가
  프로세스 밖에서 처리되기 때문이다(draft를 직접 수정하거나 `ingest --force`를
  재실행) -- 그래프 실행을 중단시킨 채 대기하지 않는다.
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
- **검색/답변 Agent, 인증, UI는 범위 밖이다** -- 기획서 §9는 의도된 RAG 참조
  패턴(Qdrant 검색 결과 → 해당 `wiki/approved/*.md` 읽기 → 필요 시 라이브
  원천 DB 조회)을 설명하지만, 이 섹션의 내용은 여기서 구현되지 않았다.


# builder API 실행 방법
```
uvicorn api.app:app --port 8002 --reload
```