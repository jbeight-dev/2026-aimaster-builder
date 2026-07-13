You are grounding a generated Wiki document body against the original
extracted source content (S5.5 - Grounded Verification). You are not
self-reviewing your own writing style; you are fact-checking the generated
text against a separate, authoritative source.

Given the ORIGINAL content (rendered from the raw extracted blocks) and the
GENERATED body, identify:

- `faithfulness`: specific claims in the GENERATED body that are NOT
  supported by the ORIGINAL (hallucinations, invented numbers/steps/facts).
  For each, give the exact claim text, whether it is grounded (`false` for a
  hallucination you are flagging), an optional evidence pointer, and a
  severity (`low`/`med`/`high`) based on how consequential the inaccuracy is
  if a reader acted on it.
- `completeness`: notable sections, tables, or entities present in the
  ORIGINAL that are missing from the GENERATED body.

Do not flag phrasing, tone, or organization -- only factual grounding and
content coverage. If nothing is wrong, return empty lists for both fields.
