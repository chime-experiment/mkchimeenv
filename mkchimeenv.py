"""Install and manage CHIME pipeline environments."""

from pathlib import Path
import json
import sys
import subprocess
import venv

import click
import git

from rich.console import Console
from rich.progress import Progress

from virtualenvapi.manage import VirtualEnvironment

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("mkchimeenv")
except PackageNotFoundError:
    # package is not installed
    pass

del version, PackageNotFoundError


# Installing this repo will install all other
# CHIME packages and dependencies
MAIN_REPO = "ch_pipeline"


def _clone_path(repo, ssh=True):
    if ssh:
        return f"ssh://git@github.com/{repo}"
    else:
        return f"https://github.com/{repo}.git"


def public_repositories(ssh=True):
    return {
        # radio cosmology packages
        "caput": (_clone_path("radiocosmology/caput", ssh=ssh), None),
        "cora": (_clone_path("radiocosmology/cora", ssh=ssh), None),
        "driftscan": (_clone_path("radiocosmology/driftscan", ssh=ssh), None),
        "draco": (_clone_path("radiocosmology/draco", ssh=ssh), None),
        "fluxcat": (_clone_path("radiocosmology/fluxcat", ssh=ssh), None),
        # chimedb core code and extensions
        "chimedb": (_clone_path("chime-experiment/chimedb", ssh=ssh), None),
        "chimedb-data_index": (
            _clone_path("chime-experiment/chimedb_di", ssh=ssh),
            None,
        ),
        "chimedb-dataflag": (
            _clone_path("chime-experiment/chimedb_dataflag", ssh=ssh),
            None,
        ),
        "chimedb-dataset": (
            _clone_path("chime-experiment/chimedb_dataset", ssh=ssh),
            None,
        ),
        # chime specific repositories
        "ch_ephem": (_clone_path("chime-experiment/ch_ephem", ssh=ssh), None),
        "ch_util": (_clone_path("chime-experiment/ch_util", ssh=ssh), None),
        "ch_pipeline": (_clone_path("chime-experiment/ch_pipeline", ssh=ssh), None),
    }


private_repositories = {
    "chimedb-config": (_clone_path("chime-experiment/chimedb_config", ssh=True), None),
}


def match_opcode(opcode: int) -> tuple[int, str, bool]:
    """Match GitPython opcode to a description of the operation.

    Parameters
    ----------
    opcode
        GitPython opcode.

    Returns
    -------
    code
        Code for the actual operation being performed. This differs from the opcode as
        the input opcode may contain extra information.
    msg
        Text description of operation.
    done
        Has the operation just finished.
    """

    codes = (
        (git.RemoteProgress.COUNTING, "Counting (remote)"),
        (git.RemoteProgress.COMPRESSING, "Compressing (remote)"),
        (git.RemoteProgress.RECEIVING, "Receiving"),
        (git.RemoteProgress.RESOLVING, "Resolving"),
    )

    done = (opcode & git.RemoteProgress.END) != 0

    for code, msg in codes:
        if opcode & code != 0:
            return (code, msg, done)
    else:
        return (0, "Unknown", done)


class RichProgress(git.RemoteProgress):
    """Use `rich` to show the progress of a GitPython operation.

    This shows two bars, one for the overall operation progress, just indicating at
    which stage the process is and a second for the progress of the current operation
    (which is more easily metered)

    Parameters
    ----------
    label
        A string label to use on the progress bar.
    progress
        The Progress instance to add the meters too.
    """

    def __init__(self, label: str, progress: Progress):
        super().__init__()

        self.progress = progress
        self.overall_task = progress.add_task(f"{label}", total=4)

        self.tasks: dict[int, int] = {}

    def update(self, op_code, cur_count, max_count=None, message=""):
        code, msg, done = match_opcode(op_code)

        if code not in self.tasks:
            self.tasks[code] = self.progress.add_task(msg, total=max_count)

        task = self.tasks[code]
        self.progress.update(task, completed=cur_count, visible=(not done))

        if done:
            self.progress.advance(self.overall_task)


