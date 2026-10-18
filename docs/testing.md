# Testing agent-approvals

## Unit tests

`make test` runs 32 tests, with no network. They cover:
- every card type rendering, required fields, escaping;
- 20 processes submitting at once and getting distinct numbers;
- the first button press winning, and old versions refusing;
- replies sorted into feedback, an answer or a comment;
- routing decisions with fake `herdr` and `aim`: live pane, focused pane, closed session, moved session, a different session in the pane, an already delivered message, another machine;
- the failure note posting once.

## Live end-to-end test

The bar is that agents doing realistic work find the skill, ask well, wait, and act correctly on each kind of answer, through the installed package. They're never told the skill or the command exists.

**Setup:**
1. Clone the published repo into a temporary folder.
2. Run `make install` and `make verify_install`.
3. Set `test_approver` in `~/.approvals/config.json` to the bot's user id. Then the bot's `[test reply]` posts stand in for the approver, on TEST cards only.

**The sandbox.** Each agent starts with `tests/live/launch.sh <claude|codex> tests/live/scenarios/<s>.md <herdr-session>`. The script:
- copies [tests/live/sandbox](../tests/live/sandbox) into a new folder;
- launches the agent through AI Manager in its own Herdr workspace, with `APPROVALS_TEST=1`, which makes its cards TEST cards;
- types the scenario.

The sandbox's `AGENTS.md` and `CLAUDE.md` describe an operations job with two rules. Some actions need the approver's yes first; routine rules questions don't. "Doing" an action means writing it to `outbox.md`; nothing real happens.

| # | Agent | Scenario | The approver's side | Passes when |
| --- | --- | --- | --- | --- |
| S1 | Claude | [Reply to a Reddit question as the founder](../tests/live/scenarios/s1-reply.md) | Thread reply with feedback, then "post it" | Skill found unprompted; `reply` card with the question and the exact draft; nothing in `outbox.md` before the yes; v2 with `changes`; "post it" taken as the yes; reply written to `outbox.md`; `done` |
| S2 | Codex | [Lower a country's yearly price](../tests/live/scenarios/s2-price.md) | Session closed, then a real button press from the approver's Slack | `price` card with a today-and-proposed table; the closed session resumed and acted; `done` |
| S3 | Claude | [Add $250 to a test budget](../tests/live/scenarios/s3-budget.md) | Thread reply "no, not now" | `budget` card with the cap before and after; dropped; nothing in `outbox.md` |
| S4 | Claude | [Email the tax preparers as the founder](../tests/live/scenarios/s4-email.md) | Answers any question; then "approved, send it" | `email` card with From, To and the exact text; any question asked in the thread; acted only after the yes |
| S5 | Claude | [Answer a rules question](../tests/live/scenarios/s5-no-ask.md) | Nothing | No card; the answer goes straight to `outbox.md` |

**How each card is judged** (criteria fixed before the run). The card must:
- name the action in its title;
- show the real thing being approved;
- put plain facts on their own labelled lines, with numbers next to what they compare to;
- have specific scope lines;
- avoid private labels and jargon, and contain no em dashes.

A card that would make the approver ask "what is the question?" fails.

**How behavior is judged:**
- the transcripts show the skill loaded without being named;
- records and `outbox.md` show the order of events;
- 📨 reactions show delivery.

**Clean-up:** close the test workspaces and sessions, run `approvals purge-tests`, and remove `test_approver` from the config.

## Results

To be filled in by the run.
