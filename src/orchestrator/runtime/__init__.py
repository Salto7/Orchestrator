"""Core runtime exports: install resolve + shell runner + host bridges."""

from orchestrator.runtime.shell import ProvisionService, ShellRunner, StreamEmitter

__all__ = [
    "InstallResolver",
    "ProvisionService",
    "RpcServer",
    "ShellRunner",
    "StreamEmitter",
    "StreamSocketServer",
    "close_rpc_server",
    "start_rpc_server",
]


def __getattr__(name: str):
    if name == "InstallResolver":
        from orchestrator.runtime.resolve import InstallResolver

        return InstallResolver
    if name in {"RpcServer", "close_rpc_server", "start_rpc_server"}:
        from orchestrator.runtime import rpc as _rpc

        return getattr(_rpc, name)
    if name == "StreamSocketServer":
        from orchestrator.runtime.stream_server import StreamSocketServer

        return StreamSocketServer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
