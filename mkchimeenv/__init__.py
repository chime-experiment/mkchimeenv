"""Install and manage CHIME pipeline environments."""

import json
import sys
import venv
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import click
import git
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.text import Text

from .gitutils import RichProgress, update_single_repo
from .repos import private_repositories, public_repositories
from .venvutils import VirtualEnv, install_to_env

try:
    __version__ = version("mkchimeenv")
except PackageNotFoundError:
    # package is not installed
    pass

del version, PackageNotFoundError


# Installing this repo will install all other CHIME packages and dependencies
MAIN_REPO = "ch_pipeline"


@click.group()
@click.version_option(__version__)
def cli():
    """Create and manage CHIME python environments."""


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
        console.print(f"Specified path={path!s} does not exist. Creating...")
        path.mkdir()
    elif not path.is_dir():
        console.print(f"Specified path={path!s} exists, but is not a directory.")
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
    env = VirtualEnv(str(venv_path))

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

        console.rule("Checking for missing packages")
        missing = []
        grid = Table.grid(padding=(0, 1))
        grid.add_column()  # names
        grid.add_column()  # status

        for name, (url, _, sub_dir) in chime_repositories.items():
            if not (env.is_installed(name) or env.is_installed(name.replace("-", "."))):
                if sub_dir is not None:
                    url = str(url) + f"#subdirectory={sub_dir}"
                missing.append((name, url))
                status = Text("Not Found", style="red")
            else:
                status = Text("Found", style="green")

            grid.add_row(Text(f"{name}:"), status)

        console.print(grid)

        if missing:
            console.rule("Installing missing packages")
            with Progress(
                *Progress.get_default_columns()[:-1], console=console
            ) as progress:
                for name, url in missing:
                    task = progress.add_task(f"Installing {name}", total=None)

                    env.install(f"{name} @ git+{url}", options=build_options)
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
            for label, (name, (url, target, _)) in labeller(chime_repositories.items()):
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
            for label, (chime_package, (_, _, project_dir)) in labeller(
                chime_repositories.items()
            ):
                task = progress.add_task(f"{label} {chime_package}", total=None)

                options = [
                    *build_options,
                ]
                if compat:
                    options += ["--config-settings", "editable_mode=compat"]

                project_path = code_path / chime_package

                if project_dir is not None:
                    project_path = project_path / project_dir

                env.install(f"-e {project_path}", options=options)
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
        except Exception as e:  # noqa BLE001
            console.print(f"Failed to download skyfield data. Error: {e}")


STATUS_MAP = {
    True: ("green", "Updated"),
    False: ("red", "Update failed"),
    None: ("blue", "Nothing to update"),
}


@cli.command()
@click.argument("path", type=click.Path(resolve_path=True, path_type=Path))
def update(path):
    """Update all the packages in the virtualenvironment at PATH."""
    console = Console()

    code_path = path / "code"
    venv_path = path / "venv"

    if not code_path.exists():
        console.print(
            "No code path found - can't update an env installed as `release`!"
        )
        sys.exit(1)

    if not venv_path.exists():
        console.print("`venv` path not found - check that this is a valid environment!")
        sys.exit(1)

    env = VirtualEnv(str(venv_path))

    console.print("Upgrading pip")
    env.upgrade("pip")

    # Iterate through repos and fetch any recent git changes
    console.rule("Fetching remote updates")
    updated = []
    failed = []
    repos_to_update = []

    for d in sorted(x for x in code_path.iterdir() if x.is_dir()):
        try:
            repo = git.Repo(d)
        except git.InvalidGitRepositoryError:
            continue

        repos_to_update.append((repo, d))

    with Progress(
        SpinnerColumn(finished_text=" "),
        TextColumn("{task.description}"),
        console=console,
    ) as progress:
        for label, (repo, d) in labeller(repos_to_update):
            task = progress.add_task(f"{label} {d.name!s}", total=len(repos_to_update))

            result, message = update_single_repo(repo)

            if result:
                # TODO: handle dependency changes
                updated.append(repo)
            elif result is False:
                failed.append(repo)

            style, status = STATUS_MAP[result]

            progress.update(
                task,
                description=f"[{style}]{status}[/] [bold]{d.name!s}[/]: {message}",
                total=1,
                completed=1,
            )


def labeller(enumerable):
    """For each item in an iterable, also return a string label giving the position."""

    total = len(enumerable)

    for ii, item in enumerate(enumerable):
        label = f"[{ii + 1:{len(str(total))}d}/{total:d}]"
        yield label, item
