# agent-approvals

AI agents ask a person for approval as cards in a Slack channel. The person presses a button, or replies in the card's thread with anything they want. That answer is typed straight back into the agent session that asked, whether that's Claude Code or Codex, open or closed. If the session has closed, it's resumed first.

```
agent ──approvals submit──▶ card in #approvals ──▶ you: ✅ / ❌ / reply in the thread
  ▲                                                        │
  └──── "[Approvals] AP-0013 ... approved it" ◀── listener ┘   (resumes the session if it closed)
```

- **One card per request, formatted by type.** There are 12 types: creative, public reply, budget, purchase, money, player message, price, experiment, store or web page, content, email in your name, and account action. Each card puts the real thing on it: the ad, the exact reply, a table of today beside proposed, the email word for word. It also says exactly what a yes covers.
- **One thread per request.** Your replies are feedback. The agent's questions and its next versions arrive in the same thread, and the first post always shows the newest version with working buttons.
- **Answers reach the exact session that asked.**
  - An open session gets the answer typed into its terminal pane.
  - A closed session is resumed through [AI Manager](#requirements) in a new [Herdr](https://herdr.dev) pane, then told.
  - A session that moved to another account is found by its content, and the newest copy gets the answer.
  - Every answer is checked in the session's transcript and marked 📨 in Slack.
- **Agents learn it from a skill.** The `approvals` skill teaches when to ask (only when a yes is required, and once), how to write a card someone can decide from at a glance, and what to do with each kind of answer.

## Requirements

- macOS, for the launchd service. The command itself is plain Python.
- [uv](https://docs.astral.sh/uv/).
- A Slack app, with:
  - Socket Mode on, and an app-level token with `connections:write`;
  - Interactivity on, which Socket Mode carries;
  - bot scopes `chat:write`, `channels:history` (`groups:history` for a private channel), `channels:read`, `reactions:write`, `files:write` and `users:read`;
  - the bot invited to the approvals channel.
- To deliver into agent sessions: [Herdr](https://herdr.dev), with agents running in Herdr panes. To resume closed sessions: AI Manager's `aim` command. Without them, answers wait in each agent's `approvals inbox`.

## Install

```bash
git clone git@github.com:aelaguiz/agent-approvals.git
cd agent-approvals
make install
```

The first run creates `~/.approvals/config.json` from [config.example.json](config.example.json) and stops. Fill it in, then run `make install` again. That run:
- installs the `approvals` command with `uv tool install`;
- copies the skill into `~/.agents/skills/approvals` (Codex) and `~/.claude/skills/approvals` (Claude Code);
- starts the `com.agent-approvals.listener` service;
- runs `approvals doctor`.

`make verify_install` checks all of it.

| Setting | What it is |
| --- | --- |
| `channel` | The approvals channel's id. |
| `approver` | The Slack user id whose button presses and replies count. |
| `approver_name` | What cards and agent messages call them. |
| `bot_token_file`, `bot_token_key` | The env-style file holding the bot token, and the variable name in it. |
| `app_token_file`, `app_token_key` | The same for the Socket Mode app token. |
| `timezone` | For deadlines shown on cards. |
| `default_herdr_session` | Where the listener opens panes when an agent's own Herdr session is gone. |
| `video_share_command` | Optional. A command that publishes a video and prints `URL: <link>`. Without it, videos go into the card's thread, where Slack plays them. |
| `test_approver` | For test runs only. A user whose `[test reply]` posts count, and only on TEST cards. |

Tokens stay in their own files; the config only points at them.

## Use

Agents use the skill. To try it by hand:

```bash
approvals submit card.json --agent "Pricing"   # posts the card, prints AP-0001 and a link
approvals ask AP-0001 "Mexico at MX$899 or MX$999?"
approvals revise AP-0001 card-v2.json          # next version, in the same thread
approvals done AP-0001 "Set both prices; read them back from the store"
approvals inbox --agent "Pricing"              # answers waiting, for agents that can't be reached directly
approvals record AP-0001 --approved --words "yes do it" --where "phone call"
approvals list --open
approvals doctor
```

A card is JSON. [skills/approvals/references/cards.md](skills/approvals/references/cards.md) describes every type, with an example of each.

## How it works

[docs/design.md](docs/design.md) covers the cards, the thread, how each answer is routed back to its session, and what happens when a session has closed or moved.

- **Records:** one JSON file per request in `~/.approvals/requests/`.
- **Log:** `~/.approvals/logs/listener.log`.
- **Restart the service:** `launchctl kickstart -k gui/$(id -u)/com.agent-approvals.listener`.

## Test

- `make test` runs the unit tests.
- [docs/testing.md](docs/testing.md) describes the live end-to-end test, with real agents and Slack, and its latest results.

## Uninstall

```bash
make uninstall
```

This removes the service, the skill and the command, and keeps your records and config.

## License

[MIT](LICENSE)
