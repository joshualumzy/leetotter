"""Manual provider smoke test: python -m companion.smoke."""

from companion.model import call_tutor


def main():
    reply = call_tutor(
        "Reply with the word ok in the requested structure. "
        "Use weak_intuition for gap, question for help_kind, and null for follow_up_question."
    )
    print(reply.model_dump_json())


if __name__ == "__main__":
    main()
