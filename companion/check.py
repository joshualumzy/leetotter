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


def _thinking_evidence(response):
    choices = getattr(response, "choices", ())
    reasoning = False
    if choices:
        message = choices[0].message
        reasoning_content = getattr(message, "reasoning_content", None)
        content = getattr(message, "content", None) or ""
        reasoning = bool(reasoning_content or "<think>" in content.lower())
    usage = getattr(response, "usage", None)
    details = getattr(usage, "completion_tokens_details", None)
    tokens = getattr(details, "reasoning_tokens", None)
    return getattr(usage, "completion_tokens", None), reasoning or (tokens is not None and tokens > 0)


def _thinking_probe(model_client, model):
    prompt = [{"role": "user", "content":
               'Think through whether two pointers can solve Two Sum on an unsorted array, then return JSON: {"answer":"ok"}.'}]
    samples = {False: [], True: []}
    for _ in range(2):
        for thinking_off in (False, True):
            observed = []
            started = time.perf_counter()
            try:
                model_client.call_role("observer", prompt, ProbeReply, model=model, response_mode="plain",
                                       thinking_off=thinking_off, on_response=observed.append)
            except Exception:
                continue
            latency = round((time.perf_counter() - started) * 1000)
            if observed:
                completion, reasoning = _thinking_evidence(observed[-1])
                samples[thinking_off].append((completion, reasoning, latency))

    def median_value(side, index):
        values = [sample[index] for sample in samples[side] if sample[index] is not None]
        return statistics.median(values) if values else None

    without, with_request = median_value(False, 0), median_value(True, 0)
    without_reasoning = sum(sample[1] for sample in samples[False])
    with_reasoning = sum(sample[1] for sample in samples[True])
    complete = len(samples[False]) == len(samples[True]) == 2
    token_drop = without is not None and with_request is not None and without > 0 and with_request * 2 <= without
    if complete and (token_drop or (without_reasoning == 2 and with_reasoning == 0)):
        status = "honored"
    elif (complete and without_reasoning == with_reasoning == 2 and without is not None
          and with_request is not None and without * 0.8 <= with_request <= without * 1.2):
        status = "not honored"
    else:
        status = "unknown"
    def number(value):
        return f"{value:g}" if value is not None else "unavailable"

    return (f"{status} (completion tokens {number(without)} without, {number(with_request)} with; "
            f"reasoning {without_reasoning}/{len(samples[False])} without, {with_reasoning}/{len(samples[True])} with; "
            f"latency {number(median_value(False, 2))} ms without, {number(median_value(True, 2))} ms with)")


def run_checks(*, client=None, db_path: str | Path | None = None):
    """Probe output modes per model, thinking-off, and three streamed Tutor calls."""
    config = load_roles()
    model_client = ModelClient(config=config, client=client, db_path=db_path)
    models = {model: role for role, assigned in config.roles.items() for model in assigned}
    for model in config.roles["observer"]:
        models[model] = "observer"
    outcomes = {}
    print("Provider phase-1 checks")
    for model, role in models.items():
        outcomes[model] = {}
        for mode in ("json_schema", "json_object", "plain"):
            prompt = [{"role": "user", "content":
                       'Think through whether two pointers can solve Two Sum on an unsorted array, then return JSON: {"answer":"ok"}.'
                       if role == "observer" else 'Return JSON with answer set to ok.'}]
            try:
                model_client.call_role(role, prompt, ProbeReply, model=model, response_mode=mode)
                result = "supported"
            except ValueError as error:
                result = f"invalid reply ({_error_summary(error)})"
            except Exception as error:
                result = f"rejected ({_error_summary(error)})"
            outcomes[model][mode] = result
        print(f"{model}: " + ", ".join(f"{mode}={result}" for mode, result in outcomes[model].items()))
    for model in config.roles["observer"]:
        print(f"Observer thinking-off: {_thinking_probe(model_client, model)}")
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
