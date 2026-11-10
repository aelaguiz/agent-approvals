# How agent-approvals works

## The pieces

| Piece | What it does | Where |
| --- | --- | --- |
| `approvals` command | Agents submit cards, ask questions, post new versions, mark requests done, and read their inbox | In each agent's session |
| Records | One JSON file per request: every version, every reply and question, the decision, the outbox of messages to the agent, and how to reach the agent | `~/.approvals/requests/` on the hub |
| Listener | On the hub: holds the Slack connection, records button presses and replies, enforces deadlines, serves the records to the other machines. On every machine: delivers messages to the agents there | launchd or systemd service, `approvals listen` |
| Skill | Teaches agents when to ask, how to write a card, and what to do with answers | `skills/approvals/` |

**More than one Slack workspace.** Each workspace has its own folder (`~/.approvals` for the main one, `~/.approvals/<name>/` for each other), with its own config, records, request numbers and listener. The code is the same for all of them; only the folder differs. The command picks the folder by itself: a request's number prefix says which workspace it belongs to, and for a new request or the inbox, the agent's working folder decides, by each workspace's `folders` setting. The skill is the same everywhere, so an agent never chooses a workspace and can't choose the wrong one.

**More than one machine.** Slack hands each button press to only one of the connections an app holds, so a workspace whose agents run on several machines still has one listener connected to Slack: the hub's. A listener per machine with its own records would lose the presses that went to another machine and post the morning list once per machine.
- **The records stay on the hub.** On any other machine, the command and the service read and change them over HTTP (`hub.py`). Each change is made to the copy it read, and when the record changed on the hub in between, the hub refuses it and the change is made again on the new copy. So two machines answering, acknowledging or delivering on one request at the same moment can't undo each other.
- **Each machine posts to Slack itself** with its own copy of the bot token. The hub accepts only callers that sign with a key derived from that token, so no other secret is shared.
- **Each card names the machine it was asked from** (`reach.machine`, from `~/.approvals/machine.json`). That machine's service asks the hub every 10 seconds for the messages due for its sessions, and delivers them as below. A card from before machines had names belongs to the hub, and without a hub every card belongs to the one machine.
- **The machine follows the agent.** Any command the asker runs updates the card's reach, its machine included. A message the named machine can't deliver because the session's transcript isn't there is marked a stray: every machine looks for that session's transcript about once a minute, and the one that has it takes the message and the card's later ones.

**The Slack connection.** The hub holds it with Slack's built-in Socket Mode client, which opens a new connection by itself when one dies. That client reads every connection, and answers Slack's pings, on one long-lived thread. When a TLS write fails with a system error such as a broken pipe, Python's `ssl` module leaves that error queued on the thread, and the next read there that has to wait for data reports it again, on whatever connection it's reading. So one ping answered on a dead connection made every later connection fail on its first read. On November 8, 2026 that left the hub reconnecting every 5 seconds for 42 minutes, hearing no button presses, until the service restarted. `listener.SlackConnection` empties that thread's error queue before it reads each connection.

## One thread per request

The card is the first post. Everything about the request stays in its thread:
- the agent's evidence, one post per item, with any file it names attached so it opens in Slack;
- the approver's replies, which count as feedback;
- the agent's questions, marked ❓;
- each new version as a full card, at the bottom where the approver reads.

When a version is replaced:
- the first post turns into the newest version, so it can be approved from the channel;
- the old version is never posted again, and an earlier copy of it in the thread shrinks to one line with a See button, so the thread holds one whole card at a time and what's new is plain. The new card opens with what changed and a See button for the version before it. (Amir, November 10, 2026, when each version re-posted the one before it in full: "I see an accumulating set of copies ... and can't figure out what is new.");
- older versions' buttons stop working;
- a one-line post in the channel says the new version is waiting, since the thread may be days old, and the one that announced the version before it is taken down. Questions are sent to the channel the same way.

Slack drops buttons from a thread reply that is also sent to the channel. That's why the first post carries the newest version, and the channel gets a line pointing to it rather than the card.

A decision belongs to the version it was made on. A new version after a yes reopens the request with working buttons, and the record keeps the yes on the old version. A declined request takes no new versions; a different proposal is a new card.

An agent that no longer stands behind a card runs `approvals withdraw`: the card shows "Withdrawn" with its reason, the buttons go away, and a press says the agent withdrew it.

When the approver decides:
- the card shows ✅ or ❌ with their name and the time, and the buttons go away;
- the agent adds "Done" and what it checked when it finishes.

## The card's shape, and its answers

