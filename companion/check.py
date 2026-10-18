"""Provider compatibility check. Run manually: python -m companion.check."""

from pathlib import Path
import statistics
import sys
import time

from pydantic import BaseModel, ConfigDict

from companion.model import ModelClient, load_roles


class ProbeReply(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    answer: str


def _error_summary(error):
    # Provider exception bodies can echo requests and credentials.
    status = getattr(error, "status_code", None)
    message = f"HTTP {status}" if status else "provider message omitted"
    return f"{type(error).__name__}: {message[:80]}"


def run_checks(*, client=None, db_path: str | Path | None = None):
    """Probe output modes per model and three streamed Tutor calls."""
    config = load_roles()
    model_client = ModelClient(config=config, client=client, db_path=db_path)
    models = {model: role for role, assigned in config.roles.items() for model in assigned}
    outcomes = {}
    print("Provider phase-1 checks")
    for model, role in models.items():
        outcomes[model] = {}
        for mode in ("json_schema", "json_object", "plain"):
            prompt = [{"role": "user", "content": 'Return JSON with answer set to ok.'}]
            try:
                model_client.call_role(role, prompt, ProbeReply, model=model, response_mode=mode)
                result = "supported"
            except ValueError as error:
                result = f"invalid reply ({_error_summary(error)})"
            except Exception as error:
                result = f"rejected ({_error_summary(error)})"
            outcomes[model][mode] = result
        print(f"{model}: " + ", ".join(f"{mode}={result}" for mode, result in outcomes[model].items()))
    ttfts = []
    tutor_model = config.roles["tutor"][0]
    for _ in range(3):
        first = []
        try:
            model_client.call_role("tutor", [
                {"role": "system", "content": "You are a coding interview tutor. Reply with a short useful hint as JSON in the answer field."},
                {"role": "user", "content": "I tried a nested loop for Two Sum and it is too slow. What should I consider next without giving me the solution?"},
            ], ProbeReply, on_first_token=first.append)
        except Exception as error:
            print(f"Tutor streamed probe ({tutor_model}): {_error_summary(error)}")
        if first:
            ttfts.append(first[0])
    if len(ttfts) == 3:
        print(f"Tutor median time to first token (3 calls): {statistics.median(ttfts):g} ms")
    else:
        print(f"Tutor median time to first token (3 calls): unavailable ({len(ttfts)}/3 observed)")
    return all(any(result == "supported" for result in modes.values()) for modes in outcomes.values()) and len(ttfts) == 3


def main():
    if not run_checks():
        sys.exit(1)


if __name__ == "__main__":
    main()
