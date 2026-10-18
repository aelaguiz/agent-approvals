# Card types

Read the section for the type you're writing. Each one says what the approver is judging when they look at that kind of card, what the card must include, and gives a full example. The examples show the shape; your facts come from your own work.

Contents: [Fields](#fields) · [creative](#creative) · [reply](#reply) · [budget](#budget) · [purchase](#purchase) · [money](#money) · [player_message](#player_message) · [price](#price) · [experiment](#experiment) · [page](#page) · [content](#content) · [email](#email) · [account](#account)

## Fields

| Field | What it is |
| --- | --- |
| `type` | One of the types below. It sets the card's icon, its button labels and its default deadline. |
| `title` | The action, said plainly, as the approver would say it out loud. |
| `facts` | `[label, value]` pairs, one per line. Labels are short ("Cost", "Who gets it", "Undo"). |
| `approves`, `does_not_approve` | Exactly what a yes covers, and the nearby things it doesn't cover. |
| `quotes` | `{"label", "text", "link"?, "link_text"?, "note"?}`, for words shown exactly: a post being answered, a draft, an email body. |
| `table` | `{"columns": [...], "rows": [[...], ...]}`, for today beside proposed, or several items compared. No empty cells: write why a value is missing. |
| `image`, `video` | An absolute path to the thing being approved. A video gets a link that plays it. |
| `evidence` | Notes or absolute file paths, posted in the card's thread: checks run, sources, full lists, screenshots. |
| `changes` | Required on a revision: what changed from the last version, in the approver's terms ("Japan dropped, per your reply"). |
| `deadline_hours`, `yes_label`, `no_label` | Optional overrides. |

## creative

An ad, video, image or social post going out. The approver judges it by looking: does it look and sound like us, and is it where it should be?

- **Must include:** `image` or `video` (the creative itself), and facts for where it runs and what money it uses.
- **Good facts:** the placement, the audience or country, and the budget it sits inside. Say whether it's new money. Say what was checked (for example, the text in the video is legible and the app screens are real).

```json
{"type": "creative", "title": "Run this video ad in the spring creative test",
 "video": "/abs/path/ad-v3.mp4", "image": "/abs/path/ad-v3-thumb.png",
 "facts": [["Ad", "\"Learn the game in five minutes a day\" · 21 seconds · 9:16"],
           ["Runs on", "Instagram Reels and Stories · US only"],
           ["Money", "inside the test's $50 a day. No new money."],
           ["Checked", "captions legible on a phone · app screens recorded from the live app"]],
 "approves": "this ad, in the current test.", "does_not_approve": "more money, other countries, or later cuts."}
```

## reply

Words going out publicly as the approver or the brand: a forum or Reddit reply, a review reply, a comment. The approver judges the reply against what it answers: is it right, does it sound like them, and is mentioning the product appropriate?

- **Must include:** two `quotes`. First, what we're answering, with its link. Second, our exact reply.
- **Good facts:** what's already been said in the thread, what was checked, and why this one needs a yes and isn't covered by a standing rule.

```json
{"type": "reply", "title": "Post this reply on r/poker as our founder account",
 "quotes": [{"label": "They asked", "text": "I can't afford coaching. What should I study to stop losing at 1/2?", "link": "https://www.reddit.com/r/poker/comments/abc123/", "link_text": "How to stop losing at 1/2", "note": "3 hours ago · 9 comments"},
            {"label": "We'd reply", "text": "Pick one spot you play often and drill only that for a week..."}],
 "facts": [["Already said", "two people recommended books; nobody suggested practicing decisions"],
           ["Mentions the product", "yes, in the last sentence"],
           ["Checked", "the advice against two strategy books"]],
 "approves": "this exact text in this thread.", "does_not_approve": "a reworded version or other threads."}
```

## budget

Spend on ads or tests. The approver judges how much, for how long, what it buys or teaches, and when it stops.

- **Must include:** the cap before and after, and where spending stops (the date, the cap, or a result that ends it).
- **Good facts:** spend and results so far, with their comparison, and how to stop it early.

```json
{"type": "budget", "title": "Add $250 to the search ads learning budget",
 "table": {"columns": ["Budget", "Today", "After a yes"],
           "rows": [["Learning cap", "$450", "$700"], ["Spent so far", "$431", "$431"], ["Stop early if cost per trial is over", "not set", "$20"]]},
 "facts": [["What it buys", "about two more weeks of new ad tests at today's pace"], ["Undo", "stop any time; unspent money stays unspent"]],
 "approves": "up to $250 more, in this test.", "does_not_approve": "a monthly budget or other channels."}
```

## purchase

Buying a tool, subscription, credits, domains or a paid listing. The approver judges the price, whether it recurs, and whether it's worth it.

- **Must include:** facts for the cost (one-time or monthly), what it's for, and which card or account it goes on.
- **Good facts:** a cheaper option you considered, any catch (a locked card, an annual commitment), and how to cancel.

```json
{"type": "purchase", "title": "Buy 3 domains that point to our site: $45",
 "facts": [["Cost", "$45 one time; $45 a year to renew"], ["What they do", "each redirects to our main site"], ["Card", "the company card for web costs"]],
 "approves": "these 3 domains, up to $45.", "does_not_approve": "other domains or premium names."}
```

## money

Moving money, running payroll, paying or refusing a bill. The approver judges the amount, the accounts and the timing.

- **Must include:** facts for from, to, amount and when.
- **Good facts:** what the money is for, and how it compares to last time ("same people and amounts as the last payroll").

```json
{"type": "money", "title": "Move $20,000 from savings to operating",
 "facts": [["From", "savings"], ["To", "operating, which bills and payroll come out of"], ["Next out of operating", "payroll on the 15th, about $18,000"]],
 "approves": "this one transfer.", "does_not_approve": "any later transfer."}
```

## player_message

A push, email, in-app message or survey that reaches players or customers. The approver judges what people will see, how many will see it, and how often.

- **Must include:** a `quote` with exactly what they'll see.
- **Good facts:** who gets it and roughly how many, when and how often, what tapping it does, and what today's message does by comparison.

```json
{"type": "player_message", "title": "Add a first-year offer to the trial-ending message",
 "quotes": [{"label": "What they'd see (push, the day before the trial ends)", "text": "Your trial ends Thursday. Keep it: your first year at 40% off."}],
 "facts": [["Who gets it", "people who cancelled their trial · about 40 a week"], ["Today", "the same message at full price; 0 of 300 renewed"]],
 "approves": "this offer in this message, in English.", "does_not_approve": "other languages or other offers."}
```

## price

Prices, offers, discounts, or the words on a paywall. The approver judges each change next to what's there today.

- **Must include:** a `table` with today beside proposed, one row per thing changing.
- **Good facts:** why, what it's expected to bring with its range, which stores, and how to undo it.

```json
{"type": "price", "title": "Lower the yearly price in Brazil and Mexico",
 "table": {"columns": ["Country", "Yearly today", "Yearly new"], "rows": [["Brazil", "R$399.90", "R$219.90"], ["Mexico", "MX$1,299", "MX$899"]]},
 "facts": [["Why", "today's price is above what local competitors charge"], ["Brings", "about 2 more payers a month; money about level"], ["Undo", "today's prices are saved"]],
 "approves": "these two countries at these prices, on both stores.", "does_not_approve": "other countries or the free trial."}
```

## experiment

Turning a test or feature flag on or off, or changing who's in it. The approver judges what players will see, how many, and how to undo it.

- **Must include:** facts for what's being tested, the split, the exact flag, what players see, and how to undo it.
- **Good facts:** why now, and what goes wrong if nothing is done.

```json
{"type": "experiment", "title": "Keep version 2.5 users in the home screen test, 50/50",
 "facts": [["Testing", "the new home screen vs today's · 50/50 · flag home_enabled"], ["The problem", "2.5 isn't on the flag's version list, so its users leave the test when it ships"], ["Undo", "take the builds off the flag"]],
 "approves": "adding 2.5 to the test at 50%.", "does_not_approve": "releasing 2.5 to the stores."}
```

## page

A store listing, website page, directory listing or public profile. The approver judges each field next to what's there today.

- **Must include:** a `table` with each field changing, today beside proposed. For a visual page, add a screenshot as `image`.
- **Good facts:** why, what it's worth, and when it goes live.

```json
{"type": "page", "title": "Test a new store listing title",
 "table": {"columns": ["Where", "Today", "Proposed"], "rows": [["Store title", "Example - Learn", "Example - Play Hold'em"]]},
 "facts": [["Why", "Hold'em searches convert 2x better than our other terms"], ["When", "as soon as approved"]],
 "approves": "this title.", "does_not_approve": "the description or keywords."}
```

## content

Puzzles, lessons or other content going live to players. The approver judges what's going out, whether it was tested, and where it lands.

- **Must include:** facts for what and how many, where it was tested, and where it goes.
- **Good facts:** the dates, a sample in `evidence`, and how to pull it back.

```json
{"type": "content", "title": "Put 20 daily puzzles live for next month",
 "facts": [["What", "20 daily puzzles, one a day, the 1st to the 20th"], ["Tested", "all 20 played on staging"], ["Undo", "any day can be unscheduled before it goes out"]],
 "approves": "these 20 puzzles on these dates.", "does_not_approve": "changes to existing puzzles."}
```

## email

An email or Slack message sent in the approver's name. The approver judges the exact words and who receives them.

- **Must include:** a `quote` with the exact text, and facts for From and To (plus Subject for email).
- **Good facts:** what the message answers, and whether a follow-up is planned.

```json
{"type": "email", "title": "Send this email as you to our accountants",
 "quotes": [{"label": "The email", "text": "Hi,\nCould you send us last year's full record, including the workpapers?\nThanks"}],
 "facts": [["From", "your mailbox"], ["To", "our outside accountants"], ["Subject", "Last year's records"]],
 "approves": "sending this exact email once.", "does_not_approve": "follow-ups."}
```

## account

Deleting an account, removing someone's access, or cutting a seat. The approver judges who is affected, what goes, and whether it can be undone.

- **Must include:** facts for whose account it is, who asked, what goes, and whether it can be undone. Say plainly when it can't.
- **Good facts:** how the request was checked.

```json
{"type": "account", "title": "Delete a player's account, as they asked",
 "facts": [["Asked", "by email, 3 days ago"], ["Checked", "the request came from the account's own address"], ["What goes", "sign-in and all progress. This can't be undone."]],
 "approves": "deleting this one account and confirming by email.", "does_not_approve": "refunds or other accounts."}
```
