"""Isolated learn-lab Docker for testing catalog install recipes."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from orchestrator.config import get_config
from orchestrator.sandbox import DockerCli, DockerSandbox, SandboxInfo, SandboxSession
from orchestrator.tools.catalog import ToolCatalog
from orchestrator.tools.catalog.catalog import CatalogProvisioner
from orchestrator.utils.job_env import JobEnv
from orchestrator.utils.service import SharedService

logger = logging.getLogger(__name__)

LEARN_LAB_LABEL = "orchestrator.role=learn-lab"
LEARN_LAB_FILTER = "label=orchestrator.learn_lab=1"
_DEFAULT_IMAGE = "debian:bookworm-slim"


class LearnLab(SharedService):
    """Minimal throwaway container for testing catalog install recipes.

    At most one lab exists: fixed name ``LEARN_LAB_CONTAINER`` (default
    ``{sandbox_prefix}-learn-lab``). ``ensure`` always recreates it so each
    start is a clean image with no leftover installs.

    Public API is classmethods (``LearnLab.delete()``) over the process singleton.
    """

    @classmethod
    def image(cls) -> str:
        # Independent of SANDBOX_IMAGE (often a local build tag). Override with
        # LEARN_LAB_IMAGE; default matches Peon: debian:bookworm-slim.
        return (os.environ.get("LEARN_LAB_IMAGE") or _DEFAULT_IMAGE).strip() or _DEFAULT_IMAGE

    @classmethod
    def container_name(cls) -> str:
        prefix = (get_config().sandbox_prefix or "orchestrator").strip()
        return (os.environ.get("LEARN_LAB_CONTAINER") or f"{prefix}-learn-lab").strip()

    @classmethod
    def staging_root(cls) -> Path:
        root = Path(get_config().workspaces_dir).resolve().parent / "learn_lab"
        root.mkdir(parents=True, exist_ok=True)
        return root

    @classmethod
    def _cli(cls) -> DockerCli:
        return DockerCli.shared()

    @classmethod
    def status(cls) -> dict[str, Any]:
        name = cls.container_name()
        image = cls.image()
        cli = cls._cli()
        ok, err = cli.daemon_ok()
        if not ok:
            return {
                "name": name,
                "image": image,
                "exists": False,
                "running": False,
                "docker": False,
                "error": (err or "")[:300],
            }
        exists, running = cli.inspect_running(name)
        return {
            "name": name,
            "image": image,
            "exists": exists,
            "running": running,
            "docker": True,
            "error": "",
        }

    @classmethod
    def ensure(cls) -> SandboxInfo:
        """Wipe any prior lab and create a fresh container (no leftover tools)."""
        cli = cls._cli()
        if not cli.available():
            raise RuntimeError("docker CLI missing — cannot run Learn lab tests")
        name = cls.container_name()
        image = cls.image()
        cli.require_daemon()
        # Always recreate so apt/git_clone/pip from a previous test cannot leak.
        wiped = cls.delete()
        if not wiped.get("ok"):
            raise RuntimeError(
                f"Failed to reset Learn lab {name}: {wiped.get('error') or 'unknown'}"
            )

        action = cli.ensure_running(
            name,
            image=image,
            workdir="/tmp",
            labels=[LEARN_LAB_LABEL, "orchestrator.learn_lab=1"],
            pull_image=True,
        )
        info = SandboxInfo(
            project_id="",
            name=name,
            mode="learn-lab",
            action=action,
            image=image,
            workdir="/tmp",
            base_commands=frozenset(),
        )
        cls._bind(info)
        return info

    @classmethod
    def delete(cls) -> dict[str, Any]:
        """Remove the canonical lab and any labeled duplicates."""
        name = cls.container_name()
        cli = cls._cli()
        try:
            cli.require_daemon()
        except RuntimeError as exc:
            return {"ok": False, "removed": False, "name": name, "error": str(exc)}
        removed_any = False
        exists, _ = cli.inspect_running(name)
        if exists:
            rm = cli.rm_force(name)
            if not rm.ok:
                return {
                    "ok": False,
                    "removed": False,
                    "name": name,
                    "error": (rm.stderr or rm.stdout or "").strip(),
                }
            removed_any = True
        for cid in cli.ps_ids(LEARN_LAB_FILTER):
            cli.rm_force(cid)
            removed_any = True
        SandboxSession.reset()
        if not removed_any:
            return {"ok": True, "removed": False, "name": name, "reason": "not found"}
        return {"ok": True, "removed": True, "name": name}

    @classmethod
    def _bind(cls, info: SandboxInfo) -> None:
        work = (info.workdir or "/tmp").strip() or "/tmp"
        if JobEnv.current():
            JobEnv.bind({**JobEnv.current(), "ORCHESTRATOR_SANDBOX_WORKDIR": work})
        else:
            JobEnv.bind({"ORCHESTRATOR_SANDBOX_WORKDIR": work})
        SandboxSession.bind(DockerSandbox(info, docker_bin=cls._cli().require_bin()))

    @classmethod
    def connect(cls) -> SandboxInfo:
        """Bind SandboxSession to the existing running lab (no create)."""
        cli = cls._cli()
        if not cli.available():
            raise RuntimeError("docker CLI missing — cannot run Learn lab tests")
        name = cls.container_name()
        st = cls.status()
        if not st.get("running"):
            raise RuntimeError(
                f"Learn lab {name!r} is not running — call LearnLab.ensure() first."
            )
        info = SandboxInfo(
            project_id="",
            name=name,
            mode="learn-lab",
            action="reused",
            image=cls.image(),
            workdir="/tmp",
            base_commands=frozenset(),
        )
        cls._bind(info)
        return info

    @classmethod
    def test_tool_install(
        cls, *, yaml_text: str, install_script: str = ""
    ) -> dict[str, Any]:
        """Run catalog provision for one recipe inside the Learn lab."""
        import yaml

        text = (yaml_text or "").strip()
        if not text:
            raise ValueError("yaml is required")
        try:
            raw = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ValueError(f"invalid YAML: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError("tool YAML must be a mapping")

        tid = str(raw.get("id") or "").strip()
        if not tid or "/" in tid or ".." in tid or not tid.replace("-", "").isalnum():
            raise ValueError("tool id is required and must be a simple slug")

        catalog_dir = Path(
            tempfile.mkdtemp(prefix=f"{tid}-", dir=str(cls.staging_root()))
        )
        (catalog_dir / f"{tid}.yaml").write_text(text + "\n", encoding="utf-8")
        script = (install_script or "").strip()
        if script:
            (catalog_dir / f"{tid}.sh").write_text(script + "\n", encoding="utf-8")

        prev_env = os.environ.get("TOOLS_CATALOG_DIR")
        os.environ["TOOLS_CATALOG_DIR"] = str(catalog_dir)
        ToolCatalog.shared().invalidate()
        log_lines: list[str] = []
        try:
            try:
                info = cls.ensure()
            except RuntimeError as exc:
                return {
                    "ok": False,
                    "verified": False,
                    "message": str(exc),
                    "log": "",
                    "lab": cls.status(),
                    "tool_id": tid,
                }
            log_lines.append(f"lab={info.name} image={info.image} action={info.action}")

            bootstrap = SandboxSession.current().exec(
                "export DEBIAN_FRONTEND=noninteractive; "
                "apt-get update -qq && "
                "apt-get install -y --no-install-recommends ca-certificates curl bash "
                ">/dev/null",
                timeout=300,
                shell=True,
            )
            if bootstrap.code != 0:
                detail = (bootstrap.stderr or bootstrap.stdout or "").strip()[:500]
                log_lines.append(f"bootstrap failed: {detail}")
                return {
                    "ok": False,
                    "verified": False,
                    "message": f"lab bootstrap failed: {detail}",
                    "log": "\n".join(log_lines),
                    "lab": cls.status(),
                    "tool_id": tid,
                }

            tool = ToolCatalog.shared().by_id(tid)
            if tool is None:
                return {
                    "ok": False,
                    "verified": False,
                    "message": f"catalog did not load tool {tid!r}",
                    "log": "\n".join(log_lines),
                    "lab": cls.status(),
                    "tool_id": tid,
                }

            ok, msg = CatalogProvisioner.shared().provision_binary(tool.binary or tool.id)
            log_lines.append(msg)
            verify = CatalogProvisioner.run_verify(tool)
            log_lines.append("--- verify ---")
            log_lines.append(str(verify.get("output") or ""))
            verified = bool(verify.get("ok"))
            return {
                "ok": bool(ok and verified),
                "verified": verified,
                "message": msg,
                "log": "\n".join(log_lines),
                "verify_output": str(verify.get("output") or ""),
                "verify_steps": verify.get("steps") or [],
                "lab": cls.status(),
                "tool_id": tid,
            }
        finally:
            if prev_env is None:
                os.environ.pop("TOOLS_CATALOG_DIR", None)
            else:
                os.environ["TOOLS_CATALOG_DIR"] = prev_env
            ToolCatalog.shared().invalidate()
