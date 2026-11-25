"""Settings from .env / environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

OPENWORK_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "com.differentai.openwork"


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name, "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    v = os.environ.get(name, "").strip()
    return int(v) if v else default


def _float(name: str, default: float | None) -> float | None:
    v = os.environ.get(name, "").strip()
    return float(v) if v else default


def detect_openwork_cdp_url() -> str | None:
    """OpenWork (Electron) runs with --remote-debugging-port; Chromium writes the port to DevToolsActivePort."""
    f = OPENWORK_SUPPORT_DIR / "DevToolsActivePort"
    try:
        port = f.read_text().splitlines()[0].strip()
    except (OSError, IndexError):
        return None
    return f"http://127.0.0.1:{port}" if port.isdigit() else None


@dataclass
class Settings:
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_temperature: float | None
    llm_vision: bool
    llm_reasoning_effort: str | None
    classifier_model: str
    openwork_cdp_url: str | None
    openwork_tab_match: str | None
    max_steps: int
    timeout: int
    max_rounds: int
    pace_min_s: float
    pace_max_s: float
    daily_page_cap: int
    location_max_s: int
    country: str
    judge: bool

    @classmethod
    def load(cls, env_file: str | Path | None = None) -> "Settings":
        load_dotenv(env_file or Path.cwd() / ".env", override=False)
        model = os.environ.get("LLM_MODEL", "").strip()
        return cls(
            llm_base_url=os.environ.get("LLM_BASE_URL", "").strip(),
            llm_api_key=os.environ.get("LLM_API_KEY", "").strip(),
            llm_model=model,
            llm_temperature=_float("LLM_TEMPERATURE", 0.2),
            llm_vision=_bool("LLM_VISION", True),
            llm_reasoning_effort=os.environ.get("LLM_REASONING_EFFORT", "").strip() or None,
            classifier_model=os.environ.get("CLASSIFIER_MODEL", "").strip() or model,
            openwork_cdp_url=os.environ.get("OPENWORK_CDP_URL", "").strip() or detect_openwork_cdp_url(),
            openwork_tab_match=os.environ.get("OPENWORK_TAB_MATCH", "").strip() or None,
            max_steps=_int("AGENT_MAX_STEPS", 250),
            timeout=_int("AGENT_TIMEOUT", 21600),
            max_rounds=_int("AGENT_MAX_ROUNDS", 30),
            pace_min_s=_float("PACE_MIN_SECONDS", 6.0) or 6.0,
            pace_max_s=_float("PACE_MAX_SECONDS", 14.0) or 14.0,
            daily_page_cap=_int("DAILY_PAGE_CAP", 5000),
            location_max_s=_int("LOCATION_MAX_SECONDS", 3600),
            country=os.environ.get("COUNTRY", "").strip() or "all",
            judge=_bool("AGENT_JUDGE", False),
        )

    def missing_llm(self) -> list[str]:
        return [n for n, v in (("LLM_BASE_URL", self.llm_base_url), ("LLM_MODEL", self.llm_model)) if not v]
