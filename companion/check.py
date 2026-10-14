"""Provider compatibility check. Run manually: python -m companion.check."""

from pathlib import Path
import sys
from time import perf_counter

from pydantic import BaseModel, ConfigDict

from companion.model import call_role, load_roles


class ProbeReply(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    answer: str


def run_checks(*, client=None, db_path: str | Path | None = None):
    """Probe each configured role/model and print the phase-1 questions with observations."""
    config = load_roles()
    outcomes = []
    tutor_ttft = None
    observer_ms = None
    for role, configured in config.roles.items():
        for model in configured if isinstance(configured, list) else [configured]:
            first_token = []
            started = perf_counter()
            try:
                call_role(
                    role, [{"role": "user", "content": 'Return exactly {"answer":"ok"} as JSON.'}],
                    ProbeReply, model=model, db_path=db_path, client=client, config=config,
                    stream=role == "tutor", on_first_token=first_token.append,
                )
                result = "valid structured JSON"
            except Exception as error:
                result = f"failed ({type(error).__name__}: {error})"
            elapsed = round((perf_counter() - started) * 1000)
            outcomes.append((role, model, result))
            if role == "observer":
                observer_ms = elapsed
            if role == "tutor" and first_token:
                tutor_ttft = first_token[0]
    print("Provider phase-1 checks")
    for role, model, result in outcomes:
        print(f"{role} ({model}): {result}")
    x2 = [result for _, model, result in outcomes if model == "x-test-2"]
    print(f"x-test-2 reachable with valid structured output: {'yes' if x2 and all(r == 'valid structured JSON' for r in x2) else 'no'}")
    observer = next(result for role, _, result in outcomes if role == "observer")
    print(f"Observer thinking-disabled request: {'succeeded' if observer == 'valid structured JSON' else 'failed'} (configured extra_body; confirm provider disabled thinking)")
    print(f"Observer response time: {observer_ms} ms")
    print("JSON/structured-output support by role/model: see valid structured JSON results above")
    print(f"Tutor time to first token: {tutor_ttft} ms" if tutor_ttft is not None else "Tutor time to first token: unavailable")
    return all(result == "valid structured JSON" for _, _, result in outcomes)


def main():
    if not run_checks():
        sys.exit(1)


if __name__ == "__main__":
    main()