def install_to_env(
    env: VirtualEnvironment,
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
        bufsize=-1 if skip_output else 1,
    )
    if not skip_output:
        for line in proc.stdout:
            console.print(line.strip(), soft_wrap=True, highlight=True)

    # Capture output. If output was being printed to the console,
    # we don't expect anything here
    stdout, stderr = proc.communicate()
    proc.wait()

    returncode = proc.poll()
    if returncode != 0:
        console.print(
            f"[bold red] Subprocess failed with return code {returncode}[/bold red]\n\n"
            f"stdout: {stdout}\n\n"
            f"stderr: {stderr}"
        )
        raise RuntimeError()

    return subprocess.CompletedProcess(proc.args, returncode, stdout, stderr)


def labeller(enumerable):
    """For each item in an iterable, also return a string label giving the position."""

    total = len(enumerable)

    for ii, item in enumerate(enumerable):
        label = f"[{ii + 1:{len(str(total))}d}/{total:d}]"
        yield label, item


@click.group()
@click.version_option(__version__)
def cli():
    """Create and manage CHIME python environments."""
    pass


@cli.command()
@click.argument("path", default=".", type=click.Path(resolve_path=True, path_type=Path))
@click.option(
    "--prompt",
    show_default=True,
    default="venv",
    type=str,
    help="Set the virtualenv prompt prefix.",
)
@click.option(
    "--fast/--slow",
    show_default=True,
    help="Whether to turn on some speedups that could break the install.",
)
@click.option(
    "--compat/--no-compat",
    show_default=True,
    help="Whether to use legacy editable install mode.",
)
@click.option(
    "--download/--no-download",
    show_default=True,
    default=True,
    help="Whether to try to download the skyfield data.",
)
@click.option(
    "--ignore-system-packages/--use-system-packages",
    show_default=True,
    help="Whether to ignore system site packages when creating the virtualenv.",
)
@click.option(
    "--chime-member/--non-chime-member",
    show_default=True,
    default=True,
    help="Whether to include private CHIME repositories.",
)
@click.option(
    "--release",
    is_flag=True,
    help="Do not install packages in editable mode.",
)
def create(
    path: Path,
    prompt: str,
    fast: bool,
    compat: bool,
    download: bool,
    ignore_system_packages: bool,
    chime_member: bool,
    release: bool,
):
    """Install a CHIME pipeline environment at the specified PATH.

    This will create a virtualenvironment and make editable installs of all the CHIME
    pipeline packages as well as installing all their dependencies.

    For this to work you will need to be able to authenticate to Github via ssh. Make
    sure you have your keys set up properly!
    """
    console = Console()

    if release and not chime_member:
        console.print("`Release` build requires access to private CHIME repositories.")
        sys.exit(1)

    # Create the virtual environment
    console.rule("Creating virtualenvironment")
    if not path.exists():
        console.print(f"Specified path={str(path)} does not exist. Creating...")
        path.mkdir()
    elif not path.is_dir():
        console.print(f"Specified path={str(path)} exists, but is not a directory.")
        sys.exit(1)

    venv_path = path / "venv"

    if venv_path.exists():
        console.print(
            f"Virtual environment already exists at {venv_path}. Using it anyway."
        )
    else:
        venv.create(
            venv_path,
            system_site_packages=not ignore_system_packages,
            with_pip=True,
            prompt=prompt,
        )
    env = VirtualEnvironment(str(venv_path))

    console.print("Upgrading pip")
    env.upgrade("pip")

    # Fetch the CHIME-specific repositories that can be included
    if chime_member:
        chime_repositories = {**public_repositories(ssh=True), **private_repositories}
    else:
        chime_repositories = public_repositories(ssh=False)

    build_options = ["--no-build-isolation"] if fast else []

    # Release build just pip installs the main repository at the main branch
    if release:
        console.rule("Creating `Release` CHIME environment")
        pkgargs = chime_repositories[MAIN_REPO]
        # name @ github_path, always installs main branch
        with Progress(
            *Progress.get_default_columns()[:-1], console=console
        ) as progress:
            task = progress.add_task("Installing...", total=None)
            install_to_env(
                env, f"{MAIN_REPO} @ git+{pkgargs[0]}", build_options, console
            )
            progress.reset(task, total=1, completed=1)
    # Editable build clones each chime repo and does an editable install
    else:
        console.rule("Creating `Editable` CHIME environment")

        build_options += ["--no-deps"]

        # Clone the CHIME repos and extract their dependencies
        console.rule("Cloning CHIME repositories")

        code_path = path / "code"
        code_path.mkdir()

        with Progress(
            *Progress.get_default_columns()[:-1], console=console
        ) as progress:
            for label, (name, (url, target)) in labeller(chime_repositories.items()):
                clone_path = code_path / name

                git.Repo.clone_from(
                    url,
                    branch=target,
                    to_path=clone_path,
                    progress=RichProgress(f"{label} {name}", progress),
                )

        console.rule("Analyzing dependencies")
        console.print("This might take a moment...")

        # Get the list of packages to install using a pip dry-run report
        proc = install_to_env(
            env,
            f"{MAIN_REPO} @ git+{chime_repositories[MAIN_REPO][0]}",
            ["--dry-run", "--report", "-", "--quiet"],
            console,
            skip_output=True,
        )

        report = json.loads(proc.stdout)
        requirements = []

        # Iterate through all the packages that would be installed
        for entry in report.get("install", []):
            meta = entry.get("metadata", {})
            pkg_name = meta.get("name")

            # Filter chime repos, since these will be installed directly
            # from the clone
            if pkg_name.replace(".", "-") not in chime_repositories:
                if entry.get("is_direct", False):
                    # package provided via a direct URL
                    requirements.append(f"{pkg_name}@{meta['url']}")
                else:
                    requirements.append(f"{pkg_name}=={meta['version']}")

        # Install all the non-editable dependencies
        console.rule(f"Installing {len(requirements)} dependencies...")

        with Progress(
            *Progress.get_default_columns()[:-1],
            console=console,
        ) as progress:
            for label, req in labeller(sorted(requirements, key=str.lower)):
                task = progress.add_task(f"{label} {req}", total=None)

                env.install(req, options=build_options)
                progress.reset(task, total=1, completed=1)

        console.rule("Installing CHIME packages")
        # Install the CHIME packages in editable mode. Don't try to resolve any dependencies,
        # this should have been done above and so we can install all the CHIME packages.
        # NOTE: this could in theory break if they have *build* time dependencies on one
        # another and they are installed in the wrong order, but it would be a pretty
        # terrible idea to do this
        with Progress(
            *Progress.get_default_columns()[:-1], console=console
        ) as progress:
            for label, chime_package in labeller(chime_repositories.keys()):
                task = progress.add_task(f"{label} {chime_package}", total=None)

                options = [
                    *build_options,
                ]
                if compat:
                    options += ["--config-settings", "editable_mode=compat"]

                env.install(f"-e {code_path / chime_package}", options=options)
                progress.reset(task, total=1, completed=1)

    if download:
        console.rule("Downloading skyfield ephemeris data")
        try:
            env._execute(
                [
                    env._python_rpath,
                    "-c",
                    "from caput.astro.skyfield import skyfield_wrapper as s; s.timescale; s.ephemeris",
                ]
            )
        except Exception as e:
            console.print(f"Failed to download skyfield data. Error: {e}")


@cli.command()
@click.argument("path", type=click.Path(resolve_path=True, path_type=Path))
def update(path):
    """Update all the packages in the virtualenvironment at PATH.

    Not yet implemented.
    """
    raise NotImplementedError()
