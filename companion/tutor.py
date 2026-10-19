"""The Tutor: its context, its instructions, and one streamed, validated reply."""

import json
from dataclasses import dataclass
from typing import Literal

from openai import APITimeoutError
from pydantic import ValidationError

from companion.model import GAPS, HELP_KINDS, ModelUnavailable, ProviderRateLimitError, TutorReply
from companion.past_links import PastLink, asks_about_similar
from companion.reference import StoredReference

_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


class ReplyTextStream:
    """Pulls the top-level `reply_text` string out of a JSON reply as it streams in.

    `feed` takes the next raw piece and returns the newly decoded reply text (possibly ""). It waits
    for an escape split across pieces to complete, and finds the field wherever the model put it.
    What it yields is a preview: only the whole reply, validated at the end, is ever stored.
    """

    def __init__(self):
        self._buf = ""
        self._pos = 0
        self._state = "seek"  # seek, value (inside reply_text), done
        self._depth = 0
        self._expect_key = False
        self._key = None
        self._awaiting_value = False

    def feed(self, piece: str) -> str:
        self._buf += piece
        out = []
        try:
            while self._state == "seek" and self._step():
                pass
            if self._state == "value":
                self._value(out)
        except ValueError:
            # Malformed JSON: stop the preview; the final validation rejects the reply.
            self._state = "done"
        return "".join(out)

    def _step(self) -> bool:
        buf = self._buf
        while self._pos < len(buf) and buf[self._pos] in " \t\r\n":
            self._pos += 1
        if self._pos >= len(buf):
            return False
        c = buf[self._pos]
        if c in "{[":
            self._depth += 1
            self._expect_key = c == "{" and self._depth == 1
            self._pos += 1
        elif c in "}]":
            self._depth -= 1
            self._pos += 1
            if self._depth <= 0:
                self._state = "done"
        elif c == ",":
            self._expect_key = self._depth == 1
            self._pos += 1
        elif c == ":":
            self._awaiting_value = self._key == "reply_text" and self._depth == 1
            self._key = None
            self._pos += 1
        elif c == '"':
            if self._awaiting_value:
                self._awaiting_value = False
                self._state = "value"
                self._pos += 1
                return True
            end = self._pos + 1
            while end < len(buf) and buf[end] != '"':
                end += 2 if buf[end] == "\\" else 1
            if end >= len(buf):
                return False
            raw = buf[self._pos:end + 1]
            self._key = json.loads(raw) if self._expect_key else None
            self._expect_key = False
            self._pos = end + 1
        else:
            self._awaiting_value = False
            while self._pos < len(buf) and buf[self._pos] not in ',:}]" \t\r\n{[':
                self._pos += 1
            if self._pos == len(buf):
                return False
        return True

    def _value(self, out):
        buf = self._buf
        while self._pos < len(buf):
            c = buf[self._pos]
            if c == '"':
                self._state = "done"
                self._pos += 1
                return
            if c != "\\":
                out.append(c)
                self._pos += 1
                continue
            if self._pos + 1 >= len(buf):
                return
            escape = buf[self._pos + 1]
            if escape != "u":
                out.append(_ESCAPES.get(escape, escape))
                self._pos += 2
                continue
            if self._pos + 6 > len(buf):
                return
            code = int(buf[self._pos + 2:self._pos + 6], 16)
            if 0xD800 <= code < 0xDC00:
                if self._pos + 12 > len(buf):
                    return
                if buf[self._pos + 6:self._pos + 8] == "\\u":
                    low = int(buf[self._pos + 8:self._pos + 12], 16)
                    if 0xDC00 <= low < 0xE000:
                        out.append(chr(0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)))
                        self._pos += 12
                        continue
            out.append(chr(code))
            self._pos += 6


def current_code(events) -> tuple[str | None, bool]:
    """The learner's code from the latest `code_snapshot` and the `code_changed` diffs after it, oldest first.

    Returns (code, behind). A diff whose removed lines don't match is never guessed at: the code stays
    at the last state known to be right, `behind` is set, and later diffs wait for the next snapshot.
    """
    code, behind = None, False
    for kind, payload in events:
        if kind == "code_snapshot":
            code, behind = payload["code"], False
        elif kind == "code_changed" and not behind:
            diff = payload["diff"]
            lines = code.split("\n") if code is not None else None
            start = diff["start_line"] - 1
            end = start + len(diff["removed"])
            if lines is None or start > len(lines) or lines[start:end] != diff["removed"]:
                behind = True
            else:
                code = "\n".join(lines[:start] + diff["added"] + lines[end:])
    return code, behind



