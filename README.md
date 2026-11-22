# agent-approvals

AI agents ask a person for approval as cards in a Slack channel. The person presses a button, or replies in the card's thread with anything they want. That answer is typed straight back into the agent session that asked, whether that's Claude Code, Codex or Antigravity. If that session isn't running, nothing starts it: the answer waits until the agent is back and checks for it.

```
agent ──approvals submit──▶ card in #approvals ──▶ you: ✅ / ❌ / reply in the thread
  ▲                                                        │
  └──── "[Approvals] AP-0013 ... approved it" ◀── listener ┘   (waits if the session isn't running)
```

- **One card per request, shaped to how you'll judge it.** Its type (creative, public reply, budget, purchase, money, player message, price, experiment, store or web page, content, email in your name, account action) says what the action is. Its shape says how you answer: a yes or no; today's screen beside the after, swipeable, with the change outlined; a set of related changes with a yes, a change or a no on each row and Approve all; a pick between options shown side by side; a batch with Pick which; words in your name you can edit before they go. Every card says what it's part of, what a yes covers, and what happens with no answer.
- **The card keeps going after the yes.** The agent posts each step on it (built, waiting at Apple, live, the first read) until the thing is really out, and a condition only you can clear shows as waiting on you, on the card and in the morning list. These redraw the card in place, without a notification.
- **The card names the thing it decides.** "Part of:" gives the initiative and the id of the pod dashboard's thing the card decides (`about`), where your answer lands as your decision. Cards no longer keep a step on a Basecamp card: `--basecamp`, or `"basecamp"` in a card's JSON, is refused with what to name instead. A card made before keeps its Basecamp link on "Part of:" as written, and nothing writes there again.
- **One thread per request.** Your replies are feedback, and each reaches the agent within seconds as its own message, even mid-task, so a correction is never held back. The agent's questions and its next versions arrive in the same thread, and the first post always shows the newest version with working buttons.
- **Answers reach the exact session that asked.**
  - A running session gets the answer typed into its terminal pane.
  - A session that isn't running is never started for an answer: you decide when an agent runs. The thread says the answer is waiting, and the agent picks it up with `approvals inbox` when it's back, which the skill tells it to run whenever it picks work back up. Answers that came while it was stopped aren't typed in when it returns either. An agent started again in a new session is not stopped: when the session that asked is gone and the same agent (its name and folder) runs in a newer one, on any machine, the answer is typed in there and its open cards go with it.
  - A session that moved to another account, or whose work continued in a new session after a usage limit, is found by its content, and the newest one that can still work gets the answer. Nothing is typed into a session stopped at its usage limit.
  - Approvals pressed in a row reach an agent one at a time: each goes once the agent has acknowledged the one before, or 2 minutes after it if no acknowledgement comes. A card whose answer is waiting its turn shows ⏳. Replies in a thread never wait.
  - Every answer is checked in the session's transcript and marked 📨 in Slack. 📨 means it was typed into the session, or the agent took it from its inbox, nothing more.
  - The agent then replies in the thread: after a decision, one line on what it understood and what it's doing now; after a reply or a question, its answer (`approvals answer`, verdict first) or its next version, with nothing before it. If nothing comes within 10 minutes, the thread gets a warning, so a delivery never looks like a handled answer, and a reply is typed into the agent again, then every 30 minutes, until it posts on the card. Recording his answer with `approvals record` counts as a post.
