import os
import json
from openai import OpenAI

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini")

def ask_about_snapshot(question: str, snapshot: dict, previous_response_id: str | None = None) -> dict:
    instructions = (
        "You are a finance spreadsheet assistant. "
        "The user is asking about a live spreadsheet snapshot from Google Sheets. "
        "Answer directly from the spreadsheet content and normal reasoning. "
        "If something is ambiguous, ask a clarification question. "
        "If the user explains the meaning of a tab/column, use that explanation in later turns of the same conversation. "
        "Mention tab names and columns when helpful. "
        "Do not be vague if the spreadsheet already contains enough information."
    )

    snapshot_text = json.dumps(snapshot, ensure_ascii=False)

    kwargs = {
        "model": MODEL,
        "instructions": instructions,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": f"Spreadsheet snapshot:\n{snapshot_text}\n\nQuestion: {question}"
                    }
                ],
            }
        ],
    }

    if previous_response_id:
        kwargs["previous_response_id"] = previous_response_id

    response = client.responses.create(**kwargs)

    return {
        "answer": response.output_text,
        "response_id": response.id,
    }
