from __future__ import annotations

from archy.app import run


def _enable_crash_diagnostics() -> None:
    """Let a hung/crashed run be diagnosed: dump all thread stack traces on a
    fatal error, and on SIGQUIT (Ctrl-\\ in a terminal) so a freeze can be
    inspected. Harmless in normal use; only prints when triggered."""
    import contextlib
    import faulthandler

    with contextlib.suppress(Exception):
        faulthandler.enable()
        import signal

        if hasattr(signal, "SIGQUIT"):
            faulthandler.register(signal.SIGQUIT, all_threads=True)


def main() -> None:
    """Console entry point. Launches the GUI."""
    _enable_crash_diagnostics()
    raise SystemExit(run())


if __name__ == "__main__":
    main()
