# Create and Manage CHIME pipeline environments

This provides the `mkchimeenv` command to manage CHIME pipeline environments.
You can use it like:
```
$ mkchimeenv create mychimeenv
```

It will:
- Create a virtual environment in `mychimeenv/venv`
- Clone all the CHIME pipeline packages into `mychimeenv/code`
- Install all their dependencies into the virtual environment
- Perform editable installs of all the CHIME pipeline packages so you can hack
  on them to your hearts content.

To activate the environment just run `source mychimeenv/venv/bin/activate`.

The repositories are cloned from Github via ssh. Make sure you can clone the
repositories without requiring a passphrase for the key, either by using a
passphrase-less key, or (recommended) using an ssh-agent to keep the unlocked
key in memory. You will also need to be added to the *chime-experiment*
organisation, ask a friendly CHIME sysadmin to do this if you haven't already
got it setup. To test this works try cloning a small private repository, e.g.
```
$ git clone ssh://git@github.com/chime-experiment/mkchimeenv
```
