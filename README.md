# Create and Manage CHIME pipeline environments

This package provides the `mkchimeenv` command to manage CHIME pipeline
environments. On cedar this is probably preinstalled into the `chime/python`
module. If you want to install it yourself (e.g. on your own machine) it can be
done using `pip` in the usual manner:
```
$ pip install "mkchimeenv @ git+https://github.com/chime-experiment/mkchimeenv.git"
```
which will install its dependencies and the `mkchimeenv` command into the
Python environment. To use it to create a new CHIME pipeline installation, try
doing:
```
$ mkchimeenv create mychimeenv
```

It will:
- Create a virtual environment in `mychimeenv/venv`
- Clone all the CHIME pipeline packages into `mychimeenv/code`
- Try to determine and then install all their dependencies into the virtual
  environment
- Perform editable installs of all the CHIME pipeline packages so you can hack
  on them to your hearts content.
- Download the skyfield data required by `caput/ch_util` for ephemeris
  calculations.

To activate the environment just run `source mychimeenv/venv/bin/activate`.

By default, the repositories are cloned from Github via ssh.
If you're a CHIME Collaboration member, make sure you can clone the
repositories without requiring a passphrase for the key, either by using a
passphrase-less key, or (recommended) using an ssh-agent to keep the unlocked
key in memory. You will also need to be added to the *chime-experiment*
organisation, ask a friendly CHIME sysadmin to do this if you haven't already
got it setup. To test this works try cloning a small private repository, e.g.
```
$ git clone ssh://git@github.com/chime-experiment/mkchimeenv
```

If you are not a CHIME Collaboration member, the command will still work for you if
you use the `--non-chime-member` option. This option will omit CHIME-internal packages
from the virtual environment, and also clone the repositories using https instead of
ssh, which does not require a passphrase-less key to be set up.

When installing on your own computer, the `--ignore-system-packages` will ignore
packages that are already installed into your base Python environment, and will instead
perform fresh installs of the required packages into the new virtual environment. This
can sometimes be useful for avoiding conflicts with your existing Python setup.

To speed up the creation you can use the `--fast` option to the create command.
This will turn off build isolation when pip is installing the packages, which
will give a large speed boost (especially on cedar), but may be less robust.
