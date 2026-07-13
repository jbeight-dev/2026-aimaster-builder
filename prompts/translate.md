Translate Prompt

You are translating a normalized Wiki document into Korean.

Your task is to translate the provided Markdown content into natural and accurate Korean while preserving its original meaning, structure, and technical accuracy.

Translation Rules

1. Translate explanatory prose into natural Korean.
2. Preserve the original Markdown structure exactly as much as possible:
    * Headings (#, ##, ###)
    * Lists
    * Tables
    * Blockquotes
    * Links
    * Code blocks
    * Inline code
    * HTML comments
3. Do not summarize, omit, expand, or reorganize the content.
4. Do not add facts, explanations, examples, or interpretations that are not present in the source.
5. Preserve all numbers, versions, units, and technical values accurately.

Technical Terms

Preserve the following in their original form when translation could reduce technical accuracy:

* Product names
* System names
* API names
* Configuration keys
* Environment variables
* Database objects
* Table and column names
* Function and class names
* File names and paths
* URLs
* SQL
* Shell commands
* Source code
* Error messages

Examples:

* StarRocks → StarRocks
* Data Cache → Data Cache
* storage_root_path → storage_root_path
* datacache_disk_size → datacache_disk_size
* SELECT * FROM ... → unchanged

For well-known technical concepts, use a natural Korean translation while optionally retaining the original English term when useful for clarity.

Examples:

* garbage collection → 가비지 컬렉션(Garbage Collection)
* query acceleration → 쿼리 가속화
* shared-data cluster → shared-data cluster
* cache eviction → 캐시 제거(eviction)

Code and Commands

Do not translate or modify the contents of:

* Fenced code blocks
* Inline code
* SQL statements
* Shell commands
* Configuration examples
* JSON, YAML, XML, or other structured data

Preserve their syntax exactly.

Markdown

The output must remain valid Markdown.

Preserve:

* Heading hierarchy
* List hierarchy
* Tables
* Code fences and language identifiers
* Links and anchors
* HTML comments

Do not wrap the result in an additional Markdown code block.

Output Language

The final document must be written primarily in Korean.

If the source document is already written in Korean, preserve the original content and only translate meaningful non-Korean explanatory passages when necessary.

Grounding

Translate only what is present in the source document.

Do not:

* Invent missing information
* Correct technical claims based on external knowledge
* Add explanations
* Remove uncertain content
* Resolve ambiguities by guessing

If the source contains an ambiguous or malformed technical expression, preserve its meaning as closely as possible rather than inventing a correction.

Output

Return only the translated Markdown document.