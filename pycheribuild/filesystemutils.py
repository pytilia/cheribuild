#
# Copyright (c) 2016 Alex Richardson
# All rights reserved.
#
# This software was developed by SRI International and the University of
# Cambridge Computer Laboratory under DARPA/AFRL contract FA8750-10-C-0237
# ("CTSRD"), as part of the DARPA CRASH research programme.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
# OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
# OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
# SUCH DAMAGE.
#

import os
import shutil
import subprocess
import sys
import threading
import typing
from pathlib import Path

from .processutils import print_command, run_command
from .utils import AnsiColour, ConfigBase, fatal_error, status_update, ThreadJoiner, warning_message


class FileSystemUtils(object):
    def __init__(self, config: ConfigBase):
        self.config = config

    def makedirs(self, path: Path):
        print_command("mkdir", "-p", path, print_verbose_only=True)
        if not self.config.pretend and not path.is_dir():
            os.makedirs(str(path), exist_ok=True)

    def _delete_directories(self, *dirs):
        # http://stackoverflow.com/questions/5470939/why-is-shutil-rmtree-so-slow
        # shutil.rmtree(path) # this is slooooooooooooooooow for big trees
        run_command("rm", "-rf", *dirs)

    def clean_directory(self, path: Path, keep_root=False, ensure_dir_exists=True) -> None:
        """ After calling this function path will be an empty directory
        :param path: the directory to delete
        :param keep_root: Whether to keep the root directory (e.g. for NFS exported mountpoints)
        :param ensure_dir_exists: Create the cleaned directory if it doesn't exist
        """
        if path.is_dir():
            # If the root dir is used e.g. as an NFS mount we mustn't remove it, but only the subdirectories
            entries = list(map(str, path.iterdir())) if keep_root else [path]
            self._delete_directories(*entries)
        # always make sure the path exists
        if ensure_dir_exists:
            self.makedirs(path)

    class DeleterThread(threading.Thread):
        def __init__(self, parent: "FileSystemUtils", path: Path):
            super().__init__(name="Deleting " + str(path))
            self.path = path
            self.parent = parent

        def run(self):
            try:
                if self.parent.config.verbose:
                    status_update("Deleting", self.path, "asynchronously")
                self.parent._delete_directories(self.path)
                if self.parent.config.verbose:
                    status_update("Async delete of", self.path, "finished")
            except Exception as e:
                warning_message("Could not remove directory", self.path, e)

    def async_clean_directory(self, path: Path, *, keep_root=False, keep_dirs: list = None) -> ThreadJoiner:
        """
        Delete a directory in the background (e.g. deleting the cheribsd build directory delays the build a lot)
        ::
            with self.async_clean_directory("foo"):
                # foo has been moved to foo.tmp and foo is now and empty dir:
                do_something()
            # now foo.tmp no longer exists

        :param path: the directory to clean
        :param keep_root: currently not supported
        :param keep_dirs: list of directories to keep (e.g. for NFS mountpoints). The contents of those directories will
        be deleted though.
        :return:
        """
        deleter_thread = None
        tempdir = path.with_suffix(".delete-me-pls")
        if not path.is_dir():
            self.makedirs(path)
        elif len(list(path.iterdir())) == 0:
            status_update("Not cleaning", path, "it is already empty")
        else:
            if tempdir.is_dir():
                warning_message("Previous async cleanup of ", path, "failed. Cleaning up now")
                self._delete_directories(tempdir)
            if keep_root:
                # Move all subdirectories/files to a temp directory and delete that
                self.makedirs(tempdir)
                if not self.config.pretend:
                    assert tempdir.is_dir()
                    assert len(list(tempdir.iterdir())) == 0, list(tempdir.iterdir())
                all_entries = list(path.iterdir())
                if keep_dirs:
                    all_entries_new = []
                    for i in all_entries:
                        if i.name in keep_dirs:
                            status_update("Not deleting", i, "- If you really want it removed, delete it manually.")
                        else:
                            all_entries_new.append(i)
                    all_entries = all_entries_new
                all_entries = list(map(str, all_entries))
                if all_entries:
                    run_command(["mv"] + all_entries + [str(tempdir)], print_verbose_only=True)
            else:
                # rename the directory, create a new dir and then delete it in a background thread
                run_command("mv", path, tempdir)
                self.makedirs(path)
        if not self.config.pretend:
            assert path.is_dir()
            if not (keep_dirs and keep_root):
                assert len(list(path.iterdir())) == 0, list(path.iterdir())
        if tempdir.is_dir() or self.config.pretend:
            # we now have an empty directory, start background deleter and return to caller
            deleter_thread = FileSystemUtils.DeleterThread(self, tempdir)
        return ThreadJoiner(deleter_thread)

    def copy_directory(self, src_path: Path, dst_path: Path):
        print_command("cp", "-r", src_path, dst_path, print_verbose_only=True)
        if not self.config.pretend:
            shutil.copytree(str(src_path), str(dst_path))

    def delete_file(self, file: Path, print_verbose_only=False, warn_if_missing=False):
        print_command("rm", "-f", file, print_verbose_only=print_verbose_only)
        if not file.is_file() and not file.is_symlink():
            if warn_if_missing:
                warning_message("Expected", file, "to exist but is missing!")
            return
        if self.config.pretend:
            return
        file.unlink()

    @staticmethod
    def copy_remote_file(remote_path: str, target_file: Path):
        # if we have rsync we can skip the copy if file is already up-to-date
        if shutil.which("rsync"):
            try:
                run_command("rsync", "-aviu", "--progress", remote_path, target_file)
            except subprocess.CalledProcessError as err:
                if err.returncode == 127:
                    warning_message("rysnc doesn't seem to be installed on remote machine, trying scp")
                    run_command("scp", remote_path, target_file)
                else:
                    raise err
        else:
            run_command("scp", remote_path, target_file)

    def read_file(self, file: Path) -> str:
        # just return an empty string in pretend mode
        if self.config.pretend and not file.is_file():
            return "\n"
        with file.open("r", encoding="utf-8") as f:
            return f.read()

    def write_file(self, file: Path, contents: str, *, overwrite: bool, never_print_cmd=False, mode=None) -> None:
        """
        :param file: The target path to write contents to
        :param contents: the contents of the new file
        :param mode: The file mode for the resulting file (octal number or string)
        :param overwrite: If true the file will be overwritten, otherwise it will cause an error if the file exists
        :param never_print_cmd: don't ever print the echo commmand (even in verbose)
        """
        if not never_print_cmd:
            print_command("echo", contents, colour=AnsiColour.green, output_file=file, print_verbose_only=True)
        if self.config.pretend:
            return
        if not overwrite and file.exists():
            fatal_error("File", file, "already exists!")
        self.makedirs(file.parent)
        with file.open("w", encoding="utf-8") as f:
            f.write(contents)
        if mode:
            file.chmod(mode)

    @staticmethod
    def create_symlink(src: Path, dest: Path, *, relative=True, cwd: Path = None, print_verbose_only=True):
        assert dest.is_absolute() or cwd is not None
        if not cwd:
            cwd = dest.parent
        if relative:
            if src.is_absolute():
                src = os.path.relpath(str(src), str(dest.parent if dest.is_absolute() else cwd))
            if cwd is not None and cwd.is_dir():
                dest = dest.relative_to(cwd)
            run_command("ln", "-fsn", src, dest, cwd=cwd, print_verbose_only=print_verbose_only)
        else:
            run_command("ln", "-fsn", src, dest, cwd=cwd, print_verbose_only=print_verbose_only)

    def move_file(self, src: Path, dest: Path, force=False, create_dirs=True):
        if not src.exists():
            fatal_error(src, "doesn't exist")
        cmd = ["mv", "-f"] if force else ["mv"]
        if create_dirs and not dest.parent.exists():
            self.makedirs(dest.parent)
        run_command(cmd + [str(src), str(dest)])

    def install_file(self, src: Path, dest: Path, *, force=False, create_dirs=True, print_verbose_only=True, mode=None):
        if force:
            print_command("cp", "-f", src, dest, print_verbose_only=print_verbose_only)
        else:
            print_command("cp", src, dest, print_verbose_only=print_verbose_only)
        if self.config.pretend:
            if mode is not None:
                print_command("chmod", oct(mode), dest, print_verbose_only=print_verbose_only)
            return
        assert not dest.is_dir(), "install_file: target is a directory and not a file: " + str(dest)
        if (dest.is_symlink() or dest.exists()) and force:
            dest.unlink()
        if not src.exists():
            fatal_error("Required file", src, "does not exist")
        if create_dirs and not dest.parent.exists():
            self.makedirs(dest.parent)
        if dest.is_symlink():
            dest.unlink()
        # noinspection PyArgumentList
        shutil.copy2(str(src), str(dest), follow_symlinks=False)
        if mode is not None:
            print_command("chmod", oct(mode), dest, print_verbose_only=print_verbose_only)
            dest.chmod(mode)

    def rewrite_file(self, file: Path, rewrite: typing.Callable[[typing.Iterable[str]], typing.Iterable[str]]):
        if self.config.pretend:
            return
        if not file.exists():
            fatal_error("Required file", file, "does not exist")
        with file.open("r+", encoding="utf-8") as f:
            lines = list(rewrite(f.read().splitlines()))
            f.seek(0)
            f.writelines(map(lambda line: line + '\n', lines))
            f.truncate()

    def add_unique_line_to_file(self, file: Path, line: str):
        status_update("Adding '", line, "' to ", file, sep="")
        self.rewrite_file(file, lambda lines: lines if line in lines else (lines + [line]))

    @property
    def triple_prefixes_for_binaries(self) -> typing.Iterable[str]:
        raise ValueError("Must override triple_prefixes_for_binaries to use create_triple_prefixed_symlinks!")

    def create_triple_prefixed_symlinks(self, tool_path: Path, tool_name: str = None,
                                        create_unprefixed_link: bool = False, cwd: str = None):
        """
        Create mips4-unknown-freebsd, cheri-unknown-freebsd and mips64-unknown-freebsd prefixed symlinks
        for build tools like clang, ld, etc.
        :param create_unprefixed_link: whether to create a symlink tool_name -> tool_path.name
        (in case the real tool_path is prefixed)
        :param cwd: the working directory
        :param tool_path: the binary for which the symlinks will be created
        :param tool_name: the unprefixed name of the tool_path (defaults to tool_path.name) such as e.g. "ld", "ar"
        """
        cwd = cwd or tool_path.parent  # set cwd before resolving potential symlink
        if not tool_name:
            tool_name = tool_path.name
        if not tool_path.is_file():
            fatal_error("Attempting to create symlink to non-existent build tool_path:", tool_path)

        # a prefixed tool_path was installed -> create link such as mips4-unknown-freebsd-ld -> ld
        if create_unprefixed_link:
            assert tool_path.name != tool_name
            run_command("ln", "-fsn", tool_path.name, tool_name, cwd=cwd, print_verbose_only=True)

        for target in self.triple_prefixes_for_binaries:
            link = tool_path.parent / (target + tool_name)  # type: Path
            if link == tool_path:  # happens for binutils, where prefixed tools are installed
                # if self.config.verbose:
                #    print(coloured(AnsiColour.yellow, "Not overwriting", link, "because it is the target"))
                continue
            run_command("ln", "-fsn", tool_path.name, target + tool_name, cwd=cwd, print_verbose_only=True)

    @staticmethod
    # Not cached since another target could write to this dir: @functools.lru_cache(maxsize=20)
    def is_nonexistent_or_empty_dir(d: Path):
        # print("Checking if dir is empty:", d)
        if not d.exists():
            return True
        for _ in d.iterdir():
            # print(d, "is not empty, found ", item)
            return False
        # print(d, "is empty")
        return True

    @staticmethod
    def realpath(p: Path):
        # TODO: Require 3.6 after Ubuntu 16.04 EOL (scheduled for April 30, 2021)
        # Python 3.5 always raises an exception for non-existent files.
        if sys.version_info > (3, 6):
            return p.resolve(strict=False)
        else:
            if p.exists():
                return p.resolve()
            return Path(os.path.normpath(str(p.absolute())))

    def sha256sum(self, file: Path) -> str:
        # Based on https://stackoverflow.com/a/44873382/894271
        import hashlib  # rarely need, so imported on demand to reduce startup time
        h = hashlib.sha256()
        b = bytearray(128 * 1024)
        mv = memoryview(b)
        if not file.exists():
            fatal_error("Cannot hash", file, "since it does not exist", pretend=self.config.pretend)
            if self.config.pretend:
                return "0"
        with file.open('rb', buffering=0) as f:
            # PyCharm thinks .readinto is not supported.
            # noinspection PyUnresolvedReferences
            for n in iter(lambda: f.readinto(mv), 0):
                h.update(mv[:n])
        return h.hexdigest()
