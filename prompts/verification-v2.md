You are grounding a generated Wiki document body against the original
extracted source content (S5.5 - Grounded Verification). You are not
self-reviewing your own writing style; you are fact-checking the generated
text against a separate, authoritative source.

Given the ORIGINAL content (rendered from the raw extracted blocks) and the
GENERATED body, identify:

- `faithfulness`: specific claims in the GENERATED body that are NOT
  supported by the ORIGINAL (hallucinations, invented numbers/steps/facts --
  i.e. something added that has no counterpart in the ORIGINAL at all). For
  each, give the exact claim text, whether it is grounded (`false` for a
  hallucination you are flagging), an optional evidence pointer, and a
  severity (`low`/`med`/`high`) based on how consequential the inaccuracy is
  if a reader acted on it.
- `completeness`: notable sections, tables, or entities present in the
  ORIGINAL that are missing from the GENERATED body.
- `value_changes`: numbers, SQL statements/clauses, CLI commands or flags, or
  configuration values that appear in BOTH the ORIGINAL and the GENERATED
  body -- describing the same thing, in the same place/context -- but whose
  VALUE differs between them. This is different from `faithfulness`: the
  content is not invented and not missing, it is present in both but silently
  altered, e.g. a port number changed from 8080 to 8081, a SQL WHERE clause's
  predicate or table name changed, a CLI flag's value changed, a config key's
  value changed, a count/threshold/version number changed. For each, report
  `kind` (`number`/`sql`/`command`/`config`), the exact `original_value`, the
  exact `changed_value`, and an optional evidence pointer to where it appears.
  Do NOT report:
    * paraphrasing or reordering of surrounding prose that leaves the actual
      value unchanged
    * formatting-only differences that preserve the value, e.g. "8,080" vs
      "8080", "0.5" vs "0.50", added thousands separators, whitespace/casing
      differences in SQL keywords, or a command reformatted across lines with
      the same flags and values
    * a number that is a plain rounding of the original to a coarser but
      equivalent precision when the ORIGINAL itself signals it is approximate
      (e.g. "about 100 users" -> "~100 users")
    * a genuinely NEW value that has no counterpart in the ORIGINAL at all --
      that is an addition, so it belongs in `faithfulness`, not here
    * a value that appears in the ORIGINAL but is absent from the GENERATED
      body entirely -- that is an omission, so it belongs in `completeness`,
      not here
  Only report a value_change when the SAME field/parameter/quantity is
  present in both texts and the value itself differs.

Do not flag phrasing, tone, or organization -- only factual grounding,
content coverage, and value fidelity. If nothing is wrong, return empty
lists for all three fields.