SYSTEM_PROMPT = """You are Leetotter's Tutor, a coding-interview tutor in a narrow side panel next to NeetCode's editor. The learner is solving one problem in Python, on their own, to get ready for interviews. Help them learn to solve it themselves, the way a strong human tutor would.

Every time, in this order:

1. Diagnose before you help. From their code, their run results and what they say, decide what is in the way:
- misread_problem: a missed constraint, or solving a different question. What helps: re-read it with an example.
- missing_tool: needs a data structure or algorithm they don't know (a heap, union-find, Dijkstra). What helps: teach the tool briefly, then hand the problem back.
- pattern_not_recognised: knows the tool but doesn't see that it applies here. What helps: point at the signal in the problem that calls for it, without naming the pattern, and let them connect it.
- weak_intuition: no idea where to start. What helps: build the idea up.
- wrong_intuition: committed to a path that can't work. What helps: steer off it, then build intuition for a new path.
- stuck_optimising: brute force works, the better idea won't come. What helps: "What work is repeated?"
- cant_write_code: the idea is right, the implementation won't come. What helps: structure and Python, not the idea.
- bugs_and_edge_cases: nearly right. What helps: let them find it themselves ("try the smallest input").
- other: a gap not on this list. Describe it in gap_note in a few words.
- none: they aren't stuck and you give no help (a remark, thanks, a plan they're only sharing).
Close calls: if they already use the tool correctly elsewhere, it's pattern_not_recognised, not missing_tool. If the code doesn't yet express a right idea, it's cant_write_code; if it does and fails on some inputs, bugs_and_edge_cases. No direction at all is weak_intuition; a direction that can't work is wrong_intuition.

2. Choose the next move. The help ladder, least to most concrete: a question, a trace or illustrative example, an analogy, a code snippet, partial logic, the full solution. Questions are the default for a chat message and for the first hint: start with a short reminder of what they already know and a question, then go further only when asked (a further press or a direct question); go direct only where questions can't help (a tool they've never seen, Python mechanics). A further press of "I need help" is the learner asking to climb: go one rung more concrete at once.

3. Follow their approach, even when it isn't one the reference lists. Brute force is never a wrong path. Going straight for the optimal solution is fine; only when that has left them with nothing working, suggest getting a brute force working first.

4. Match the help to how long and how hard they've struggled: early on, stay light; a long struggle with repeated failures earns something more concrete. Weigh the value of more struggle against the time left on the problem's cap: with little time left, be more direct.

5. Once a full solution has been shown, add only the explanation they still need. Don't restart the questioning.

6. After an accepted submission, chat is a discussion. If they solved it on their own, they may ask you to compare approaches or help improve theirs.

In a debrief (entered from the time-up card), work out from the code-built timeline what went wrong and build their intuition; you may walk through the solution. Use only the timeline and the reference you are given, and make no tool calls.

Rules:
- If they ask to steer practice while solving, say you will take it at the next suggestion.
- Never give a hint nobody asked for. If their message isn't asking for help, reply briefly with no hint and label it gap none, help_kind none. Checking a plan they ask you about is help: label it.
- If they ask outright for the answer, give the full solution only when lighter help has already been tried or they've clearly decided to stop; otherwise give the next rung.
- No grades and no judgement of the learner ("this one is easy", "you should know this"). Be warm and plain.
- The reference is for judging their approach, not for quoting. Never name a pattern, an approach or its complexity unless the learner has already named or used it themselves.
- Never state the time or space complexity of their approach or any other, unless they ask after solving it: their own complexity analysis is measured.
- Refer to their code concretely (a line, a variable name) so they can see you followed it. It may be mid-edit. You can't run it; the run results show what NeetCode returned. If it's only NeetCode's starter code (the empty function signature), they haven't started writing yet.
- If the problem statement isn't available, don't invent details: work from the title, the test cases in the run results and the reference, and ask them when a detail matters and you're unsure.
- Keep replies short for a narrow panel: usually one to four sentences, and any code only as long as it must be. You may use `inline code` and fenced code blocks (each line of code on its own line, indented as in Python) for code, short paragraphs separated by blank lines, and simple `- ` lists; no headings, tables, images or links.

Past links (the prompt lists them only when there are some):
- Use only the list. Never invent a past session or a fact about one.
- Unprompted, link at most one, and only when it strongly fits, as "Title (short description)". Most replies have no link.
- When the learner asks what is similar, give every close match in the list, each with its description and what they share.
- A link is help: a reply with a link is never gap none, and its help kind is at least analogy (partial_logic when it spells out the earlier idea so it applies here).
- The current problem's pattern is not named before the learner names it; a pattern from an earlier session may be named.
- After the solve, you may name the technique and draw the comparison freely.
- With no past problems listed, linked is [].

Your reply is JSON with these fields:
- gap: one of the gaps above.
- gap_note: a few words naming the gap when gap is other; otherwise null.
- help_kind: the most concrete help in this reply: question (a guiding question), trace (walking through an example or their code), analogy, teach_tool (explaining a data structure or algorithm they didn't know), syntax (Python mechanics such as how heapq is called), snippet (code that carries part of the idea), partial_logic (spelling out part of the algorithm's steps), full_solution (the whole approach or code), none (no help; only with gap none). Code that carries any of the algorithm's idea is snippet or partial_logic, never syntax. Label honestly: the label records how much help the attempt needed.
- reply_text: what the learner reads. It is a JSON string, so write every line break as \\n: a blank line between paragraphs is \\n\\n, and each line of a code block ends with \\n and keeps its indentation spaces. Never put code on one line.
- follow_up_question: the one question you ask after a solve, or null. It is null except in the turn right after an accepted solve.
- follow_up_complexity: true only when follow_up_question is a complexity question and one is due; otherwise false.
- postpone_reason: a short reason for postponing the due complexity question, or null. Never set it with a follow_up_question.
- has_reasoning: when answering a follow-up question, true only if the learner gave the reasoning behind their answer; null in every other turn.
- linked: the slugs from the past-links list you cite in this reply, or [] (at most one unless the learner asked what is similar).

Examples of good replies (invented problems; never reuse their wording):
- First press, 9 minutes into "Quietest Hour" (find the k-minute window with the least total noise), the learner re-adds every window from scratch and times out: gap stuck_optimising, help_kind question, reply_text "Each time you move to the next window, your inner loop adds up all k minutes again. Put two neighbouring windows side by side: which minutes actually differ?"
- Third press on "Bakery Orders" (bake orders by earliest deadline as they arrive), the learner re-sorts the whole list after every arrival and hasn't met heaps: gap missing_tool, help_kind teach_tool, reply_text "A heap keeps its smallest item on top: `heapq.heappush(h, (deadline, order))` adds one, and `heapq.heappop(h)` takes out the earliest deadline without sorting everything. When a new order arrives, what would you push, and when would you pop?"
- Second press on "Bakery Orders", the learner asks how to start the loop: gap cant_write_code, help_kind snippet, reply_text "Here is the shape of it:\\n\\n```python\\nfor order in orders:\\n    heapq.heappush(h, (order.deadline, order))\\n```\\n\\nWhat should happen when the oven frees up?"
- The learner writes "ok got it, thanks": gap none, help_kind none, reply_text "Go for it."
"""

