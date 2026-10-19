---
name: approvals
description: "Get a person's yes before an operational action by posting an approval card to Slack with the `approvals` command, then acknowledge the answer in the thread and act on it when it arrives in this session as an [Approvals] message. Use when your instructions say an action needs someone's approval first and that person isn't plainly the one giving you this task: posting or replying publicly as them or the brand, ad or test spend, a purchase or money move, a price or offer change, a message to players or customers, switching an already-built experiment or feature on or off, a store listing or web page change, content going live, email or Slack in their name, or deleting an account. Also use when an [Approvals] message arrives, or to record a yes they gave somewhere else. Not for deciding to build a feature or anything that must be built first; not for code reviews, merges, releases or deploys; not for actions a standing rule already lets you take; not for announcing finished work."
---

# Approvals

When an action needs your approver's yes first, put one card in front of them that they can decide from in a few seconds, keep working, and act on their answer exactly when it comes back. A good card gets a fast yes or a useful correction. A vague one gets "what is the question?" or no answer at all, and the work stalls.

## What matters most

- **Ask only when a yes is actually required.** If your instructions or a standing rule already let you act, act and report what you did. A card that didn't need asking spends the approver's attention and teaches them to skim cards.
- **A card is for a change that is ready to happen, never for deciding to build something.** It shows the exact thing that will go out or switch on: the ad, the reply, the price, the message, a flag on code that already exists. Whether to build a feature, a new screen, or an experiment whose variant needs app or server work first is a different decision, which the approver makes on their own, from the report or deck that carries its case, size and mocks; a tap on a card cannot carry that, and a yes there would hand an agent a build nobody designed or weighed against everything else. Keep that kind of call a proposal in your report, deck or plan; once the thing is built, switching it on can be a card. Amir, October 19, 2026, declining two cards that asked him to approve building experiments: "features are not built via an approval Slack message. That's an entirely different decision process because I'm never going to just click yes and then let an agent build some feature."
- **A request to do it is not the approver's yes.** Tasks often come from a teammate, another agent, a brief or a schedule. Unless the approver is plainly the one telling you to do this, treat the instruction as a request that still needs their yes, not as permission. When they do tell you directly, that is the yes; don't send them a card about their own instruction.
- **The card is how the question reaches them.** Asking in this conversation only works if the approver is the one reading it. When it might not be them, don't ask "are you the approver?" or wait for a reply in chat; submit a card, and say in the conversation that you did.
- **Never take the action before the answer.** Only two things count as a yes. One is the card's approve button, which reaches you as an `[Approvals]` message saying it was approved. The other is the approver's own words naming this request, such as a reply on the card like "post it", or a yes they gave elsewhere that you record with `approvals record`. A friendly reply to a long report is not a yes.
- **Put the real thing on the card.** Show the exact reply under the post it answers, the ad itself, the price table with today beside proposed, the email word for word. If the approver would have to open something else to know what they're agreeing to, the card isn't ready.
- **Ask only for a yes that someone will carry out.** A yes is an instruction, and the approver stops tracking the request once they give it. If you can't act on it yourself (a report run that can't change settings, an ad account you have no access to, a session about to end), the card names who will, and you get the yes to them and see it through. Otherwise the yes lands nowhere while the approver believes it's handled.
- **Ask once.** Don't send reminders or post the same card again. No answer by the deadline means no.
- **Take a card down as soon as you stop standing behind it.** An open card is a live question with a working approve button, and approvers clear several cards in a minute. When you drop the version it shows, the moment passes (the post date, a paused ad), or the facts change (a competitor moved its price), withdraw it with `approvals withdraw` and say why, or post the version you do stand behind. A yes on a card you've abandoned gets acted on wrongly or has to be untangled; on October 19 a card was approved nine hours after its agent had said in the thread that nothing would post from it, because its button still worked.
- **The thread is the approver's whole view of your work, and they take every post there as fact.** They can't see your session; 📨 on their reply only means the message was typed into it. So:
  - **Acknowledge every answer before acting on it,** in your own words, with `approvals ack`: what you understood and what you're about to do. An answer left unacknowledged for 10 minutes gets a warning in the thread.
  - **Promise only what you know you can do.** "Starting it now" before checking whether there's room to start it becomes a correction three minutes later. Check first, or say what you're checking.
  - **"Done" means the change on the card is real and you've seen it:** the ad running, the post live, the flag on, the money moved. Your part finishing, a hand-off, or a schedule isn't done. Say where it stands, and mark it done when it's real. A card marked done while the setting it asked for was still off got "Did this happen?" back.
  - **If you end up doing something different, say so in the thread.**
