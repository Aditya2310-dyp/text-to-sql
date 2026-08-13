from nodes import AMBIGUITY_PROMPT

context = "\nQ: How would you like to rank artists, and how many should I show?\nA: by revenue , top 5 "
print(AMBIGUITY_PROMPT.format(
    schema_text="(schema here, doesn't matter for this check)",
    clarification_context=context,
    question="Who are the top artists?"
))