"""Parse a usage-limit reset time out of Claude Code's limit message.

Examples seen in the CLI:
    "Resets 1:30am (America/New_York)"
    "Resets Jun 12, 12am (America/New_York)"
    "try again at 3:00 PM"

We parse the clock (and optional date) and return the next future occurrence in
UTC. Timezone names in the message are ignored — the local machine's timezone is
assumed (this is a local tool). Returns None if nothing parseable is found.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    )
}

# optional "Mon DD," then a clock "H[:MM] am/pm"
_RE = re.compile(
    r"(?:reset[s]?|try again)\b[^\n]*?"
    r"(?:(?P<mon>[A-Za-z]{3})[a-z]*\s+(?P<day>\d{1,2}),?\s+)?"
    r"(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm)",
    re.IGNORECASE,
)


def parse_reset_time(text: str, now: Optional[datetime] = None) -> Optional[datetime]:
    if not text:
        return None
    m = _RE.search(text)
    if not m:
        return None
    now_local = (now or datetime.now().astimezone()).astimezone()
    tz = now_local.tzinfo

    hour = int(m.group("h")) % 12
    if m.group("ap").lower() == "pm":
        hour += 12
    minute = int(m.group("m") or 0)

    if m.group("mon") and m.group("mon").lower() in _MONTHS:
        month = _MONTHS[m.group("mon").lower()]
        day = int(m.group("day"))
        year = now_local.year
        try:
            target = datetime(year, month, day, hour, minute, tzinfo=tz)
        except ValueError:
            return None
        if target < now_local:  # rolled past year-end
            target = target.replace(year=year + 1)
    else:
        target = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now_local:
            target += timedelta(days=1)

    return target.astimezone(timezone.utc)
