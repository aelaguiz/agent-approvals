"""One made-up sample card per type."""
BASE = {"approves": "this exact thing.", "does_not_approve": "anything else."}


def s(**kw):
    return dict(BASE, **kw)


SAMPLES = {
    "creative": s(type="creative", title="Run this video ad in the spring creative test", image="/tmp/example-ad.png",
                  facts=[["Ad", "Learn the game in five minutes a day"], ["Money", "inside the test's $50 a day"]]),
    "reply": s(type="reply", title="Post this reply on r/boardgames as our account",
               quotes=[{"label": "They asked", "text": "What book should I read?", "link": "https://reddit.com/r/x", "link_text": "Best books?"},
                       {"label": "We'd reply", "text": "Pause the video & pick your play first <really>."}],
               facts=[["Checked", "voice check clean"]]),
    "budget": s(type="budget", title="Add $250 to the search ads learning budget",
                table={"columns": ["Budget", "Today", "After a yes"], "rows": [["Learning cap", "$450", "$700"]]},
                facts=[["Ends", "when the cap is spent"]]),
    "purchase": s(type="purchase", title="Buy 3 domains that point to our site: $45", facts=[["Cost", "$45 one time"]]),
    "money": s(type="money", title="Move $20,000 from savings to operating", facts=[["From", "savings"], ["To", "operating"]]),
    "player_message": s(type="player_message", title="Add a first-year offer to the trial-ending message",
                        quotes=[{"label": "What they'd see", "text": "Your trial ends Thursday. Keep it: your first year at 40% off."}],
                        facts=[["Who gets it", "about 40 a week"]]),
    "price": s(type="price", title="Lower the yearly price in two countries",
               table={"columns": ["Country", "Today", "New"], "rows": [["Brazil", "R$399.90", "R$219.90"], ["Mexico", "MX$1,299", "MX$899"]]},
               facts=[["Undo", "old prices saved"]]),
    "experiment": s(type="experiment", title="Keep version 2.5 in the new home screen test, 50/50", facts=[["Flag", "home_enabled"]]),
    "page": s(type="page", title="Test a new store listing title",
              table={"columns": ["Where", "Today", "Proposed"], "rows": [["Store title", "Example - Learn", "Example - Play"]]},
              facts=[["Worth", "about $40 over 60 days"]]),
    "content": s(type="content", title="Put 20 daily puzzles live for next month", facts=[["Tested", "all 20 on staging"]]),
    "email": s(type="email", title="Send this email as you to our accountants",
               quotes=[{"label": "The email", "text": "Hi,\nCould you send last year's full record?\nThanks"}],
               facts=[["From", "you@example.com"], ["To", "the accountants"]]),
    "account": s(type="account", title="Delete a player's account, as they asked", facts=[["Asked", "by email, 3 days ago"]]),
}
