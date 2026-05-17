"""Shared command parsing for Telegram-style search commands."""

from __future__ import annotations

import argparse
import shlex
from dataclasses import dataclass, field
from typing import Iterable, List

from utils.search_planner import SearchPlanner


@dataclass
class SearchCommand:
    query: str = ""
    platforms: List[str] = field(default_factory=list)
    regions: List[str] = field(default_factory=list)
    amount: int | None = None
    output_format: str = "csv"
    deep: bool = False
    browser: bool = False
    headful: bool = False
    profile: str = "default"
    custom_searches: List[str] = field(default_factory=list)
    custom_links: List[str] = field(default_factory=list)
    browser_app: str = "firefox"
    firefox_profile: str | None = None


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def parse_search_command(text_or_args: str | Iterable[str]) -> SearchCommand:
    if isinstance(text_or_args, str):
        tokens = shlex.split(text_or_args)
    else:
        tokens = list(text_or_args)

    parser = _Parser(add_help=False)
    parser.add_argument("-p", "--platform", "--platforms", default="")
    parser.add_argument("-q", "--query", default="")
    parser.add_argument("-n", "--number", "--amount", type=int)
    parser.add_argument("-r", "--region", "--regions", default="")
    parser.add_argument("--format", choices=["csv", "json"], default="csv")
    parser.add_argument("--deep", action="store_true")
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--headful", action="store_true")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--custom", "--custom-search", "--custom-searches", default="")
    parser.add_argument("--link", "--custom-link", action="append", default=[])
    parser.add_argument("--browser-app", default="firefox")
    parser.add_argument("--firefox-profile", default=None)
    parser.add_argument("free_query", nargs="*")
    ns = parser.parse_args(tokens)

    platforms = [
        SearchPlanner.PLATFORM_ALIASES.get(item.strip().lower(), item.strip().lower())
        for item in ns.platform.replace(" ", "").split(",")
        if item.strip()
    ]
    regions = [item.strip() for item in ns.region.split(",") if item.strip()]
    custom_searches = [item.strip() for item in ns.custom.split(",") if item.strip()]
    query = ns.query or " ".join(ns.free_query).strip()

    return SearchCommand(
        query=query,
        platforms=[] if "all" in platforms else platforms,
        regions=regions,
        amount=ns.number,
        output_format=ns.format,
        deep=bool(ns.deep),
        browser=bool(ns.browser),
        headful=bool(ns.headful),
        profile=ns.profile,
        custom_searches=custom_searches,
        custom_links=ns.link,
        browser_app=ns.browser_app,
        firefox_profile=ns.firefox_profile,
    )
