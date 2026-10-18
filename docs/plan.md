# Plan: package agent-approvals, rewrite its skill, and test it for real

As of October 18, 2026.

**The tool works. Three things are not done yet:**
- **Packaging:** it's installed by hand.
- **The skill:** written without the skill and prompt authoring standards.
- **Testing:** the end-to-end tests told agents exactly what to do.

**This plan:**
- packages it the way [arch_skill](https://github.com/aelaguiz/arch_skill) is packaged;
- rewrites the skill with `$skill-authoring` and `$prompt-authoring`;
- tests it with agents that are never told the skill exists;
- publishes it to [aelaguiz/agent-approvals](https://github.com/aelaguiz/agent-approvals).

## 1. Repository layout

Modeled on arch_skill: skills live in `skills/<name>/`, a Makefile installs them, the README carries install and use, MIT license.

```
README.md                      what it is, how it works, install, configure, use, test, uninstall
LICENSE                        MIT
Makefile                       install, verify_install, uninstall, test, test-live
pyproject.toml, uv.lock        the Python package (CLI and service)
config.example.json            every setting, with placeholders
src/approvals/                 the code
skills/approvals/SKILL.md      what agents read
skills/approvals/references/   card examples per type, read when writing a card
skills/approvals/agents/openai.yaml   Codex display metadata
launchd/listener.plist.in      service template; the installer fills in paths
docs/design.md                 how it works: cards, threads, routing, AI Manager and Herdr
docs/testing.md                the test plan and the latest results
docs/plan.md                   this plan
tests/                         unit tests
tests/live/                    end-to-end scenarios and the sandbox the test agents work in
private/                       internal research and planning notes; git-ignored, never published
```

**What must not be published.** The research catalog, the earlier plans and the Slack history notes quote company finances, payroll, staff names and session ids. They move to `private/`, which is git-ignored. The code must not carry Amir's Slack ids or secret file paths either: those move into `~/.approvals/config.json`, which the installer creates from `config.example.json`. Test samples use made-up content. Before the first push, a scan checks the whole tree for Slack tokens (`xoxb-`, `xapp-`), secret file names and personal ids.

## 2. Configuration

Everything machine- or workspace-specific moves into `~/.approvals/config.json`:
- channel, approver and bot user ids
- the files and variable names that hold the bot token and the Socket Mode token
- timezone
- the Herdr session where new panes open
- an optional video share command
- an optional test approver

The code keeps no defaults that point at Amir's workspace.

**Video without cf-share.** If no video share command is set, the video is uploaded into the card's thread, where Slack plays it, and the card links there. cf-share stays the setting on this Mac.

**`approvals doctor`** checks everything the service needs and prints one line per check: config present and complete, both tokens readable, Slack accepts the bot token, a Socket Mode connection opens, the bot is in the channel, and `herdr`, `aim` and `uv` are on PATH. The installer runs it, and it's the first thing to run when something seems off.

## 3. Installer

| Target | What it does |
| --- | --- |
| `make install` | Installs the `approvals` command with `uv tool install`, so it doesn't depend on where the repo sits. Copies `skills/approvals` into `~/.agents/skills/` (Codex) and `~/.claude/skills/` (Claude Code), replacing old copies or links. Creates `~/.approvals/config.json` from the example if it's missing and stops there with what to fill in. Otherwise renders the launchd file with real paths, restarts the service, and runs `approvals doctor`. |
| `make verify_install` | Checks that the skill is in both folders and matches the repo, the command is on PATH, the service is running, and `doctor` passes. |
| `make uninstall` | Stops and removes the service, removes both skill copies and the command. Leaves `~/.approvals` (records and config). |
| `make test` | Unit tests. |
| `make test-live` | The end-to-end scenarios in section 5. |

## 4. Rewriting the skill with `$skill-authoring` and `$prompt-authoring`

**The job and the leverage.** Agents doing operational work hit moments where Amir must say yes first: posting as him, spending, changing prices, messaging players, emailing as him. Without the skill they stop and ask in chat, or bury the ask in a report he never answers. With it they put one card in front of him that he can decide from at a glance, keep working, and act on his answer when it arrives in their session.

**Canonical asks it must handle:**
- "Reply to this Reddit thread as Amir."
- "Raise the ad test budget by $250."
- "Email our tax preparers from Amir's account."

**And one it must reject:** "Merge this PR." That's a coding approval, which isn't this skill.

**Nearest lookalikes:**
- `slack-post-engineering`, which announces finished work in Slack;
- sending email with the Gmail skills.

The discriminator is a decision. This skill is for getting Amir's yes before acting. Posting or sending once he has said yes belongs to the agent's own tools.

**Shape:**
- **`SKILL.md` carries what every invocation needs, highest consequence first:**
  - ask only when his yes is actually needed, and once;
  - never act until a button press or his words naming the request;
  - the card carries the real thing;
  - after submitting, don't wait around;
  - what to do with each kind of answer.
- **`references/cards.md` holds what's needed only while writing a card:** for each of the 12 types, what Amir judges it by, its required fields, and one worked example from the approved mocks. Writing a card without reading it is allowed but discouraged.
- **`agents/openai.yaml`** keeps implicit invocation on, because agents have to find the skill on their own.
- The description stays under 1,024 characters. It names the trigger in agents' own words ("needs Amir's approval", "before posting as Amir", "spend") and the nearest lookalike.

**Checks before testing:**
- frontmatter valid, description length, body lines and tokens measured;
- every reference linked with a reason to read it;
- a cold read of the whole entry file through the real tool surface, with nothing truncated.

## 5. The real end-to-end test

The bar: agents doing realistic work, never told the skill or the command exists, find the skill, ask well, wait, and act correctly on each kind of answer, through the installed package.

**Install from scratch first.**
1. Clone [aelaguiz/agent-approvals](https://github.com/aelaguiz/agent-approvals) into a temporary folder.
2. Run `make install` against the existing config, then `make verify_install`.
3. Every scenario runs against that install, not the working copy.

**Each test agent gets a sandbox, not real power.**
- It works in `tests/live/sandbox/`. That folder has an `AGENTS.md` and a `CLAUDE.md` describing a realistic Poker Skill operations role, with one standing rule: posting as Amir, spending, price changes and email in his name need his approval first.
- Doing the action means appending it to `outbox.md` in the sandbox, which the instructions call "how this sandbox posts, spends and sends". Nothing real happens.
- The instructions never mention the approvals skill, the command, Slack or #approvals.
- Agents are launched through AI Manager (`aim claude run sonnet`, `aim codex run`) in their own Herdr workspace, in bypass mode, with `APPROVALS_TEST=1` set. That makes their cards TEST cards, the only cards where the bot's `[test reply]` posts count as Amir's.

**Scenarios.** Each starts with one plain task message in the agent's pane:

| # | Agent | Task given | What Amir's side does | Pass when |
| --- | --- | --- | --- | --- |
| S1 | Claude | Reply to a quoted Reddit question about learning poker, as Amir | Thread reply with feedback; then a plain "post it" | Finds the skill; a `reply` card with the question and the exact draft; nothing in `outbox.md` before the yes; v2 after feedback with `changes`; treats "post it" as the yes; writes the reply to `outbox.md`; runs `done` |
| S2 | Codex | Lower yearly Plus in Brazil from R$399.90 to R$219.90 | Its session is closed, then a button press | Finds the skill; a `price` card with a today-and-proposed table; the closed session is resumed and acts; `done` |
| S3 | Claude | Add $250 to the ad test's learning budget | Thread reply "no, not now" | A `budget` card with the cap before and after; drops it; nothing in `outbox.md` |
| S4 | Claude | Email the tax preparers from Amir's account asking for last year's full record | One question back from the agent, answered; then approve | An `email` card with From, To and the exact text; any question goes in the thread; acts only after the yes |
| S5 | Claude | Answer a routine rules question in the community thread, which the sandbox says needs no approval | Nothing | No card at all; the answer goes straight to `outbox.md` |

**How each card is judged.** The criteria are written here, before the run, so the run can't shape them. The card must:
- name the action in its title;
- show the real thing being approved;
- put plain facts on their own labelled lines, with numbers next to what they compare to;
- have specific scope lines;
- avoid private labels and jargon, and contain no em dashes.

A card that technically posts but would make Amir ask "what is the question?" fails.

**How the rest is judged:**
- The agent transcripts must show the skill loaded without being named.
- Records and `outbox.md` show the order of what happened.
- The 📨 reactions show delivery.

**If something fails,** the fix goes into the skill or the code, the package is reinstalled, and that scenario runs again from scratch. The results go in `docs/testing.md`.

**One real check from Amir's own account:** S2's button press goes through Amir's Slack in the BrowserOS Work window, as in the earlier round. The bot can't press buttons.

**Clean-up:** close the test workspaces and sessions the run opened, delete the TEST cards with `approvals purge-tests`, and remove the test approver from the config.

## 6. Publishing

1. Scan the tree for secrets and personal ids. Confirm `private/` is ignored.
2. Commit and push to `main` on [aelaguiz/agent-approvals](https://github.com/aelaguiz/agent-approvals) (public). The remote is already set.
3. The fresh-clone install in section 5 runs from what was pushed, so the published package is the one tested.

## Order

1. Layout, configuration, `doctor`, Makefile, README, license, sanitized samples. Unit tests pass.
2. Rewrite the skill. Run the checks in section 4.
3. Commit and push after the secret scan.
4. Fresh clone, `make install`, `make verify_install`.
5. Scenarios S1 to S5; fix and rerun until all pass.
6. Record the results in `docs/testing.md`, push, clean up.
