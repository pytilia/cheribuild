#
# SPDX-License-Identifier: BSD-2-Clause
#
# Copyright (c) 2020 Alex Richardson
#
# This work was supported by Innovate UK project 105694, "Digital Security by
# Design (DSbD) Technology Platform Prototype".
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND ANY
# EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
import typing
from pathlib import Path

from .crosscompileproject import (CompilationTargets, CrossCompileProject,
                                  GitRepository)
from ..project import DefaultInstallDir
from ...config.compilation_targets import CheriBSDTargetInfo
from ...config.target_info import CrossCompileTarget


class BuildCheriExercises(CrossCompileProject):
    target = "cheri-exercises"
    repository = GitRepository("https://github.com/CTSRD-CHERI/cheri-exercises.git")
    supported_architectures = [CompilationTargets.CHERIBSD_RISCV_PURECAP,
                               CompilationTargets.CHERIBSD_MORELLO_PURECAP,
                               CompilationTargets.CHERIBSD_MIPS_PURECAP,  # untested, but should work
                               ]
    default_install_dir = DefaultInstallDir.ROOTFS_OPTBASE
    path_in_rootfs = "/opt/cheri-exercises"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.compiled_files = []  # type: typing.List[Path]

    def _compile_file(self, output: Path, *args, target_override: CrossCompileTarget = None):
        assert isinstance(self.target_info, CheriBSDTargetInfo)
        target_flags = self.target_info.get_essential_compiler_and_linker_flags(xtarget=target_override,
                                                                                default_flags_only=True)
        warning_flags = ["-Wall", "-Wcheri"]
        self.run_cmd([self.CC] + target_flags + warning_flags + ["-fuse-ld=lld", "-o", output, *args],
                     print_verbose_only=False)
        self.compiled_files.append(output)

    def _compile_for_cheri_and_non_cheri(self, output_name_prefix: str, *src_and_args):
        non_cheri_target = self.crosscompile_target.get_non_cheri_target()
        self._compile_file(self.build_dir / (output_name_prefix + "-" + non_cheri_target.generic_suffix), *src_and_args,
                           target_override=non_cheri_target)
        self._compile_file(self.build_dir / (output_name_prefix + "-cheri"), *src_and_args)

    def compile(self, **kwargs):
        # Compile and run RISC-V and CHERI-RISC-V programs
        self._compile_for_cheri_and_non_cheri(
            "print-pointer", self.source_dir / "src/exercises/compile-and-run/print-pointer.c")
        self._compile_file(self.build_dir / "print-capability",
                           self.source_dir / "src/exercises/compile-and-run/print-capability.c")

        # Exercise an inter-object buffer overflow (needs -G0)
        self._compile_for_cheri_and_non_cheri(
            "buffer-overflow", self.source_dir / "src/exercises/buffer-overflow/buffer-overflow.c", "-G0")

        # Exercise a subobject buffer overflow
        self._compile_for_cheri_and_non_cheri(
            "buffer-overflow-subobject",
            self.source_dir / "src/exercises/buffer-overflow-subobject/buffer-overflow-subobject.c")
        self._compile_file(
            self.build_dir / "buffer-overflow-subobject-cheri-subobject-safe",
            self.source_dir / "src/exercises/buffer-overflow-subobject/buffer-overflow-subobject.c",
            "-Xclang", "-cheri-bounds=subobject-safe")  # compile another version with subobject bounds

        # Corrupt a control-flow pointer using a subobject buffer overflow
        self._compile_for_cheri_and_non_cheri(
            "buffer-overflow-fnptr",
            self.source_dir / "src/exercises/control-flow-pointer/buffer-overflow-fnptr.c")

        # Exercise integer-pointer type confusion bug
        self._compile_for_cheri_and_non_cheri(
            "union-int-ptr", self.source_dir / "src/exercises/type-confusion/union-int-ptr.c")

        # Demonstrate pointer injection
        self._compile_for_cheri_and_non_cheri(
            "long-over-pipe", self.source_dir / "src/exercises/pointer-injection/long-over-pipe.c")
        self._compile_for_cheri_and_non_cheri(
            "ptr-over-pipe", self.source_dir / "src/exercises/pointer-injection/ptr-over-pipe.c")

        # TODO: Demonstrate pointer revocation (however that needs caprevoke)

        # TODO: also add missions?

    def install(self, **kwargs):
        self.makedirs(self.install_dir)
        if self.config.clean:
            self.clean_directory(self.install_dir, ensure_dir_exists=True)
        for i in self.compiled_files:
            self.install_file(i, self.install_dir / i.name, print_verbose_only=False)
        # Also install them to the hybrid rootfs:
        hybrid_target = self.crosscompile_target.get_cheri_hybrid_target()
        hybrid_rootfs_project = self.target_info.get_rootfs_project(xtarget=hybrid_target)
        hybrid_install_dir = hybrid_rootfs_project.install_dir / self.path_in_rootfs[1:]
        self.makedirs(hybrid_install_dir)
        if self.config.clean:
            self.clean_directory(hybrid_install_dir, ensure_dir_exists=True)
        for i in self.compiled_files:
            self.install_file(i, hybrid_install_dir / i.name, print_verbose_only=False)
