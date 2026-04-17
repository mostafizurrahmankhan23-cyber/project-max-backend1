import os
from openai import OpenAI

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini")

def upload_spreadsheet(file_bytes: bytes, filename: str) -> str:
    uploaded = client.files.create(
        file=(filename, file_bytes),
        purpose="user_data",
    )
    return uploaded.id

def ask_about_file(question: str, file_id: str, previous_response_id: str | None = None) -> dict:
    instructions = (
        "You are a finance spreadsheet assistant. "
        "Answer questions based on the uploaded spreadsheet and normal reasoning. "
        "If something in the spreadsheet is ambiguous, ask a clarification question. "
        "If the user explains the meaning, use that explanation in later turns of the same conversation. "
        "Mention relevant tab names and columns when helpful. "
        "Do not give vague answers when the spreadsheet already contains enough information."
    )

    kwargs = {
        "model": MODEL,
        "instructions": instructions,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_file", "file_id": file_id},
                    {"type": "input_text", "text": question},
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
