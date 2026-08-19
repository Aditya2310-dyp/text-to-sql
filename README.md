# Text-to-SQL Assistant with a Clarification Engine

A Text-to-SQL system that detects when a natural-language question is too
ambiguous to translate into a single correct query, and asks the user a
clarifying question instead of silently guessing. Supports querying any
uploaded CSV or Excel data, not just a fixed demo database. Built with
LangGraph, LangChain, Ollama, and Streamlit.

## Why this exists

Most Text-to-SQL demos generate a query no matter what you ask. "Who are
the top customers?" gets turned into *some* SQL — but top by what metric?
Revenue? Order count? How many results? A wrong silent guess is worse
than a question, especially for anything used to make a real decision.
This project adds an explicit ambiguity-detection step before generation,
so the system asks instead of assumes — then validates every generated
query against the real schema before it's ever executed.

It also doesn't assume you're working with a pre-built database. Upload
one or more CSV/Excel files and the app infers table relationships and
lets you query across them immediately, with no manual schema setup.

## Architecture

```
question -> [detect_ambiguity] --ambiguous--> ask user, wait for reply
                   |
                clear
                   v
            [generate_sql] -> [validate_sql] --invalid--> retry (max 2) -> give up
                   |
                 valid
                   v
            [execute_sql] -> [explain_result] -> answer
```

Built as a `langgraph.StateGraph` (`graph.py`), with node logic in
`nodes.py`.

- **detect_ambiguity** — structured-output LLM call (Pydantic model)
  classifying whether the question has a well-defined SQL translation:
  undefined metric ("top", "best"), missing time range, undefined
  top-N count, or a term that maps to more than one column/table. Once a
  clarification round has produced context, re-classification is
  skipped — the resolved answer is trusted and the pipeline proceeds
  straight to SQL generation, rather than asking the model to re-judge a
  question it's already shown to handle unreliably with extra context.
- **generate_sql** — schema + resolved question → SQL, using whichever
  database is currently loaded (`db_path` flows through graph state, so
  a fresh upload is picked up without restarting the app). On a
  validation retry, the previous error is fed back into the prompt so
  the next attempt can actually correct the mistake.
- **validate_sql** — hard-blocks non-`SELECT` statements and a keyword
  denylist (`insert`, `update`, `delete`, `drop`, `alter`, `create`,
  `attach`, `pragma`), then dry-runs `EXPLAIN` against the active SQLite
  file to catch hallucinated tables/columns before execution.
- **execute_sql** — runs the query, caps rows returned to the LLM to
  keep the explanation step's context small.
- **explain_result** — turns raw rows into a plain-English answer,
  including the actual computed values (e.g. revenue figures), not just
  row labels.

**Multi-turn clarification** happens outside the graph, in `app.py`. The
graph itself is single-shot — when a question is ambiguous, it routes to
`END` and hands control back to the caller. Streamlit's `session_state`
tracks the pending clarification and stitches the user's follow-up answer
back into a `clarification_context` string before re-invoking the graph.

## Data ingestion & schema-less relationship inference

Any uploaded CSV or Excel file is converted into a SQLite table via
`ingest.py` (multi-sheet Excel workbooks become one table per sheet).
Everything downstream — schema extraction, ambiguity detection, SQL
generation, validation, execution — is unchanged by the input format,
since the rest of the pipeline only ever talks to a SQLite file.

Uploaded tabular data has no declared foreign keys — `PRAGMA
foreign_key_list` returns nothing for a table created from a spreadsheet.
`relationship_inference.py` detects likely relationships between such
tables using two signals rather than trusting the LLM to guess from
schema text alone (a failure mode the SQL-generation step ran into
independently — the same class of hallucination `validate_sql` exists to
catch elsewhere in the pipeline):

1. **Column-naming heuristics** — e.g. a column `customer_id` in one
   table plausibly references a table `Customer`/`Customers` with an
   `id`/`CustomerId` column.
2. **Actual value-overlap verification** — the real check: what fraction
   of the candidate column's values actually exist in the referenced
   column. A naming match alone is only a candidate; a relationship is
   only reported once it clears an overlap threshold (default 80%).

Inferred relationships are appended directly to the schema text handed
to the LLM, so joins across uploaded tables work the same way they would
against a database with real foreign keys.

