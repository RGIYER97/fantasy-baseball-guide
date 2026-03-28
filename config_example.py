"""
Fantasy Baseball Helper — Configuration Template
=================================================
Copy this file to config_private.py and fill in your values.
config_private.py is gitignored and will NOT be committed.

How to find each value:

LEAGUE_ID:
    Go to your league on ESPN → the URL looks like:
    https://fantasy.espn.com/baseball/league?leagueId=XXXXX
    XXXXX is your LEAGUE_ID.

YEAR:
    The current MLB season year (e.g. 2025).

ESPN_S2 and SWID (required for private leagues):
    1. Log into ESPN Fantasy in your browser.
    2. Open Developer Tools (F12 or right-click → Inspect).
    3. Go to Application tab → Cookies → espn.com.
    4. Copy the values of 'espn_s2' and 'SWID'.

TEAM_NAME:
    Your fantasy team's display name exactly as it appears on ESPN.
"""

LEAGUE_ID = 12345
YEAR = 2025
ESPN_S2 = "paste_your_espn_s2_cookie_here"
SWID = "{paste-your-swid-here}"
TEAM_NAME = "Your Fantasy Team Name"

# Optional: MLB season year for pybaseball FanGraphs leader stats (defaults to YEAR).
# FANGRAPHS_SEASON = 2025
