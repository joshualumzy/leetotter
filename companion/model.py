"""Validated tutor calls through an OpenAI-compatible endpoint."""

import os
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from companion.store import open_store

MODEL = "x-test-1"


class TutorReply(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    reply_text: str = Field(min_length=1)
    gap: Literal[
        "misread_problem", "missing_tool", "weak_intuition", "wrong_intuition",
        "stuck_optimising", "cant_write_code", "bugs_and_edge_cases",
    ]
    help_kind: Literal[
        "question", "trace", "analogy", "teach_tool", "syntax", "snippet",
        "partial_logic", "full_solution",
    ]
    follow_up_question: str | None


def call_tutor(prompt: str, *, db_path: str | Path | None = None, client=None) -> TutorReply:
    """Request one tutor reply; retry once if its structure is invalid."""
    base_url = os.environ.get("NUS_SOCLAAS_BASE_URL")
    if not base_url:
        raise RuntimeError("NUS_SOCLAAS_BASE_URL is required")
    api_key = os.environ.get("NUS_SOCLAAS_API_KEY")
    if not api_key:
        raise RuntimeError("NUS_SOCLAAS_API_KEY is required")

    if client is None:
        client = OpenAI(base_url=base_url, api_key=api_key, timeout=60.0, max_retries=0)

    messages = [
        {"role": "system", "content": "You are the Leetotter Tutor. Return a reply matching the requested JSON schema. Diagnose the learner's gap and label the kind of help."},
        {"role": "user", "content": prompt},
    ]
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": "tutor_reply", "strict": True, "schema": TutorReply.model_json_schema()},
    }

    retried = False
    while True:
        started = perf_counter()
        response = client.chat.completions.create(
            model=MODEL, messages=messages, response_format=response_format
        )
        latency_ms = round((perf_counter() - started) * 1000)
        content = response.choices[0].message.content or ""
        try:
            reply = TutorReply.model_validate_json(content)
        except ValidationError as error:
            if retried:
                raise ValueError(f"Tutor reply invalid after retry: {error}") from error
            retried = True
            messages.append({"role": "assistant", "content": content})
            messages.append({
                "role": "user",
                "content": f"Your previous reply failed schema validation. Error: {error}. Return corrected JSON only.",
            })
            continue

        usage = response.usage
        with open_store(db_path) as db:
            db.execute(
                "INSERT INTO model_calls (role, model, latency_ms, prompt_tokens, completion_tokens, validation_result, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "tutor", MODEL, latency_ms,
                    usage.prompt_tokens if usage else None,
                    usage.completion_tokens if usage else None,
                    "valid", datetime.now(timezone.utc).isoformat(),
                ),
            )
        return reply
