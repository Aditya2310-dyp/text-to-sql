from typing import TypedDict , Optional ,List

class GraphState(TypedDict,total=False):
    #input
    question:str
    schema_text:str
    clarification_context:str

    # --- Ambiguity detection output ---
    is_ambiguous: bool
    ambiguity_reason: str
    clarifying_question: str
    clarifying_options: List[str]

    # --- SQL generation / validation / execution ---
    sql_query: str
    is_valid_sql: bool
    validation_error: str
    sql_result: str
    row_count: int

    # --- Final output ---
    final_answer: str

    # --- Control flow ---
    status: str   # "needs_clarification" | "proceeding" | "done" | "error"
    _sql_retries: int # tracks how many times genrate_sql has been retried