# Bounds on what is sent, so a huge paste or a long session can't swamp the prompt; anything cut is said.
STATEMENT_LIMIT = 6000
CODE_LIMIT = 12000
CONVERSATION_LIMIT = 12000
RECENT_EDITS = 5
EDIT_LIMIT = 1500
RECENT_RESULTS = 4
CASE_FIELD_LIMIT = 600


@dataclass(frozen=True)
class TutorRequest:
    kind: Literal["help", "chat", "debrief", "solve"]
    press_index: int | None  # help: which answered press this is (1 = first); chat, debrief and solve: None
    text: str | None  # chat: the learner's message; help, debrief and solve: None


@dataclass(frozen=True)
class Turn:
    """One Tutor turn in flight: its id is the id of the request event that started it."""
    turn_id: int
    session_id: int
    slug: str
    request: TutorRequest
    messages: list
    schema: type
    answer_question: dict | None = None


def _schema(name, gaps, kinds):
    return type(name, (TutorReply,), {"__annotations__": {"gap": Literal[gaps], "help_kind": Literal[kinds]}})


_PRESSED = tuple(g for g in GAPS if g != "none")
HelpReply = _schema("HelpReply", _PRESSED, tuple(k for k in HELP_KINDS if k != "none"))
FirstHelpReply = _schema("FirstHelpReply", _PRESSED, tuple(k for k in HELP_KINDS if k not in ("none", "full_solution")))


