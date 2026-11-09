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
- each new version as a full card.

When a version is replaced:
- the first post turns into the newest version, so it can be approved from the channel;
- older versions show "replaced", and their buttons stop working;
- a one-line post in the channel says the new version is waiting, since the thread may be days old. Questions are sent to the channel the same way.

Slack drops buttons from a thread reply that is also sent to the channel. That's why the first post carries the newest version, and the channel gets a line pointing to it rather than the card.

A decision belongs to the version it was made on. A new version after a yes reopens the request with working buttons, and the record keeps the yes on the old version. A declined request takes no new versions; a different proposal is a new card.

An agent that no longer stands behind a card runs `approvals withdraw`: the card shows "Withdrawn" with its reason, the buttons go away, and a press says the agent withdrew it.

When the approver decides:
- the card shows ✅ or ❌ with their name and the time, and the buttons go away;
- the agent adds "Done" and what it checked when it finishes.

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
5. **Confirms delivery.** The message's reference code must appear in the session's transcript within 90 seconds. Then the approver's reply, or the card for a button press, gets 📨.
6. **If delivery fails,** it retries every 5 minutes, then hourly after three failures. It posts one ⚠️ note in the thread saying why. A request asked from outside Claude or Codex gets the message in its `approvals inbox`. A session whose transcript isn't on this machine gets it there too, until a machine that has the transcript takes it (above). A session whose transcript is here is here, whatever name the card gives the machine.

A request's messages arrive in the order they were sent: a later one waits until the earlier one has landed. One waiting for an agent that wasn't running doesn't hold up the next: an answer that comes once the agent is running again is typed in, and the waiting ones are in its inbox.

**Resuming, for agent-email.** `router.deliver` can still resume a closed session, and agent-email, which imports it, does, so a reply to an email reaches a session that has closed. It finds the newest session that can still work (the newest moved copy or continuation, or the asker, skipping any stopped at its usage limit); opens a tab in the agent's Herdr workspace, or in a new workspace if that's gone, starting `default_herdr_session` headless if no Herdr session is running; runs `aim claude resume <id>` or `aim codex resume <id>`; waits until the pane is ready (closing it if it lands on Claude's usage-limit menu); records the id the resumed session reports; and sends the message. Approvals calls it with `resume=False`.

Only the asker, a moved copy or a continuation of it can change where a request's answers go. A continuation's `approvals inbox` also shows the request's waiting answers. If another session runs a command on the request, that's noted on the record and changes nothing.

Every message starts with `[Approvals]` and the request number, quotes the approver's words exactly, and says what to do next. It's sent as one line, so nothing is submitted half-typed.

## Acknowledgements

📨 means one thing: the service saw its message in the agent's transcript. That proves delivery, not understanding.

**The agent's acknowledgement:**
- Every answer tells the agent to acknowledge first. Deadlines are the exception.
- The agent posts in the thread with `approvals ack`, in its own words: what it understood and what it's about to do. For example: "Got it: you approved v2. Posting the reply now, then I'll mark it done."
- The thread then shows, from the agent itself, what will happen. Any later difference between the acknowledgement and the action is visible there.

**The warning.** A delivered answer that hasn't been acknowledged after 10 minutes gets one ⚠️ note in the thread. It names the agent and says nothing confirms the answer was understood or acted on.

## Polling and deadlines

- **Replies:** the listener polls each open request's thread directly, capped at about 40 Slack calls a minute, so an old card's thread is still checked. Socket Mode message events, when the app receives them, trigger an immediate check.
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
