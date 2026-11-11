# Cards

Two things shape a card. Its **type** says what the action is: read the section for your type below for what the approver judges and what the card must include. Its **kind** is how he'll judge it and answer it: a yes or no, today beside after, a row each, a pick, a batch. The kinds are combinations of the fields below and work with any type. The examples show the shape; your facts come from your own work.

Contents: [Fields](#fields) · [What goes on the card](#what-goes-on-the-card-and-what-goes-in-the-thread) · [Kinds](#kinds-how-he-judges-it-and-answers-it) · [creative](#creative) · [reply](#reply) · [budget](#budget) · [purchase](#purchase) · [money](#money) · [player_message](#player_message) · [price](#price) · [experiment](#experiment) · [page](#page) · [content](#content) · [email](#email) · [account](#account)

## Fields

| Field | What it is |
| --- | --- |
| `type` | One of the types below. It sets the card's icon, its button labels and its default deadline. |
| `title` | The action, said plainly, as the approver would say it out loud. |
| `why` | Required. One or two sentences, drawn first under the title: what the business gets and why now, in the approver's words. For a test, what its result decides. Under 400 characters. |
| `facts` | `[label, value]` pairs. Each is drawn as its own block with its label above it, so keep labels short ("Cost", "Who gets it", "What it takes away") and values to a sentence or two. A value that is a list puts each item on its own line starting with "- ", and they show as bullets. |
| `approves`, `does_not_approve` | Exactly what a yes covers, and the nearby things it doesn't cover. |
| `quotes` | `{"label", "text", "link"?, "link_text"?, "note"?}`, for words shown exactly: a post being answered, a draft, an email body. |
| `table` | `{"columns": [...], "rows": [[...], ...]}`, for today beside proposed, or several items compared. No empty cells: write why a value is missing. |
| `image`, `video` | An absolute path to the thing being approved. A video gets a link that plays it. Pictures go up at their own resolution, and See it and Full size open them in Slack's viewer, so a small picture stays small there. |
| `evidence` | A list of notes, links and file paths (or one block of text), posted in the card's thread: checks run, sources, research, full lists, screenshots. A file path, alone or after a label (`"Recipe: /abs/path/recipe.json"`, or a path relative to your folder), is attached so it opens in Slack. |
| `changes` | Required on a revision: what changed from the version he saw, in the approver's terms ("Japan dropped, per your reply"). It opens the new card, and the old version shrinks to a line, so it is how he learns what is new. |
| `deadline_hours`, `yes_label`, `no_label` | Optional overrides. Name buttons for what happens ("Release all 3", "Send as you", "Run the read"). |
| `part_of` | Required. The initiative the card belongs to, in the approver's words for it, or `null` when it stands alone. Shown under the title. |
| `if_no_answer` | What happens if the deadline passes with no answer ("it stops at 72 hours"). Shown with the deadline in the small line under the buttons and on the notification line; told back to you if it expires. |
| `gallery` | Pictures, each `{"image": "/abs/path.png", "label": "…", "note": "…", "mark": [x, y, w, h], "video": "/abs/clip.mp4"}`. The label says what it is: today or after, real or mock, which phone and build (under 150 characters). `mark` outlines what changed, in the picture's pixels; mark the same place on the today picture so the two line up. `video` adds a Play button (a clip of motion). One picture shows full width; two to ten are a swipeable carousel, each slide zoomed onto its mark (or showing the whole picture) with a Full size button. The pictures also go in the card's thread, full size. |
| `sections` | Titled groups of lines: `{"title": "What the reads found", "lines": [...]}`. A line is text, `[label, value]`, or `{"text": "…", "tag": "Updaters"}`. Start a line with ✅, ⚪ or ⏳ when that helps him read it. |
| `links` | Up to 5 `[label, "https://…"]` buttons that open a page, beside the answer buttons: the deck, the reads, the clips. |
| `parts` | Rows answered one by one: `{"title", "note", "quotes", "image", "yes_label", "no_label", "options", "pick", "own_yes", "id"}`. Up to 10. The card numbers the rows, so titles don't need numbers. A row's `quotes` (`{"label", "text"}`, like their message and your reply) are drawn under its title, right above its buttons. Each row gets its own buttons (Approve and No unless you name them), and a row with a picture gets a See it button. Approve all appears while two or more plain rows are open; `own_yes: true` keeps a row out of it. A revision keeps the answer of every row you leave exactly as it was (matched by `id`, else position). |
| `options`, `pick` | A choice: `{"name": "A", "title": "a panel from the hand count", "image": "/abs/a.png"}`, and the name of your pick. On the card, options with pictures sit side by side, each with its own Pick button; on a row, they're buttons named by `name`. `no_label` adds a decline ("Neither"). |
| `items`, `pick_label` | A batch: each item its name or `[name, what differs]`. A yes covers all of them; Pick which opens a checklist whose button (`pick_label`, like `"Send {n}"`) sends the ticked ones. |
| `change_label` | Adds a button ("Change", "Change this fix") that opens a box for what should change; on a set, one on every row. His words reach you as feedback and go in the thread. |
| `from` | Who it goes out as ("your X account @handle"), shown first. |
| quote `edit: true`, `hint` | The words that go out. Adds Edit the words, which opens them in a box (`hint` under it); what he sends from there is his yes, with his words. One per card, under 3,000 characters. |
| quote `button` | Puts the quote behind a button with that label instead of on the card: only for words he might want but won't decide on, like the whole thread behind the line you quote. Never what a yes sends or decides, and refused on a card with rows. |
| `offer` | After a yes, asks whether to stop asking for this kind: say exactly what would stop ("first emails to creators with a free year of Plus"). His answer comes back to you. Never on money: he wants to approve each spend. |

One card is answered one way: plain yes or no, `options`, `parts` or `items`. Slack holds 50 blocks a message, so a card with too much on it is refused with a count; move detail to `evidence`, or send a set.

## One card or a set

A card is one decision he makes from what's on it, with the buttons right under what he just read. So for each thing you'd ask, the question is whether what decides it fits right above its buttons. A line or two (a store, a small change shown in its picture, a read with its cost) is a row. A reading of its own (a reply to a person, a design he judges whole, a plan, a spend whose case takes more than a few lines) is a card of its own. Proof that you did the work never settles it either way: it goes in `evidence`, in the thread. Things that belong together arrive together, as rows on one card or as a set of cards; things that don't belong together never share a card, whatever minute they're ready.

Where it went each way, all from cards in November 2026:

- **A set, not rows.** Four people waiting on answers about charges, each with their message and the reply you'd send as him. On one card with a row each, the replies didn't fit in the rows, so they sat behind four Read buttons at the bottom and each row said "The reply is Reply 1 below": "I would rather get separate approval cards than have this weird homework." As four cards sent together, each holds their words, your reply and its own Send and No, and each is answered in one look.
- **Together, not one by one.** Four small changes toward one goal came as four cards over 34 minutes. He answered them in nine as the set they were (two approved, one declined, one sent back for better words) and asked that related asks "come together rather than individually." Each was judged from its before-and-after picture and a line, so they're rows on one card; had each needed its own reading, a set.
- **Rows.** A release's server, Google Play and iPhone. Each is a line, and he answers them differently because the stores leave him different time to catch a problem.
- **Rows, once the proof moved out.** Two reads that decide one plan. As rows of 300 words each (the method, the counts behind the counts, what each fallback costs) they were a wall; as two or three lines each (what it finds, what it changes, what it costs), with the method in `evidence`, he answers each where he reads it.
- **One yes, not four rows.** Four small checks toward one purpose at a few dollars each, which he had approved one by one until he answered one that rose to about $15 with "i don't care $15 is cheap". One row, "Run all four", listing them inside it.
- **One card, not a set.** Ten emails from one template, only the names and links differing: one reading, so one card with `items` and the email on the card. Ten emails each written for its person are ten readings, so a set.
- **Not on this card at all.** A card asking to ship a release also carried another team's two decisions and six sections of what was in it, each line tagged with card numbers: "I can't tell if this is all stuff thats ready to go and you're asking if it can ship or if you're asking to build something". A ship card asks only to ship and says up front that everything on it is built and tested; what players get is a plain list, and another team's decision comes on that team's own card.
- **Two cards, not a set.** A price change and an ad, ready in the same minute. A set tells him these go together; unrelated cards just post one after the other, each from the agent whose work it is, so its answer comes back to that agent.

A set is one piece of work he'd decide in one sitting, so its cards share `part_of` and go out in one `approvals submit a.json b.json`. It isn't a day's queue: a set so long he'd lose which card he's on is two pieces of work.

## What goes on the card, and what goes in the thread

The card carries what decides the answer; the thread carries what proves you did the work. Here is one fact line from a card that asked to run a still ad as a short video, before and after.

Before, on the card (one of nine fact lines, 530 words in all):

```json
["What the ad platform says", "The platform does this itself. Its image animation tool turns one still into a short video: last year it said nearly 2 million advertisers were using it and a related video tool, and this spring that use was growing fast and the tool now runs in every placement. Its only performance number is for the related tool, which makes a video from several pictures: over 3% higher conversion rates in its own tests."]
```

After, on the card:

```json
["Why a video", "the platform barely shows our stills: 4 impressions in 5 hours, against 9 to 110 an hour for the videos beside it"],
["Research", "the platform's own tool turns stills into videos for about 2 million advertisers; details in the thread"]
```

And the paragraph moves to `evidence`, with the link to its source. The approver still sees why, and can open the research when they want it.

## Kinds: how he judges it and answers it

Before the fields, shape the card around how he will judge it. Picture the approver deciding: what does he have to see, and what does he have to be able to say back? Most of his replies to cards that failed him were the card's shape, not its content: "show me the before and after for each change", "I don't want to click through and approve each of them", "I want to see it as designed, I don't wanna see plain text", and "I should approve the store builds and app store submission separately from google play". So:

- **If he judges it by looking, put it on the card the way people will meet it.** A change to a screen is today beside the after, each picture saying whether it's real or a mock and from which phone and build, with the change outlined (`gallery`, with `mark`). An email is its designed render at phone width, a push its exact words, an ad its first frame full height. Text describing a screen makes him ask for the screen. Each picture also has to be readable when he opens it full size and zooms in, because the card shows it as a thumbnail and the picture itself is all he has: a screen at the phone's own resolution, a slide or mock exported at two or three times the size it's shown at, and cut to what the row is about rather than a whole slide around it. Rows 2 to 4 of the coaching-at-the-table card on November 9 were whole deck slides at 1,100 pixels, six phone screens to a slide, and nothing in them could be read at any zoom ("I can't tell shit from a blurry small image"); the one screen that changed, at full resolution, could. A picture that only sets the scene, like a hand of six moments shown as one strip, is the exception that reads at a glance, so it needs size but no crop.
- **If he'll want to answer the pieces differently, give each piece its own answer:** a row (`parts`) when a line or two decides it, its own card in a set when it needs its own reading ([One card or a set](#one-card-or-a-set)). A row's own words (their message, your reply) go in its `quotes`, drawn above its buttons. A piece that takes something away from people or speaks in his name never rides on Approve all (`own_yes`).
- **If the call is between things, name the things.** Approve and No don't fit "A or B", and they misfire when your pick isn't the yes: a green Approve beside a red No reads as "the work dies if you follow me". Use `options` with your `pick`, on the card or on a row, and name each button for what happens ("Stop at 72 h, resume if the read says so" and "Keep training").
- **If it's many of the same thing, ask once.** Ten emails to ten creators are one card with `items`: a yes sends all, and Pick which lets him hold some back.
- **If it goes out in his name, let him change the words himself.** Mark the outgoing words `edit: true`, so Edit the words opens them in a box; what he sends from there is his yes with his words.
- **If waiting costs something, say what happens with no answer and when** (`if_no_answer`). It goes on the card and on the lock-screen line, which is all he sees on his phone.

These combine: a release has what players get and a row per store; a call on spend has the reads behind it and a row per decision. The kinds below show each.

Each example is made up, modelled on a real card that failed in its old shape.

### Before and after

Anything users will see change: a screen, words in the app, a lesson, a store listing. He judges it by comparing, so each change is today's real screen beside the after, the change outlined.

```json
{"type": "content", "title": "Release three lesson fixes to every user",
 "why": "Three lessons teach a step wrong or out of order today, and every user who plays them learns it that way.",
 "part_of": "Today's lessons teach what users need",
 "gallery": [
   {"image": "/abs/fix1-today.png", "label": "Fix 1 of 3 · TODAY · real screen, iPhone, 2.1.65", "mark": [220, 200, 455, 225]},
   {"image": "/abs/fix1-after.png", "label": "Fix 1 of 3 · AFTER · real screen, staging", "note": "New: this step comes between step 4 and the board-only question.", "mark": [220, 200, 455, 225]},
   {"image": "/abs/fix2-today.png", "label": "Fix 2 of 3 · TODAY · real screen, iPhone, 2.1.65"},
   {"image": "/abs/fix2-after.png", "label": "Fix 2 of 3 · AFTER · real screen, staging"}],
 "facts": [["Who gets it", "every user on today's lessons, app versions back to 2.1.38, in all seven languages"]],
 "yes_label": "Release all 3", "change_label": "Change this fix", "no_label": "No",
 "approves": "these three fixes to today's lessons.", "does_not_approve": "the new Learn path test."}
```

### A set of related changes, each with its own answer

Several changes toward one initiative, ready around the same time, each decided from its picture and a line. One card, a row each, so he can approve two, decline one and send one back in the same minute.

```json
{"type": "content", "title": "Payers keep using what they paid for: 4 changes",
 "why": "Payers who use Plus in their first two weeks keep paying, and each of these is a place they stall or are told something untrue.",
 "part_of": "Payers keep using what they paid for (your ask, 7:21 am)",
 "facts": [["Done looks like", "payers active in more of their first two weeks, read against the 30% held back"]],
 "links": [["Open the deck", "https://docs.google.com/presentation/d/…"]], "change_label": "Change",
 "parts": [
   {"title": "Right after paying, take payers to what they came for", "note": "Everyone who starts Plus", "image": "/abs/after-paying-before-after.png"},
   {"title": "Practice vs AI stops calling itself a beta", "note": "Everyone who opens Play", "image": "/abs/no-beta-before-after.png"},
   {"title": "The paywall stops promising \"no ads\"", "note": "Every paywall, and 14 App Store subscription descriptions", "image": "/abs/paywall-before-after.png"},
   {"title": "Tickets stops selling Plus to people who have it", "note": "Plus users who open Tickets", "image": "/abs/tickets-before-after.png"}],
 "approves": "each row you approve, as shown.", "does_not_approve": "rows you decline or change, or anything else in this work."}
```

When he asks for a change on row 3, post the next version with only row 3 changed (and `changes` saying so): rows 1, 2 and 4 keep their answers, and row 3 comes back marked new with its buttons.

### Cards that go together, each needing its own reading

Four replies to four people, each with their words and yours. One file per card, sent in one command so they arrive together, marked "1 of 4" to "4 of 4". Each card stands on its own: he may answer the third first.

```json
{"type": "email", "title": "Reply to a yearly charge after a cancelled trial",
 "why": "This person cancelled their trial the day before they were charged the yearly fee, and has waited 14 days for an answer.",
 "part_of": "Answer people waiting on us about a charge",
 "from": "your company address, in their thread",
 "quotes": [{"label": "Their words", "text": "I cancelled my trial yesterday and was still charged for the year."},
            {"label": "Your reply", "text": "Hi,\n\nSorry this sat so long. …\n\nAmir", "edit": true}],
 "facts": [["Why trial users get charged before they can cancel", "the store takes the first payment about 8 hours before a trial ends, and our reminder gives only the date; fixing the reminder is its own card"]],
 "yes_label": "Send", "change_label": "Change",
 "approves": "sending this reply once.", "does_not_approve": "a refund from us: the store handles these."}
```

```
approvals submit reply-1.json reply-2.json reply-3.json reply-4.json --agent "Revenue"
```

### A call on spend, with the reads behind it

Money where the call turns on reads. Lead with what the reads found, put the clock on it, and give each decision its own row named for what happens.

```json
{"type": "budget", "title": "Keep training the run past 72 hours, for a policy with fewer holes?",
 "why": "The read decides whether more training is worth about $200 a day: it measures how many of the AI's untrained spots more days would close.",
 "part_of": "Five players, trained in a week",
 "if_no_answer": "it stops at 72 hours.",
 "sections": [{"title": "What the reads found", "lines": [
   ["✅ Strength", "about as fast as plain training (0.96 to 1.27 times)"],
   ["✅ Holes", "cut games with a hole by 23% and 41%, against plain training at equal hours"],
   "⏳ Do the near-even moves give up money? The read below answers it by about 7 am"]}],
 "facts": [["The run has cost", "about $500 at list price"]],
 "parts": [
   {"title": "The near-even moves read · $80 to $90", "yes_label": "Run the read", "no_label": "Don't"},
   {"title": "The run past 72 hours · about $200 a day", "note": "Training on until the read: about $100 per 12 hours. Stopping and resuming: about 45 minutes.",
    "options": [{"name": "Stop at 72 h, resume if the read says so"}, {"name": "Keep training"}], "pick": "Stop at 72 h, resume if the read says so"}],
 "approves": "the read, and what you choose for the run.", "does_not_approve": "any other run or more money."}
```

### A batch of the same thing

Many asks of one kind. One card; a yes covers all, and he can hold some back.

```json
{"type": "email", "title": "Send 10 emails as you, each with a free year of Plus",
 "why": "Creators who play Poker Skill on camera bring their audiences, and a free year is the cheapest way to get them playing it.",
 "part_of": "Poker creators use Poker Skill",
 "facts": [["From", "your company address. The same email to each, with their name and their own link, every link tested on iPhone and Android"]],
 "items": [["River Rat Poker", "Instagram Reels · follow-up"], ["Two Pair Tom", "YouTube vlog"], "The Grind Podcast (Sam)", "…"],
 "quotes": [{"label": "The email", "text": "Hi {name}, …"}],
 "yes_label": "Send all 10", "pick_label": "Send {n}",
 "offer": "You've said yes to all 48 creator messages you've answered this month. Stop asking for first emails to creators with a free year of Plus?",
 "approves": "sending these emails once each.", "does_not_approve": "follow-ups or other creators."}
```

### Something going public

An ad, a Short, a Reel, a post. He judges the whole thing as people will see it, and what it costs to find out whether it works.

```json
{"type": "creative", "title": "Run a new ad: \"You're not unlucky. You were never taught why.\"",
 "why": "We need a second ad that brings paying iPhone users, and this one competes with the newscast ad for that slot.",
 "part_of": "A second ad that brings paying iOS users",
 "video": "/abs/ad.mp4", "image": "/abs/ad-first-frame.png",
 "facts": [["To read it", "about $10. It's a finalist at 0.5 or more installs per dollar"],
           ["🟠 Still open", "nobody has listened to it yet"], ["Competing with", "the newscast, for the second finalist place"]],
 "approves": "running this ad in the iOS test at its $10 read.", "does_not_approve": "more money or other placements."}
```

### Words to users, as they'll see them

A push, an email, an in-app message: the words as they land, the designed render at phone width, who gets it and when.

```json
{"type": "player_message", "title": "Tell people on a year of Plus when their iPhone trial's charge fails",
 "why": "People whose charge fails lose Plus without knowing why, and telling them keeps the ones who meant to pay.",
 "part_of": "Payers keep using what they paid for",
 "quotes": [{"label": "The push, on their lock screen", "text": "Your trial's charge didn't go through\nYour year of Plus is still on. …"}],
 "gallery": [{"image": "/abs/email-iphone-width.png", "label": "The email, as designed · real render at iPhone width"}],
 "facts": [["Who gets it", "anyone on a year of Plus whose iPhone trial charge failed in the last 60 days"],
           ["When", "the email an hour after the failure; the push after it, 9 am to 9 pm their time"], ["Where a tap goes", "the subscription screen"]],
 "approves": "this push and email, in every language.", "does_not_approve": "other messages to payers."}
```

### A release

It asks only to ship, and says so: everything on it is built and tested. What players get is a plain list by when they get it, with no card numbers; each store is its own yes; anything else that needs a decision comes on its own card from whoever owns it.

```json
{"type": "content", "title": "Ship 2.1.65: your move shows the moment you tap",
 "why": "It puts today's coaching and fixes in players' hands. Everything on this card is built, merged and tested: a yes ships it, and nothing here asks to build anything.",
 "part_of": null,
 "sections": [
   {"title": "What players get", "lines": [
     ["Everyone, when the server deploys", "the evening Missions push stops reaching players who already finished"],
     ["Everyone who updates", "their move shows the moment they tap"],
     ["In the build, off until its test starts", "the new report card"]]}],
 "facts": [["QA", "ship it"], ["⚠️ Known and going out", "after sign-in, Home can offer the wrong next lesson for half a second (#9818)"],
           ["🛑 Can't be undone", "an hourly sweep deletes finished hand records older than a day"]],
 "parts": [{"title": "Server", "own_yes": true},
           {"title": "iPhone, to App Review", "own_yes": true, "note": "Manual release: when Apple approves, this row asks again."},
           {"title": "Google Play, 100%, live in minutes", "own_yes": true, "note": "QA tried Play's own build against production"}],
 "approves": "each store you approve, as listed.", "does_not_approve": "anything not in the list."}
```

### A message sent as him

The chat as it will look, sent as him, with the words his to edit.

```json
{"type": "email", "title": "Send this X message as you to River Rat Poker",
 "why": "They said they'd try it, and their free-year link no longer needs an email, so this gets them in today.",
 "part_of": "Poker creators use Poker Skill",
 "from": "your X account",
 "quotes": [{"label": "River Rat Poker", "note": "Nov 3, 11:29 AM", "text": "Appreciate you setting that up, I'll take a look and see how it works."},
            {"label": "Your reply", "text": "Good news, no email needed anymore. …\n[their own free-year link]\nNo strings on it.", "edit": true, "hint": "Their link goes where the brackets are."}],
 "facts": [["✅ Their link tested", "App Store on iPhone, Google Play on Android"]], "yes_label": "Send as you",
 "approves": "sending this reply once.", "does_not_approve": "follow-ups."}
```

When they reply, `approvals status AP-0013 --their-answer "<their reply>"` puts it on the card.

### A choice between options

Two or three designs or wordings. Each option as a picture with its own button, your pick marked, and why in a line.

```json
{"type": "content", "title": "Pick how users see the hand they just played",
 "why": "How the last hand opens decides whether players look back at it between hands, which is where the coaching sticks.",
 "part_of": "Coaching at the table feels smooth",
 "options": [{"name": "A", "title": "a panel from the hand count", "image": "/abs/option-a.png"},
             {"name": "B", "title": "a rail of past hands", "image": "/abs/option-b.png"}],
 "pick": "A", "no_label": "Neither", "links": [["Play both at real speed", "https://…"]],
 "facts": [["Our pick is A", "it answers a tap users already make, and keeps every hand; B's strength is each result at a glance"]],
 "approves": "building the option you pick.", "does_not_approve": "the other options, or releasing it."}
```

If he picks A "but I'll play it myself before it merges", that condition is `approvals status AP-0013 --on-you "you play it yourself before it merges"`.

### A test to build and read

Only where your repository's rules send a build decision to a card that links its deck. What users see in each half, what decides it, what stops it, and what it holds up.

```json
{"type": "experiment", "title": "Build and test two screens on the onboarding paywall",
 "why": "The test decides which paywall new users see: if the test half brings more payers by day 8, it goes to everyone; if not, today's stays.",
 "part_of": "More new users pay when they first see Plus",
 "gallery": [{"image": "/abs/test-half.png", "label": "TEST HALF · mock"}, {"image": "/abs/today.png", "label": "Today · real"}],
 "facts": [["Who", "half of new users"], ["Decides it", "payers by day 8: about 17 to 35 days, early reads at about 3 and 10"],
           ["Stops it", "about 1.1 fewer trials per 100 users shown, visible in about 9 days"], ["Holds", "this screen until about mid-December"],
           ["Replaces", "the design you sent back at 7:23 pm"]],
 "links": [["Open the deck", "https://…"]], "yes_label": "Build and test", "change_label": "Change",
 "approves": "building and running this test, and acting on its read within the deck's bar.", "does_not_approve": "ad spend or any other paywall."}
```

Every read then lands on the same card: `approvals status AP-0013 "Early read, day 3: no harm to trials"`.

## creative

An ad, video, image or social post going out. The approver judges it by looking: does it look and sound like us, and is it where it should be?

- **Must include:** `image` or `video` (the creative itself), and facts for where it runs and what money it uses.
- **Good facts:** the placement, the audience or country, and the budget it sits inside. Say whether it's new money. Say what was checked (for example, the text in the video is legible and the app screens are real).

```json
{"type": "creative", "title": "Run this video ad in the spring creative test",
 "why": "The spring test decides which ads we put money behind, and this one tries a promise we haven't run: learning in five minutes a day.",
 "part_of": "Ads that bring paying users",
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
 "why": "People asking how to stop losing are who we're for, and a useful answer from the founder brings them to us.",
 "part_of": "People who ask how to improve find us",
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
 "why": "The learning budget is nearly spent, and the new ad tests it pays for are how search ads get to paying for themselves.",
 "part_of": "Search ads that pay for themselves",
 "table": {"columns": ["Budget", "Today", "After a yes"],
           "rows": [["Learning cap", "$450", "$700"], ["Spent so far", "$431", "$431"], ["Stop early if cost per trial is over", "not set", "$20"]]},
 "facts": [["What it buys", "about two more weeks of new ad tests at today's pace"]],
 "approves": "up to $250 more, in this test.", "does_not_approve": "a monthly budget or other channels."}
```

## purchase

Buying a tool, subscription, credits, domains or a paid listing. The approver judges the price, whether it recurs, and whether it's worth it.

- **Must include:** facts for the cost (one-time or monthly), what it's for, and which card or account it goes on.
- **Good facts:** a cheaper option you considered, any catch (a locked card, an annual commitment), and how to cancel.

```json
{"type": "purchase", "title": "Buy 3 domains that point to our site: $45",
 "why": "People who guess our address land on someone else's site today; these send them to ours.",
 "part_of": null,
 "facts": [["Cost", "$45 one time; $45 a year to renew"], ["What they do", "each redirects to our main site"], ["Card", "the company card for web costs"]],
 "approves": "these 3 domains, up to $45.", "does_not_approve": "other domains or premium names."}
```

## money

Moving money, running payroll, paying or refusing a bill. The approver judges the amount, the accounts and the timing.

- **Must include:** facts for from, to, amount and when.
- **Good facts:** what the money is for, and how it compares to last time ("same people and amounts as the last payroll").

```json
{"type": "money", "title": "Move $20,000 from savings to operating",
 "why": "Payroll on the 15th, about $18,000, would leave operating short without it.",
 "part_of": null,
 "facts": [["From", "savings"], ["To", "operating, which bills and payroll come out of"], ["Next out of operating", "payroll on the 15th, about $18,000"]],
 "approves": "this one transfer.", "does_not_approve": "any later transfer."}
```

## player_message

A push, email, in-app message or survey that reaches players or customers. The approver judges what people will see, how many will see it, and how often.

- **Must include:** a `quote` with exactly what they'll see.
- **Good facts:** who gets it and roughly how many, when and how often, what tapping it does, and what today's message does by comparison.

```json
{"type": "player_message", "title": "Add a first-year offer to the trial-ending message",
 "why": "People who cancel their trial almost never come back at full price, and this tests whether the price is why.",
 "part_of": "More trial users keep Plus",
 "quotes": [{"label": "What they'd see (push, the day before the trial ends)", "text": "Your trial ends Thursday. Keep it: your first year at 40% off."}],
 "facts": [["Who gets it", "people who cancelled their trial · about 40 a week"], ["Today", "the same message at full price; 0 of 300 renewed"]],
 "approves": "this offer in this message, in English.", "does_not_approve": "other languages or other offers."}
```

## price

Prices, offers, discounts, or the words on a paywall. The approver judges each change next to what's there today.

- **Must include:** a `table` with today beside proposed, one row per thing changing.
- **Good facts:** what it's expected to bring, with its range, and which stores.

```json
{"type": "price", "title": "Lower the yearly price in Brazil and Mexico",
 "why": "Today's yearly price is above what local competitors charge, which costs us payers in both countries.",
 "part_of": "Prices that fit each country",
 "table": {"columns": ["Country", "Yearly today", "Yearly new"], "rows": [["Brazil", "R$399.90", "R$219.90"], ["Mexico", "MX$1,299", "MX$899"]]},
 "facts": [["Brings", "about 2 more payers a month; money about level"]],
 "approves": "these two countries at these prices, on both stores.", "does_not_approve": "other countries or the free trial."}
```

## experiment

Turning a test or feature flag on or off, or changing who's in it, when everything it switches is already built. An experiment that needs app or server work before it can run is a decision to build, which goes on a card only when your repository's rules send it there with its deck ([A test to build and read](#a-test-to-build-and-read)). The approver judges what players will see, how many, and for how long.

- **Must include:** facts for what's being tested, who is in it and how many, and what players see.
- **Good facts:** what goes wrong if nothing is done.

```json
{"type": "experiment", "title": "Keep version 2.5 users in the home screen test, 50/50",
 "why": "Without this, version 2.5's users leave the home screen test when it ships, and its read loses them.",
 "part_of": "A home screen that brings users back",
 "facts": [["Testing", "the new home screen against today's, half of users each"], ["The problem", "2.5 isn't on the test's version list, so its users leave the test when it ships"]],
 "approves": "adding 2.5 to the test at 50%.", "does_not_approve": "releasing 2.5 to the stores."}
```

## page

A store listing, website page, directory listing or public profile. The approver judges each field next to what's there today.

- **Must include:** a `table` with each field changing, today beside proposed. For a visual page, add a screenshot as `image`.
- **Good facts:** what it's worth, and when it goes live.

```json
{"type": "page", "title": "Test a new store listing title",
 "why": "Hold'em searches convert twice as well as our other terms, so a title that says Hold'em should bring more installs per visit.",
 "part_of": "More store visitors install",
 "table": {"columns": ["Where", "Today", "Proposed"], "rows": [["Store title", "Example - Learn", "Example - Play Hold'em"]]},
 "facts": [["When", "as soon as approved"]],
 "approves": "this title.", "does_not_approve": "the description or keywords."}
```

## content

Puzzles, lessons or other content going live to players. The approver judges what's going out, whether it was tested, and where it lands.

- **Must include:** facts for what and how many, where it was tested, and where it goes.
- **Good facts:** the dates, a sample in `evidence`, and how to pull it back.

```json
{"type": "content", "title": "Put 20 daily puzzles live for next month",
 "why": "These keep the Daily going for the first 20 days of next month.",
 "part_of": "A daily puzzle worth coming back for",
 "facts": [["What", "20 daily puzzles, one a day, the 1st to the 20th"], ["Tested", "all 20 played on staging"]],
 "approves": "these 20 puzzles on these dates.", "does_not_approve": "changes to existing puzzles."}
```

## email

An email or Slack message sent in the approver's name. The approver judges the exact words and who receives them.

- **Must include:** a `quote` with the exact text, and facts for From and To (plus Subject for email).
- **Good facts:** what the message answers, and whether a follow-up is planned.

```json
{"type": "email", "title": "Send this email as you to our accountants",
 "why": "We need last year's full record, workpapers included, for this year's books.",
 "part_of": null,
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
 "why": "The player asked us to delete it, and we owe them that.",
 "part_of": null,
 "facts": [["Asked", "by email, 3 days ago"], ["Checked", "the request came from the account's own address"], ["What goes", "sign-in and all progress. This can't be undone."]],
 "approves": "deleting this one account and confirming by email.", "does_not_approve": "refunds or other accounts."}
```