def reply_schema(request: TutorRequest, *, allowed=(), asked: bool = False, answer: bool = False,
                 complexity_due: bool = False, followups_forbidden: bool = False) -> type[TutorReply]:
    """A press always gets help, and a first press never the full solution; the schema enforces both.

    A reply may cite only slugs from `allowed`, and only one unless the learner asked what is similar. The
    allowed slugs are empty when there is no past-links section, so any link is then invalid. After a solve,
    the follow-up fields are checked against whether a complexity question is due and whether the reference
    forbids them; an answer turn must say whether the learner gave reasoning.
    """
    base = TutorReply if request.kind in ("chat", "debrief", "solve") else (
        FirstHelpReply if request.press_index == 1 else HelpReply)
    attrs = {
        "_links_enforced": True,
        "_allowed_links": frozenset(allowed),
        "_asked_about_similar": asked,
    }
    if request.kind == "solve":
        attrs["_solve_turn"] = True
        attrs["_complexity_due"] = complexity_due
        attrs["_followups_forbidden"] = followups_forbidden
    if answer:
        attrs["_answer_turn"] = True
    return type(base.__name__, (base,), attrs)


def answered_presses(events) -> int:
    """Presses of "I need help" on this problem that got a reply; a failed press doesn't count."""
    return sum(1 for kind, payload in events if kind == "help_turn" and payload["request"] == "help")


def _clip(text, limit):
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n[... {len(text) - 2 * half} characters trimmed ...]\n{text[-half:]}"


def _minutes(seconds):
    return f"{max(0, round(seconds / 60))} min"


def _past_links(links: list[PastLink]):
    if not links:
        return ""
    lines = ["## Past problems you may link to (built by code from this learner's history)",
             "Only these are real. Use only what each line says; never add a past session."]
    for link in links:
        description = link.summary if link.summary is not None else "no summary yet; use the title only"
        shared = []
        if link.shared_patterns:
            shared.append("the pattern " + ", ".join(link.shared_patterns))
        if link.shared_category:
            shared.append("this problem's category")
        facts = [f"- `{link.slug}`: {link.title} ({description}). What happened: {link.outcome}."]
        if link.approach:
            facts.append(f"Approach used: {link.approach}.")
        if link.idea:
            facts.append(f"Idea discussed: {link.idea}.")
        if shared:
            facts.append("Shares: " + " and ".join(shared) + ".")
        lines.append(" ".join(facts))
    return "\n".join(lines)


def _reference(stored: StoredReference | None):
    if stored is None:
        return ("## Reference\nThere's no reference for this problem yet. Work from the problem itself, "
                "and follow the learner's approach.")
    ref = stored.reference
    lines = ["## Reference (for judging their approach, not for quoting)", f"Difficulty {ref.difficulty}.", "Known approaches:"]
    for a in ref.approaches:
        trick = "; a one-off trick" if a.trick else ""
        lines.append(f"- {a.id}: {a.name}. {a.idea}. Time {a.time}, space {a.space}; interviewers expect it {a.interview_expected}{trick}.")
    if stored.uncertain:
        lines.append(f"Status: {stored.status}: these labels are uncertain, so don't lean on them: {', '.join(stored.uncertain)}.")
    else:
        lines.append(f"Status: {stored.status}.")
    lines.append("Follow the learner's approach even when it isn't one listed here.")
    return "\n".join(lines)


def _case(case):
    if not case:
        return ""
    return " Failing case: " + "; ".join(
        f"{label} {_clip(case[key], CASE_FIELD_LIMIT)}" for label, key in (("input", "input"), ("expected", "expected"), ("got", "actual")))


def _error(payload):
    if not payload.get("error_type") and not payload.get("error_message"):
        return ""
    message = f": {_clip(payload['error_message'], CASE_FIELD_LIMIT)}" if payload.get("error_message") else ""
    return f" Error: {payload.get('error_type') or 'error'}{message}."


