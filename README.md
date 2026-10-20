<div align="center">

<img src="extension/icons/otter.svg" width="96" alt="Leetotter otter">

# Leetotter

**An AI tutor for the NeetCode 150 that remembers how you did.**

![License: MIT](https://img.shields.io/badge/license-MIT-blue)
![Chrome extension](https://img.shields.io/badge/Chrome-side%20panel-4285F4?logo=googlechrome&logoColor=white)
![Python 3.14](https://img.shields.io/badge/python-3.14-3776AB?logo=python&logoColor=white)
![macOS](https://img.shields.io/badge/macOS-only-lightgrey?logo=apple)

<img src="docs/images/session.png" width="360" alt="A hint in the Session tab, pointing back to a mistake made on an earlier problem">&nbsp;&nbsp;<img src="docs/images/progress.png" width="360" alt="The Progress tab: problems solved on your own and strength per category">

</div>

A chatbot in the sidebar helps with one problem, then forgets you. Leetotter sits beside NeetCode's editor in Chrome and keeps track of what you solved, where you slipped and what to do next.

| | Chatbot | Leetotter |
|---|---|---|
| Remembers your past mistakes | ✗ | ✓ |
| Picks your next problem | ✗ | ✓, spread across categories |
| Brings problems back before you forget them | ✗ | ✓, spaced repetition (FSRS) |
| Waits for you to ask before hinting | rarely | always |
| Shows what you can solve on your own | ✗ | ✓, per category |
| Sums up each session | ✗ | ✓, and takes your corrections |

## Features

- **Plans your practice.** Steer it in plain words: "something harder", "more graphs".
- **Practises like an interview.** Each problem has a time cap. Hints come only when you ask, and only as much as you need, often pointing back to a problem where you made the same mistake. After a solve it may ask about complexity or trade-offs.
- **Recaps every session.** A few bullets on what it noticed about how you think. Tell it if something's off and it corrects its picture of you.
- **Grades honestly.** Two graders judge each solve independently, so "done" means you really solved it on your own.
- **Starts from where you are.** One click imports your existing NeetCode progress.
- **Private by default.** It watches only during a session, never runs your code, and keeps your history in one file on your Mac.

## Get started

You need macOS, Chrome, Python 3.14 with [`uv`](https://docs.astral.sh/uv/), Node.js, and an API key for any OpenAI-compatible model provider. NeetCode must be set to Python.

```sh
git clone https://github.com/joshualumzy/leetotter && cd leetotter
security add-generic-password -a "$USER" -s NUS_SOCLAAS_API_KEY -w   # paste your API key
./scripts/install.sh --base-url 'https://your-provider.example/v1'
cd extension && npm install && npm run build
```

In Chrome, open `chrome://extensions`, turn on Developer mode, choose **Load unpacked** and pick `extension/dist`. Then open Leetotter from Chrome's side panel on any NeetCode page.

The full setup, testing and uninstall notes are in [docs/development.md](docs/development.md).

## License

MIT. See [LICENSE](LICENSE).
