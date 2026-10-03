# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

_CODE = re.compile(r"^[A-Za-z0-9]{16}$")


def parse_report_url(url: str) -> tuple[str, int | None]:
    """Report code and fight id from a WCL report link (or a bare report code).

    Accepts ``https://cn.warcraftlogs.com/reports/CODE?fight=32``, the older ``#fight=32`` form and
    ``fight=last`` (returned as None, i.e. "let the user pick").
    """
    s = url.strip()
    if _CODE.match(s):
        return s, None
    u = urlparse(s if "://" in s else "https://" + s)
    if not u.netloc.endswith("warcraftlogs.com"):
        raise ValueError(f"不是 warcraftlogs.com 的链接：{url}")
    m = re.search(r"/reports/([A-Za-z0-9]+)", u.path)
    if not m:
        raise ValueError(f"链接里没有报告代码（/reports/XXXX）：{url}")
    code = m.group(1)
    fight: int | None = None
    for part in (u.query, u.fragment):
        vals = parse_qs(part).get("fight")
        if vals and vals[0].isdigit():
            fight = int(vals[0])
    return code, fight