- **A morning list of what's waiting on you.** At 8 am each day the service posts one message in each approver's channel that mentions them and lists only their cards waiting on an answer, grouped by when it closes (today, tomorrow, later), each linked to its card. It also lists cards that closed with no answer since the last list, since a reply reopens them. All day it keeps that message current: each line is struck through, with what happened, once the card stops waiting on them.
- **Answer from the list in one tap.** Under each card waiting on you are its buttons: **Approve** (a yes to all of it: every plain row of a set, every item of a batch; a choice shows its options instead, unless they're pictures you need to see; a row that needs its own yes, like each store on a release card, gets its own Approve named for it), **Deny**, and **Remove**, which takes a card that shouldn't be waiting on you off the list: it closes with nothing approved, and the agent is told it's no verdict on the idea. Each tap is the same press as the card's own buttons, and its line crosses off within a second or two.
- **The same list whenever you ask.** Post anything in the channel itself, not in a card's thread ("what's waiting?"), and within seconds the service posts a fresh list at the bottom, without mentioning you. Only the newest list is kept current; the one it replaces shrinks to a link to it.
- **A second reader for money.** One agent session, the CFO, can review every budget, purchase and money card. It hears each one as it's posted, each new version and each of your answers, and puts its read in the card's thread with `approvals note`, so it sits beside the ask when you decide. Nothing waits on it, the asker's side doesn't change, and with no reviewer set up nothing changes at all.
- **A deputy for the routine cards.** One agent session, the deputy, can hear every card for the workspace's default approver as it's posted and make its own call on each: approve it for you, or leave it for you, with one or two sentences of why. Cards for another approver wait for that person and never go to the deputy. Until you turn auto-approval on it's a dry run: each call is recorded and shown on the card in one gray line, and nothing is approved. Once it's on, the deputy's approve reaches the asking agent as your yes for the whole card (every row, the ones marked as needing their own yes included; on a card that asks for a pick it takes the pick the card names, and says so), the card says "🤖 Auto-approved" with its why, and a **Take it back** button turns it into your no at any time, even after the agent is done. Right after the 8 am list, one message sums up its calls since yesterday morning, with a button to turn auto-approval on or pause it. Which cards to approve is the deputy agent's own judgment; the tool only carries the call. The agents that post cards change nothing they do.
- **Agents learn it from a skill.** The `approvals` skill teaches when to ask (only when a yes is required, and once), how to write a card someone can decide from at a glance, and what to do with each kind of answer.

## Requirements

- macOS (a launchd service) or Linux (a systemd user service). The command itself is plain Python.
- [uv](https://docs.astral.sh/uv/).
- A Slack app, with:
  - Socket Mode on, and an app-level token with `connections:write`;
  - Interactivity on, which Socket Mode carries;
  - the bot event `message.channels` (`message.groups` for a private channel), so a reply in a thread is read within seconds rather than on the next pass through every card's thread;
  - bot scopes `chat:write`, `chat:write.customize`, `channels:history` (`groups:history` for a private channel), `channels:read`, `reactions:write`, `files:write`, `files:read` and `users:read`;
  - for cards sent to the approver's team, bot scopes `im:write` to open direct messages and `im:history` to read their threads;
  - the bot invited to every approver's channel.
- To deliver into agent sessions: [Herdr](https://herdr.dev), with agents running in Herdr panes. Without it, answers wait in each agent's `approvals inbox`.

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
| `channel` | The default approver's channel id. |
| `approver` | The default approver's Slack user id, whose button presses and replies count on their cards. |
| `approver_name` | What cards and agent messages call the default approver. |
| `approvers` | Optional. Named approvers, each with their own `approver`, `approver_name` and `channel`. See [More than one approver](#more-than-one-approver). |
| `team` | Optional. Up to five people who can answer cards for the default approver. Each short name gives a Slack `user` id, display `name`, optional `quiet` hours that also hold weekend messages, and optional weekday `work` hours for hand-off returns. The default work window is `["09:00", "18:00"]`. |
| `team_answers_any` | Optional boolean, `false` by default. Set to `true` to let the default approver and everyone in `team` answer any card in this workspace. |
| `handoff_hours` | Working hours after scheduled delivery before an unanswered card goes back to the approver. Defaults to 3. |
| `list_as` | Optional. `{"name": "Approval desk", "icon_url": "https://example.com/desk.png"}` gives the morning list, lists asked for, and the deputy's summary their own name and picture. Unset: the app's usual name and picture. |
| `bot_token_file`, `bot_token_key` | The env-style file holding the bot token, and the variable name in it. |
| `app_token_file`, `app_token_key` | The same for the Socket Mode app token. |
| `timezone` | For deadlines shown on cards, team quiet and work hours, and the morning list's 8 am. |
| `default_herdr_session` | Where the listener opens panes when an agent's own Herdr session is gone. |
| `video_share_command` | Optional. A command that publishes a video and prints `URL: <link>`. Without it, videos go into the card's thread, where Slack plays them. |
| `page_share_command` | Optional. Publishes journey folders and related-card pages. The command takes `--title`, `--description` and the folder path and prints `URL: <link>`. Set it on the hub for related-card pages. |
| `page_slug_flag` | Optional. The share command's flag for a stable page address, for example `"--slug"`. The hub passes the flag and the group's saved random slug. With the flag unset, card buttons follow the address returned on each rebuild. |
| `test_approver` | For test runs only. A user whose `[test reply]` posts count, and only on TEST cards. |
| `alarm` | Optional. `{"channel": "<Slack channel id>", "wake": ["<program>", "<arg>"]}` posts delivery alarms to that channel and runs the command with the alarm on standard input. A leading `~` in the program path is expanded. |
| `id_prefix` | Optional. What request numbers start with; `AP` unless set. Each Slack workspace needs its own. |
| `folders` | Optional. Agents working in these folders, or inside them, use this workspace. See [More than one Slack workspace](#more-than-one-slack-workspace). |
| `record_url`, `record_token_file`, `record_token_key` | Optional. The pod dashboard's address, and the env file and key holding the token its `POD_DASHBOARD_HUB_TOKEN` is set to (a raw `record_token` also works, in a machine's own settings only). When set, the hub keeps every card in the dashboard's record instead of in `requests/`, and works from the record's answers: see [When the record keeps the cards](#when-the-record-keeps-the-cards). |
| `hub`, `hub_url` | Optional. The machine that holds the records and the Slack connection, and where the others reach it. See [More than one machine](#more-than-one-machine). |

Tokens stay in their own files; the config only points at them.

### When the record keeps the cards

With `record_url` set, the pod dashboard's record is where every answer lands, and the hub carries each one out:

- **The hub doesn't connect to Slack.** The Slack app's Socket Mode is off, and its interactivity and events go to the record's own address. The record takes every press and pop-up answer, keeps it, and only then answers Slack; it refuses in the words the buttons always used, in the presser's thread. It opens each pop-up from the ones kept with the card's version, so a pop-up opens while the hub is stopped.
- **The hub carries out every answer the record took**, in the order taken, with the same steps a press runs (the card redrawn, the list crossed off, the asker told), as it hears each card change on the record's stream of changes. The write that carries one out names it, so the record marks it carried out once. When the hub starts, it carries out whatever was taken while it was stopped. If its own steps refuse an answer (the card moved on in a way the record's rules don't cover), the presser is told in Slack, as before, and the record marks the answer refused with those words.
- **Every version is kept with its pop-ups**: the card as asked, Change, Edit the words, each quote and picture's own, and Pick which once for each number of ticks. The first write of each version draws them; on start the hub draws them for any version without.
- **The hub's own deadlines and hand-backs, the deputy's calls and answers recorded with `approvals record` go through the record too**, which refuses them in the same words the commands always printed. `approvals call` and `approvals record` on any machine send theirs to the hub, which has the record take it and carries it out before the command returns.
- **Who decides, the team and the deputy are the record's**: `approver`, `approver_name`, `channel`, `approvers`, `team` and `team_answers_any` are read from it, and every other machine asks the hub for them. A teammate's `quiet` and `work` hours stay in these settings, under a `team` entry with the same Slack `user`. Only the approver switches auto-approval, with the button on the morning summary; the hub redraws the summary when the record says it changed.
- **A thread reply is read at once** when the record says a line was said on the card (the record keeps a card's Slack thread on the card itself), and otherwise on the usual pass through every card's thread, which reads the most recently active cards first, so after a restart the cards in conversation are read in the first minutes, not after the older ones.
- **A comment typed on the card on the pod dashboard is his reply in the thread.** The record takes it first (a `comment` answer, with the page's key, so trying again never makes a second one). The hub posts it in the card's thread as "💬 Amir replied on the card:" with his words quoted, takes that post's Slack id as the reply's (so 📨 lands on it and the dashboard shows the line once), and hands it to the asker exactly as a reply in the thread, saying "on the pod dashboard". Slack refusing the post refuses the comment, and the record keeps Slack's words for the page.

### More than one approver

One Slack workspace can send cards to several approvers through the same app and listener. The top-level `approver`, `approver_name` and `channel` remain the default. Add other approvers under short names in `approvers`, using their Slack user and channel ids:

```json
{"approvers": {"lead": {"approver": "UOTHER", "approver_name": "Team lead", "channel": "COTHER"}}}
```

`approvals submit card.json --to lead` sends the card to that approver; for a set, the same `--to` applies to every card. An unknown name is refused with the workspace's available names. The agent's own repository says which decisions go to whom. Without `--to`, the default approver gets the card, as before.

Each card keeps its approver's short name as `target`; old cards without it still use the default. Its posts, updates and thread use that approver's channel. On a card with a named target, only that approver's buttons, pop-ups and replies count when `team_answers_any` is off. Each approver gets a separate morning list; posting in their channel asks for their list. The deputy serves only the default approver. Removing a named approver from config while their cards still exist makes commands on those cards fail with an error rather than send them to the default.

### A card sent to the approver's team

With `team_answers_any` on, the default approver and anyone in `team` can use the buttons and pop-ups or reply in the channel thread on any card, even when it went to someone else. Only the card's own approver can remove it, take back a deputy approval, use **Back to me** or **Hand to**, switch the deputy on or off, or ask for a fresh list in the channel.

With `team` set in [the workspace config](config.example.json), the deputy can leave a card for the default approver with `approvals call AP-0001 --his --also riley --why "<why>"`. Both people read the same why. The approver keeps the card in the channel and can answer immediately. If Riley has a `quiet` window, the deputy's direct message waits through that window and weekends in the workspace's `timezone` unless the call adds `--urgent` to send it on the next sweep. The two clock times can span midnight.

The default approver can use **Hand to Riley** on the channel card or the morning list. The card moves to **With your team** and leaves the count of cards waiting on the approver. Riley can answer as soon as it is handed over. The direct message also waits through Riley's `quiet` window and weekends if that window is set. The return clock starts at scheduled delivery and counts only time inside Riley's weekday work window. The card's deadline extends if needed to give the approver its usual window after the return. An unanswered card returns with a mention in its Slack thread. If Riley answers one row or replies and the agent answers without posting a new version, the open card stays with Riley past the return time. **Back to me** restores the card to the approver's list and Riley can still answer; pressed before the message reached Riley, it means the message never goes out.

Riley can use the card's buttons and reply in either thread. The first answer stands, and an answer from Riley names Riley and counts as the approver's for the card as written. **Not mine** hands the card back and tells the deputy. When `team_answers_any` is off, it also ends Riley's access. A revision keeps an unreturned hand-off with Riley and starts a fresh clock. The deputy makes a new call for each version and can use `--also` only if it has no current send. The morning summary shows team sends when auto-approval is on, and `approvals history` shows hand-offs, deliveries and returns.

The open direct-message thread also shows the asking agent's answers, acknowledgements and questions, along with the approver's replies, once a hand-off is delivered or a teammate responds to a deputy's send. The listener checks open direct-message threads on a 15-second schedule.

### More than one machine

Agents on several machines can share one approvals channel, each getting its answers in its own session.

- **One machine is the hub.** Name it in `hub`, with the address the others reach it at in `hub_url` (`http://<hub>:8790`), and give every machine the same config. Slack hands each button press to only one connected listener, so only the hub connects. It keeps the records, watches the threads, posts the morning list, and serves the records to the other machines over HTTP on that port.
- **Every machine runs the service.** On the hub it does everything. On the others it asks the hub every 5 seconds for answers waiting on the sessions there and types them in, the same way the hub does for its own.
- **Each card names the machine it was asked from**, the name `make install MACHINE=<name>` gave, and follows the agent when it runs a command from another machine. An answer the named machine can't deliver because the session isn't there goes to whichever machine has the session's transcript.
- **Only machines that can post as the bot reach the hub.** They post to Slack themselves, and the hub accepts calls signed with a key derived from the bot token. A workspace whose bot token this machine can't read gets no service here, and its commands stop with that error before saving anything.
- **When the hub is down,** commands on the other machines fail with that error, and answers wait until it's back.

### More than one Slack workspace

One install can serve several Slack workspaces, each with its own bot, default approver and optional named approvers. Agents use the same skill and the same command in all of them, and never choose a workspace themselves.

- **Add a workspace** by giving it a folder inside `~/.approvals`, such as `~/.approvals/acme/`, with its own `config.json`: its channel, approver and token files, an `id_prefix` of its own (`AC`), and the `folders` its agents work in. Its bot needs its own Slack app, set up as in [Requirements](#requirements); a bot that another program also connects to with Socket Mode would receive some of the button presses meant for this one. Then run `make install`.
- **Each workspace runs its own service**, `com.agent-approvals.listener.<folder name>`, with its own records and log in its folder. The main workspace keeps `~/.approvals` and `com.agent-approvals.listener`.
- **The command picks the workspace.** A command about a request goes to the workspace whose prefix its number carries, wherever it's run. Otherwise an agent working in one of a workspace's `folders`, or inside one, uses that workspace; everything else uses the main one. Setting `APPROVALS_HOME` to a workspace's folder overrides both.

## Use

Agents use the skill. To try it by hand:

```bash
approvals guide email                          # before writing: the open corrections for email cards, then how to write one
approvals read card.json                       # have a fresh reader check the card without posting it
approvals submit card.json --agent "Pricing"   # posts the card, prints AP-0001 and a link
approvals submit card.json --to lead           # sends it to the workspace's named approver
approvals submit a.json b.json c.json --agent "Revenue"   # a set: three cards together, each its own decision, marked "1 of 3"
approvals submit card.json --thread 'https://example.slack.com/archives/CEXAMPLE/p1800000000000001'   # the message this card answers
approvals ask AP-0001 "Mexico at MX$899 or MX$999?"
approvals revise AP-0001 card-v2.json          # next version, in the same thread
approvals revise AP-0001 card-v2.json --answer "No. The new look picker dropped the ask; it goes back in this build."   # after his reply: the answer, then one line saying the card changed
approvals ack AP-0001 "Got it: setting both prices now"   # the agent's own acknowledgement, in the thread
approvals withdraw AP-0001 "Prices changed; a new card is coming"   # take down a card the agent no longer stands behind
approvals status AP-0001 "Brazil live; Mexico waiting for Google's review" --next   # keep the card true after the yes
approvals status AP-0001 --part-of "Prices that fit each country"                    # set what a posted card belongs to, in place
approvals done AP-0001 "Set both prices; read them back from the store"
approvals inbox --agent "Pricing"              # answers that came while the agent wasn't running, or couldn't be typed in
approvals inbox --peek --session <session id>  # prints only the number waiting for this recorded session; hands nothing out
approvals fork <session id>                    # register a new fork, whose family is its first session id
approvals fork <new session id> --same-as <earlier session id>  # keep a moved or continued fork in its family
approvals fork                                # list registered ids, one session and family per line
approvals record AP-0001 --approved --words "yes do it" --where "phone call"   # also after an answered question that lets it go ahead as it stands
approvals repoint <old session> <new session>   # a launcher that starts an agent again in a fresh session (a pod restore) hands it the old one's open cards
approvals list --open
approvals doctor
approvals review --types budget,purchase,money --agent "CFO"   # this session reviews those cards; again from a new session moves it there; --off ends it
approvals note AP-0001 --file read.md          # the reviewer's read, in the card's thread
approvals deputy --agent "Approver"            # this session hears every card and makes its own call on each; again from a new session moves it there
approvals deputy --live                        # auto-approval on: the deputy's approve is the approver's yes (--dry-run pauses it; --off ends the role; alone, says who holds it). With the record keeping the cards, only the approver switches it, from the morning summary
approvals call AP-0001 --approve --why "Inside the test's cap, like every top-up he has approved"   # the deputy's call (or --his, to leave it for him)
approvals history --agent "Pricing" --type price --days 30   # cards newest first, each with what happened on it in order; --calls: only cards the deputy called
approvals names --days 3                       # recent Part of names, card counts, senders and newest titles
approvals pictures AP-0001                     # saves the newest version's pictures into a folder and prints their paths
```

`approvals read a.json b.json` reads cards together; `--with claude` or `--with codex` chooses the reader. Set
`reader_rules` to a Markdown file, optionally followed by `#` and a heading, and `reader_knows` to what the
approver already knows. Both settings are optional, and the command needs no Slack credentials.

The name a card and its replies show is `--agent`, unless whatever started the agent set `APPROVALS_FROM` (a team's launcher, say "AI pod · lead"): that wins, so the approver can always tell which team and member sent it.

Whatever creates a fork registers it in the workspace's folder and registers each later session id with `--same-as` when it moves or continues. A registered fork cannot collect or redirect answers belonging to an unregistered session or another fork family, even when their histories and agent names match. Within one family, the usual copy and continuation checks still apply; two unregistered sessions behave as before. Registry reads are cached for 15 seconds. `inbox --peek --session` asks the hub for a count without collecting answers; an operator's `inbox --all` still collects all waiting asker answers.

### The sender's name and picture

Put `.approvals.json` in an agent's working folder or a parent folder to give its Slack posts a name and picture:

```json
{"post_as": {"name": "Design team", "icon_url": "https://example.com/design.png"}}
```

The nearest file wins. No file, or a nearest file without `post_as`, keeps the app's usual sender. The name must be nonempty and at most 80 characters; the picture must be an HTTPS URL. A malformed sender refuses the command. `list_as` in the workspace config uses the same shape.

Each command uses the sender from its own working folder, so a reviewer's note carries the reviewer's sender. The card keeps the asker's sender for posts the service makes in the conversation it came from. File uploads and the service's delivery notes keep the app's usual name. All messages still belong to the approvals app, so their buttons reach the same listener.

### The Slack thread a card answers

Use `--thread <message link>`, or `"thread": "<message link>"` in the card JSON, to name the Slack message the card answers. `--thread` overrides the JSON on every card in a set. The link must be an HTTPS message link in the same Slack workspace, with an optional `thread_ts` query. The command finds that message's thread root.

The full card opens with a link to the conversation and, when readable and written by a person, a short quote of the message. The conversation gets a small card with its current state and a link to the full one. A plain yes-or-no card has the same answer buttons there; picks, rows and batches link to the full card. New versions, answers and status changes update the small card, and your replies on the full card are copied into the conversation once.

If the app cannot read the conversation, the full card still carries its link. The command says that the thread will not show the card and tells the agent to link it there itself.

A card is JSON. [skills/approvals/references/cards.md](skills/approvals/references/cards.md) describes every field, type and kind, with an example of each; `approvals guide <type>` prints that type's section under its open corrections.

### Show a user journey

Use `journey` to show today and the proposed change, screen by screen. Give one entry or a list of up to four, one per kind of user. Each entry needs `who` (up to 200 characters) and can name its paths with `today` and `after` (up to 40 characters each). They default to “Today” and “With the change”.

- Use `steps` for an ordered path of one to eight steps. Each step has `today`, `after` or both. Either side can show a screen or `{"ends": "What happens when this path stops"}` (up to 200 characters). An `after` can also be `"same"` when `today` is a screen. On each step with another step after it, give `then` as the action that gets the user there, up to 80 characters. Use `{"today": "...", "after": "..."}` when the actions differ.
- Use `places` for one to six separate comparisons. Give each place a `name` (up to 60 characters) and a screen on both sides. Each comparison stands on its own.
- Each screen needs `image` (a local picture file), `label` (what the user sees, up to 150 characters) and `source` (real or mock, which phone and build, up to 150 characters). Mark a changed screen with `"new": true`.
- Every entry shows what happens today and at least one screen with the change. Use `ends` when the path doesn't exist today.

The journey counts as media for a card that needs it. It can sit above a `gallery` on the same card. Slack shows one map per entry, and the thread has the maps and full-size journey pictures. With `page_share_command` set, **See it big** opens the version's page. Click a screen for the full-screen viewer, or link straight to it with `#big=0` for the first entry or `#big=0:1` for its second step. Use the arrow keys, Space, B and Esc, or swipe on a phone.

### See related approval cards together

Use the same **Part of** line on cards from the same ask. The hub treats names as equal when they differ only in whitespace or case and keeps each target channel and TEST cards separate. When two or more cards have journeys published with picture references, **All 5 in Library search** opens their shared page. It shows cards waiting on the approver first, then up to 20 cards settled in the past seven days, newest first. A short list links to other cards still waiting without a published journey.

The page shows each card's state, latest status and screens, with one viewer across all cards. A link like `#big=AP-0001:0:1` opens the second screen of that card's first entry. Use **Answer on the card** to return to Slack.

`approvals names [--days N]` lists the names used in the past three days by default. When a draft or new card has no other recent card under its name, `approvals read` and `approvals submit` print the recent names for its channel and card type (real or TEST) on standard error. Use `approvals status AP-0001 --part-of "Library search"` to move an existing card.

## How it works

[docs/design.md](docs/design.md) covers the cards, the thread, how each answer is routed back to its session, and what happens when a session has closed or moved.

- **Records:** one JSON file per request in `~/.approvals/requests/`, on the hub.
- **Log:** `~/.approvals/logs/listener.log`, on each machine.
- **The lists:** the default approver's newest list and what it lists in `~/.approvals/digest.json`, and `~/.approvals/digest-<name>.json` for each named approver, on the hub.
- **The reviewer:** which session reviews which card types, in `~/.approvals/reviewer.json`, on the hub.
- **The deputy:** which session it is and whether auto-approval is on, in `~/.approvals/deputy.json`, on the hub.
- **Fork families:** each registered session and its first fork session id, in `~/.approvals/forks.json`, on the hub.
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
