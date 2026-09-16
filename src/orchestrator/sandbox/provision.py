"""Docker sandbox provision / bind / remove (host-policy free of Django)."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from orchestrator.config import RuntimeConfig, get_config
from orchestrator.sandbox.backend import BaseCommandCache, SandboxInfo
from orchestrator.sandbox.cli import DockerCli
from orchestrator.sandbox.docker import DockerSandbox
from orchestrator.sandbox.session import SandboxSession
from orchestrator.utils.job_env import JobEnv
from orchestrator.utils.service import SharedService

logger = logging.getLogger(__name__)


class SandboxProvisioner(SharedService):
    """Create/start a Docker sandbox and bind it for the agent runtime."""

    def __init__(self, *, config: RuntimeConfig | None = None) -> None:
        self._config = config

    def _cfg(self) -> RuntimeConfig:
        return self._config or get_config()

    def per_session(self) -> bool:
        return bool(self._cfg().sandbox_enabled)

    def shared_container_name(self) -> str:
        prefix = (self._cfg().sandbox_prefix or "orchestrator").strip()
        return f"{prefix}-shared"

    def dedicated_name(self, session_id: str) -> str:
        prefix = (self._cfg().sandbox_prefix or "orchestrator").strip()
        safe = DockerCli.sanitize_name_fragment(session_id)
        budget = max(8, 63 - len(prefix) - 1)
        return f"{prefix}-{safe[:budget]}"

    def container_name(self, session_id: str) -> str:
        if not self.per_session():
            return self.shared_container_name()
        return self.dedicated_name(session_id)

    def image(self) -> str:
        return (self._cfg().sandbox_image or "orchestrator-sandbox:local").strip()

    def _cli(self) -> DockerCli:
        return DockerCli.shared()

    def provision(
        self,
        session_id: str,
        *,
        workspace: Path | None = None,
        skills_dir: Path | None = None,
        tools_dir: Path | None = None,
    ) -> SandboxInfo:
        cfg = self._cfg()
        sid = str(session_id or "").strip() or "default"
        cli = self._cli()
        if not cli.available():
            raise RuntimeError(
                "docker CLI missing — skill/CLI execution requires a Docker sandbox"
            )
        cli.require_daemon()

        image = self.image()
        ws = Path(workspace or (cfg.workspaces_dir / sid)).resolve()
        ws.mkdir(parents=True, exist_ok=True)

        if self.per_session():
            name = self.dedicated_name(sid)
            mode = "docker"
            volume_args, container_ws = self._volume_args_per_session(
                ws, skills_dir, tools_dir
            )
            labels = [
                "orchestrator.role=session-sandbox",
                f"orchestrator.session_id={sid}",
            ]
        else:
            name = self.shared_container_name()
            mode = "shared"
            volume_args, container_ws = self._volume_args_shared(
                ws, skills_dir, tools_dir
            )
            labels = ["orchestrator.role=shared-sandbox"]

        action = cli.ensure_running(
            name,
            image=image,
            workdir=container_ws,
            labels=labels,
            volume_args=volume_args,
        )

        if JobEnv.current():
            # Replace map in-place for callers that already bound JobEnv.
            JobEnv.bind(
                {**JobEnv.current(), "ORCHESTRATOR_SANDBOX_WORKDIR": container_ws}
            )
        else:
            JobEnv.bind({"ORCHESTRATOR_SANDBOX_WORKDIR": container_ws})

        cache = BaseCommandCache.shared()
        base = cache.get(image)
        info = SandboxInfo(
            project_id=sid,
            name=name,
            mode=mode,
            action=action,
            image=image,
            workdir=container_ws,
            base_commands=base or frozenset(),
        )
        backend = DockerSandbox(info, docker_bin=cli.require_bin())
        if base is None:
            base = backend.base_commands()
            cache.put(image, base)
            info = SandboxInfo(
                project_id=sid,
                name=name,
                mode=mode,
                action=action,
                image=image,
                workdir=container_ws,
                base_commands=base,
            )
            backend.info = info
        SandboxSession.bind(backend)
        return info

    def remove(self, session_id: str) -> dict[str, Any]:
        sid = str(session_id or "").strip()
        if not sid:
            return {"action": "skipped", "reason": "no session_id"}
        cli = self._cli()
        shared = self.shared_container_name()
        dedicated = self.dedicated_name(sid)
        if not cli.available():
            return {
                "action": "skipped",
                "reason": "docker unavailable",
                "name": dedicated,
            }

        removed: list[str] = []
        errors: list[str] = []

        def _rm(target: str) -> None:
            if target == shared:
                return
            proc = cli.rm_force(target)
            if proc.ok:
                removed.append(target)
                return
            err = (proc.stderr or proc.stdout or "").strip()
            if err and "No such container" not in err:
                errors.append(f"{target}: {err}")

        try:
            _rm(dedicated)
        except Exception as exc:
            errors.append(f"{dedicated}: {exc}")

        try:
            for cid in cli.ps_ids(f"label=orchestrator.session_id={sid}"):
                if cid in removed or cid == dedicated:
                    continue
                try:
                    _rm(cid)
                except Exception as exc:
                    errors.append(f"{cid}: {exc}")
        except Exception as exc:
            errors.append(str(exc))

        SandboxSession.reset()
        if removed:
            return {
                "action": "removed",
                "name": dedicated,
                "removed": removed,
                "errors": errors,
            }
        if errors:
            return {"action": "error", "name": dedicated, "errors": errors}
        return {"action": "missing", "name": dedicated}

    def _skills_tools(
        self, skills_dir: Path | None, tools_dir: Path | None
    ) -> tuple[Path, Path]:
        cfg = self._cfg()
        skills = Path(skills_dir or cfg.skills_dir).resolve()
        tools = Path(tools_dir or cfg.tools_catalog_dir).resolve()
        tools_root = tools.parent if tools.name == "catalog" else tools
        return skills, tools_root

    def _library_volume_args(self) -> list[str]:
        """Mount orchestrator package parent so sandbox skill scripts can ``import orchestrator``."""
        try:
            import orchestrator as _pkg

            # …/src/orchestrator/__init__.py → …/src
            src_root = Path(_pkg.__file__).resolve().parents[1]
        except Exception:
            return []
        if not (src_root / "orchestrator").is_dir():
            return []
        cli = self._cli()
        return ["-v", f"{cli.host_bind_path(src_root)}:/orchestrator-src:ro"]

    def _socket_volume_args(self) -> list[str]:
        """Mount host dirs that hold stream/RPC sockets at the same path in the container."""
        parents: list[Path] = []
        for key in ("ORCHESTRATOR_RPC_SOCKET", "ORCHESTRATOR_STREAM_SOCKET"):
            raw = JobEnv.get(key) or (os.environ.get(key) or "").strip()
            if not raw:
                continue
            parent = Path(raw).expanduser().resolve().parent
            if parent.is_dir() and parent not in parents:
                parents.append(parent)
        extra = (JobEnv.get("ORCHESTRATOR_SOCKETS_DIR") or os.environ.get("ORCHESTRATOR_SOCKETS_DIR") or "").strip()
        if extra:
            p = Path(extra).expanduser().resolve()
            if p.is_dir() and p not in parents:
                parents.append(p)
        cli = self._cli()
        args: list[str] = []
        for parent in parents:
            host = cli.host_bind_path(parent)
            args.extend(["-v", f"{host}:{parent.as_posix()}"])
        return args

    def _volume_args_per_session(
        self,
        ws: Path,
        skills_dir: Path | None,
        tools_dir: Path | None,
    ) -> tuple[list[str], str]:
        skills, tools_root = self._skills_tools(skills_dir, tools_dir)
        cli = self._cli()
        container_ws = "/workspace"
        volume_args = [
            "-v",
            f"{cli.host_bind_path(ws)}:/workspace",
            "-v",
            f"{cli.host_bind_path(skills)}:/skills:ro",
            "-v",
            f"{cli.host_bind_path(tools_root)}:/tools:ro",
            *self._library_volume_args(),
            *self._socket_volume_args(),
        ]
        return volume_args, container_ws

    def _volume_args_shared(
        self,
        ws: Path,
        skills_dir: Path | None,
        tools_dir: Path | None,
    ) -> tuple[list[str], str]:
        skills, tools_root = self._skills_tools(skills_dir, tools_dir)
        cfg = self._cfg()
        root = Path(cfg.workspaces_dir).resolve()
        try:
            rel = ws.resolve().relative_to(root).as_posix()
        except ValueError:
            rel = ws.name
        container_ws = f"/workspace/{rel}" if rel else "/workspace"
        cli = self._cli()
        volume_args = [
            "-v",
            f"{cli.host_bind_path(root)}:/workspace",
            "-v",
            f"{cli.host_bind_path(skills)}:/skills:ro",
            "-v",
            f"{cli.host_bind_path(tools_root)}:/tools:ro",
            *self._library_volume_args(),
            *self._socket_volume_args(),
        ]
        return volume_args, container_ws
