"""Group a session's events into problem attempts.

The same rule everywhere: a `problem_opened` for another problem before the previous
attempt's `problem_left` is held pending, so the previous attempt still owns its left
event (and its outcome) rather than being closed at the open.
"""


def _opened(event):
    id_, session, ts, _, payload = event
    return {"session": session, "slug": payload["slug"], "title": payload.get("title"),
            "start": id_, "opened": ts, "events": [event], "left": None}


def attempts(events):
    """Every attempt in event order, including one still open. Events are (id, session, ts, kind, payload)."""
    active = {}
    pending_open = {}
    found = []
    for event in events:
        id_, session, ts, kind, payload = event
        if kind == "problem_opened":
            previous = active.get(session)
            if previous is not None and previous["slug"] != payload["slug"]:
                pending_open[session] = _opened(event)
            elif previous is None:
                active[session] = _opened(event)
            else:
                previous["events"].append(event)
                previous["title"] = payload.get("title") or previous["title"]
        elif kind == "problem_left":
            attempt = active.get(session)
            if attempt is not None and attempt["slug"] == payload["slug"]:
                active.pop(session)
                attempt["events"].append(event)
                attempt["left"] = event
                found.append(attempt)
                if session in pending_open:
                    active[session] = pending_open.pop(session)
            elif session in pending_open and pending_open[session]["slug"] == payload["slug"]:
                attempt = pending_open.pop(session)
                attempt["events"].append(event)
                attempt["left"] = event
                found.append(attempt)
        elif session in active:
            attempt = active[session]
            pending = pending_open.get(session)
            event_slug = payload.get("slug")
            if event_slug is None or event_slug == attempt["slug"]:
                attempt["events"].append(event)
            elif pending is not None and event_slug == pending["slug"]:
                pending["events"].append(event)
    found.extend(active.values())
    found.extend(pending_open.values())
    found.sort(key=lambda attempt: attempt["start"])
    return found