A card's `type` says what the action is; its fields say how the approver judges it and answers it. The renderer is `cards.py`; what an answer does to the record is `core.py`. One card is answered one way:

- **A yes or no**, as before, with buttons named for what happens. `change_label` adds a Change button; quotes marked `edit` add Edit the words.
- **A pick** among `options`. With pictures, the options are carousel cards side by side, each with its own Pick button and the sender's pick marked; without, they're buttons. The decision records the `choice`.
- **A row each** (`parts`): each row has its own buttons and its own answer, kept in `answers` by the row's id (its `id`, else its position). The card is waiting on the approver while any row is open, back with the agent while a row waits for a new version (Change on that row), and otherwise approved if any row was approved or chosen, declined if none was. Approve all answers every open plain row except those marked `own_yes`, in one message. At the deadline only the open rows close.
- **A batch** (`items`): a yes covers all; Pick which opens a checklist, and the decision records which `items` were approved.

Everything else (the frame line `part_of`, `if_no_answer`, `gallery`, `sections`, `links`, `from`) can go on any card.

**Each answer reaches the agent as its own message, naming exactly what it answers:** a row's answer names the row by number and title and says where the other rows stand ("This answer is for part 2 only; part 1 is approved, parts 3 and 4 are waiting"); a pick names the option; a batch lists what to do and what to leave out; edited words are quoted as a JSON string, so the line breaks survive the one-line message.

**Buttons.** Each button's value is `<id>|<version>|<what>`, as before: `approve` and `decline` mean what they always did, so cards already in the channel keep working. The new ones are `pick:<i>`, `all`, `p<j>:approve|decline|pick:<i>`, `offer:stop|keep`, and the ones that open a pop-up: `change`, `p<j>:change`, `edit`, `items`, `read:<quote>`, `pic:<picture>` and `see`.

**Pop-ups.** Slack allows 3 seconds from a press to open a pop-up, so the hub opens it right after acknowledging the press, after checking the card hasn't moved on (a newer version, an answered row). A submission is checked against the record again, since the version can be replaced while the box is open, and is refused inside the box when it has; only the approver's submissions count. A change from a pop-up is posted in the thread too, so the thread keeps the whole record. Only the approver can open the ones that decide; anyone can open Read and See the card, and Full size and See it are links anyone can follow.

**Pictures.** Slack shows a picture inside a carousel card only once the file is shared where the card is; a privately uploaded file shows as a broken image there, though it works in an image block. So a new card is posted, its pictures are posted in its thread in one message (full size, which is also where they open), and the card is redrawn with them. Slack also crops a carousel card's picture to a fixed landscape frame, which cuts a phone screenshot down to its middle, so each carousel picture is first fitted to that frame (`pictures.card_frame`): zoomed onto what changed and outlined when it has a `mark`, otherwise whole and letterboxed. Every picture is also posted whole, at its own resolution, and a slide's Full size and a row's See it are links to that post, so it opens in Slack's own viewer at full resolution with zoom; a pop-up would shrink it to the pop-up's width, about 500 pixels, where a slide or a phone screen can't be read. A row's thumbnail is a section accessory, which Slack doesn't let anyone tap. Cards from before this change have no links and still open the picture in a pop-up.

**Revisions.** A new version keeps the answer of each row the agent left exactly as it was, and asks again about every row it changed, plus rows that closed with no answer. The changed row is marked new, and the line in the channel carries the version's `changes`.

