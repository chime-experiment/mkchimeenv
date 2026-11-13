"""Helpers to manage venvs."""

import json
import re
import subprocess
import sys
import threading
import tomllib
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


def uv_pip_command(
    env: VirtualEnv,
    pkgstr: str | list[str],
    options: list,
    console: Console,
    uv_command: str = "install",
    skip_output: bool = False,
):
    """Run a pip command in a venv.

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
    if isinstance(pkgstr, str):
        pkgstr = [pkgstr]

    proc = subprocess.Popen(
        [
            "uv",
            "pip",
            uv_command,
            "--python",
            Path(env.path) / "bin" / "python",
            *map(str, pkgstr),
            *options,
        ],
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
        sys.exit(1)

    return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)


def resolve_dependencies(
    package: str | list[str],
    env: VirtualEnv,
    console: Console,
    ignore_deps: list | None = None,
    skip_output: bool = False,
):
    if isinstance(package, str):
        package = [package]

    if ignore_deps is None:
        ignore_deps = []

    desired = uv_pip_command(
        env,
        package,
        ["--no-header", "--no-annotate", "--quiet", "--format", "pylock.toml"],
        console,
        uv_command="compile",
        skip_output=skip_output,
    )

    wants = tomllib.loads(desired.stdout)

    state = uv_pip_command(
        env,
        [],
        ["--format", "json"],
        console,
        uv_command="list",
        skip_output=skip_output,
    )

    has_list = json.loads(state.stdout)

    # convert `has` from a list to a dict for easier
    # membership lookup
    has = {entry["name"]: entry for entry in has_list}

    # make sure that possible variants of the dependency names
    # are handled
    ignore_variants = set(ignore_deps)
    for pkg in ignore_deps:
        ignore_variants.add(pkg.replace("-", "_"))
        ignore_variants.add(pkg.replace("_", "-"))

    requirements = []

    for entry in wants.get("packages", []):
        wants_name = entry["name"]
        wants_version = entry["version"]

        if wants_name in ignore_variants:
            continue

        has_meta = has.get(wants_name, {})

        if has_meta.get("version") == wants_version:
            # already exists
            continue

        if "editable_project_location" in has_meta:
            # don't want to overwrite editable installs
            continue

        # By this point, we want to install the package listed in
        # `wants`, so figure out the package string
        if "vcs" in entry:
            package_string = f"{wants_name}@{entry['vcs']['url']}"
        else:
            package_string = f"{wants_name}=={wants_version}"

        requirements.append(package_string)

    return requirements