### Measured accuracy

`evaluate_relationship_inference.py` measures this against Chinook's own
real, declared foreign keys as ground truth — run inference blind
(without access to the declared keys) and check what's recovered.
Comparison is direction-agnostic, since detecting `Album → Artist` and
`Artist → Album` both correctly identify the same real relationship,
just from opposite tables.

```
Precision: 90.0%
Recall:    81.8%
F1 score:  85.7%
```

Run it yourself:
```
python evaluate_relationship_inference.py
```

The two missed relationships are explainable, not random noise: one is a
self-referencing relationship (`Employee.ReportsTo → Employee.EmployeeId`),
which the current implementation structurally excludes by skipping
same-table comparisons; the other (`Customer.SupportRepId →
Employee.EmployeeId`) uses a naming pattern the heuristic doesn't cover.

## Setup

1. Install [Ollama](https://ollama.com) and pull a model that supports
   tool calling (needed for structured output):
   ```
   ollama pull qwen2.5:7b
   ```
   A smaller model like `llama3.2:3b` runs faster but is noticeably less
   reliable at the ambiguity classification step — see *Known
   limitations* below.
2. Install Python dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Run:
   ```
   streamlit run app.py
   ```
4. The app starts with a bundled Chinook (music store) database loaded
   by default. To query your own data, use the sidebar uploader to add
   one or more CSV/Excel files, then click **Load Uploaded Data** — the
   schema, inferred relationships, and chat history all reset to reflect
   the new dataset.

## Try these to see the clarification engine trigger

- "Who are the top artists?" → ambiguous (top by what metric? how many?)
- "What was the best selling genre last year?" → ambiguous (units vs.
  revenue, and an undefined time range)
- "List all artists whose name starts with B" → not ambiguous, answers
  directly

## Testing

`test_core.py` covers SQL validation, execution, and graph routing logic
without needing a live Ollama server (no LLM calls):
```
python test_core.py
```

The ambiguity-detection and SQL-generation prompts were iterated on using
few-shot examples rather than abstract instructions alone — abstract
rules (e.g. "flag vague superlatives") were consistently insufficient on
their own for a 7B model to follow reliably; showing a fully worked
example of the exact desired output shape resolved this in every case
encountered during development.

## Known limitations

- **Small local models are unreliable at nuanced classification without
  few-shot scaffolding.** `llama3.2:3b` initially failed to flag "who are
  the top artists?" as ambiguous at all, and separately left
  `clarifying_question`/`clarifying_options` empty even after correctly
  classifying a question as ambiguous. Both were resolved by adding fully
  worked examples to the prompt rather than relying on abstract rules —
  `qwen2.5:7b` is noticeably more reliable at the same task.
- **A model given the correct schema can still hallucinate an incorrect
  join.** SQL generation initially selected a column that doesn't exist
  on the joined table, even with the correct schema in the prompt, and
  repeated the same mistake across retries despite the validation error
  being fed back. Fixed with an explicit rule plus a worked example
  showing the full correct join chain for that shape of query.
- **Relationship inference misses self-referencing foreign keys** (a
  table referencing itself, e.g. an employee reporting to another
  employee) by design — same-table comparisons are skipped entirely.
- **The keyword denylist in `validate_sql` uses substring matching**, not
  whole-word matching — a column genuinely named e.g. `dropdown_choice`
  would false-positive as containing `drop`. Worth knowing before
  trusting it against an arbitrary uploaded schema with unusual column
  names.
- **The file uploader accepts CSV/Excel only** — uploading an existing
  `.db`/`.sqlite` file directly isn't currently exposed in the UI, though
  `ingest.py` supports it.

## Possible extensions

- **Real schema linking** — embed table/column descriptions and retrieve
  only relevant ones per question instead of always sending the full
  schema. Matters more on databases with far more tables than Chinook's 11.
- **Multi-turn ambiguity** — currently one clarification round; could
  loop if the user's answer is itself ambiguous.
- **Swap Ollama for a hosted model** (Anthropic/OpenAI API) so the app
  doesn't require anyone else running it to install Ollama locally, and
  to reduce the small-model reliability issues documented above.
- **Query approval before execution** — show the generated SQL and let
  the user approve/edit it before it runs, rather than executing
  immediately after validation.
