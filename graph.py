from langgraph.graph import StateGraph,END
from state import GraphState
from nodes import detect_ambiguity,generate_sql,validate_sql,execute_sql,explain_result

MAX_SQL_RETRIES = 2

def route_after_ambiguity(state: GraphState) -> str:
    return "needs_clarification" if state.get("is_ambiguous") else "generate_sql"

def route_after_validation(state: GraphState) -> str:
    if state.get("is_valid_sql"):
        return "execute_sql"
    retries = state.get("_sql_retries", 0)
    if retries < MAX_SQL_RETRIES:
        return "retry"
    return "give_up"

def _track_retry(state: GraphState) -> dict:
    return {"_sql_retries": state.get("_sql_retries", 0) + 1}

def _give_up(state: GraphState) -> dict:
    return {
        "final_answer": (
            "I couldn't produce a valid SQL query for this after a few attempts. "
            f"Last error: {state.get('validation_error', 'unknown')}"
        ),
        "status": "error",
    }

def build_graph():
    graph = StateGraph(GraphState)

 # 1. register every node
    graph.add_node("detect_ambiguity", detect_ambiguity)
    graph.add_node("generate_sql", generate_sql)
    graph.add_node("validate_sql", validate_sql)
    graph.add_node("execute_sql", execute_sql)
    graph.add_node("explain_result", explain_result)
    graph.add_node("track_retry", _track_retry)
    graph.add_node("give_up", _give_up)

# 2. entry point
    graph.set_entry_point('detect_ambiguity')

# 3. branch: ambiguous -> END, clear -> generate_sql
    graph.add_conditional_edges(
        'detect_ambiguity',
        route_after_ambiguity,
        {'needs_clarification':END,'generate_sql':'generate_sql'}
    )

 # 4. straight line: generate_sql -> validate_sql
    graph.add_edge("generate_sql", "validate_sql")

# 5. branch: valid -> execute, invalid -> retry or give up
    graph.add_conditional_edges(
        'validate_sql',
        route_after_validation,
        {'execute_sql':'execute_sql','retry':'track_retry','give_up':'give_up'}
    )

    #6
     # 6. remaining straight lines
    graph.add_edge("track_retry", "generate_sql")   # loop back for another attempt
    graph.add_edge("execute_sql", "explain_result")
    graph.add_edge("explain_result", END)
    graph.add_edge("give_up", END)

    return graph.compile()

if __name__ == "__main__":
    from  schema_utils import get_schema_text

    app = build_graph()
    schema = get_schema_text('chinookdb.sqlite')

    print("====TEST 1 :unambiguous question ====")
    result1=app.invoke({
        'question':"List all the artist whose name starts with B",
        'schema_text':schema,
        'clarification_context':""
    })
    print(result1.get('status'))
    print(result1.get('final_answer'))

    print('\n===TEST 2 : ambiguous question === ')
    result2 = app.invoke({
        'question':'Who are the top artist',
        'schema_text':schema,
        'clarification_context':""
    })
    print(result2.get('status'))
    print(result2.get('clarifying_question'))
    print(result2.get("clarifying_options"))