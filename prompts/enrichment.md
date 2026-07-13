You are extracting semantic metadata from a normalized Wiki document. Given
the full Markdown body, produce:

- `doc_summary`: 1-3 sentences summarizing the whole document. Grounded only
  in the given text.
- `section_summaries`: one entry per top-level content section, each a single
  sentence, with `section_path` matching the section's heading text (or the
  `>`-joined heading path for nested sections, e.g. `절차>센서 초기화`).
- `entities`: notable named things (systems, components, terms, people,
  organizations, products, procedures) mentioned in the text, each with
  `name` and `type` (use only the allowed entity types provided). Do not set
  `canonical` -- that is resolved later against the existing document index.
- `keywords`: short list of salient terms.
- `concepts`: short list of broader topics/themes.

Do not invent entities, numbers, or facts absent from the text. Never fabricate
a canonical name or alias.
