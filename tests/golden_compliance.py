"""Golden outputs of the compliance tools and prompt, captured through a real MCP client.

``tests/fixtures/compliance_golden/`` holds one gzipped JSON-lines file per tool (and one
for the prompt). Each line is a
call's arguments and its result as the client receives it: ``is_error``, the structured
content, and each text content block. Three long constant strings (the disclaimer, the
content notice and the estimate note) are stored as placeholders, and ``constants.json``
pins their SHA-256 so a change to them still shows. A successful call's text block is
the JSON of its structured content, so it is stored as the SHA-256 and length of the
exact text; an error's text is stored in full.

Regenerate (only when an output is meant to change)::

    uv run python tests/golden_compliance.py --write

The argument grid is the set of ``args`` already in the files; ``--write`` re-captures it.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from fastmcp import Client

from taxcode_mcp.disclaimers import BILINGUAL_DISCLAIMER, CONTENT_NOTICE, ESTIMATE_NOTE
from taxcode_mcp.server import mcp

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "compliance_golden"
TOOLS = (
    "compliance_evaluate_state_residency",
    "compliance_evaluate_georgia_residency",
    "compliance_evaluate_pr_bona_fide_residency",
    "compliance_calculate_built_in_gain_sourcing",
)
PROMPT = "compliance_cross_border_tax_analysis"
PROMPT_SCENARIOS = (
    "",
    "Moved from New York to San Juan in March 2025; sells NVDA shares in 2031.",
    "Georgia retiree, age 66, spends 190 days in Puerto Rico.",
)
CONSTANTS: dict[str, Any] = {
    "<<BILINGUAL_DISCLAIMER>>": BILINGUAL_DISCLAIMER,
    "<<CONTENT_NOTICE>>": CONTENT_NOTICE,
    "<<ESTIMATE_NOTE>>": ESTIMATE_NOTE,
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def constants_record() -> dict[str, str]:
    """SHA-256 of each placeholder's value (as JSON, keys in order) at capture time."""
    return {name: _sha(json.dumps(v, ensure_ascii=False)) for name, v in CONSTANTS.items()}


def _placeholders(value: Any) -> Any:
    for name, constant in CONSTANTS.items():
        if type(value) is type(constant) and value == constant:
            return name
    if isinstance(value, list):
        return [_placeholders(v) for v in value]
    if isinstance(value, dict):
        return {k: _placeholders(v) for k, v in value.items()}
    return value


def line(record: dict[str, Any]) -> str:
    """One canonical JSON line: sorted keys, no spaces, UTF-8 kept."""
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


async def _tool_record(client: Client, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    result = await client.call_tool(tool, args, raise_on_error=False)
    content: list[dict[str, Any]] = []
    for block in result.content:
        text = getattr(block, "text", None)
        assert isinstance(text, str), block
        if result.is_error:
            content.append({"type": block.type, "text": text})
        else:
            content.append({"type": block.type, "sha256": _sha(text), "chars": len(text)})
    return {
        "args": args,
        "result": {
            "is_error": result.is_error,
            "structured": _placeholders(result.structured_content),
            "content": content,
        },
    }


async def capture(cases: dict[str, list[dict[str, Any]]]) -> dict[str, list[str]]:
    """Run every case and the prompt; return the golden lines per file stem."""
    out: dict[str, list[str]] = {}
    async with Client(mcp) as client:
        for tool in TOOLS:
            out[tool] = [line(await _tool_record(client, tool, args)) for args in cases[tool]]
        prompts = []
        for scenario in PROMPT_SCENARIOS:
            res = await client.get_prompt(PROMPT, {"taxpayer_scenario": scenario})
            messages = [m.model_dump(mode="json", exclude_none=True) for m in res.messages]
            prompts.append(line({"args": {"taxpayer_scenario": scenario}, "messages": messages}))
        out[PROMPT] = prompts
    return out


def golden_path(stem: str) -> Path:
    return GOLDEN_DIR / f"{stem}.jsonl.gz"


def read_lines(stem: str) -> list[str]:
    """The committed golden lines of one file."""
    return gzip.decompress(golden_path(stem).read_bytes()).decode("utf-8").splitlines()


def read_cases() -> dict[str, list[dict[str, Any]]]:
    """The arguments of every golden line, per tool."""
    return {tool: [json.loads(text)["args"] for text in read_lines(tool)] for tool in TOOLS}


def write(cases: dict[str, list[dict[str, Any]]]) -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for stem, lines in asyncio.run(capture(cases)).items():
        data = ("\n".join(lines) + "\n").encode("utf-8")
        golden_path(stem).write_bytes(gzip.compress(data, compresslevel=9, mtime=0))
    (GOLDEN_DIR / "constants.json").write_text(
        json.dumps(constants_record(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":  # pragma: no cover
    if sys.argv[1:] == ["--write"]:
        write(read_cases())
    elif len(sys.argv) == 3 and sys.argv[1] == "--from-cases":
        write(json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")))
    else:
        sys.exit("usage: golden_compliance.py --write | --from-cases CASES.json")
