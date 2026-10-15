"""Manual provider smoke test: python -m companion.smoke."""

from companion.model import ModelClient, TutorReply


def main():
    reply = ModelClient().call_role(
        "tutor",
        [{"role": "system", "content": "You are the Leetotter Tutor. Return JSON matching the requested schema."},
         {"role": "user", "content": "Reply with the word ok. Use weak_intuition for gap, null for gap_note, question for help_kind, and null for follow_up_question."}],
        TutorReply,
    )
    print(reply.model_dump_json())


if __name__ == "__main__":
    main()
