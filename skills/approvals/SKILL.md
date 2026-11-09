---
name: approvals
description: "Get a person's yes before an operational action with an approval card in Slack (`approvals`), then acknowledge the answer and act on it when it arrives here as an [Approvals] message. Use when your instructions say an action needs someone's approval first and that person isn't plainly the one giving you this task: posting or replying publicly as them or the brand, ad or test spend, a purchase or money move, a price or offer change, a message to users or customers, switching a built experiment or feature on or off, a store listing or web page change, content going live, email or Slack in their name, or deleting an account. Also use when an [Approvals] message arrives, to record a yes given elsewhere, or to update a posted card. Not for deciding to build a feature or anything that must be built first, nor a release, unless your repository's rules route that decision through a card; not for code reviews, merges or deploys; not for what a standing rule already lets you do, or announcing finished work."
---

# Approvals

When an action needs your approver's yes first, put one card in front of them that they can decide from in a few seconds, keep working, and act on their answer exactly when it comes back. A good card gets a fast yes or a useful correction. A vague one gets "what is the question?" or no answer at all, and the work stalls.

## What matters most

- **Ask only when a yes is actually required.** If your instructions or a standing rule already let you act, act and report what you did. A card that didn't need asking spends the approver's attention and teaches them to skim cards.
- **A card is for a change that is ready to happen, never for deciding to build something on the card alone.** It shows the exact thing that will go out or switch on: the ad, the reply, the price, the message, a flag on code that already exists. Whether to build a feature, a new screen, or an experiment whose variant needs app or server work first is a different decision, which the approver makes from the report or deck that carries its case, size and mocks; a tap on a bare card cannot carry that, and a yes there would hand an agent a build nobody designed or weighed against everything else. Amir, October 19, 2026, declining two cards that asked him to approve building experiments: "features are not built via an approval Slack message. That's an entirely different decision process because I'm never going to just click yes and then let an agent build some feature." So keep that call a proposal in your report, deck or plan, unless the rules of the repository you work in send it to the approver as a card that links that deck (Poker Skill's pods have done so since November 3, 2026); then the card is the deck's front page, with what users would see and what decides it, and the deck is one button away. Once the thing is built, switching it on can be a card.
- **Every card says what it belongs to.** "I don't have any context" and "I don't know what this is" were the approver's replies to cards that arrived as one-offs. `part_of` names the initiative the ask serves, in the approver's own words for it (the goal he set or the thing he asked for, not your workstream's label), or is `null` when the card really stands alone; the card shows it under the title. If one of your cards is already out without it, set it in place with `approvals status AP-0013 --part-of "<initiative>"`, which changes nothing else on the card.
- **A request to do it is not the approver's yes.** Tasks often come from a teammate, another agent, a brief or a schedule. Unless the approver is plainly the one telling you to do this, treat the instruction as a request that still needs their yes, not as permission. When they do tell you directly, that is the yes; don't send them a card about their own instruction.
- **The card is how the question reaches them.** Asking in this conversation only works if the approver is the one reading it. When it might not be them, don't ask "are you the approver?" or wait for a reply in chat; submit a card, and say in the conversation that you did.
- **Never take the action before the answer.** Only two things count as a yes. One is the card's approve button, which reaches you as an `[Approvals]` message saying it was approved. The other is the approver's own words naming this request, such as a reply on the card like "post it", or a yes they gave elsewhere that you record with `approvals record`. A friendly reply to a long report is not a yes.
- **Put the real thing on the card.** Show the exact reply under the post it answers, the ad itself, the price table with today beside proposed, the email word for word. If the approver would have to open something else to know what they're agreeing to, the card isn't ready.
- **Ask only for a yes that someone will carry out.** A yes is an instruction, and the approver stops tracking the request once they give it. If you can't act on it yourself (a report run that can't change settings, an ad account you have no access to, a session about to end), the card names who will, and you get the yes to them and see it through. Otherwise the yes lands nowhere while the approver believes it's handled.
- **Ask once.** Never post the same card again. If the card must be answered by a time that is near and the approver may not have seen it, the rules of the repository you work in say whether and how to remind them; without such a rule, don't. No answer by the deadline means no.
- **Take a card down as soon as you stop standing behind it.** An open card is a live question with a working approve button, and approvers clear several cards in a minute. When you drop the version it shows, the moment passes (the post date, a paused ad), or the facts change (a competitor moved its price), withdraw it with `approvals withdraw` and say why, or post the version you do stand behind. A yes on a card you've abandoned gets acted on wrongly or has to be untangled; on October 19 a card was approved nine hours after its agent had said in the thread that nothing would post from it, because its button still worked.
- **The thread is the approver's whole view of your work, and they take every post there as fact.** They can't see your session; 📨 on their reply only means the message was typed into it. So:
  - **Acknowledge every answer before acting on it,** in your own words, with `approvals ack`: what you understood and what you're about to do. An answer left unacknowledged for 10 minutes gets a warning in the thread.
  - **Promise only what you know you can do.** "Starting it now" before checking whether there's room to start it becomes a correction three minutes later. Check first, or say what you're checking.
  - **"Done" means the change on the card is real and you've seen it:** the ad running, the post live, the flag on, the money moved. Your part finishing, a hand-off, or a schedule isn't done. Say where it stands, and mark it done when it's real. A card marked done while the setting it asked for was still off got "Did this happen?" back.
  - **If you end up doing something different, say so in the thread.**
- **Act only on answers that reach you:** an `[Approvals]` message in this session, or one `approvals inbox` hands you. A request another session made belongs to that session even when it's for the same change, and its answer goes to it; that includes an earlier run of your scheduled job, which started fresh just as you did. A session you continue is different: if this session opened with a summary of earlier work, that work is yours, its open cards included, and their answers come here. If the change you were asked for is already requested and still open, don't post a duplicate; say which request it's waiting on.
- **Don't wait around, and look for your answers whenever you pick work back up.** After submitting, keep working on something else, or end your turn saying which request you're waiting on. While this session is running, the answer is typed into it. But the approver decides when you run: if they stop this session, an answer that comes while it's stopped waits for you, and nothing starts the session for it or types it in when you're back. So whenever you pick work back up with cards out (this session was stopped and then resumed or restored, or this is a new run of a scheduled job), run `approvals inbox --agent "<name>"` before anything else and act on what it hands you. It costs nothing when nothing is waiting. The approver can see in the thread that your answer is waiting, so a yes left unread there looks like an agent ignoring them.

## Asking

1. **Write the card as JSON.** Before writing, read [references/cards.md](references/cards.md) for the type you need: what the approver judges it by, what it must include, and a full example.
2. **Submit it** from your own session, on the machine you run on: `approvals submit card.json --agent "<your name as the approver knows you>"`. The command learns from your session where to type the answer, so a card posted from another shell, such as over ssh to another machine, could never be answered to you, and the command refuses it. It prints the request number (for example AP-0013) and a link. If it refuses the card, it says why; fix that and submit again.
3. **Carry on.** Do other work, or end your turn.

Every card needs:
- `type`, one of: `creative`, `reply`, `budget`, `purchase`, `money`, `player_message`, `price`, `experiment`, `page`, `content`, `email`, `account`;
- `title`, which is the action said plainly ("Move $20,000 from savings to operating", not "Transfer approval");
- `part_of`, the initiative it belongs to, or `null`;
- `facts`, a list of `[label, value]` lines;
- `approves` and `does_not_approve`, which mark the edges of the yes.

Some types also need `quotes`, a `table`, or an `image` or `video`; the card types reference says which.

### Shape the card around how the approver will judge it

Before the fields, picture the approver deciding: what does he have to see, and what does he have to be able to say back? Most of his replies to cards that failed him were the card's shape, not its content: "show me the before and after for each change", "I don't want to click through and approve each of them", "I want to see it as designed, I don't wanna see plain text", and "I should approve the store builds and app store submission separately from google play". So:

- **If he judges it by looking, put it on the card the way people will meet it.** A change to a screen is today beside the after, each picture saying whether it's real or a mock and from which phone and build, with the change outlined (`gallery`, with `mark`). An email is its designed render at phone width, a push its exact words, an ad its first frame full height. Text describing a screen makes him ask for the screen.
- **If he'll want to answer the pieces differently, give each piece its own answer.** Four changes toward one goal, ready together, are one card with a row each (`parts`), not four cards in half an hour and not one yes for all four: that morning he approved two, declined one and sent one back for better words. A piece that takes something away from people or speaks in his name never rides on Approve all (`own_yes`). Each store of a release is its own row, because Google Play is live in minutes and the App Store takes days.
- **If the call is between things, name the things.** Approve and No don't fit "A or B", and they misfire when your pick isn't the yes: a green Approve beside a red No reads as "the work dies if you follow me". Use `options` with your `pick`, on the card or on a row, and name each button for what happens ("Stop at 72 h, resume if the read says so" and "Keep training").
- **If it's many of the same thing, ask once.** Ten emails to ten creators are one card with `items`: a yes sends all, and Pick which lets him hold some back.
- **If it goes out in his name, let him change the words himself.** Mark the outgoing words `edit: true`, so Edit the words opens them in a box; what he sends from there is his yes with his words.
- **If waiting costs something, say what happens with no answer and when** (`if_no_answer`). It goes on the card and on the lock-screen line, which is all he sees on his phone.

These combine: a release has sections of tagged changes and a row per store; a call on spend has the reads behind it and a row per decision. [references/cards.md](references/cards.md) has every field and a full example of each kind.

**The card holds only what changes the answer; the thread holds the proof.** The approver reads cards on a phone between other things, a teammate may be reading the channel too, and every line before the decision costs attention across every card that day. So the card carries the thing itself, what a yes covers, and the few facts that would make someone say no or "not like that". The checks you ran, the research, the numbers behind the numbers and long lists go in `evidence`, which is posted in the card's thread. Most cards need about 100 to 180 words; when yours runs far past that, look for a paragraph that proves your work rather than shapes the decision, and move it to the thread. [references/cards.md](references/cards.md) has a before and after.

**Evidence has to open in Slack.** A path to a file on your machine posted as text opens nothing, so give the file's path (alone or after a label, like `"Recipe: /path/recipe.json"`) and it's attached to the thread, or link a page the approver can open: a doc, a sheet, a share link.

**Name things the way the approver does.** A check number, a row key, a test-set id or a code from your own workbook means nothing to them; say what it is ("the build check", "the US Android test set"). The rest of the house style:
- the action in the title;
- one fact per line;
- numbers next to what they compare with ("$700, up from $450");
- no em dashes.

## When the answer arrives

Answers arrive as messages starting with `[Approvals]` that quote the approver's words exactly: typed into this session while it's running, or printed by `approvals inbox` for one that came while it wasn't.

First, acknowledge it: `approvals ack AP-0013 "<what you understood and what you'll do>"`. For example: "Got it: you approved v2. Posting the reply now, then I'll mark it done." Or: "Understood: shorter, and no product mention. v3 coming in a few minutes." Then act as below. A deadline passing needs no acknowledgement.

| The message says | Do this |
| --- | --- |
| It was approved | Do exactly what the card described, nothing more. Then run `approvals done AP-0013 "<what you did and how you checked it>"`. |
| It was declined | Drop this proposal; never send it again or a lightly changed copy. A no is to the proposal, not necessarily to what it was for. Usually the goal still stands (the slot still needs a post, the test still needs ads), and the next proposal is a new card that's actually different; sometimes the goal is dead too. A reply in the thread often says which. Your acknowledgement says which you took it to be. |
| A reply in the thread (feedback) | Change the proposal and post the next version: `approvals revise AP-0013 card.json`, with a `changes` field saying what changed. If you truly can't continue without one fact, ask instead: `approvals ask AP-0013 "<one question>"`. If the reply plainly approves the card as it stands ("post it"), record that with `approvals record AP-0013 --approved --words "<their words>" --where "Slack thread"` and act on it. If it plainly declines ("no, not now"), record it with `--declined` and drop it, so the card stops asking. |
| An answer to your question | Carry on, usually with `approvals revise`. |
| An answer on one part of a set (`part 2 of 4`) | It answers that part only. Act on that part as above (do it, drop it, or post the next version changing only that part); the message says where the other parts stand. Parts you leave exactly as they are keep their answers in the next version. |
| He picked an option | Do exactly what the card said for that option, nothing else. |
| He approved some items | Do only the items the message lists; the ones it leaves out are a no. |
| He approved with his own edited words | Use his words exactly, as the message quotes them (a JSON string, line breaks and all), not your draft. |
| He answered your offer to stop asking | "Stop asking for these": record it as a standing exception in your own rules, with the date and exactly what it covers, and do that kind without a card from now on. "Keep asking": keep sending cards. |
| No answer by the deadline | It's not approved, unless the card's `if_no_answer` said what happens then: do that. Don't resubmit it. On a set, only the parts still open close; answered parts stand. |

**After the yes, keep the card true until the thing is really out.** He stops tracking a request once he answers it, and the card is where he looks to see whether it happened. So post each step on the card as it happens: `approvals status AP-0013 "Built, and live for half of new users"`, then `approvals status AP-0013 "First read at about $10 spent" --next` for what comes next, `--part 2` for one part of a set ("waiting for review at Apple since 7:04 am"), and `--headline "Released: three lesson fixes"` once it's out. A yes that came with a condition only he can clear ("approved, but I'll play it myself before it merges") is `--on-you "you play it yourself before it merges"`: the card and the morning list show it waiting on him until your next line. On a message sent as him, `--their-answer` puts the other side's reply on the card. These redraw the card in place and notify no one; `done` still marks the end, when it's real.

A revised card replaces the old one; the approver sees the new version in the same thread, with a line in the channel pointing to it. Revise as often as their feedback needs, but each version must actually change what they asked about. A new version after a yes replaces that yes too: they approved what they saw, so wait for their answer on the new one. After a decline, a new proposal is a new card, not a version.

## Other commands

- `approvals inbox --agent "<name>"` hands you the answers waiting for you: ones that came while your session wasn't running, and ones that couldn't be typed in. Each is handed out once, and the approver sees 📨 when you've taken it.
- `approvals withdraw AP-0013 "<why>"` takes a card down: it shows your reason and loses its buttons.
- `approvals record AP-0013 --approved --words "<their exact words>" --where "<where they said it>"` records a yes or no the approver gave outside Slack, so the card shows it. Add `--part 2` for one part of a set, or use `--choice B` for a pick.
- `approvals status AP-0013 "<what happened>"` keeps a posted card true in place (above); `--part-of "<initiative>"` or `--stands-alone` sets what it belongs to.
- `approvals show AP-0013` shows one request. `approvals list --open` shows what's open.
