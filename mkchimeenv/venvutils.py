"""Helpers to manage venvs."""

import re
import subprocess
import threading
from pathlib import Path

from rich.console import Console
from virtualenvapi.manage import VirtualEnvironment


class VirtualEnv(VirtualEnvironment):
    """Fix name parsing for install packages."""

    @property
    def installed_package_names(self):
        names = []
        for name, _ in self.installed_packages:
            name = re.split(r"\s*@\s*|==", name, maxsplit=1)[0]
            names.append(name.strip().lower())

        return names


def _drain_stream(stream, lines, callback):
    for line in stream:
        lines.append(line)

        if callback:
            callback(line.rstrip())

    stream.close()


def install_to_env(
    env: VirtualEnv,
    pkgstr: str,
    options: list,
    console: Console,
    skip_output: bool = False,
):
    """Install a package into a venv.

    Intended to be a more verbose version of `env.install`.

    Parameters
    ----------
    env
        Virtual environment manager.
    pkgstr
        Package to install. Supports whatever formats pip supports.
    options
        List of options passed to pip via `subprocess.Popen`.
    console
        Console handler.
    skip_output
        If True, do not display the subprocess stdout or stderr.

    Returns
    -------
    CompletedProcess
        Same output as returned by `subprocess.run`.
    """
    proc = subprocess.Popen(
        [Path(env.path) / "bin" / "pip", "install", pkgstr, *options],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    # Ensure that we don't block `stderr` while printing `stdout`
    output_lines = []
    error_lines = []

    output_callback = (
        None
        if skip_output
        else lambda s: console.print(s, soft_wrap=True, highlight=True)
    )
    error_callback = (
        None if skip_output else lambda s: console.print(s, soft_wrap=True, style="red")
    )

    # drain in separate threads. This lets us separate outputs while
    # ensuring we never hit a deadlock, in the case where we want to
    # write console output in real time
    threads = [
        threading.Thread(
            target=_drain_stream, args=(proc.stdout, output_lines, output_callback)
        ),
        threading.Thread(
            target=_drain_stream, args=(proc.stderr, error_lines, error_callback)
        ),
    ]
    # run threads to completion
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    returncode = proc.wait()

    stdout = "".join(output_lines)
    stderr = "".join(error_lines)

    if returncode != 0:
        console.print(
            f"[bold red] Subprocess failed with return code {proc.returncode}[/bold red]\n\n"
            f"stdout: {stdout}\n\n"
            f"stderr: {stderr}"
        )
        raise RuntimeError()

    return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)
