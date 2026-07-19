You are performing S5.5 Grounded Verification of a generated Wiki document.

You are comparing two separate inputs:

1. ORIGINAL: authoritative content rendered from the extracted source blocks.
2. GENERATED: the Wiki document body produced from the ORIGINAL.

Your task is not to review writing quality, tone, formatting preference, or
style. Your task is to identify factual grounding issues, meaningful omissions,
and altered technical values.

The GENERATED document may reorganize, summarize, paraphrase, or translate the
ORIGINAL. Treat semantically equivalent content as grounded even when the
wording, language, section order, or formatting differs.

Evaluate the following categories.

1. Faithfulness

Identify specific claims in the GENERATED body that are not supported by the
ORIGINAL.

Examples include:

* invented facts, behaviors, explanations, recommendations, or guarantees
* invented procedures or steps
* invented commands, configuration keys, SQL, flags, limits, or defaults
* conclusions that cannot reasonably be derived from the ORIGINAL

For each issue, return:

* claim: A concise Korean description of the unsupported claim.
* grounded: always false for a reported issue
* severity: low, med, or high
* reason: a concise explanation of why the claim is unsupported
* generated_evidence: the GENERATED section title and a short exact excerpt
* original_evidence: a relevant ORIGINAL section title and excerpt when one
    helps demonstrate the mismatch; otherwise null
* suggested_action: one of remove, revise, or verify_with_source

Do not report:

* faithful paraphrasing or summarization
* translated content that preserves the original meaning
* information that can be directly and unambiguously inferred from the
    ORIGINAL
* content that exists elsewhere in the ORIGINAL under a different heading
* stylistic connective text that does not introduce a new factual claim

2. Completeness

Identify notable information present in the ORIGINAL but missing from the
GENERATED body.

Only report omissions that materially reduce the usefulness, correctness, or
independent understandability of the Wiki.

Notable omissions may include:

* an entire technical section or subsection
* required procedure steps or prerequisites
* warnings, limitations, exceptions, or compatibility conditions
* commands, SQL examples, configuration examples, or table rows
* named entities, parameters, supported values, defaults, or constraints
* information needed to correctly interpret another retained statement

For each issue, return:

* missing_content: a concise description of what is missing
* severity: low, med, or high
* reason: why the omission matters to a reader
* original_evidence: the ORIGINAL section title and a short exact excerpt
* expected_wiki_section: the GENERATED section where the information should
    appear, if identifiable; otherwise null
* suggested_action: always add

Do not report:

* navigation menus, breadcrumbs, page headers, page footers, feedback prompts,
    edit links, version selectors, previous/next links, or other page chrome
* repeated content that was correctly deduplicated
* minor examples removed during summarization when all governing rules and
    technical meaning remain intact
* content that is faithfully represented elsewhere in the GENERATED body
* purely decorative tables or formatting that were converted into equivalent
    prose or lists

3. Value Changes

Identify numbers, SQL statements or clauses, CLI commands or flags, and
configuration values that describe the same field, parameter, quantity, or
operation in both the ORIGINAL and the GENERATED body, but whose actual value
differs.

For each issue, return:

* kind: one of number, sql, command, or config
* subject: the field, parameter, quantity, command, or operation being
    compared
* original_value: the exact value from the ORIGINAL
* changed_value: the exact value from the GENERATED body
* severity: low, med, or high
* reason: a concise explanation of the technical difference
* original_evidence: the ORIGINAL section title and a short exact excerpt
* generated_evidence: the GENERATED section title and a short exact excerpt
* suggested_action: always restore_original_value

Only report a value change when:

* the same subject is present in both texts
* both values have the same contextual role
* the value itself has changed

Do not report:

* paraphrasing or reordering that leaves the value unchanged
* formatting-only differences such as:
    * 8,080 versus 8080
    * 0.5 versus 0.50
    * SQL keyword casing
    * harmless whitespace or line-break changes
    * equivalent quoting or Markdown code-fence formatting
* an equivalent unit conversion when the meaning and precision are preserved
* approximate rounding when the ORIGINAL explicitly indicates approximation
* a new value with no ORIGINAL counterpart; report that under faithfulness
* an ORIGINAL value absent from the GENERATED body; report that under
    completeness
* values that happen to be numerically equal or similar but refer to different
    fields or contexts

Severity Guidelines

Use severity based on the consequence if a reader relies on the issue:

* high
    * could cause an incorrect command, query, configuration, deployment, data
        result, security decision, compatibility decision, or operational failure
    * removes or changes a mandatory prerequisite, warning, restriction, or
        critical procedure step
* med
    * changes technical understanding or makes an important workflow incomplete
    * omits a useful example, condition, exception, supported option, or section
        that a reader would reasonably expect
* low
    * has limited operational impact but is still a factual addition, omission,
        or mismatch worth reviewing

Evidence Rules

* Evidence excerpts must be short and exact.
* Prefer the nearest section heading as the evidence location.
* Do not invent line numbers, section names, or evidence.
* Use null when no valid evidence pointer exists.
* Do not use external knowledge.
* Judge only the supplied ORIGINAL and GENERATED content.

Classification Rules

Each problem must appear in exactly one category.

Apply the following precedence:

1. If content exists in both texts but the corresponding value differs,
    classify it as value_changes.
2. If content exists only in the GENERATED body, classify it as
    faithfulness.
3. If content exists only in the ORIGINAL, classify it as completeness.

Do not duplicate the same underlying problem across categories.

When several nearby excerpts represent the same underlying issue, combine them
into one issue unless they require different corrections or have meaningfully
different consequences.

Ignore phrasing, tone, stylistic quality, heading preference, and organization
unless organization causes factual meaning to change or required context to be
lost.

If no issues are found, return empty lists for all three categories.

Return only output matching the required structured response schema.