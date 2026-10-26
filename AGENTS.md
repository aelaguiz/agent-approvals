# AGENTS.md

agent-approvals lets AI agents ask Amir for approval as Slack cards in
#approvals and routes his answers back into the agent session that asked.
It serves two Slack workspaces: Fun Country (requests AP-0001 on) and
Cratejoy (CJ-0001 on), one service each, picked by the agent's folder.
It's live: the services run on Amir's Mac, real agents submit real requests,
and he answers them from Slack. A bug here means an agent never hears his
answer, or acts on one he didn't give. [README.md](README.md) covers install
and use.

## Commands

```bash
make test                                   # unit tests, no network, under a second
uv run pytest -q tests/test_router_listener.py -k <name>   # one test
make install                                # reinstall the command, copy the skill, restart both services
make verify_install                         # check the skill copies, command, services and Slack access
launchctl kickstart -k gui/$(id -u)/com.agent-approvals.listener            # restart Fun Country's service only
launchctl kickstart -k gui/$(id -u)/com.agent-approvals.listener.cratejoy   # restart Cratejoy's service only
```

- Python runs through uv (`uv run ...`), Python 3.12+.
- The running service and the installed skill are copies. A change to `src/`
  or `skills/` does nothing live until `make install`.
- Fun Country's log is `~/.approvals/logs/listener.log`, and each request's
  record is `~/.approvals/requests/<id>.json`. Cratejoy's are the same paths
  under `~/.approvals/cratejoy/`, whose `config.json` also lists the folders
  Cratejoy's agents work in.
- The live end-to-end test, with real agents and Slack, is in
  [docs/testing.md](docs/testing.md).

## Done means

- `make test` passes. Add or change a unit test only for behavior that could
  really break; tests use fakes for Herdr, AI Manager and Slack.
- A change to delivery, routing or the listener is seen working on the live
  service after `make install`: the log shows the answer delivered and the
  agent's acknowledgement shows up in the thread. Unit tests alone don't prove
  the live path.
- [README.md](README.md), [docs/design.md](docs/design.md) and
  [docs/testing.md](docs/testing.md) still describe what the code does.
- The change is committed and pushed to `main`. A task that delivers a change
  here already authorizes that commit and push.

## How to build here

This is an internal tool for one person and his agents. Build the simplest
thing that does the job, and weigh every guard by what it costs when it
fires against how likely the thing it guards against really is. Two
examples of getting that wrong: the service once held answers for up to 10
minutes whenever the agent's pane was "focused", in case Amir was typing
there, though someone answering in Slack isn't typing in a terminal; and an
early plan was built to a security standard a private tool doesn't need
("don't make it NSA grade"). When you feel the pull to add a wait, a retry
layer, a fallback path, a flag or a mode, ask what real failure it prevents.

- Make the smallest change that solves the ask. No speculative options,
  wrappers or compatibility layers. Fail loud when something required is
  missing.
- Match the surrounding code: its naming, comment density and idiom.
  Comments explain non-obvious intent, never syntax.
- [skills/approvals/](skills/approvals/) is what agents read to use this.
  Edit it with `$skill-authoring` and `$prompt-authoring`: teach agents why,
  with a few varied examples, rather than adding if-then rules. Keep
  `agents/openai.yaml` in line with `SKILL.md`, and never write tests that
  check the skill's wording.

## Red lines

- **This repo is public.** Never commit company finances, staff names,
  session ids, Slack ids, token file paths or tokens. Internal research and
  plans go in `private/`, which git ignores. Read the diff before pushing.
- **Never print a token.** The config only points at token files.
- **#approvals holds real requests.** Never purge, edit or answer one you
  didn't make. Clean up your own test cards with
  `approvals purge-tests --only <ids>`; without `--only` it deletes every
  TEST card, real agents' included.
- **The service runs on one machine.** Don't install or start it anywhere
  else.
- **Git:** stage exact paths. Ask before a force push, a history rewrite or
  discarding work. Leave changes you didn't make alone.
- **Shared machine:** close the Herdr panes, agent sessions and temporary
  clones you start for live tests.

## Replies

Write in plain English, answer first, in Amir's terms for his product: "your
reply reached the agent 5 seconds after the restart", not "outbox item
23923f delivered". No em dashes. Say what you didn't verify plainly: "Only
AGENTS.md changed, so I didn't run tests."
