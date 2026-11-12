# agent-approvals

AI agents ask a person for approval as cards in a Slack channel. The person presses a button, or replies in the card's thread with anything they want. That answer is typed straight back into the agent session that asked, whether that's Claude Code or Codex. If that session isn't running, nothing starts it: the answer waits until the agent is back and checks for it.

```
agent ──approvals submit──▶ card in #approvals ──▶ you: ✅ / ❌ / reply in the thread
  ▲                                                        │
  └──── "[Approvals] AP-0013 ... approved it" ◀── listener ┘   (waits if the session isn't running)
```

- **One card per request, shaped to how you'll judge it.** Its type (creative, public reply, budget, purchase, money, player message, price, experiment, store or web page, content, email in your name, account action) says what the action is. Its shape says how you answer: a yes or no; today's screen beside the after, swipeable, with the change outlined; a set of related changes with a yes, a change or a no on each row and Approve all; a pick between options shown side by side; a batch with Pick which; words in your name you can edit before they go. Every card says what it's part of, what a yes covers, and what happens with no answer.
- **The card keeps going after the yes.** The agent posts each step on it (built, waiting at Apple, live, the first read) until the thing is really out, and a condition only you can clear shows as waiting on you, on the card and in the morning list. These redraw the card in place, without a notification.
- **The Basecamp card shows it too.** A card can name the Basecamp cards or to-dos its work lives on (`--basecamp <url>`). "Part of:" on the card links to the first, and each Basecamp card keeps one step, written as the agent that asked, saying where the approval stands: waiting on you, your answer and when, "not yet seen by" the agent until it has it, and checked once it's done, withdrawn or expired. Steps notify nobody, so your own answer never comes back to you.
- **One thread per request.** Your replies are feedback, and each reaches the agent within seconds as its own message, even mid-task, so a correction is never held back. The agent's questions and its next versions arrive in the same thread, and the first post always shows the newest version with working buttons.
- **Answers reach the exact session that asked.**
  - A running session gets the answer typed into its terminal pane.
  - A session that isn't running is never started for an answer: you decide when an agent runs. The thread says the answer is waiting, and the agent picks it up with `approvals inbox` when it's back, which the skill tells it to run whenever it picks work back up. Answers that came while it was stopped aren't typed in when it returns either.
  - A session that moved to another account, or whose work continued in a new session after a usage limit, is found by its content, and the newest one that can still work gets the answer. Nothing is typed into a session stopped at its usage limit.
  - Approvals pressed in a row reach an agent one at a time: each goes once the agent has acknowledged the one before, or 2 minutes after it if no acknowledgement comes. A card whose answer is waiting its turn shows ⏳. Replies in a thread never wait.
  - Every answer is checked in the session's transcript and marked 📨 in Slack. 📨 means it was typed into the session, or the agent took it from its inbox, nothing more.
  - The agent then replies in the thread: after a decision, one line on what it understood and what it's doing now; after a reply or a question, its answer (`approvals answer`, verdict first) or its next version, with nothing before it. If nothing comes within 10 minutes, the thread gets a warning, so a delivery never looks like a handled answer.