- **Act only on answers that reach you:** an `[Approvals]` message in this session, or one `approvals inbox` hands you. A request another session made belongs to that session even when it's for the same change, and its answer goes to it; that includes an earlier run of your scheduled job, which started fresh just as you did. A session you continue is different: if this session opened with a summary of earlier work, that work is yours, its open cards included, and their answers come here. If the change you were asked for is already requested and still open, don't post a duplicate; say which request it's waiting on.
- **Don't wait around.** After submitting, keep working on something else, or end your turn saying which request you're waiting on. The answer is typed into this session. If this session has closed by then, it's resumed first and then told.

## Asking

1. **Write the card as JSON.** Before writing, read [references/cards.md](references/cards.md) for the type you need: what the approver judges it by, what it must include, and a full example.
2. **Submit it:** `approvals submit card.json --agent "<your name as the approver knows you>"`. It prints the request number (for example AP-0013) and a link. If it refuses the card, it names the missing field; fix that and submit again.
3. **Carry on.** Do other work, or end your turn.

Every card needs:
- `type`, one of: `creative`, `reply`, `budget`, `purchase`, `money`, `player_message`, `price`, `experiment`, `page`, `content`, `email`, `account`;
- `title`, which is the action said plainly ("Move $20,000 from savings to operating", not "Transfer approval");
- `facts`, a list of `[label, value]` lines;
- `approves` and `does_not_approve`, which mark the edges of the yes.

Some types also need `quotes`, a `table`, or an `image` or `video`; the card types reference says which.

**The card holds only what changes the answer; the thread holds the proof.** The approver reads cards on a phone between other things, a teammate may be reading the channel too, and every line before the decision costs attention across every card that day. So the card carries the thing itself, what a yes covers, and the few facts that would make someone say no or "not like that". The checks you ran, the research, the numbers behind the numbers and long lists go in `evidence`, which is posted in the card's thread. Most cards need about 100 to 180 words; when yours runs far past that, look for a paragraph that proves your work rather than shapes the decision, and move it to the thread. [references/cards.md](references/cards.md) has a before and after.

**Evidence has to open in Slack.** A path to a file on your machine posted as text opens nothing, so give the file's path (alone or after a label, like `"Recipe: /path/recipe.json"`) and it's attached to the thread, or link a page the approver can open: a doc, a sheet, a share link.

**Name things the way the approver does.** A check number, a row key, a test-set id or a code from your own workbook means nothing to them; say what it is ("the build check", "the US Android test set"). The rest of the house style:
- the action in the title;
- one fact per line;
- numbers next to what they compare with ("$700, up from $450");
- no em dashes.

## When the answer arrives

Answers arrive as messages starting with `[Approvals]` that quote the approver's words exactly.

First, acknowledge it: `approvals ack AP-0013 "<what you understood and what you'll do>"`. For example: "Got it: you approved v2. Posting the reply now, then I'll mark it done." Or: "Understood: shorter, and no product mention. v3 coming in a few minutes." Then act as below. A deadline passing needs no acknowledgement.

| The message says | Do this |
| --- | --- |
| It was approved | Do exactly what the card described, nothing more. Then run `approvals done AP-0013 "<what you did and how you checked it>"`. |
| It was declined | Drop this proposal; never send it again or a lightly changed copy. A no is to the proposal, not necessarily to what it was for. Usually the goal still stands (the slot still needs a post, the test still needs ads), and the next proposal is a new card that's actually different; sometimes the goal is dead too. A reply in the thread often says which. Your acknowledgement says which you took it to be. |
| A reply in the thread (feedback) | Change the proposal and post the next version: `approvals revise AP-0013 card.json`, with a `changes` field saying what changed. If you truly can't continue without one fact, ask instead: `approvals ask AP-0013 "<one question>"`. If the reply plainly approves the card as it stands ("post it"), record that with `approvals record AP-0013 --approved --words "<their words>" --where "Slack thread"` and act on it. If it plainly declines ("no, not now"), record it with `--declined` and drop it, so the card stops asking. |
| An answer to your question | Carry on, usually with `approvals revise`. |
| No answer by the deadline | It's not approved. Don't resubmit or send a reminder. |

A revised card replaces the old one; the approver sees the new version in the same thread, with a line in the channel pointing to it. Revise as often as their feedback needs, but each version must actually change what they asked about. A new version after a yes replaces that yes too: they approved what they saw, so wait for their answer on the new one. After a decline, a new proposal is a new card, not a version.

## Other commands

- `approvals inbox --agent "<name>"` lists answers waiting for you. If you run on a schedule, check it at the start of each run.
- `approvals withdraw AP-0013 "<why>"` takes a card down: it shows your reason and loses its buttons.
- `approvals record AP-0013 --approved --words "<their exact words>" --where "<where they said it>"` records a yes or no the approver gave outside Slack, so the card shows it.
- `approvals show AP-0013` shows one request. `approvals list --open` shows what's open.
