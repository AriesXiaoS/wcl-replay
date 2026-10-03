# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

_CODE = re.compile(r"^[A-Za-z0-9]{16}$")


def normalize_host(value: str) -> str:
    """Accept official domain boundaries, never userinfo or custom authentication endpoints."""
    parsed = urlparse(value.strip() if "://" in value else "https://" + value.strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or not re.fullmatch(r"(?:[a-z0-9]+(?:-[a-z0-9]+)*\.)*warcraftlogs\.com", host)
    ):
        raise ValueError("API 域名必须是 warcraftlogs.com 的 HTTPS 官方域名")
    return host


def report_host(url: str) -> str | None:
    value = url.strip()
    if _CODE.fullmatch(value):
        return None
    return normalize_host(value)


def parse_report_url(url: str) -> tuple[str, int | None]:
    """Report code and fight id from a WCL report link (or a bare report code).

    Accepts ``https://cn.warcraftlogs.com/reports/CODE?fight=32``, the older ``#fight=32`` form and
    ``fight=last`` (returned as None, i.e. "let the user pick").
    """
    s = url.strip()
    if _CODE.match(s):
        return s, None
    u = urlparse(s if "://" in s else "https://" + s)
    normalize_host(s)
    m = re.fullmatch(r"/reports/([A-Za-z0-9]{16})/?", u.path)
    if not m:
        raise ValueError(f"链接里没有报告代码（/reports/XXXX）：{url}")
    code = m.group(1)
    fight: int | None = None
    for part in (u.query, u.fragment):
        vals = parse_qs(part).get("fight")
        if vals and vals[0].isdigit():
            fight = int(vals[0])
    return code, fight
