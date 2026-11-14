"""One made-up card per kind of ask, shaped like the real ones the kinds were designed from."""
from samples import SCREEN

BASE = {"approves": "this exact thing.", "does_not_approve": "anything else."}


def k(**kw):
    return dict(BASE, **kw)


KINDS = {
    "before_after": k(
        type="content", title="Release three lesson fixes to every user", why="What it gets the business, in one sentence.", part_of="Today's lessons teach what users need",
        gallery=[{"image": SCREEN, "label": "Fix 1 of 3 · TODAY · real screen, iPhone, 2.1.65"},
                 {"image": SCREEN, "label": "Fix 1 of 3 · AFTER · real screen, staging",
                  "note": "New: this step comes between step 4 and the board-only question.", "mark": [40, 300, 310, 120]}],
        facts=[["Who gets it", "every user on today's lessons, app versions back to 2.1.38, in all seven languages"]],
        yes_label="Release all 3", change_label="Change this fix", no_label="No"),
    "set": k(
        type="content", title="Payers keep using what they paid for: 4 changes", why="What it gets the business, in one sentence.", part_of="Payers keep using what they paid for",
        facts=[["Done looks like", "payers active in more of their first two weeks, read against the 30% held back"]],
        links=[["Open the deck", "https://example.com/deck"]], change_label="Change",
        parts=[{"title": "Right after paying, take payers to what they came for", "note": "Everyone who starts Plus", "image": SCREEN},
               {"title": "Practice vs AI stops calling itself a beta", "note": "Everyone who opens Play", "image": SCREEN},
               {"title": "The paywall stops promising \"no ads\"", "note": "Every paywall", "image": SCREEN},
               {"title": "Tickets stops selling Plus to people who have it", "note": "Plus users who open Tickets", "image": SCREEN}]),
    "spend": k(
        type="budget", title="Keep training the run past 72 hours, for a policy with fewer holes?", why="What it gets the business, in one sentence.", part_of="Five players, trained in a week",
        if_no_answer="it stops at 72 hours",
        sections=[{"title": "What the reads found", "lines": [["✅ Strength", "about as fast as plain training"],
                                                             "⏳ Do the near-even moves give up money? The read below answers it by about 7 am"]}],
        facts=[["Spent so far", "about $500"]],
        parts=[{"title": "The near-even moves read · about $85", "yes_label": "Run the read", "no_label": "Don't"},
               {"title": "The run past 72 hours · about $200 a day", "note": "Training on until the read: about $100 per 12 hours.",
                "options": [{"name": "Stop at 72 h, resume if the read says so"}, {"name": "Keep training"}],
                "pick": "Stop at 72 h, resume if the read says so"}]),
    "batch": k(
        type="email", title="Send 10 emails as you, each with a free year of Plus", why="What it gets the business, in one sentence.", part_of="Poker creators use Poker Skill",
        facts=[["From", "your company address; the same email to each, with their name and own link"]],
        items=[["Creator One", "Instagram Reels · follow-up"], ["Creator Two", "YouTube vlog"], "Creator Three", "Creator Four",
               "Creator Five", "Creator Six", "Creator Seven", "Creator Eight", "Creator Nine", "Creator Ten", "Creator Eleven"],
        quotes=[{"label": "The email", "text": "Hi {name},\nHere's your own link to a free year.", "button": "Read the email"}],
        yes_label="Send all 11", pick_label="Send {n}",
        offer="You've said yes to all 48 creator messages since November 2. Stop asking for first emails to creators?"),
    "public": k(
        type="creative", title="Run a new ad: \"You're not unlucky. You were never taught why.\"", why="What it gets the business, in one sentence.", part_of="A second ad that brings paying iOS users",
        image=SCREEN, facts=[["To read it", "about $10; a finalist at 0.5 or more installs per dollar"], ["🟠 Still open", "nobody has listened to it yet"]]),
    "message": k(
        type="player_message", title="Tell people on a year of Plus when their iPhone trial's charge fails", why="What it gets the business, in one sentence.", part_of="Payers keep using what they paid for",
        quotes=[{"label": "The push, on their lock screen", "text": "Your trial's charge didn't go through\nYour year of Plus is still on."}],
        gallery=[{"image": SCREEN, "label": "The email, as designed · real render at iPhone width"}],
        facts=[["Who gets it", "anyone on a year of Plus whose iPhone trial charge failed; about 4 people a year"],
               ["When", "the email an hour after the failure"]]),
    "release": k(
        type="content", title="Release 2.1.65", why="What it gets the business, in one sentence.", part_of=None,
        sections=[{"title": "Coaching at the table · feature and product pods", "lines": [
                      {"text": "Your move shows the moment you tap.", "tag": "Updaters"}]},
                  {"title": "Fixes", "lines": [{"text": "The evening push stops reaching users who already finished.", "tag": "Everyone at deploy"}]}],
        facts=[["⚠️ Known and going out", "after sign-in, Home can offer the wrong next lesson for half a second"],
               ["🛑 Can't be undone", "an hourly sweep deletes finished hand records older than a day"]],
        parts=[{"title": "Server", "own_yes": True}, {"title": "iPhone, to App Review", "own_yes": True},
               {"title": "Google Play, 100%, live in minutes", "note": "QA tried Play's own build against production", "own_yes": True}]),
    "as_you": k(
        type="email", title="Send this X message as you to a creator", why="What it gets the business, in one sentence.", part_of="Poker creators use Poker Skill",
        **{"from": "your X account"},
        quotes=[{"label": "The creator", "note": "Nov 3, 11:29 AM", "text": "Appreciate you setting that up."},
                {"label": "Your reply", "text": "Good news, no email needed anymore. [their own free-year link]", "edit": True,
                 "hint": "Their link goes where the brackets are."}],
        facts=[["✅ Their link tested", "App Store on iPhone, Google Play on Android"]], yes_label="Send as you"),
    "choice": k(
        type="content", title="Pick how users see the hand they just played", why="What it gets the business, in one sentence.", part_of="Coaching at the table feels smooth",
        options=[{"name": "Build the hand-count panel", "title": "a panel that opens from the hand count", "image": SCREEN},
                 {"name": "Build the rail of past hands", "title": "a rail of past hands", "image": SCREEN}],
        pick="Build the hand-count panel", no_label="Neither", links=[["Play both at real speed", "https://example.com/clips"]],
        facts=[["Why the panel", "it answers the tap about 300 users a week already make, and keeps every hand"]]),
    "test": k(
        type="experiment", title="Build and test two screens on the onboarding paywall", why="What it gets the business, in one sentence.", part_of="More new users pay when they first see Plus",
        gallery=[{"image": SCREEN, "label": "TEST HALF · mock"}, {"image": SCREEN, "label": "Today · real"}],
        facts=[["Who", "half of new users"], ["Decides it", "payers by day 8, in about 17 to 35 days"], ["Stops it", "fewer trials, visible in about 9 days"]],
        links=[["Open the deck", "https://example.com/deck"]], yes_label="Build and test", change_label="Change"),
}
