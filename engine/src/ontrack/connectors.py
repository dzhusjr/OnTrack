"""Use connectors (Jira, mail, calendar…) only when the question needs them: starting MCP servers costs seconds."""
from __future__ import annotations

import re

_NEEDS = re.compile(
    r"(?<!\w)(jira|ticket|tickets|issue|issues|task|tasks|story|epic|sprint|board|backlog|bug|pr|pull request|"
    r"confluence|wiki|calendar|meeting|meetings|invite|schedule|deadline|email|e-mail|mail|inbox|slack|"
    r"assigned|assignee|in review|code review|status|progress|update|working on|work on|done|finished|merged|"
    r"deployed|release|blocker|blocked|"
    r"тікет|тікети|задач\w*|таск\w*|спринт\w*|баг\w*|ревю|рев'ю|дошк\w*|календар\w*|зустріч\w*|мітинг\w*|"
    r"пошт\w*|лист\w*|імейл\w*|дедлайн\w*|статус\w*|прогрес\w*|апдейт\w*|зробив|зробила|працюєш|працював|"
    r"мерж\w*|реліз\w*|блокер\w*|"
    r"taak|taken|vergadering|agenda)(?!\w)"
    r"|(?<![\w.])[A-Z][A-Z0-9]+-\d+(?!\w)"  # PROJ-123
    r"|(?<![\w.])#?\d{3,6}(?![\w.])",  # 1234 — a ticket number said out loud
    re.IGNORECASE | re.UNICODE)


def needs_connectors(*texts: str) -> bool:
    return any(_NEEDS.search(t or "") for t in texts)
