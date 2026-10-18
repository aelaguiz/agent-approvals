# How agent-approvals works

## The pieces

| Piece | What it does | Where |
| --- | --- | --- |
| `approvals` command | Agents submit cards, ask questions, post new versions, mark requests done, and read their inbox | In each agent's session |
| Records | One JSON file per request: every version, every reply and question, the decision, the outbox of messages to the agent, and how to reach the agent | `~/.approvals/requests/` |
| Listener | Holds the Slack connection, records button presses and replies, enforces deadlines, delivers messages to agents | launchd service, `approvals listen` |
| Skill | Teaches agents when to ask, how to write a card, and what to do with answers | `skills/approvals/` |

## One thread per request

The card is the first post. Everything about the request stays in its thread:
- the agent's evidence;
- the approver's replies, which count as feedback;
- the agent's questions, marked ❓;
- each new version as a full card.

When a version is replaced:
- the first post turns into the newest version, so it can be approved from the channel;
- older versions show "replaced", and their buttons stop working.

Slack drops buttons from a thread reply that is also sent to the channel. That's why the first post carries the newest version instead.

When the approver decides:
- the card shows ✅ or ❌ with their name and the time, and the buttons go away;
- the agent adds "Done" and what it checked when it finishes.

## Who counts

- **Buttons:** only presses by the configured approver count.
- **Thread replies:** only replies the approver posted count. The agent's own posts in the thread never do.
- **Elsewhere:** a yes given outside Slack counts once an agent records it with `approvals record`, quoting the approver's words.
- **Order of saving:** a button press is saved together with the message to the agent before Slack is told the press was received. Stamping the card comes after and is retried on its own, so a failed card update can't lose a decision.

## Getting an answer to the agent

Every request records how to reach the agent that asked. The `approvals` command reads this from the agent's own environment on every call:
- runtime: Claude Code `CLAUDE_CODE_SESSION_ID` or Codex `CODEX_THREAD_ID`;
- working folder and machine;
- Herdr session, workspace and pane.

When there's a message for the agent, the listener:

1. **Checks it isn't already there.** If the session's transcript already holds the message's reference code, it marks the message delivered and stops. That covers a restart between sending and recording.
2. **Finds where the asker is running.**
   - It checks the recorded pane first, then every pane on the Mac.
   - **Claude:** each process writes the session id it's running to `<config dir>/sessions/<pid>.json`.
   - **Codex:** the session id is read from the session file the process holds open.
   - **A different session counts as the asker only if it's a moved copy:** AI Manager rewrote its ids when it moved it to another account, so it starts with the same first prompt and has the request number in its history. A later run of the same scheduled agent, another agent in the same folder, or a sub-agent may start the same way, but none of them made this request.
3. **If the asker is running,** it waits while that pane is focused (someone may be typing, up to 10 minutes), then types the message and presses Enter through Herdr. It never starts a second copy of a session that's already running. If the session runs outside Herdr, the message waits and is retried.
4. **If it isn't running, it resumes it and then tells it.**
   - **Finds the newest copy:** the newest moved copy, by the rule above, or the asker itself.
   - **Resumes it.** It opens a tab in the agent's Herdr workspace, or in an `approvals` workspace if that's gone, and runs `aim claude resume <id>` or `aim codex resume <id>`.
   - **Waits until it's ready.** For Claude, the pane shows "bypass permissions" and a Claude process reports a session. For Codex, its input screen is showing.
   - **Records the real id** the resumed session reports, then sends the message.
5. **Confirms delivery.** The message's reference code must appear in the session's transcript within 90 seconds. Then the approver's reply, or the card for a button press, gets 📨.
6. **If delivery fails,** it retries every 5 minutes, then hourly after three failures. It posts one ⚠️ note in the thread saying why. Sessions on another machine, or in runtimes it can't resume, get the message in their `approvals inbox`.

A request's messages arrive in the order they were sent: a later one waits until the earlier one has landed.

Only the asker or a moved copy of it can change where a request's answers go. If another session runs a command on the request, that's noted on the record and changes nothing.

Every message starts with `[Approvals]` and the request number, quotes the approver's words exactly, and says what to do next. It's sent as one line, so nothing is submitted half-typed.

## Polling and deadlines

- **Replies:** the listener polls each open request's thread directly, capped at about 40 Slack calls a minute, so an old card's thread is still checked. Socket Mode message events, when the app receives them, trigger an immediate check.
- **Deadlines:** at its deadline a request is marked "No answer: not approved" and the agent is told. A reply after that reopens it.
