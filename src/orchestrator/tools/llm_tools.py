"""LangChain capability tools for the sandbox agent.

Importing this module registers tools via ``@capability`` decorators.
Call ``ensure_registered()`` (or ``ensure_tools_registered()``) so registration
runs even when this module was not imported yet.
"""

from __future__ import annotations

import os

from langchain_core.tools import tool

from orchestrator.agent.context import get_agent_config, get_context
from orchestrator.capabilities.registry import CapabilityGroup, capability, ensure_registered
from orchestrator.runtime.shell import ShellRunner
from orchestrator.sandbox import SandboxSession
from orchestrator.sandbox.backend import require_docker_bound
from orchestrator.skills.execute import SkillExecutionDispatcher, SkillRunRequest
from orchestrator.skills.misc.catalog import filter_skills
from orchestrator.skills.misc.registry import SkillRegistry


def ensure_tools_registered() -> None:
    """Import capability tools (alias for ``capabilities.ensure_registered``)."""
    ensure_registered()


def _require_sandbox() -> str | None:
    return require_docker_bound(SandboxSession.current().info.mode)


def _sandbox_summary() -> str:
    info = SandboxSession.current().info
    return f"name={info.name} mode={info.mode} session_id={info.project_id or '-'}"


def _emit(kind: str, content: str, **metadata: object) -> None:
    get_context().ports.emit(kind, content, **metadata)


@capability(CapabilityGroup.SANDBOX)
@tool
def sandbox_setup() -> str:
    """Confirm the Docker sandbox is bound (host provisions before the agent)."""
    err = _require_sandbox()
    return err or ("Sandbox ready — " + _sandbox_summary())


@capability(CapabilityGroup.SANDBOX)
@tool
def sandbox_status() -> str:
    """Show bound sandbox mode and name."""
    err = _require_sandbox()
    if err:
        return err
    lines = [_sandbox_summary()]
    try:
        which = SandboxSession.current().which
        for binary in ("python3", "bash", "curl", "apt-get"):
            lines.append(f"{binary}={'yes' if which(binary) else 'no'}")
    except Exception:
        pass
    return "\n".join(lines)


@capability(CapabilityGroup.SANDBOX)
@tool
def provision_cli(binary: str, package: str = "", skill_name: str = "") -> str:
    """Install/verify a CLI (tools/catalog → skill INSTALL.md → LLM)."""
    err = _require_sandbox()
    if err:
        return err
    name = (binary or "").strip()
    if not name:
        return "Error: provide a binary name."
    from orchestrator.runtime.resolve import InstallResolver

    skill = (skill_name or "").strip() or (
        os.environ.get("ORCHESTRATOR_SKILL_NAME") or ""
    ).strip()
    ok, msg = InstallResolver.shared().resolve(
        name, package=package, skill_name=skill
    )
    if ok:
        _emit("log", msg or f"provisioned {name}")
        return msg or f"provisioned {name}"
    return f"Error: {msg}"


@capability(CapabilityGroup.SANDBOX)
@tool
def run_cli(command: str) -> str:
    """Run an ad-hoc shell command in the bound Docker sandbox."""
    err = _require_sandbox()
    if err:
        return err
    cmd = (command or "").strip()
    if not cmd:
        return "Error: empty command."
    _emit("tool", f"run_cli: {cmd[:200]}")
    return f"exit={ShellRunner.shared().run_shell(cmd)}"


@capability(CapabilityGroup.SANDBOX, tags={"code"})
@tool
def run_code(code: str, language: str = "python") -> str:
    """Execute a short code snippet inside the sandbox (python or bash)."""
    err = _require_sandbox()
    if err:
        return err
    src = (code or "").strip()
    if not src:
        return "Error: empty code."
    lang = (language or "python").strip().lower()
    _emit("tool", f"run_code({lang}): {src[:120]}")
    if lang in {"python", "py", "python3"}:
        cmd = f"python3 - <<'ORCHESTRATOR_EOF'\n{src}\nORCHESTRATOR_EOF"
    elif lang in {"bash", "sh", "shell"}:
        cmd = (
            f"bash -lc {src!r}"
            if "\n" not in src
            else f"bash <<'ORCHESTRATOR_EOF'\n{src}\nORCHESTRATOR_EOF"
        )
    else:
        return f"Error: unsupported language {language!r} (use python or bash)"
    return f"exit={ShellRunner.shared().run_shell(cmd)}"


@capability(CapabilityGroup.SANDBOX, tags={"watchdog", "periodic"})
@tool
def run_periodic(
    command: str,
    interval_seconds: int = 30,
    duration_seconds: int = 120,
    package: str = "",
) -> str:
    """Repeat a sandbox shell command on an interval (watchdog ticks)."""
    err = _require_sandbox()
    if err:
        return err
    cmd = (command or "").strip()
    if not cmd:
        return "Error: empty command."
    _emit(
        "tool",
        f"run_periodic({interval_seconds}s/{duration_seconds}s): {cmd[:160]}",
    )
    code = ShellRunner.shared().run_periodic(
        cmd,
        interval_seconds=int(interval_seconds),
        duration_seconds=int(duration_seconds),
        package=package or "",
    )
    return f"exit={code}"


