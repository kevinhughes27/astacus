"""
version

Copyright (c) 2019 Aiven Ltd
See LICENSE for details
"""
import importlib.util
import os
import subprocess


def save_version(*, new_ver, old_ver, version_file):
    "Save new version file, if old_ver != new_ver"
    if not new_ver:
        return False
    version_file = os.path.join(os.path.dirname(__file__), version_file)
    if not old_ver or new_ver != old_ver:
        with open(version_file, "w") as file_handle:
            file_handle.write('"""{}"""\n__version__ = "{}"\n'.format(__doc__, new_ver))
    return True


def update_project_version(version_file):
    "Update the version_file, and return the version number stored in the file"
    version_file_full_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), version_file)
    module_spec = importlib.util.spec_from_file_location("verfile", version_file_full_path)
    module = importlib.util.module_from_spec(module_spec)
    file_ver = getattr(module, "__version__", None)

    os.chdir(os.path.dirname(__file__) or ".")
    try:
        git_out = subprocess.check_output(["git", "describe", "--always"], stderr=getattr(subprocess, "DEVNULL", None))
    except (OSError, subprocess.CalledProcessError):
        pass
    else:
        git_ver = git_out.splitlines()[0].strip().decode("utf-8")
        if "." not in git_ver:
            git_ver = "0.0.1-0-unknown-{}".format(git_ver)
        if save_version(new_ver=git_ver, old_ver=file_ver, version_file=version_file):
            return git_ver

    makefile = os.path.join(os.path.dirname(__file__), "Makefile")
    if os.path.exists(makefile):
        with open(makefile, "r") as file_handle:
            lines = file_handle.readlines()
        short_ver = [line.split("=", 1)[1].strip() for line in lines if line.startswith("short_ver")][0]
        if save_version(new_ver=short_ver, old_ver=file_ver, version_file=version_file):
            return short_ver

    if not file_ver:
        raise Exception("version not available from git or from file {!r}".format(version_file))

    return file_ver


if __name__ == "__main__":
    import sys

    update_project_version(sys.argv[1])
