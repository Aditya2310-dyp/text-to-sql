from typing import List, Optional
from pydantic import BaseModel, Field
import sqlite3

class AmbiguityCheck(BaseModel):
    is_ambiguous: bool = Field(
        description="True if the question cannot be turned into a single, unambiguous SQL query"
    )
    reasoning: str = Field(
        description="One sentence explaining why it is or isn't ambiguous"
    )
    clarifying_question: Optional[str] = Field(
        default=None,
        description="A short question to ask the user, only if is_ambiguous is true"
    )
    clarifying_options: Optional[List[str]] = Field(
        default=None,
        description="2-4 concrete answer options for the clarifying question, if applicable"
    )

from langchain_core.prompts import ChatPromptTemplate

AMBIGUITY_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """You are a query analyst for a SQL assistant. Given a database schema and a
user's natural-language question, decide whether the question is precise enough to translate
into ONE unambiguous SQL query.

Flag it as ambiguous when the question:
- Uses a vague superlative without defining the metric (e.g. "best", "top", "most popular" —
  by revenue? by quantity sold? by count of rows?)
- Has no time range where one plausibly matters (e.g. "top selling" — all-time, this year, last quarter?)
- Uses a term that could map to more than one column or table given this schema
- Has an undefined "N" for a top-N request (e.g. "show me the top customers" — how many?)

Do NOT flag it as ambiguous if:
- It's a simple lookup with clear filters
- The vagueness doesn't change the SQL structure meaningfully
- Prior clarification context already resolves the ambiguity

Whenever is_ambiguous is true, you MUST also fill in clarifying_question and clarifying_options
with a specific, concrete question and 2-4 concrete answer choices. Never leave them empty or null
when is_ambiguous is true.

If the prior clarification context already answers the ambiguity (e.g. specifies the metric, time
range, or count that was missing), you MUST set is_ambiguous to false and proceed — do not ask again.

Examples:

Question: "Who are the top artists?"
Prior clarification context: (none)
-> is_ambiguous: true
   reasoning: "Top" is undefined — could mean units sold, revenue, or track count. No count given either.
   clarifying_question: "How would you like to rank artists, and how many should I show?"
   clarifying_options: ["By total revenue", "By number of tracks sold", "By number of albums", "Top 10"]

Question: "Who are the top artists?"
Prior clarification context: "Q: How would you like to rank artists, and how many should I show?\\nA: by revenue, top 5"
-> is_ambiguous: false
   reasoning: The prior clarification already specifies ranking by revenue and a count of 5, resolving the original ambiguity.
   clarifying_question: null
   clarifying_options: null

Question: "What was the best selling genre last year?"
Prior clarification context: (none)
-> is_ambiguous: true
   reasoning: "Best selling" is undefined (units vs revenue), and the database's date range may not include "last year".
   clarifying_question: "Should I rank genres by number of tracks sold or by revenue, and over what time period?"
   clarifying_options: ["By units sold, all-time", "By revenue, all-time", "By units sold, last 12 months of available data"]

Question: "List all artists whose name starts with B"
Prior clarification context: (none)
-> is_ambiguous: false
   reasoning: This is a clear filter with no undefined terms.
   clarifying_question: null
   clarifying_options: null

Question: "Show me all tracks in the Jazz genre"
Prior clarification context: (none)
-> is_ambiguous: false
   reasoning: Genre is a specific named filter, no undefined metric involved.
   clarifying_question: null
   clarifying_options: null

Schema:
{schema_text}

Prior clarification context (may be empty):
{clarification_context}
"""),
    ("human", "{question}"),
])


from langchain_ollama import ChatOllama
from state import GraphState

MODEL_NAME = "qwen2.5:7b"
llm = ChatOllama(model=MODEL_NAME, temperature=0)

ambiguity_chain = AMBIGUITY_PROMPT | llm.with_structured_output(AmbiguityCheck)


def detect_ambiguity(state: GraphState) -> dict:
    print(">>> running detect_ambuiguity")
    print(">>> clarification_context received:", repr(state.get("clarification_context", "")))

    if state.get("clarification_context","").strip():
        return {'is_ambiguous':False,"ambiguity_reason":'Resolved via prior clarification',"status":'proceeding'}

    result: AmbiguityCheck = ambiguity_chain.invoke({
        "schema_text": state["schema_text"],
        "question": state["question"],
        "clarification_context": state.get("clarification_context", "(none)"),
    })
    print(">>>raw result:",result)

    if result.is_ambiguous:
        return {
            "is_ambiguous": True,
            "ambiguity_reason": result.reasoning,
            "clarifying_question": result.clarifying_question or "Could you clarify your question?",
            "clarifying_options": result.clarifying_options or [],
            "status": "needs_clarification",
        }
    return {
        "is_ambiguous": False,
        "ambiguity_reason": result.reasoning,
        "status": "proceeding",
    }

