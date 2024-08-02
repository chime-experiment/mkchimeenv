"""Install and manage CHIME pipeline environments."""

from pathlib import Path
import sys
import tempfile
import toml
from typing import Optional, Tuple, List
import venv


import click
import git
from packaging.utils import canonicalize_name
from rich.progress import Progress
from rich.console import Console

from packaging.requirements import Requirement, InvalidRequirement

from virtualenvapi.manage import VirtualEnvironment


__version__ = "2024.08"


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
        "ch_util": (_clone_path("chime-experiment/ch_util", ssh=ssh), None),
        "ch_pipeline": (_clone_path("chime-experiment/ch_pipeline", ssh=ssh), None),
    }


private_repositories = {
    "chimedb-config": (_clone_path("chime-experiment/chimedb_config", ssh=True), None),
}

# At the moment this script struggles to determine extra requirements and so I just list
# them by hand here
extra_packages = [
    "versioneer",
    "bitshuffle",
    "numcodecs",
    "zarr",
    "papermill",  # used to render jupyter notebooks
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


def find_requirements(path: str) -> List[Requirement]:
    """Read and structure dependencies in a pyproject.toml file.

    Parameters
    ----------
    path
        Project path

    Returns
    -------
    requirements
        List of parsed Requirement objects
    """
    requirements = []

    # Get the project file as a pathlib path
    file = Path(path) / "pyproject.toml"

    if not file.is_file():
        raise FileNotFoundError(f"Project file {file} not found.")

    data = toml.load(file)
    project = data.get("project", {})
    dependencies = project.get("dependencies", {}).copy()

    for item in dependencies:
        try:
            req = Requirement(item)
        except InvalidRequirement:
            continue

        requirements.append(req)

    return sorted(requirements, key=lambda x: x.name)


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
@click.option("--compat", is_flag=True, help="Use legacy editable install mode.")
@click.option(
    "--download/--no-download", default=True, help="Download the skyfield data."
)
@click.option(
    "--ignore-system-packages", is_flag=True, help="Ignore system site packages."
)
@click.option(
    "--chime-member/--non-chime-member",
    default=True,
    help="Whether to include private CHIME repositories.",
)
def create(
    path: Path,
    prompt: str,
    fast: bool,
    compat: bool,
    download: bool,
    ignore_system_packages: bool,
    chime_member: bool,
):
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
            system_site_packages=not ignore_system_packages,
            with_pip=True,
            prompt=prompt,
        )
    env = VirtualEnvironment(str(venv_path))
    console.print("Upgrading pip")
    env.upgrade("pip")

    # Determine which repositories to close
    if chime_member:
        chime_repositories = {**public_repositories(ssh=True), **private_repositories}
    else:
        chime_repositories = public_repositories(ssh=False)

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

    console.rule("Analyzing dependencies...")
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
    requirements += [Requirement(req) for req in extra_packages]
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
            req_strs = [str(req) for req in reqs]
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
            if compat:
                options += ["--config-settings", "editable_mode=compat"]
            env.install(f"-e {code_path / chime_package}", options=options)
            progress.reset(task, total=1, completed=1)

    console.rule("Downloading skyfield ephemeris data")
    try:
        env._execute(
            [
                env._python_rpath,
                "-c",
                "from caput.time import skyfield_wrapper as s; s.timescale; s.ephemeris",
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
    pass