@capability(CapabilityGroup.SKILLS)
@tool
def run_skill_script(
    skill_name: str, script: str = "scripts/run.py", command: str = ""
) -> str:
    """Run a skill script (skills/<name>/scripts/…) inside the sandbox."""
    err = _require_sandbox()
    if err:
        return err
    skill = (skill_name or "").strip()
    path = (script or "scripts/run.py").strip() or "scripts/run.py"
    if not skill:
        return "Error: skill_name required."
    _emit(
        "tool",
        f"run_skill_script({skill}, {path}"
        + (f", command={command[:120]!r}" if command else "")
        + ")",
    )
    result = SkillExecutionDispatcher.shared().run(
        SkillRunRequest(skill_name=skill, script=path, command=command or "")
    )
    out = (result.output or "").strip()
    if len(out) > 12000:
        out = out[:12000] + "\n…(truncated)"
    return f"ok={result.ok} exit={result.exit_code}\n{out}"


@capability(CapabilityGroup.SKILLS)
@tool
def skills_list() -> str:
    """List jobable skills from the filesystem catalog."""
    skills = filter_skills(
        SkillRegistry.shared().get_registry().values(), jobable_only=True
    )
    if not skills:
        return "No jobable skills."
    lines = []
    for s in sorted(skills, key=lambda x: x.name):
        lines.append(f"{s.name} [{s.category or '-'}] — {(s.description or '')[:120]}")
    return "\n".join(lines)


@capability(CapabilityGroup.SKILLS)
@tool
def skill_view(name: str, path: str = "") -> str:
    """Show a skill's instructions (or a reference file under the skill dir)."""
    del path
    skill = SkillRegistry.shared().load_skill((name or "").strip())
    if skill is None:
        return f"Skill not found: {name!r}"
    body = (skill.instructions or "").strip() or skill.description or ""
    return (
        f"name: {skill.name}\n"
        f"category: {skill.category or '-'}\n"
        f"allowed-tools: {' '.join(skill.tools or []) or '-'}\n"
        f"requires_clis: {', '.join(skill.toolkit or []) or '-'}\n\n"
        f"{body[:6000]}"
    )


@capability(CapabilityGroup.ENGAGEMENT)
@tool
def list_objectives() -> str:
    """List host-defined objectives for the current session (via ports)."""
    return get_context().ports.list_objectives()


@capability(CapabilityGroup.ENGAGEMENT)
@tool
def update_objective_status(seq: int, status: str, note: str = "") -> str:
    """Update an objective status via host ports."""
    return get_context().ports.update_objective_status(int(seq), status, note)


@capability(CapabilityGroup.ENGAGEMENT)
@tool
def record_finding(
    title: str,
    severity: str = "info",
    kind: str = "observation",
    evidence: str = "",
    host: str = "",
) -> str:
    """Record one structured finding via host ports."""
    return get_context().ports.record_finding(
        title=title,
        severity=severity,
        kind=kind,
        evidence=evidence,
        host=host,
    )


@capability(CapabilityGroup.ENGAGEMENT)
@tool
def record_findings(findings_json: str) -> str:
    """Record multiple findings from a JSON list via host ports."""
    return get_context().ports.record_findings(findings_json)


@capability(CapabilityGroup.ENGAGEMENT)
@tool
def list_findings(kind: str = "") -> str:
    """List findings for the current session via host ports."""
    return get_context().ports.list_findings(kind)


@capability(CapabilityGroup.CORE, tags={"subagent"})
@tool
def spawn_subagent(title: str, description: str, skill_names: str = "") -> str:
    """Spawn a child agent run via host ports. skill_names: comma-separated ids."""
    cfg = get_agent_config()
    ctx = get_context()
    if ctx.depth >= cfg.max_subagent_depth:
        return f"Error: subagent depth limit ({cfg.max_subagent_depth})"
    names = [n.strip() for n in (skill_names or "").split(",") if n.strip()]
    try:
        child_id = ctx.ports.spawn_child(
            title=title, description=description, skill_names=names
        )
    except Exception as exc:
        return f"Error spawning subagent: {exc}"
    _emit(
        "log",
        f"spawned subagent {child_id}: {title}",
        metadata={"event": "spawn_subagent", "child_id": child_id},
    )
    return f"spawned child job {child_id}"


@capability(CapabilityGroup.CORE, tags={"subagent"})
@tool
def wait_for_subagents(timeout_seconds: int = 600, job_ids: str = "") -> str:
    """Check whether spawned child runs finished (non-blocking; call again later)."""
    ids = [j.strip() for j in (job_ids or "").split(",") if j.strip()] or None
    try:
        return get_context().ports.wait_children(
            job_ids=ids, timeout_seconds=int(timeout_seconds)
        )
    except Exception as exc:
        return f"Error waiting for subagents: {exc}"