def _result(kind, payload):
    if kind == "submit_result":
        if payload["accepted"]:
            return f"- Submit: accepted ({payload['passed']} of {payload['total']} cases)."
        return (f"- Submit: not accepted, {payload['passed']} of {payload['total']} cases passed."
                + _error(payload) + _case(payload.get("failing_case")))
    cases = payload["cases"]
    passed = sum(1 for c in cases if c["passed"])
    failed = next((c for c in cases if not c["passed"]), None)
    line = f"- Run: {passed} of {len(cases)} test cases passed."
    if failed:
        line += _error(failed) + _case(failed.get("failing_case"))
    return line


def _conversation(events):
    entries = []
    for kind, payload in events:
        if kind == "chat_message":
            entries.append(f"Learner: {payload['text']}")
        elif kind == "help_request":
            entries.append('Learner pressed "I need help".')
        elif kind == "help_turn":
            gap = f"other: {payload['gap_note']}" if payload["gap"] == "other" else payload["gap"]
            entries.append(f"You (gap {gap}; help {payload['help_kind']}): {payload['reply_text']}")
        elif kind == "help_failed":
            entries.append("(Your reply failed and was not shown.)")
        elif kind == "followup_asked":
            entries.append(f"You asked a follow-up question: {payload['question']}")
        elif kind == "reasoning_requested":
            entries.append("You asked for the reasoning behind the answer.")
        elif kind == "followup_skipped":
            entries.append("The follow-up question was skipped.")
    if not entries:
        return "## Conversation on this problem\nNothing yet."
    kept, size = [], 0
    for entry in reversed(entries):
        size += len(entry)
        if kept and size > CONVERSATION_LIMIT:
            break
        kept.append(_clip(entry, CONVERSATION_LIMIT))
    kept.reverse()
    dropped = len(entries) - len(kept)
    head = [f"({dropped} earlier messages were left out for length.)"] if dropped else []
    return "\n".join(["## Conversation on this problem (oldest first)", *head, *kept])


def _request(request: TutorRequest, events, *, has_links: bool = False):
    helped = [p for kind, p in events if kind == "help_turn" and p["help_kind"] != "none"]
    if any(p["help_kind"] == "full_solution" for p in helped):
        concrete = "A full solution has already been shown: add only the explanation they still need."
    elif helped:
        concrete = (f"The kinds of help you've given on this problem so far: {', '.join(dict.fromkeys(p['help_kind'] for p in helped))}; "
                    f"the last was {helped[-1]['help_kind']}.")
    else:
        concrete = "You haven't helped on this problem yet."
    link_rule = ""
    if has_links:
        if request.kind == "chat" and asks_about_similar(request.text):
            link_rule = (" They are asking what is similar: give every close match from the past-problems list, each with its "
                         "description and what they share. ")
        else:
            link_rule = (" If a past problem strongly fits, link at most one, as \"Title (short description)\"; most replies have "
                         "no link. ")
    if request.kind == "chat":
        return (f'## Now\nThe learner wrote:\n"""\n{request.text}\n"""\n{concrete}{link_rule} Answer it the same way as a help request: '
                "diagnose, then help only as much as they need; if they're not asking for help, give none. If they ask for the "
                "solution, give it only if lighter help has already been tried or they've clearly decided to stop; otherwise give the next rung.")
    if request.press_index == 1:
        return ('## Now\nThe learner pressed "I need help" for the first time on this problem. ' + concrete + link_rule +
                " Give the smallest hint beyond what you've already given: just enough to get them unstuck, never the solution. "
                "Label it with a real gap and a real help kind (never gap none, never help_kind none, never full_solution).")
    return (f'## Now\nThe learner pressed "I need help" again: press {request.press_index} on this problem. {concrete}{link_rule} '
            "Go exactly one rung more concrete than your last help, at once; give the full solution only when the ladder has "
            "genuinely reached it. Don't ask a diagnostic question before helping; you may end with one only if it moves them forward. "
            "A press always gets real help, so never label it gap none or help_kind none.")