- **A morning list of what's waiting on you.** At 8 am each day the service posts one message in the channel that mentions you and lists every card waiting on your answer, grouped by when it closes (today, tomorrow, later), each linked to its card. It also lists cards that closed with no answer since the last list, since a reply reopens them. All day it keeps that message current: each line is struck through, with what happened, once the card stops waiting on you.
- **Answer from the list in one tap.** Under each card waiting on you are its buttons: **Approve** (a yes to all of it: every plain row of a set, every item of a batch; a choice shows its options instead, unless they're pictures you need to see), **Deny**, and **Remove**, which takes a card that shouldn't be waiting on you off the list: it closes with nothing approved, and the agent is told it's no verdict on the idea. Each tap is the same press as the card's own buttons, and its line crosses off within a second or two.
- **The same list whenever you ask.** Post anything in the channel itself, not in a card's thread ("what's waiting?"), and within seconds the service posts a fresh list at the bottom, without mentioning you. Only the newest list is kept current; the one it replaces shrinks to a link to it.
- **A second reader for money.** One agent session, the CFO, can review every budget, purchase and money card. It hears each one as it's posted, each new version and each of your answers, and puts its read in the card's thread with `approvals note`, so it sits beside the ask when you decide. Nothing waits on it, the asker's side doesn't change, and with no reviewer set up nothing changes at all.
- **A deputy for the routine cards.** One agent session, the deputy, can hear every card as it's posted and make its own call on each: approve it for you, or leave it for you, with one or two sentences of why. Until you turn auto-approval on it's a dry run: each call is recorded and shown on the card in one gray line, and nothing is approved. Once it's on, the deputy's approve reaches the asking agent as your yes, the card says "🤖 Auto-approved" with its why, and a **Take it back** button turns it into your no at any time, even after the agent is done. Right after the 8 am list, one message sums up its calls since yesterday morning, with a button to turn auto-approval on or pause it. Which cards to approve is the deputy agent's own judgment; the tool only carries the call. The agents that post cards change nothing they do.
- **Agents learn it from a skill.** The `approvals` skill teaches when to ask (only when a yes is required, and once), how to write a card someone can decide from at a glance, and what to do with each kind of answer.

## Requirements

- macOS (a launchd service) or Linux (a systemd user service). The command itself is plain Python.
- [uv](https://docs.astral.sh/uv/).
- A Slack app, with:
  - Socket Mode on, and an app-level token with `connections:write`;
  - Interactivity on, which Socket Mode carries;
  - the bot event `message.channels` (`message.groups` for a private channel), so a reply in a thread is read within seconds rather than on the next pass through every card's thread;
  - bot scopes `chat:write`, `channels:history` (`groups:history` for a private channel), `channels:read`, `reactions:write`, `files:write`, `files:read` and `users:read`;
  - the bot invited to the approvals channel.
- To deliver into agent sessions: [Herdr](https://herdr.dev), with agents running in Herdr panes. Without it, answers wait in each agent's `approvals inbox`.
- For Basecamp steps: the Basecamp command-line tool, `basecamp`, and in each agent's folder (or one above it) a `.basecamp/config.json` whose `default_profile` is that agent's own login. Without one, a card's Basecamp step is skipped.

## Install

```bash
git clone git@github.com:aelaguiz/agent-approvals.git
cd agent-approvals
make install MACHINE=laptop
```

`MACHINE` names this machine, once; it's kept in `~/.approvals/machine.json`. The first run creates `~/.approvals/config.json` from [config.example.json](config.example.json) and stops. Fill it in, then run `make install` again. That run:
- installs the `approvals` command with `uv tool install`;
- copies the skill into `~/.agents/skills/approvals` (Codex) and `~/.claude/skills/approvals` (Claude Code);
- starts the `com.agent-approvals.listener` service (`agent-approvals` on Linux);
- runs `approvals doctor`.

`make verify_install` checks all of it.

| Setting | What it is |
| --- | --- |
| `channel` | The approvals channel's id. |
| `approver` | The Slack user id whose button presses and replies count. |
| `approver_name` | What cards and agent messages call them. |
| `bot_token_file`, `bot_token_key` | The env-style file holding the bot token, and the variable name in it. |
| `app_token_file`, `app_token_key` | The same for the Socket Mode app token. |
| `timezone` | For deadlines shown on cards, and the morning list's 8 am. |
| `default_herdr_session` | Where the listener opens panes when an agent's own Herdr session is gone. |
| `video_share_command` | Optional. A command that publishes a video and prints `URL: <link>`. Without it, videos go into the card's thread, where Slack plays them. |
| `test_approver` | For test runs only. A user whose `[test reply]` posts count, and only on TEST cards. |
| `id_prefix` | Optional. What request numbers start with; `AP` unless set. Each Slack workspace needs its own. |
| `folders` | Optional. Agents working in these folders, or inside them, use this workspace. See [More than one Slack workspace](#more-than-one-slack-workspace). |
| `hub`, `hub_url` | Optional. The machine that holds the records and the Slack connection, and where the others reach it. See [More than one machine](#more-than-one-machine). |

Tokens stay in their own files; the config only points at them.

### More than one machine

Agents on several machines can share one approvals channel, each getting its answers in its own session.

- **One machine is the hub.** Name it in `hub`, with the address the others reach it at in `hub_url` (`http://<hub>:8790`), and give every machine the same config. Slack hands each button press to only one connected listener, so only the hub connects. It keeps the records, watches the threads, posts the morning list, and serves the records to the other machines over HTTP on that port.
- **Every machine runs the service.** On the hub it does everything. On the others it asks the hub every 5 seconds for answers waiting on the sessions there and types them in, the same way the hub does for its own.
- **Each card names the machine it was asked from**, the name `make install MACHINE=<name>` gave, and follows the agent when it runs a command from another machine. An answer the named machine can't deliver because the session isn't there goes to whichever machine has the session's transcript.
- **Only machines that can post as the bot reach the hub.** They post to Slack themselves, and the hub accepts calls signed with a key derived from the bot token. A workspace whose bot token this machine can't read gets no service here, and its commands stop with that error before saving anything.
- **When the hub is down,** commands on the other machines fail with that error, and answers wait until it's back.

### More than one Slack workspace

One install can serve several Slack workspaces, each with its own approvals channel, bot and approver. Agents use the same skill and the same command in all of them, and never choose a workspace themselves.

- **Add a workspace** by giving it a folder inside `~/.approvals`, such as `~/.approvals/acme/`, with its own `config.json`: its channel, approver and token files, an `id_prefix` of its own (`AC`), and the `folders` its agents work in. Its bot needs its own Slack app, set up as in [Requirements](#requirements); a bot that another program also connects to with Socket Mode would receive some of the button presses meant for this one. Then run `make install`.
- **Each workspace runs its own service**, `com.agent-approvals.listener.<folder name>`, with its own records and log in its folder. The main workspace keeps `~/.approvals` and `com.agent-approvals.listener`.
- **The command picks the workspace.** A command about a request goes to the workspace whose prefix its number carries, wherever it's run. Otherwise an agent working in one of a workspace's `folders`, or inside one, uses that workspace; everything else uses the main one. Setting `APPROVALS_HOME` to a workspace's folder overrides both.

## Use

Agents use the skill. To try it by hand:

```bash
approvals submit card.json --agent "Pricing"   # posts the card, prints AP-0001 and a link
approvals submit a.json b.json c.json --agent "Revenue"   # a set: three cards together, each its own decision, marked "1 of 3"
approvals submit card.json --basecamp https://app.basecamp.com/<account>/buckets/<project>/card_tables/cards/<card>   # the Basecamp card it serves; repeatable
approvals ask AP-0001 "Mexico at MX$899 or MX$999?"
approvals revise AP-0001 card-v2.json          # next version, in the same thread
approvals ack AP-0001 "Got it: setting both prices now"   # the agent's own acknowledgement, in the thread
approvals withdraw AP-0001 "Prices changed; a new card is coming"   # take down a card the agent no longer stands behind
approvals status AP-0001 "Brazil live; Mexico waiting for Google's review" --next   # keep the card true after the yes
approvals status AP-0001 --part-of "Prices that fit each country"                    # set what a posted card belongs to, in place
approvals done AP-0001 "Set both prices; read them back from the store"
approvals inbox --agent "Pricing"              # answers that came while the agent wasn't running, or couldn't be typed in
approvals record AP-0001 --approved --words "yes do it" --where "phone call"   # also after an answered question that lets it go ahead as it stands
approvals repoint <old session> <new session>   # a launcher that starts an agent again in a fresh session (a pod restore) hands it the old one's open cards
approvals list --open
approvals doctor
approvals review --types budget,purchase,money --agent "CFO"   # this session reviews those cards; again from a new session moves it there; --off ends it
approvals note AP-0001 --file read.md          # the reviewer's read, in the card's thread
approvals deputy --agent "Approver"            # this session hears every card and makes its own call on each; again from a new session moves it there
approvals deputy --live                        # auto-approval on: the deputy's approve is the approver's yes (--dry-run pauses it; --off ends the role; alone, says who holds it)
approvals call AP-0001 --approve --why "Inside the test's cap, like every top-up he has approved"   # the deputy's call (or --his, to leave it for him)
approvals history --agent "Pricing" --type price --days 30   # cards newest first, each with what happened on it in order; --calls: only cards the deputy called
approvals pictures AP-0001                     # saves the newest version's pictures into a folder and prints their paths
```

The name a card and its replies show is `--agent`, unless whatever started the agent set `APPROVALS_FROM` (a team's launcher, say "AI pod · lead"): that wins, so the approver can always tell which team and member sent it.

A card is JSON. [skills/approvals/references/cards.md](skills/approvals/references/cards.md) describes every field, type and kind, with an example of each.

## How it works

[docs/design.md](docs/design.md) covers the cards, the thread, how each answer is routed back to its session, and what happens when a session has closed or moved.

- **Records:** one JSON file per request in `~/.approvals/requests/`, on the hub.
- **Log:** `~/.approvals/logs/listener.log`, on each machine.
- **The list:** the newest list's message and what it lists, in `~/.approvals/digest.json`, on the hub.
- **The reviewer:** which session reviews which card types, in `~/.approvals/reviewer.json`, on the hub.
- **The deputy:** which session it is and whether auto-approval is on, in `~/.approvals/deputy.json`, on the hub.
- **Restart the service:** `launchctl kickstart -k gui/$(id -u)/com.agent-approvals.listener`, or `systemctl --user restart agent-approvals` on Linux.
- Another workspace's records, log and service are the same paths in its own folder, and `com.agent-approvals.listener.<folder name>`.

## Test

- `make test` runs the unit tests.
- [docs/testing.md](docs/testing.md) describes the live end-to-end test, with real agents and Slack, and its latest results.

## Uninstall

```bash
make uninstall
```

This removes the services, the skill and the command, and keeps your records and config.

## License

[MIT](LICENSE)