SQL_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """You write SQLite queries. Given the schema and the user's fully-clarified
question, output ONLY the SQL query — no markdown fences, no explanation, no comments.
Rules:
- SELECT statements only. Never write INSERT/UPDATE/DELETE/DROP/ALTER.
- Use only tables/columns that exist in the schema below.
- Prefer explicit column lists over SELECT *.
- Add a LIMIT clause for "top N" style requests.
- The Artist's name is ONLY in the Artist table (Artist.Name). Album has no Name column for the
  artist — it only has ArtistId, a foreign key. To get artist names, you must JOIN Artist.

Example — ranking artists by revenue:
Question: "top 5 artists by revenue"
SQL:
SELECT Artist.Name, SUM(InvoiceLine.UnitPrice * InvoiceLine.Quantity) AS Revenue
FROM Artist
JOIN Album ON Artist.ArtistId = Album.ArtistId
JOIN Track ON Album.AlbumId = Track.AlbumId
JOIN InvoiceLine ON Track.TrackId = InvoiceLine.TrackId
GROUP BY Artist.ArtistId, Artist.Name
ORDER BY Revenue DESC
LIMIT 5;

Schema:
{schema_text}

Resolved context from clarification (may be empty):
{clarification_context}

Previous attempt failed validation with this error (fix it), or empty if this is a first attempt:
{validation_error}
"""),
    ("human", "{question}"),
])

sql_chain = SQL_PROMPT | llm

def generate_sql(state:GraphState) -> dict:
    print(">>> running generate_sql")
    print(">>> schema_text length:", len(state.get("schema_text", "")))   # <-- add this
    print(">>> validation_error passed in:", repr(state.get("validation_error", "")))
    response = sql_chain.invoke({
        "schema_text":state["schema_text"],
        "question": state["question"],
        "clarification_context":state.get("clarification_context","(none)"),
        "validation_error":state.get("validation_error",""),
    })
    sql = response.content.strip()
    # defensive cleanup in case the model added markdown fences anyway
    if sql.startswith("```"):
        sql = sql.strip("`")
        if sql.lower().startswith("sql"):
            sql = sql[3:]
        sql = sql.strip()
    print(">>>generated SQL:",sql)
    return {"sql_query": sql}

BLOCKED_KEYWORDS = ('insert','update','delete','drop','alter','create','attach','pragma') 
def validate_sql(state:GraphState) -> dict:
    print(">>> running validate_sql")
    print(">>> validating SQL:", state["sql_query"])
    sql = state['sql_query'].strip()
    lowered = sql.lower()

    if not lowered.startswith('select'):
        return {'is_valid_sql':False,'validation_error':'Only SELECT statements are allowed.'}
    if any(k in lowered for k in BLOCKED_KEYWORDS):
        return {'is_valid_sql':False,'validation_error':"Query contains a disallowed keyword."}
    try:
        con = sqlite3.connect('chinookdb.sqlite')
        cur = con.cursor()
        cur.execute(f"EXPLAIN {sql}")
        con.close()
        return {'is_valid_sql':True,'validation_error':""}
    except sqlite3.Error as e:
        return {'is_valid_sql':False,'validation_error':str(e)}

def execute_sql(state: GraphState) -> dict:
    print(">>> running execute_sql")
    con = sqlite3.connect("chinookdb.sqlite")
    cur = con.cursor()
    cur.execute(state["sql_query"])
    rows = cur.fetchmany(50)
    col_names = [d[0] for d in cur.description] if cur.description else []
    con.close()

    formatted = f"Columns: {col_names}\nRows: {rows}"
    return {"sql_result": formatted, "row_count": len(rows), "status": "done"}

EXPLAIN_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """Turn the SQL query result into a short, plain-English answer to the
user's original question. Be concise — a sentence or two, plus a compact list if there
are multiple rows worth showing.

IMPORTANT: If the result includes a computed value (e.g. revenue, count, average, total),
you MUST include that value next to each item — never list just the names and drop the
numbers. For example, if the result has artist names and revenue figures, show both:
"Iron Maiden ($138.60), U2 ($105.93), ..." not just "Iron Maiden, U2, ...".

Don't mention SQL or column internals unless useful."""),
    ("human", "Question: {question}\n\nSQL: {sql_query}\n\nResult: {sql_result}"),
])

explain_chain = EXPLAIN_PROMPT | llm

def explain_result(state: GraphState) -> dict:
    print(">>> running explain_result")
    response = explain_chain.invoke({
        "question": state["question"],
        "sql_query": state["sql_query"],
        "sql_result": state["sql_result"],
    })
    return {"final_answer": response.content.strip()}

from schema_utils import get_schema_text
schema = get_schema_text("chinookdb.sqlite")
# import os 
# print("CWD",os.getcwd())
# print("Files here:",os.listdir('.'))
# print("DB exists:",os.path.exists('chinookdb.sqlite'))
# print("LENGTH:", len(schema))
# print("REPR:", repr(schema[:200]))


if __name__ == "__main__":
    schema = get_schema_text("chinookdb.sqlite")

    # # unambiguous case
    # state1 = {"question": "List all artists whose name starts with B", "schema_text": schema,
    #           "clarification_context": "", "validation_error": ""}
    # print(generate_sql(state1))

    # # simulated post-clarification case
    # state2 = {"question": "Who are the top artists?", "schema_text": schema,
    #           "clarification_context": "Q: How would you like to rank artists?\nA: By total revenue, top 5",
    #           "validation_error": ""}
    # print(generate_sql(state2))

    # bad_state = {"sql_query": "SELECT A.Name AS ArtistName FROM Album A"}
    # print(validate_sql(bad_state))

    # good_state = {"sql_query": "SELECT Name FROM Artist LIMIT 5"}
    # print(validate_sql(good_state))

    good_state = {
        "question": "List all artists whose name starts with B",
        "sql_query": "SELECT Name FROM Artist WHERE Name LIKE 'B%' LIMIT 10",
    }
    exec_result = execute_sql(good_state)
    print(exec_result)

    good_state.update(exec_result)
    final = explain_result(good_state)
    print(final)
