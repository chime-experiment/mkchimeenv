"""Install and manage CHIME pipeline environments."""
from pathlib import Path
import sys
import tempfile
from typing import Optional, Tuple, List
import venv


import click
import git
from packaging.utils import canonicalize_name
from rich.progress import Progress
from rich.console import Console

from requirements_detector import find_requirements
from requirements_detector.requirement import DetectedRequirement

from virtualenvapi.manage import VirtualEnvironment


__version__ = "2022.06"


chime_repositories = {
    # radio cosmology packages
    "caput": ("ssh://git@github.com/radiocosmology/caput", None),
    "cora": ("ssh://git@github.com/radiocosmology/cora", None),
    "driftscan": ("ssh://git@github.com/radiocosmology/driftscan", None),
    "draco": ("ssh://git@github.com/radiocosmology/draco", None),
    # chimedb core code and extensions
    "chimedb": ("ssh://git@github.com/chime-experiment/chimedb", None),
    "chimedb-config": ("ssh://git@github.com/chime-experiment/chimedb_config", None),
    "chimedb-data_index": ("ssh://git@github.com/chime-experiment/chimedb_di", None),
    "chimedb-dataflag": (
        "ssh://git@github.com/chime-experiment/chimedb_dataflag",
        None,
    ),
    "chimedb-dataset": ("ssh://git@github.com/chime-experiment/chimedb_dataset", None),
    # chime specific repositories
    "ch_util": ("ssh://git@github.com/chime-experiment/ch_util", None),
    "ch_pipeline": ("ssh://git@github.com/chime-experiment/ch_pipeline", None),
}

# At the moment this script struggles to determine extra requirements and so I just list
# them by hand here
extra_packages = [
    "bitshuffle",
    "numcodecs",
    "zarr",
]


def match_opcode(opcode: int) -> Tuple[int, str, bool]:
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


def labeller(enumerable):
    """For each item in an iterable, also return a string label giving the position."""

    total = len(enumerable)

    for ii, item in enumerate(enumerable):
        label = f"[{ii + 1:{len(str(total))}d}/{total:d}]"
        yield label, item


@click.group()
def cli():
    """Create and manage CHIME python environments."""
    pass


def install_multiple(
    env: VirtualEnvironment, packages: List[str], options: Optional[List[str]] = None
):
    """Install multiple packages into a virtualenvironment at once."""

    # Use a temporary requirements file and then use the usual `env.install`
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as tfh:

        for pkg in packages:
            tfh.write(f"{pkg}\n")

        tfh.flush()

        env.install(f"-r {tfh.name}", options=options)


@cli.command()
@click.argument("path", default=".", type=click.Path(resolve_path=True, path_type=Path))
@click.option(
    "--prompt", default="venv", type=str, help="Set the virtualenv prompt prefix."
)
@click.option(
    "--fast", is_flag=True, help="Turn on some speedups that could break the install."
)
def create(path: Path, prompt: str, fast: bool):
    """Install a CHIME pipeline environment at the specified PATH.

    This will create a virtualenvironment and make editable installs of all the CHIME
    pipeline packages as well as installing all their dependencies.

    For this to work you will need to be able to authenticate to Github via ssh. Make
    sure you have your keys set up properly!
    """

    console = Console()

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
            system_site_packages=True,
            with_pip=True,
            prompt=prompt,
        )
    env = VirtualEnvironment(str(venv_path))
    console.print("Upgrading pip")
    env.upgrade("pip")

    # Clone the CHIME repos and extract their dependencies
    console.rule("Cloning CHIME repositories")

    code_path = path / "code"
    code_path.mkdir()

    requirements = []

    with Progress(
        *Progress.get_default_columns()[:-1],
        console=console,
    ) as progress:
        for label, (name, (url, target)) in labeller(chime_repositories.items()):

            clone_path = code_path / name

            git.Repo.clone_from(
                url,
                branch=target,
                to_path=clone_path,
                progress=RichProgress(f"{label} {name}", progress),
            )

            requirements += find_requirements(clone_path)

    console.rule("Analyzing dependencies")
    for req in requirements:
        if req.name is None and "@" in req.url:
            name, url = req.url.split("@")
            name = name.split("[")[0]
            req.name = name.rstrip()
            req.url = url.lstrip()
    console.print(f"{len(requirements)} total dependencies.")

    # Remove the specified CHIME packages from the install list
    chime_repo_names = list(chime_repositories.keys())
    requirements = [
        req
        for req in requirements
        if req.name.replace(".", "-") not in chime_repo_names
    ]
    console.print(f"{len(requirements)} after removing CHIME pipeline packages.")

    # Also filter out packages that are already installed
    # NOTE: this uses a private method from virtualenv-api so may be fragile
    installed_packages = [
        p.split("==")[0] for p in env._execute_pip(["freeze"]).splitlines()
    ]
    requirements = [req for req in requirements if str(req) not in installed_packages]
    console.print(f"{len(requirements)} after removing already installed packages.")

    # Add the extras to make up for problems parsing
    requirements += [DetectedRequirement(req) for req in extra_packages]
    console.print(f"{len(requirements)} after adding manual extras.")

    # Group packages together by name to prevent repeated install attempts
    req_dict = {}
    for req in requirements:
        name = canonicalize_name(req.name)
        if name not in req_dict:
            req_dict[name] = []
        req_dict[name].append(req)
    console.print(f"{len(req_dict)} after removing dupes.")

    # Go through and install all the remaining packages into the virtualenv
    console.rule("Installing remaining dependencies")

    with Progress(
        *Progress.get_default_columns()[:-1],
        console=console,
    ) as progress:

        for label, (reqname, reqs) in labeller(req_dict.items()):
            task = progress.add_task(f"{label} {reqname}", total=None)

            # Request all versions of the same package be installed at once and let pip
            # figure out what to actually do
            req_strs = [
                f"{req.name} @ {req.url}" if req.url else str(req) for req in reqs
            ]
            options = ["--no-build-isolation"] if fast else None
            install_multiple(env, list(set(req_strs)), options=options)
            progress.reset(task, total=1, completed=1)

    console.rule("Installing CHIME packages")

    # Install the CHIME packages in editable mode. Don't try to resolve any
    # dependencies, this should have been done above and so we can install all the CHIME
    # packages.
    # NOTE: this could in theory break if they have *build* time dependencies on one
    # another and they are installed in the wrong order
    with Progress(
        *Progress.get_default_columns()[:-1],
        console=console,
    ) as progress:

        for label, chime_package in labeller(chime_repo_names):

            task = progress.add_task(
                f"{label} {chime_package}",
                total=None,
            )

            options = ["--no-deps"]
            if fast:
                options += ["--no-build-isolation"]
            env.install(f"-e {code_path / chime_package}", options=options)
            progress.reset(task, total=1, completed=1)


@cli.command()
@click.argument("path", type=click.Path(resolve_path=True, path_type=Path))
def update(path):
    """Update all the packages in the virtualenvironment at PATH.

    Not yet implemented.
    """
    pass