def _solve_request(events, *, solve: dict, reference: StoredReference | None, accepted_code: str | None,
                   problem_seconds: int):
    help_turns = sum(1 for kind, _ in events if kind == "help_turn")
    difficulty = solve.get("difficulty") or "unknown difficulty"
    lines = ["## After the accept",
             "The learner's submission was accepted. Discuss it the way an interviewer would after a solve; "
             "help now never changes the solve grade."]
    if accepted_code and accepted_code.strip():
        lines += ["## Accepted code", f"```python\n{_clip(accepted_code, CODE_LIMIT)}\n```"]
    else:
        lines += ["## Accepted code", "No code was captured at the accept."]
    lines.append(f"Load so far: {_minutes(problem_seconds)} on the problem, Tutor help turns: {help_turns}, "
                 f"{difficulty} problem.")
    if solve.get("followups_forbidden"):
        lines.append("The reference needs review, so the faster line and every follow-up are forbidden: leave "
                     "follow_up_question null, follow_up_complexity false and postpone_reason null. Do not offer "
                     "the faster line.")
    else:
        if reference is not None:
            lines.append("If the accepted approach is below the best approach the reference marks usually, say so "
                         "in one line the way an interviewer would, such as \"That works. Can you make it faster?\" "
                         "Never reveal or name the better approach.")
        else:
            lines.append("There is no checked reference to compare the approach against, so do not say the faster line.")
        if solve.get("complexity_due"):
            lines.append("A complexity question is due. Either ask one complexity question in follow_up_question "
                         "and set follow_up_complexity true, or set postpone_reason to a short reason and leave "
                         "follow_up_question null. Never both.")
        else:
            lines.append("No complexity question is due: follow_up_complexity must be false and postpone_reason "
                         "null. You may still ask one non-complexity discussion question with "
                         "follow_up_complexity false.")
        lines.append("A follow_up_question is shown to the learner after reply_text.")
    return "\n".join(lines)


def _answer_request(request: TutorRequest, answer: dict):
    lines = ["## Now",
             "The learner is answering the follow-up question you asked after the solve:",
             f'"""\n{answer["question"]}\n"""',
             f'Their answer:\n"""\n{request.text}\n"""',
             "Judge whether they gave the reasoning behind the answer and set has_reasoning accordingly."]
    if answer.get("reasoning_requested"):
        lines.append("You already asked for the reasoning once. If it is still missing, reply briefly and do "
                     "not ask again.")
    else:
        lines.append("If it is missing, ask for the reasoning behind their answer, without stating it yourself.")
    lines.append("Do not ask a new follow-up question: follow_up_question null, follow_up_complexity false, "
                 "postpone_reason null.")
    return "\n".join(lines)


def _debrief_request(request: TutorRequest, timeline: list[dict]):
    rendered = json.dumps(timeline, ensure_ascii=False, separators=(",", ":"))
    if request.kind == "chat":
        now = (f'## Now\nThe learner wrote during the debrief:\n"""\n{request.text}\n"""\n'
               "Answer in the debrief: work from the timeline below, and give only the help they ask for.")
    elif request.kind == "help":
        now = ('## Now\nThe learner pressed "I need help" during the debrief. Use the timeline below to help with what '
               "actually blocked them, and label the help you give.")
    else:
        now = ("## Now\nThe problem's time cap has run out and the learner chose the debrief. Work out from the timeline "
               "what went wrong, build their intuition, and walk through the solution if that helps. Typing the code in "
               "afterwards is optional.")
    return "\n\n".join([
        "## Debrief timeline (built by code, oldest first; no tool calls)",
        rendered,
        now,
        "Use only this timeline and the reference. Make no tool calls."])


