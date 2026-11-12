"""Helpers to manage git repos."""

import git
from rich.progress import Progress

REMOTE = "origin"


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


def find_main_branch(repo: git.Repo):
    """Prefer origin/HEAD, fall back to main, then master."""
    try:
        ref = repo.git.symbolic_ref(f"refs/remotes/{REMOTE}/HEAD")
        return ref.rsplit("/", 1)[-1]
    except git.GitCommandError:
        pass
    remote_refs = {r.remote_head for r in repo.remotes[REMOTE].refs}
    for name in ("main", "master"):
        if name in remote_refs:
            return name
    return None


def has_upstream(repo: git.Repo, branch: str):
    """Check if a branch has a remote."""
    try:
        return repo.git.rev_parse("--abbrev-ref", f"{branch}@{{upstream}}") != ""
    except git.GitCommandError:
        return False


def update_single_repo(repo: git.Repo) -> tuple[bool | None, str]:
    """Update a single repository.

    Returns
    -------
    tuple(bool | None, str):
        - True if repo was updated
        - False if update was skipped
        - None is no updates available
        Includes a relevant message
    """
    # Various repo status checks
    if repo.head.is_detached:
        return (False, "detached HEAD")

    if REMOTE not in {r.name for r in repo.remotes}:
        return (False, f"no remote: {REMOTE}")

    branch = repo.active_branch.name
    main = find_main_branch(repo)

    # Always abort on dirty state
    if repo.is_dirty(untracked_files=True):
        message = (
            f"on '{REMOTE} with uncommitted changes! Move them to a new branch.'"
            if branch == main
            else "found uncommitted changes; commit your changes and try again!"
        )

        return (False, message)

    initial_hash = repo.head.commit.hexsha

    # Run git fetch for upstream changes
    try:
        repo.git.fetch(REMOTE)
    except git.GitCommandError as exc:
        return (False, f"fetch failed: {exc.stderr.strip()}")

    # (a) pull remote changes for the current branch (merge)
    if has_upstream(repo, branch):
        try:
            repo.git.pull("--ff-only", "--autostash", REMOTE, branch)
        except git.GitCommandError as e:
            try:
                repo.git.merge("--abort")
            except git.GitCommandError:
                pass
            return (
                False,
                f"CONFLICT merging {REMOTE}/{branch}: {e.stderr.strip() or e.stdout.strip()}",
            )

    if branch != main:
        try:
            repo.git.rebase("--autostash", f"{REMOTE}/{main}")
        except git.GitCommandError as exc:
            try:
                repo.git.rebase("--abort")
            except git.GitCommandError:
                pass
            return (
                False,
                f"CONFLICT rebasing {branch} onto {REMOTE}/{main}: {exc.stderr.strip() or exc.stdout.strip()}",
            )
    else:
        try:
            repo.git.merge("--ff-only", f"{REMOTE}/{main}")
        except git.GitCommandError as exc:
            return (False, f"cannot fast-forward {main}: {exc.stderr.strip()}")

    end_hash = repo.head.commit.hexsha

    return (
        True if initial_hash != end_hash else None
    ), f"branch={branch} (main={main})"