**Keeping a posted card true.** `approvals status` adds a line to a card (what happened, what comes next, a condition waiting on the approver, the other side's reply to a message sent as him), sets the answered card's headline, or sets the frame line. Each redraws the card in place, so it notifies no one and leaves answers and buttons as they were. A condition waiting on the approver is also posted in the thread and keeps the card on the morning list until the agent's next line. An answered card collapses to its timeline, with a button that opens the card as it was asked.

## Who counts

- **Buttons:** only presses by the configured approver count.
- **Thread replies:** only replies the approver posted count. The agent's own posts in the thread never do.
- **Elsewhere:** a yes given outside Slack counts once an agent records it with `approvals record`, quoting the approver's words.
- **After a decision:** a reply on an approved, declined, done or withdrawn card still goes to the agent, as a reply after the decision.
- **Order of saving:** a button press is saved together with the message to the agent before Slack is told the press was received. Stamping the card comes after and is retried on its own, so a failed card update can't lose a decision.

## Getting an answer to the agent

Every request records how to reach the agent that asked. The `approvals` command reads this from the agent's own environment on every call:
- runtime: Claude Code `CLAUDE_CODE_SESSION_ID` or Codex `CODEX_THREAD_ID`;
- working folder and machine;
- Herdr session, workspace and pane.

`approvals submit` refuses a card when its shell has neither session id, since nothing could ever be typed back to it. That happened when two pods on other machines posted their cards over ssh to the Mac (November 7 and 8, 2026): 18 cards were saved with no asker, and every answer waited in an inbox.

When there's a message for the agent, the listener:

1. **Checks it isn't already there.** If the session's transcript already holds the message's reference code, it marks the message delivered and stops. That covers a restart between sending and recording.
2. **Finds where the asker is running.**
   - It checks the recorded pane first, then every pane on the machine.
   - **Claude:** each process writes the session id it's running to `<config dir>/sessions/<pid>.json`.
   - **Codex:** the session id is read from the session file the process holds open.
   - **A different session counts as the asker only if it's a moved copy or a continuation.** A moved copy is the asker after AI Manager moved it to another account: it starts with the same first prompt and first reply (AI Manager rewrites session ids inside the text, so ids are ignored in the comparison) and has the request number in its history. A continuation carries the asker's work into a new session, as AI Manager does when an account hits its usage limit: it opens with Claude's summary, which names the transcript it continues, so the chain can be followed back. A later run of the same scheduled agent, another agent in the same folder, or a sub-agent may start the same way, but continues nothing and made none of this request.
   - **A session stopped at its usage limit can't take an answer.** Its last reply is Claude's "You've hit your ... limit" notice, or its screen shows the usage-limit menu. The service looks past it to a session continuing its work, and never types into that menu, whose third choice switches the account to paid usage credits.
3. **If the asker is running and can work,** it types the message and presses Enter through Herdr straight away. It never starts a second copy of a session that's already running. If the session runs outside Herdr, the message waits and is retried.
4. **If it isn't running, nothing starts it.** The approver decides when an agent runs: they may have stopped a pod on purpose, and an answer is not an instruction to run. The message waits on the record, and the thread gets one 💤 note saying the agent isn't running and the answer is waiting. When the agent is back, the skill has it run `approvals inbox` before anything else, which hands it every answer that came while it was stopped and marks each 📨. Those answers are never typed in by the service, even once the session is running again: the agent reads them itself. A session that is running but stopped at its usage limit isn't "not running": its answer waits for a session continuing its work, as above.
5. **Confirms delivery, separately.** Once the message is typed, the machine checks the session's transcript every 3 seconds for its reference code, which appears when the agent takes the message in. Then the approver's reply, or the card for a button press, gets 📨. A message not seen within 90 seconds counts as a failure (below).
6. **If delivery fails,** it retries every 5 minutes, then hourly after three failures. It posts one ⚠️ note in the thread saying why. A request asked from outside Claude or Codex gets the message in its `approvals inbox`. A session whose transcript isn't on this machine gets it there too, until a machine that has the transcript takes it (above). A session whose transcript is here is here, whatever name the card gives the machine.

A request's messages arrive in the order they were sent. A later one is typed right behind the earlier one, as soon as that one is typed, without waiting for it to show up in the transcript. Claude takes typed messages in at its next step, so the agent reads them together: a "wait, don't send" lands with the yes it takes back, and a run of corrections arrives at once. When each waited for the one before to show up, an agent acted on a yes in the 5 seconds before the wait behind it arrived (November 9, 2026). A later message waits only when the earlier one couldn't be typed. One waiting for an agent that wasn't running doesn't hold up the next: an answer that comes once the agent is running again is typed in, and the waiting ones are in its inbox.

**Approvals pressed in a row go one at a time.** Without this, a run of yeses landed within seconds of each other, before the agent had reacted to the first: on November 8, 2026 nine Instagram approvals pressed within 23 seconds were all typed into one agent within 29. Now a decision (a button or a pop-up that decides: approve, decline, a pick, Approve all, a row's answer, Pick which, Edit the words) waits while its agent, the session that asked, has an answer typed in during the last 2 minutes that it hasn't acknowledged. The agent's waiting decisions go oldest first, one at a time, each once the one before is acknowledged, or 2 minutes after it was typed if no acknowledgement comes. Agents usually acknowledge within seconds (half within 10, 9 in 10 within 48, over November 2 to 9). Decisions for different agents don't wait on each other. While a card's decision waits, the card has ⏳, which goes when the decision is released; 📨 still marks it landing. A thread reply, or the words in a Change pop-up, never waits: the approver correcting himself should reach the agent at once, even mid-task. Each reply is its own message. A reply on a card whose decision is waiting releases that decision, and the reply is typed right behind it, so a "wait, don't send" is never held behind the yes it takes back.

**Resuming, for agent-email.** `router.deliver` can still resume a closed session, and agent-email, which imports it, does, so a reply to an email reaches a session that has closed. It finds the newest session that can still work (the newest moved copy or continuation, or the asker, skipping any stopped at its usage limit); opens a tab in the agent's Herdr workspace, or in a new workspace if that's gone, starting `default_herdr_session` headless if no Herdr session is running; runs `aim claude resume <id>` or `aim codex resume <id>`; waits until the pane is ready (closing it if it lands on Claude's usage-limit menu); records the id the resumed session reports; and sends the message. Approvals calls it with `resume=False`.

Only the asker, a moved copy or a continuation of it can change where a request's answers go. A continuation's `approvals inbox` also shows the request's waiting answers. If another session runs a command on the request, that's noted on the record and changes nothing.

Every message starts with `[Approvals]` and the request number, quotes the approver's words exactly, and says what to do next. It's sent as one line, so nothing is submitted half-typed.

## Acknowledgements

📨 means one thing: the service saw its message in the agent's transcript. That proves delivery, not understanding.

**The agent's acknowledgement:**
- Every answer tells the agent to acknowledge first. Deadlines are the exception.
- The agent posts in the thread with `approvals ack`, in its own words: what it understood and what it's about to do. For example: "Got it: you approved v2. Posting the reply now, then I'll mark it done."
- An acknowledgement covers every answer on the card that has gone to the agent (typed in, or read from its inbox), not one still waiting its turn. Answers are counted as gone the moment typing starts, since agents often acknowledge before their transcript shows the message.
- The thread then shows, from the agent itself, what will happen. Any later difference between the acknowledgement and the action is visible there.

**The warning.** A delivered answer that hasn't been acknowledged after 10 minutes gets one ⚠️ note in the thread. It names the agent and says nothing confirms the answer was understood or acted on.

## Polling and deadlines

- **Replies:** when the approver posts in a card's thread, Slack's message event has that thread read in the next round, within 5 seconds, ahead of everything else. Other posts (agents' acknowledgements, the service's own notes) don't. The hub hands the reply to its own sessions within 5 seconds, and the other machines ask for theirs every 5 seconds.
- **The safety net:** the listener also reads every watched thread in turn (any card with activity in the last 14 days), 3 every 5 seconds. With hundreds of cards a full pass takes about 15 minutes, so this only catches replies Slack didn't flag, such as while the connection is down.
- **Deadlines:** at its deadline a request is marked "No answer: not approved" and the agent is told. A reply after that reopens it.

## The morning list

Cards arrive all day and night, and a card nobody answers closes as a no, so its agent drops it. The morning list keeps one place that says what still waits on the approver.

- **When:** at 8:00 am in the workspace's `timezone`, the listener posts one message in the channel, mentioning the approver. A service that was down at 8 posts when it comes back that day. `digest.json` in the workspace's folder records the newest list's message and what it lists.
- **On demand:** any post of the approver's own in the channel, outside a card's thread, asks for a fresh list. The channel holds only cards, so nothing else is posted there that way. Every 5 seconds the listener reads the channel's posts since the newest list and, if one asks, posts a new list at the bottom, without the mention. An ask before 8 am doesn't replace the morning list.
- **What's on it:** every card waiting on the approver's answer: new cards, new versions, and agents' questions (status `pending` or `question`). Cards waiting on their agent (revising after a reply, or acting on a yes) stay off, and so do TEST cards.
- **Order:** grouped by the day the card closes (today, tomorrow, later), soonest first. Each line links the request number to the card's first post, which always shows the newest version, and says the agent, when the card last came to the approver, and when it closes.
- **Closed with no answer:** cards that hit their deadline since the last list get their own section. A reply in a closed card's thread reopens it, and nothing new is posted in the channel when a card closes, so this is where the approver learns of it.
- **Kept current:** every 5 seconds, the listener renders the newest list again from the records and edits the message when anything changed. A card that stops waiting on the approver is struck through with what happened: approved, declined, replied to, withdrawn, or closed with no answer. A card that comes back with a new version is shown as waiting again. Cards that arrive after the list is posted are left to the next list.
- **One list at a time:** when a new list is posted, the one it replaces is edited down to a link to the new one, so the channel never shows two lists that disagree.
- **Nothing waiting:** one line saying so, mentioning no one.
- **Failures:** the record of today's list changes only after Slack accepts the post or edit, so a failed one is tried again on the next sweep.
