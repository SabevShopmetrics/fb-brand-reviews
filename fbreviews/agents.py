"""One browser-use agent with the Facebook reviews skill.

The skill (fbreviews/skill) is installed into the agent's own browser-harness workspace:
  <workspace>/.browser-use/agent-workspace/agent_helpers.py      -> helper functions, globals in the agent's REPL
  <workspace>/.browser-use/agent-workspace/domain-skills/facebook -> the playbook (preflight.md, reviews.md)
  <workspace>/.browser-use/agent-workspace/fb_extract.js          -> the in-page extractor
  <workspace>/.browser-use/agent-workspace/fb_config.json         -> pacing and limits
The agent finds the locations and goes through all of them itself; everything it finds is saved as files in the
workspace, so a run that stops (step budget, model outage, daily cap) continues where it left off.
Before collecting, the agent checks the extractor against the output contract (fb_preflight, preflight.md); when
Facebook changed its UI it repairs the extractor with an override in the workspace (fb_extract_override.js), which
every later round / --resume of the same run loads too.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .config import Settings

logger = logging.getLogger("fbreviews.agent")
SKILL_DIR = Path(__file__).parent / "skill"
JS_FILE = Path(__file__).parent / "js" / "fb_extract.js"

SYSTEM_RULES = """\
You work inside the user's own, logged-in Facebook session in OpenWork's built-in browser, in one tab.
Before anything else read the skill files domain-skills/facebook/preflight.md and then reviews.md (paths below)
and follow them. preflight.md defines the exact output we want and how to check and repair the extractor.
Use the skill's helpers (fb_progress, fb_preflight, fb_search_pages, fb_add_locations, fb_extract_reviews,
fb_save_result, fb_open, fb_save_override): they keep a safe pace for the account, collect lazily loaded reviews,
check the data and save everything.
If fb_preflight is not healthy (Facebook changed its UI), repair the extractor with fb_save_override until it is;
never save data you know is wrong, and never loosen the checks.
Read only. Never solve or get around a CAPTCHA / checkpoint / login: stop the run and report it.
"""


class RunSummary(BaseModel):
    brand_pages: list[str] = Field(default_factory=list)
    locations_found: int = 0
    locations_done: int = 0
    complete: bool = False
    stop_reason: str | None = Field(default=None, description="login_required | blocked | cap_reached | extractor_broken | budget | null")
    notes: str = ""


def make_llm(s: Settings) -> Any:
    from browser_use import ChatOpenAI

    return ChatOpenAI(s.llm_model, api_key=s.llm_api_key or "not-needed", base_url=s.llm_base_url,
                      temperature=s.llm_temperature, reasoning_effort=s.llm_reasoning_effort)


def install_skill(workspace: Path, config: dict) -> Path:
    """Copy the skill into the agent's browser-harness workspace (fresh each run, so edits to the repo apply)."""
    aw = workspace / ".browser-use" / "agent-workspace"
    aw.mkdir(parents=True, exist_ok=True)
    shutil.copy(SKILL_DIR / "agent_helpers.py", aw / "agent_helpers.py")
    shutil.copy(JS_FILE, aw / "fb_extract.js")
    dst = aw / "domain-skills"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(SKILL_DIR / "domain-skills", dst)
    (aw / "fb_config.json").write_text(json.dumps(config, indent=1))
    os.environ["BH_DOMAIN_SKILLS"] = "1"  # BH_* reaches the agent's REPL worker; goto_url then lists the skill files
    return aw


def task_text(brand: str, aw: Path, max_locations: int, hint: str | None, round_no: int, locations_given: bool) -> str:
    skill = aw / "domain-skills" / "facebook" / "reviews.md"
    pre = aw / "domain-skills" / "facebook" / "preflight.md"
    lines = [
        f'Brand: "{brand}". Collect the Facebook reviews (and their comment counts) for every physical location '
        f"of this brand (up to {max_locations} locations), one location at a time.",
        f"Skill: read {pre} first (print(open(r'{pre}').read())): it defines the output we want and the "
        f"preflight / self-repair. Then read {skill} (print(open(r'{skill}').read())) and follow both.",
        "Start with fb_progress(): files from earlier rounds are kept, continue from there. If its "
        "preflight['due'] is true, run fb_preflight() before extracting locations (see preflight.md); "
        "if it is not healthy, repair the extractor first.",
    ]
    if locations_given:
        lines.append("The location list is already given (locations.json): skip Phase 1, do Phase 2.")
    if hint:
        lines.append(f"Hint from the user: {hint}")
    if round_no > 1:
        lines.append(f"This is round {round_no}: an earlier round ran out of steps. Do not redo finished locations.")
    return "\n".join(lines)


async def run_agent(brand: str, *, s: Settings, llm: Any, browser: Any, workspace: Path, max_locations: int,
                    hint: str | None, round_no: int, locations_given: bool, config: dict) -> RunSummary:
    from browser_use import Agent

    aw = install_skill(workspace, config)
    kwargs: dict[str, Any] = {}
    if not s.llm_vision:
        kwargs["use_vision"] = False
    agent = Agent(
        task_text(brand, aw, max_locations, hint, round_no, locations_given),
        llm=llm, browser=browser, output_model_schema=RunSummary, workspace=str(workspace),
        extend_system_message=SYSTEM_RULES, judge=s.judge, **kwargs,
    )
    history = await agent.run(max_steps=s.max_steps, timeout=s.timeout)
    out = history.structured_output
    if isinstance(out, RunSummary):
        return out
    errs = history.errors()[-3:]
    return RunSummary(stop_reason="budget", notes=f"agent ended without a summary: {errs}")
