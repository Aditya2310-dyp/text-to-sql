import streamlit as st

from graph import build_graph
from schema_utils import get_schema_text

DB_PATH = "chinookdb.sqlite"

st.set_page_config(page_icon="🗄️",page_title='TEXT to SQL',layout='centered')
st.title("TEXT to SQL with Clarification Engine")
st.caption("Ambiguous questions get a follow-up question instead of a guessed answer.")

if "app" not in st.session_state:
    st.session_state.app = build_graph()
if "schema_text" not in st.session_state:
    st.session_state.schema_text = get_schema_text(DB_PATH)
if "messages" not in st.session_state:
    st.session_state.messages = []
if "pending_state" not in st.session_state:
    st.session_state.pending_state = None

#side bar : show schema allow rest

with st.sidebar:
    st.subheader("Schema(Chinook DB)")
    st.text(st.session_state.schema_text)

    if st.button("Reset Conversation"):
        st.session_state.messages = []
        st.session_state.pending_state = None
        st.rerun()

for msg in st.session_state.messages:
    with st.chat_message(msg['role']):
        st.markdown(msg['content'])
        if msg.get('sql'):
            with st.expander('Show SQL'):
                st.code(msg['sql'],language='sql')

user_input = st.chat_input('Ask a question about the music store database..')

if user_input:
    st.session_state.messages.append({'role':'user','content':user_input})
    with st.chat_message('user'):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):

            if st.session_state.pending_state is not None:
                prior = st.session_state.pending_state
                clarification_context = prior.get('clarification_context',"")
                clarification_context += f"\nQ: {prior.get('clarifying_question')}\nA: {user_input}"

                graph_input = {
                    "question": prior["question"],           # reuse the ORIGINAL question
                    "schema_text": st.session_state.schema_text,
                    "clarification_context": clarification_context,
                }
                st.session_state.pending_state = None  # clear it, we're resolving it now

            else:
                # this is a brand new question
                graph_input = {
                    "question": user_input,
                    "schema_text": st.session_state.schema_text,
                    "clarification_context": "",
                }

            result = st.session_state.app.invoke(graph_input)

            if result.get("status") == "needs_clarification":
                question = result["clarifying_question"]
                options = result.get("clarifying_options") or []
                text = question
                if options:
                    text += "\n\n" + "\n".join(f"- {o}" for o in options)

                st.markdown(text)
                st.session_state.messages.append({"role": "assistant", "content": text})

                # remember the ORIGINAL question and context so next turn can resolve it
                result["question"] = graph_input["question"]
                result["clarification_context"] = graph_input["clarification_context"]
                st.session_state.pending_state = result

            else:
                answer = result.get("final_answer", "Something went wrong.")
                st.markdown(answer)
                sql = result.get("sql_query")
                st.session_state.messages.append({"role": "assistant", "content": answer, "sql": sql})
                if sql:
                    with st.expander("Show SQL"):
                        st.code(sql, language="sql")