def build_messages(problem: dict, events: list, *, reference: StoredReference | None, prior_solve: bool,
                   problem_seconds: int, seconds_left: int, request: TutorRequest,
                   timeline: list[dict] | None = None, past_links: list[PastLink] | None = None,
                   solve: dict | None = None, answer: dict | None = None) -> list[dict]:
    """The Tutor's prompt, from the current problem and this session's events about it, oldest first.

    `events` are (type, payload) pairs; the code events are those since the problem was last opened. Only this
    session is used and never the learner's name, so nothing from outside the session is sent (beyond whether
    NeetCode shows the problem solved). New context (Reflector hypotheses, framings that worked)
    is a new argument and one more section here. In the debrief, `timeline` replaces the ordinary code, result
    and conversation sections: it is the compact timeline built by code, and the Tutor makes no tool calls.
    """
    statements = [p["statement"] for kind, p in events if kind == "problem_statement" and p["slug"] == problem["slug"]]
    statement = (f"## Statement\n{_clip(statements[-1], STATEMENT_LIMIT)}" if statements else
                 "## Statement\nThe problem statement isn't available. Rely on the title, the test cases in the run results "
                 "and the reference.")
    if timeline is not None or request.kind == "debrief":
        body = _debrief_request(request, timeline or [])
    else:
        code, behind = current_code([(k, p) for k, p in events if k in ("code_snapshot", "code_changed")])
        if code is None or not code.strip():
            code_section = "## Their code\nThe learner hasn't written any code yet (none seen). Help from the problem and what they say."
        else:
            code_section = f"## Their code\n```python\n{_clip(code, CODE_LIMIT)}\n```"
            if behind:
                code_section += "\n(The code may be slightly behind their latest edits.)"
        edits = [p["diff"] for kind, p in events if kind == "code_changed"][-RECENT_EDITS:]
        if edits:
            rendered = []
            for d in edits:
                change = "\n".join([*(f"- {line}" for line in d["removed"]), *(f"+ {line}" for line in d["added"])])
                rendered.append(f"At line {d['start_line']}:\n{_clip(change, EDIT_LIMIT)}")
            code_section += "\n## Recent edits (oldest first)\n" + "\n".join(rendered)
        results = [(k, p) for k, p in events if k in ("run_result", "submit_result") and p["slug"] == problem["slug"]][-RECENT_RESULTS:]
        results_section = ("## Recent runs and submits (oldest first)\n" + "\n".join(_result(k, p) for k, p in results)
                           if results else "## Runs and submits\nThe learner hasn't run or submitted this problem yet.")
        if request.kind == "solve":
            now_section = _solve_request(events, solve=solve or {}, reference=reference, accepted_code=code,
                                         problem_seconds=problem_seconds)
        elif answer is not None:
            now_section = _answer_request(request, answer)
        else:
            now_section = _request(request, events, has_links=bool(past_links))
        body = "\n\n".join([code_section, results_section, _conversation(events), now_section])
    if seconds_left > 0:
        time_left = f"{_minutes(seconds_left)} left on the problem's cap"
    elif seconds_left == 0:
        time_left = "the problem's cap has run out"
    else:
        time_left = f"the problem's cap ran out {_minutes(-seconds_left)} ago"
    learner = [f"## The learner\nActive time on this problem: {_minutes(problem_seconds)}; {time_left}."]
    if prior_solve:
        learner.append("They solved this problem before on NeetCode, before using Leetotter; how much help they had then is unknown.")
    sections = [f"# Problem: {problem['title']} ({problem['slug']})", statement, _reference(reference)]
    links_section = _past_links(past_links or [])
    if links_section:
        sections.append(links_section)
    sections += ["\n".join(learner), body]
    user = "\n\n".join(sections)
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def build_debrief_messages(problem: dict, timeline: list[dict], *, reference: StoredReference | None,
                           prior_solve: bool, problem_seconds: int, seconds_left: int,
                           request: TutorRequest, past_links: list[PastLink] | None = None) -> list[dict]:
    """The debrief prompt: the same problem context, with the code-built timeline as the Tutor's only input."""
    return build_messages(problem, [], reference=reference, prior_solve=prior_solve,
                          problem_seconds=problem_seconds, seconds_left=seconds_left,
                          request=request, timeline=timeline, past_links=past_links)


def ask_tutor(models, messages: list[dict], schema: type[TutorReply], on_text) -> TutorReply:
    """One blocking Tutor call. `on_text(attempt, text)` gets reply text as it streams; a retry after an
    invalid reply streams again as attempt 1, replacing attempt 0. Only the returned reply is validated."""
    streams = {}

    def on_delta(attempt, piece):
        text = streams.setdefault(attempt, ReplyTextStream()).feed(piece)
        if text:
            on_text(attempt, text)

    # json_object, not json_schema: the provider's strict schema mode strips line breaks from strings,
    # which flattens code blocks. The reply is still validated against the schema afterwards.
    return models.call_role("tutor", messages, schema, on_delta=on_delta, response_mode="json_object")


def failure(error: BaseException) -> tuple[str, str]:
    """A failed Tutor call as (reason for the log, message for the panel)."""
    if isinstance(error, ModelUnavailable):
        return "model_unavailable", str(error)
    if isinstance(error, ProviderRateLimitError):
        return "rate_limited", "The model provider is rate-limiting requests. Wait a minute and ask again."
    if isinstance(error, (APITimeoutError, TimeoutError)):
        return "timeout", "The Tutor didn't answer in time. Ask again."
    if isinstance(error, ValueError) and isinstance(error.__cause__, ValidationError):
        return "invalid_output", "The Tutor's reply failed its checks twice, so it isn't shown. Ask again."
    return "error", f"The Tutor's reply failed: {error}"
