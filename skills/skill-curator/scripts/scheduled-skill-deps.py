#!/usr/bin/env python3
"""Enumerate durable skill dependencies reachable from Dreaming LaunchAgents."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import shlex
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

OWNED = {
    "skill-review",
    "skill-curator",
    "memory-curator",
    "skill-create",
    "skill-manage",
}
SHARED = {"writing-great-skills", "dual-review", "authenticated-browse"}
BUILTIN_SLASH_COMMANDS = {
    "allow-all",
    "autopilot",
    "clear",
    "compact",
    "context",
    "every",
    "exit",
    "help",
    "login",
    "logout",
    "model",
    "new",
    "permissions",
    "plugin",
    "plugins",
    "restart",
    "skills",
    "status",
}
NAME_RE = r"[a-z0-9][a-z0-9._-]*"
SLASH_RE = re.compile(
    rf"(?<![\w:/])/(?:(dfrysinger-(?:dreaming|skills)):)?({NAME_RE})"
    rf"(?=[\s`'\",.)\]]|$)"
)
BACKTICK_RE = re.compile(rf"`({NAME_RE})`")
RELATIVE_FILE_RE = re.compile(
    r"(?<![A-Za-z0-9_./-])((?:references|scripts)/[A-Za-z0-9._/-]+)"
)
ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_:/])(/[A-Za-z0-9._~@%+=,/-]+)"
)


class DependencyError(ValueError):
    pass


def within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def config_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw = line.split("=", 1)
        try:
            parsed = shlex.split(raw)
        except ValueError as exc:
            raise DependencyError(f"malformed dreaming config {path}: {exc}") from exc
        if len(parsed) != 1:
            raise DependencyError(f"malformed dreaming config assignment: {key}")
        values[key] = parsed[0]
    return values


@dataclass(frozen=True)
class Roots:
    dreaming: Path
    shared: Path
    public: Path | None
    local: Path

    @property
    def dreaming_skills(self) -> Path:
        return self.dreaming / "skills"

    @property
    def shared_skills(self) -> Path:
        return self.shared / "skills"

    @property
    def public_skills(self) -> Path | None:
        return self.public / "skills" if self.public else None


def roots() -> Roots:
    script_repo = Path(__file__).resolve().parents[3]
    config = Path(
        os.environ.get(
            "DREAMING_CONFIG_FILE", Path.home() / ".copilot/dreaming/config.env"
        )
    ).expanduser()
    configured = config_values(config)
    dreaming = Path(
        os.environ.get(
            "DREAMING_REPO_ROOT",
            configured.get("DREAMING_REPO_ROOT", str(script_repo)),
        )
    ).expanduser().resolve()
    shared_raw = os.environ.get(
        "DREAMING_SHARED_SKILLS_ROOT",
        configured.get("DREAMING_SHARED_SKILLS_ROOT", ""),
    )
    if not shared_raw:
        raise DependencyError("DREAMING_SHARED_SKILLS_ROOT is not configured")
    shared = Path(shared_raw).expanduser().resolve()
    public_raw = os.environ.get(
        "SKILLS_REPO_ROOT", configured.get("SKILLS_REPO_ROOT", "")
    )
    if not public_raw:
        canonical = Path.home() / "code/skills"
        public_raw = str(canonical) if (canonical / "skills").is_dir() else ""
    public = Path(public_raw).expanduser().resolve() if public_raw else None
    local = Path(
        os.environ.get("SKILLS_LOCAL_ROOT", Path.home() / ".copilot/skills")
    ).expanduser().resolve()
    distinct = [dreaming, shared, local] + ([public] if public else [])
    if len(set(distinct)) != len(distinct):
        raise DependencyError("dreaming, shared, public, and local roots must not alias")
    if not dreaming.is_dir():
        raise DependencyError(f"dreaming root does not exist: {dreaming}")
    if not shared.is_dir():
        raise DependencyError(f"shared root does not exist: {shared}")
    if public is not None and not (public / "skills").is_dir():
        raise DependencyError(f"configured public root has no skills directory: {public}")
    if not local.is_dir():
        raise DependencyError(f"local skills root does not exist: {local}")
    return Roots(dreaming=dreaming, shared=shared, public=public, local=local)


def enumerate_skill_dirs(root: Path | None) -> dict[str, Path]:
    result: dict[str, Path] = {}
    if root is None or not root.is_dir():
        return result
    for skill_md in root.glob("*/SKILL.md"):
        result[skill_md.parent.name] = skill_md.parent.resolve()
    return result


@dataclass
class Skills:
    authoritative: dict[str, Path]
    roots: dict[str, str]
    public_catalog: dict[str, Path]


def live_skills(root_set: Roots) -> Skills:
    dreaming = enumerate_skill_dirs(root_set.dreaming_skills)
    shared = enumerate_skill_dirs(root_set.shared_skills)
    public = enumerate_skill_dirs(root_set.public_skills)
    local = enumerate_skill_dirs(root_set.local)
    missing_owned = sorted(OWNED - set(dreaming))
    missing_shared = sorted(SHARED - set(shared))
    if missing_owned:
        raise DependencyError(f"dreaming root is missing owned skills: {missing_owned}")
    if missing_shared:
        raise DependencyError(f"shared root is missing dependencies: {missing_shared}")
    extras_owned = sorted(name for name in dreaming if name not in OWNED)
    extras_shared = sorted(name for name in shared if name not in SHARED)
    if extras_owned:
        raise DependencyError(f"dreaming root contains unowned skills: {extras_owned}")
    if extras_shared:
        raise DependencyError(f"shared root contains unowned skills: {extras_shared}")

    authoritative: dict[str, Path] = {}
    root_labels: dict[str, str] = {}
    for name in sorted(OWNED):
        authoritative[name] = dreaming[name]
        root_labels[name] = "dreaming"
    for name in sorted(SHARED):
        authoritative[name] = shared[name]
        root_labels[name] = "shared"
    for name in sorted((set(public) | set(local)) - OWNED - SHARED):
        candidates = [item[name] for item in (public, local) if name in item]
        if len(candidates) > 1:
            raise DependencyError(f"ambiguous live skill name: {name}")
        authoritative[name] = candidates[0]
        root_labels[name] = "public" if name in public else "local"
    return Skills(authoritative, root_labels, public)


def skill_for_path(path: Path, skills: Skills) -> str | None:
    resolved = path.resolve(strict=False)
    for name, directory in skills.authoritative.items():
        if within(resolved, directory):
            return name
    for name, directory in skills.public_catalog.items():
        if name in SHARED and within(resolved, directory):
            return name
    return None


def add_dependency(
    dependencies: dict[str, set[str]],
    name: str,
    source: str,
    skills: Skills,
    strict: bool = True,
) -> None:
    if name not in skills.authoritative:
        if strict:
            raise DependencyError(f"{source}: referenced skill does not exist: {name}")
        return
    dependencies.setdefault(name, set()).add(source)


def managed_roots(root_set: Roots) -> list[Path]:
    result = [root_set.dreaming, root_set.shared_skills, root_set.local]
    if root_set.public_skills:
        result.append(root_set.public_skills)
    return result


def reject_non_authoritative_reserved_path(
    target: Path, source: str, root_set: Roots
) -> None:
    candidates = [(root_set.local, "local")]
    if root_set.public_skills:
        candidates.append((root_set.public_skills, "public"))
    for root, namespace in candidates:
        if not within(target, root):
            continue
        relative = target.relative_to(root)
        if not relative.parts:
            return
        name = relative.parts[0]
        if name in OWNED or (namespace == "local" and name in SHARED):
            raise DependencyError(
                f"{source}: non-authoritative {namespace} path for reserved skill "
                f"{name}: {target}"
            )


def add_path_reference(
    raw: str,
    source: str,
    skills: Skills,
    dependencies: dict[str, set[str]],
    queue: deque[Path],
    root_set: Roots,
) -> bool:
    target = Path(os.path.expanduser(raw.rstrip(".,;:)"))).resolve(strict=False)
    owner = next((root for root in managed_roots(root_set) if within(target, root)), None)
    if owner is None:
        return False
    if not target.exists():
        raise DependencyError(f"{source}: referenced path is missing: {target}")
    reject_non_authoritative_reserved_path(target, source, root_set)
    name = skill_for_path(target, skills)
    if name:
        add_dependency(dependencies, name, source, skills)
    if target.is_file():
        queue.append(target)
    return True


def variable_patterns(root_set: Roots) -> list[tuple[re.Pattern[str], Path]]:
    patterns = [
        (r"\$(?:DREAMING_REPO_ROOT|REPO)/", root_set.dreaming),
        (r"\$DREAMING_SHARED_SKILLS_ROOT/skills/", root_set.shared_skills),
        (r"\$SKILLS_LOCAL_ROOT/", root_set.local),
        (r"(?:~|\$HOME)/\.copilot/skills/", root_set.local),
        (r"(?:~|\$HOME)/code/dreaming/", root_set.dreaming),
    ]
    if root_set.public_skills:
        patterns.extend(
            [
                (r"\$SKILLS_REPO_ROOT/skills/", root_set.public_skills),
                (r"(?:~|\$HOME)/code/skills/skills/", root_set.public_skills),
            ]
        )
    return [
        (
            re.compile(
                prefix + rf"(?P<path>{NAME_RE}(?:/[A-Za-z0-9._/-]+)?)"
            ),
            root,
        )
        for prefix, root in patterns
    ]


def scan_file(
    path: Path,
    skills: Skills,
    dependencies: dict[str, set[str]],
    queue: deque[Path],
    root_set: Roots,
) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise DependencyError(f"cannot read durable dependency file {path}: {exc}") from exc
    source = str(path)
    current_skill = skill_for_path(path, skills)
    if current_skill:
        add_dependency(dependencies, current_skill, source, skills)

    for match in ABSOLUTE_PATH_RE.finditer(text):
        add_path_reference(
            match.group(1), source, skills, dependencies, queue, root_set
        )

    for pattern, root in variable_patterns(root_set):
        for match in pattern.finditer(text):
            target = (root / match.group("path").rstrip(".,;:)")).resolve(strict=False)
            add_path_reference(str(target), source, skills, dependencies, queue, root_set)

    if current_skill:
        skill_root = skills.authoritative[current_skill]
        for match in RELATIVE_FILE_RE.finditer(text):
            relative = match.group(1).rstrip(".,;:)")
            target = (skill_root / relative).resolve(strict=False)
            if not within(target, skill_root):
                raise DependencyError(f"{source}: relative reference escapes skill root")
            if not target.exists() and path.name != "SKILL.md":
                raise DependencyError(f"{source}: relative reference is missing: {target}")
            if target.is_file():
                queue.append(target)

    for match in SLASH_RE.finditer(text):
        explicit_plugin = match.group(1)
        name = match.group(2).rstrip(".,")
        if name in BUILTIN_SLASH_COMMANDS:
            continue
        if explicit_plugin == "dfrysinger-dreaming" and name not in OWNED:
            raise DependencyError(f"{source}: dreaming plugin does not own skill: {name}")
        if explicit_plugin == "dfrysinger-skills" and name in OWNED:
            raise DependencyError(f"{source}: skills plugin does not own dreaming skill: {name}")
        add_dependency(dependencies, name, source, skills, strict=bool(explicit_plugin))
    for match in BACKTICK_RE.finditer(text):
        add_dependency(dependencies, match.group(1), source, skills, strict=False)


def enumerate_dependencies(launch_agents: Path) -> dict[str, Any]:
    root_set = roots()
    skills = live_skills(root_set)
    dependencies: dict[str, set[str]] = {}
    queue: deque[Path] = deque()
    if not launch_agents.is_dir():
        raise DependencyError(f"LaunchAgents directory does not exist: {launch_agents}")
    prefixes = {
        os.environ.get(
            "DREAMING_LAUNCHD_PREFIX",
            f"com.{os.environ.get('USER') or Path.home().name}.dreaming",
        ),
        os.environ.get(
            "SKILLS_LAUNCHD_PREFIX",
            f"com.{os.environ.get('USER') or Path.home().name}.skills",
        ),
    }
    if any(not re.fullmatch(r"[A-Za-z0-9._-]+", prefix) for prefix in prefixes):
        raise DependencyError(f"invalid launchd prefix: {sorted(prefixes)}")
    plists = sorted(
        {
            path
            for prefix in prefixes
            for path in launch_agents.glob(f"{prefix}*.plist")
        }
    )
    allow_empty = os.environ.get("SKILLS_ALLOW_NO_SCHEDULED_JOBS") == "1"
    if not plists and not allow_empty:
        raise DependencyError(
            f"no managed LaunchAgents found for prefixes {', '.join(sorted(prefixes))}"
        )
    managed_paths = 0
    for plist_path in plists:
        try:
            data = plistlib.loads(plist_path.read_bytes())
        except Exception as exc:
            raise DependencyError(f"cannot parse {plist_path}: {exc}") from exc
        arguments = data.get("ProgramArguments")
        if not isinstance(arguments, list) or not arguments or not all(
            isinstance(item, str) for item in arguments
        ):
            raise DependencyError(
                f"{plist_path}: ProgramArguments must be a non-empty string array"
            )
        program = Path(os.path.expanduser(arguments[0]))
        if program.is_absolute() and not (program.is_file() and os.access(program, os.X_OK)):
            raise DependencyError(f"{plist_path}: program executable is missing: {program}")
        for raw in arguments:
            candidate = Path(os.path.expanduser(raw))
            if candidate.is_absolute() and add_path_reference(
                raw,
                str(plist_path),
                skills,
                dependencies,
                queue,
                root_set,
            ):
                managed_paths += 1
    if plists and managed_paths == 0 and not allow_empty:
        raise DependencyError("managed LaunchAgents contain no durable managed paths")

    visited: set[Path] = set()
    while queue:
        path = queue.popleft().resolve()
        if path in visited:
            continue
        visited.add(path)
        scan_file(path, skills, dependencies, queue, root_set)

    return {
        "schema_version": 2,
        "complete": True,
        "roots": {
            "dreaming": str(root_set.dreaming),
            "shared": str(root_set.shared),
            "public": str(root_set.public) if root_set.public else None,
            "local": str(root_set.local),
        },
        "launch_agents_dir": str(launch_agents.resolve()),
        "launch_agents": [str(path.resolve()) for path in plists],
        "dependencies": [
            {
                "skill": name,
                "namespace": skills.roots[name],
                "path": str(skills.authoritative[name]),
                "sources": sorted(sources),
            }
            for name, sources in sorted(dependencies.items())
        ],
        "scanned_files": [str(path) for path in sorted(visited)],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--launch-agents-dir",
        default=os.environ.get(
            "SKILLS_LAUNCH_AGENTS_DIR",
            str(Path.home() / "Library/LaunchAgents"),
        ),
    )
    parser.add_argument("--check")
    parser.add_argument("--inventory", action="store_true")
    args = parser.parse_args()
    try:
        result = enumerate_dependencies(Path(args.launch_agents_dir))
        names = {item["skill"] for item in result["dependencies"]}
        if args.check and args.check in names:
            sources = next(
                item["sources"]
                for item in result["dependencies"]
                if item["skill"] == args.check
            )
            raise DependencyError(
                f"{args.check} is an implicit pin from durable config: "
                + ", ".join(sources)
            )
        if args.inventory:
            root_set = roots()
            skills = live_skills(root_set)
            dependency_sources = {
                item["skill"]: item["sources"] for item in result["dependencies"]
            }
            rows = []
            for name, path in sorted(skills.authoritative.items()):
                row = {
                    "name": name,
                    "root": skills.roots[name],
                    "path": str(path),
                    "pinned": (path / ".pinned").is_file(),
                    "implicit_pin": name in dependency_sources,
                    "implicit_pin_sources": dependency_sources.get(name, []),
                }
                if name in SHARED and name in skills.public_catalog:
                    row["catalog_path"] = str(skills.public_catalog[name])
                    row["published_identity"] = "shared/catalog"
                rows.append(row)
            result["skills"] = rows
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except DependencyError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
