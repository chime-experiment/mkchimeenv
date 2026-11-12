"""CHIME GitHub repositories."""


def _clone_path(repo, ssh=True):
    if ssh:
        return f"ssh://git@github.com/{repo}"
    else:
        return f"https://github.com/{repo}.git"


private_repositories = {
    "chimedb-config": (
        _clone_path("chime-experiment/chimedb_config", ssh=True),
        None,
        None,
    ),
}


def public_repositories(ssh=True):
    return {
        # radio cosmology packages
        # format: (github path, branch, project subdirectory)
        "caput": (_clone_path("radiocosmology/caput", ssh=ssh), None, None),
        "cora": (_clone_path("radiocosmology/cora", ssh=ssh), None, None),
        "driftscan": (_clone_path("radiocosmology/driftscan", ssh=ssh), None, None),
        "draco": (_clone_path("radiocosmology/draco", ssh=ssh), None, None),
        "fluxcat": (_clone_path("radiocosmology/fluxcat", ssh=ssh), None, None),
        "wyvern": (
            _clone_path("radiocosmology/wyvern", ssh=ssh),
            None,
            "bindings/python",
        ),
        # chimedb core code and extensions
        "chimedb": (_clone_path("chime-experiment/chimedb", ssh=ssh), None, None),
        "chimedb-data_index": (
            _clone_path("chime-experiment/chimedb_di", ssh=ssh),
            None,
            None,
        ),
        "chimedb-dataflag": (
            _clone_path("chime-experiment/chimedb_dataflag", ssh=ssh),
            None,
            None,
        ),
        "chimedb-dataset": (
            _clone_path("chime-experiment/chimedb_dataset", ssh=ssh),
            None,
            None,
        ),
        # chime specific repositories
        "ch_ephem": (_clone_path("chime-experiment/ch_ephem", ssh=ssh), None, None),
        "ch_util": (_clone_path("chime-experiment/ch_util", ssh=ssh), None, None),
        "ch_pipeline": (
            _clone_path("chime-experiment/ch_pipeline", ssh=ssh),
            None,
            None,
        ),
    }
