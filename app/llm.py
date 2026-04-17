import os
from openai import OpenAI

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini")

def explain_finance_result(question: str, result: dict) -> dict:
    prompt = f"""
You are a finance operations copilot for a spreadsheet workflow.

User question:
{question}

Structured diagnostic result:
{result}

Write a concise response with:
1. a direct answer,
2. the top issue categories,
3. the most likely fixes,
4. a short priority order.

Do not invent spreadsheet facts beyond the structured result.
"""

    response = client.responses.create(
        model=MODEL,
        input=prompt,
    )

    return {
        "llm_answer": response.output_text
    }
