---
name: approvals
description: "Get a person's yes before an operational action by posting an approval card to Slack with the `approvals` command, then act on the answer when it arrives in this session as an [Approvals] message. Use when your instructions say an action needs someone's approval first, and that person isn't plainly the one giving you this task, so asking in this conversation wouldn't reach them: posting or replying publicly as them or the brand, ad or test spend, a purchase or money move, a price or offer change, a message to players or customers, an experiment or feature switch, a store listing or web page change, content going live, email or Slack in their name, or deleting an account. Also use when an [Approvals] message arrives, or to record a yes they gave somewhere else. Not for code reviews, merges, releases or deploys; not for actions a standing rule already lets you take; not for announcing finished work in Slack."
---

# Approvals

When an action needs your approver's yes first, put one card in front of them that they can decide from in a few seconds, keep working, and act on their answer exactly when it comes back. A good card gets a fast yes or a useful correction. A vague one gets "what is the question?" or no answer at all, and the work stalls.

## What matters most

- **Ask only when a yes is actually required.** If your instructions or a standing rule already let you act, act and report what you did. A card that didn't need asking spends the approver's attention and teaches them to skim cards.
- **A request to do it is not the approver's yes.** Tasks often come from a teammate, another agent, a brief or a schedule. Unless the approver is plainly the one telling you to do this, treat the instruction as a request that still needs their yes, not as permission. When they do tell you directly, that is the yes; don't send them a card about their own instruction.
- **The card is how the question reaches them.** Asking in this conversation only works if the approver is the one reading it. When it might not be them, don't ask "are you the approver?" or wait for a reply in chat; submit a card, and say in the conversation that you did.
- **Never take the action before the answer.** Only two things count as a yes. One is the card's approve button, which reaches you as an `[Approvals]` message saying it was approved. The other is the approver's own words naming this request, such as a reply on the card like "post it", or a yes they gave elsewhere that you record with `approvals record`. A friendly reply to a long report is not a yes.
- **Put the real thing on the card.** Show the exact reply under the post it answers, the ad itself, the price table with today beside proposed, the email word for word. If the approver would have to open something else to know what they're agreeing to, the card isn't ready.
- **Ask once.** Don't send reminders or post the same card again. No answer by the deadline means no.
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

Some types also need `quotes`, a `table`, or an `image` or `video`; the card types reference says which. Put the checks you ran, your sources and any long lists in `evidence`; they go into the card's thread, not onto the card.

Write it the way a busy owner reads:
- the action in the title;
- one fact per line;
- numbers next to what they compare with ("$700, up from $450");
- plain words, with no internal labels or codes the approver wouldn't use;
- no em dashes.

## When the answer arrives

Answers arrive as messages starting with `[Approvals]` that quote the approver's words exactly.

| The message says | Do this |
| --- | --- |
| It was approved | Do exactly what the card described, nothing more. Then run `approvals done AP-0013 "<what you did and how you checked it>"`. |
| It was declined | Drop it. Don't resubmit unless asked. |
| A reply in the thread (feedback) | Change the proposal and post the next version: `approvals revise AP-0013 card.json`, with a `changes` field saying what changed. If you truly can't continue without one fact, ask instead: `approvals ask AP-0013 "<one question>"`. If the reply plainly approves the card as it stands ("post it"), record that with `approvals record AP-0013 --approved --words "<their words>" --where "Slack thread"` and act on it. If it plainly declines ("no, not now"), record it with `--declined` and drop it, so the card stops asking. |
| An answer to your question | Carry on, usually with `approvals revise`. |
| No answer by the deadline | It's not approved. Don't resubmit or send a reminder. |

A revised card replaces the old one; the approver sees the new version in the same thread. Revise as often as their feedback needs, but each version must actually change what they asked about.

## Other commands

- `approvals inbox --agent "<name>"` lists answers waiting for you. If you run on a schedule, check it at the start of each run.
- `approvals record AP-0013 --approved --words "<their exact words>" --where "<where they said it>"` records a yes or no the approver gave outside Slack, so the card shows it.
- `approvals show AP-0013` shows one request. `approvals list --open` shows what's open.
