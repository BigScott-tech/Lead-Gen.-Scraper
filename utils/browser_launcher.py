"""Open search links in a user's local browser session."""

from __future__ import annotations

import logging
import shutil
import subprocess
import webbrowser
from dataclasses import dataclass, field
from typing import List, Sequence

logger = logging.getLogger(__name__)


@dataclass
class BrowserLaunchResult:
    app: str
    urls: List[str]
    command: List[str] = field(default_factory=list)
    used_system_browser: bool = False


class BrowserLauncher:
    """Launch URLs in local browser apps without paid/browser-cloud services."""

    BROWSER_BINARIES = {
        "firefox": ["firefox"],
        "librewolf": ["librewolf"],
        "brave": ["brave-browser", "brave"],
        "chrome": ["google-chrome", "chrome"],
        "chromium": ["chromium", "chromium-browser"],
        "edge": ["microsoft-edge"],
    }

    SYSTEM_APPS = {"", "default", "system", "xdg-open"}

    @classmethod
    def open_urls(
        cls,
        urls: Sequence[str],
        *,
        app: str = "firefox",
        profile: str | None = None,
        new_window: bool = False,
    ) -> BrowserLaunchResult:
        clean_urls = [url for url in (str(item).strip() for item in urls) if url]
        if not clean_urls:
            logger.warning("No browser URLs to open.")
            return BrowserLaunchResult(app=app, urls=[])

        requested_app = (app or "firefox").strip().lower()
        if requested_app in cls.SYSTEM_APPS:
            cls._open_with_system_browser(clean_urls)
            return BrowserLaunchResult(
                app="system",
                urls=clean_urls,
                used_system_browser=True,
            )

        binary = cls._resolve_binary(requested_app)
        if not binary:
            logger.warning(
                "Browser app '%s' was not found; falling back to system browser.",
                app,
            )
            cls._open_with_system_browser(clean_urls)
            return BrowserLaunchResult(
                app="system",
                urls=clean_urls,
                used_system_browser=True,
            )

        command = cls._build_command(binary, requested_app, clean_urls, profile, new_window)
        try:
            subprocess.Popen(command)
            logger.info("Opened %d tab(s) in %s.", len(clean_urls), requested_app)
            return BrowserLaunchResult(app=requested_app, urls=clean_urls, command=command)
        except Exception as exc:
            logger.warning(
                "Failed to launch %s with search URLs: %s; falling back to system browser.",
                requested_app,
                exc,
            )
            cls._open_with_system_browser(clean_urls)
            return BrowserLaunchResult(
                app="system",
                urls=clean_urls,
                command=command,
                used_system_browser=True,
            )

    @classmethod
    def _resolve_binary(cls, app: str) -> str:
        candidates = cls.BROWSER_BINARIES.get(app, [app])
        for candidate in candidates:
            resolved = shutil.which(candidate)
            if resolved:
                return resolved
        return ""

    @classmethod
    def _build_command(
        cls,
        binary: str,
        app: str,
        urls: Sequence[str],
        profile: str | None,
        new_window: bool,
    ) -> List[str]:
        if app in {"firefox", "librewolf"}:
            return cls._firefox_command(binary, urls, profile, new_window)
        if app in {"brave", "chrome", "chromium", "edge"}:
            return cls._chromium_command(binary, urls, profile, new_window)
        return [binary, *urls]

    @staticmethod
    def _firefox_command(
        binary: str,
        urls: Sequence[str],
        profile: str | None,
        new_window: bool,
    ) -> List[str]:
        command = [binary]
        if profile:
            command.extend(["-P", profile])
        first_flag = "--new-window" if new_window else "--new-tab"
        for index, url in enumerate(urls):
            command.extend([first_flag if index == 0 else "--new-tab", url])
        return command

    @staticmethod
    def _chromium_command(
        binary: str,
        urls: Sequence[str],
        profile: str | None,
        new_window: bool,
    ) -> List[str]:
        command = [binary]
        if new_window:
            command.append("--new-window")
        else:
            command.append("--new-tab")
        if profile:
            command.append(f"--profile-directory={profile}")
        command.extend(urls)
        return command

    @staticmethod
    def _open_with_system_browser(urls: Sequence[str]) -> None:
        for url in urls:
            webbrowser.open_new_tab(url)
        logger.info("Opened %d tab(s) with the system browser.", len(urls))
