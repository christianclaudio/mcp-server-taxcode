"""Server profiles and annotation-driven tool visibility.

A profile is one of two kinds; both coexist and both are selected with
``--profile`` / ``TAXCODE_MCP_PROFILE``:

* **Domain-mount profile** — ``domains`` names the domain sub-servers to mount.
  Use it when a job maps cleanly onto whole domains (``full``, ``system``, ``items``).
* **Allowlist profile** — ``tools`` is an explicit set of client-visible tool names
  applied over the full mounted catalog. Use it for job-shaped profiles that cut
  across domains (``intake``). Filtering is tools-only: prompts and resources stay.

``readonly=True`` keeps only tools whose MCP ``readOnlyHint`` annotation is ``True``.
The annotation is the single source of truth for read-only classification; a missing
annotation or ``readOnlyHint`` other than ``True`` counts as a write (fail closed).
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.tools import Tool
from fastmcp.utilities.versions import VersionSpec
from mcp.types import ToolAnnotations

ALL_DOMAINS: tuple[str, ...] = ("system", "items")


@dataclass(frozen=True)
class Profile:
    """A named server profile: domain mounts or a tool-name allowlist."""

    name: str
    job: str
    domains: tuple[str, ...] = ALL_DOMAINS
    tools: frozenset[str] | None = None
    readonly: bool = False

    @property
    def is_allowlist(self) -> bool:
        """True when the profile is an explicit tool-name allowlist."""
        return self.tools is not None


PROFILES: dict[str, Profile] = {
    profile.name: profile
    for profile in (
        # Domain-mount profiles
        Profile(
            name="full",
            job="Complete catalog: every tool, including bulk/destructive tools (still gated).",
        ),
        Profile(name="system", job="Upstream health checks only.", domains=("system",)),
        Profile(name="items", job="Item lifecycle within the items domain.", domains=("items",)),
        Profile(
            name="readonly",
            job="Inspect without side effects: every tool annotated readOnlyHint=True.",
            readonly=True,
        ),
        # Allowlist (job-shaped) profiles: span domains, names are client-visible names
        Profile(
            name="intake",
            job="Item intake: confirm the upstream is healthy, look up items, create new ones.",
            tools=frozenset(
                {
                    "system_get_health_status",
                    "items_list_items",
                    "items_get_item",
                    "items_create_item",
                }
            ),
        ),
    )
}

# Tools in no job (allowlist) profile. They stay reachable in ``full`` (and in the
# ``items`` domain-mount profile). Tests require every tool in ``full`` to be in a job
# profile or listed here, so a new tool is placed on purpose.
FULL_ONLY_TOOLS: frozenset[str] = frozenset({"items_delete_item", "items_bulk_delete_items"})

READ_ONLY_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


def get_profile(name: str) -> Profile:
    """Return the named profile or raise ``ValueError`` listing valid profiles."""
    key = name.lower()
    if key not in PROFILES:
        valid = ", ".join(sorted(PROFILES))
        raise ValueError(f"Unknown profile {name!r}; valid profiles: {valid}.")
    return PROFILES[key]


def validate_allowlist(profile: Profile, catalog: Collection[str]) -> frozenset[str]:
    """Return the profile allowlist, raising ``ValueError`` on any name not in ``catalog``."""
    allowlist = profile.tools or frozenset()
    unknown = sorted(allowlist - set(catalog))
    if unknown:
        raise ValueError(
            f"Profile {profile.name!r} allowlists tools not in the full catalog: "
            f"{', '.join(unknown)}."
        )
    return allowlist


def is_read_only_tool(tool: Tool | None) -> bool:
    """Return True only for a tool annotated ``readOnlyHint=True`` (fail closed)."""
    if tool is None or tool.annotations is None:
        return False
    return tool.annotations.read_only_hint is True


class ReadOnlyToolFilter(Transform):
    """Keep only tools annotated ``readOnlyHint=True``; other component types pass through."""

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [tool for tool in tools if is_read_only_tool(tool)]

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        tool = await call_next(name, version=version)
        return tool if is_read_only_tool(tool) else None


class ReadOnlyAnnotations(Transform):
    """Annotate named synthetic discovery tools as ``readOnlyHint=True``.

    FastMCP's synthetic discovery tools (``search_tools``; Code Mode ``search`` /
    ``get_schema``) ship without annotations. They only read the catalog, so the
    template marks them read-only to keep discovery usable under the readonly gate.
    ``call_tool`` / ``execute`` are deliberately not annotated: the gate classifies
    ``call_tool`` by the tool it proxies, and ``execute`` stays refused under readonly.
    """

    def __init__(self, names: Collection[str]) -> None:
        self.names = frozenset(names)

    def _annotate(self, tool: Tool) -> Tool:
        if tool.name not in self.names:
            return tool
        return tool.model_copy(update={"annotations": READ_ONLY_ANNOTATIONS})

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [self._annotate(tool) for tool in tools]

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        tool = await call_next(name, version=version)
        return None if tool is None else self._annotate(tool)
