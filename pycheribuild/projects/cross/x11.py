#
# SPDX-License-Identifier: BSD-2-Clause
#
# Copyright (c) 2021 Alex Richardson
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
import os
from pathlib import Path

from .crosscompileproject import CrossCompileAutotoolsProject, CrossCompileCMakeProject, CrossCompileMesonProject
from .freetype import BuildFreeType2
from ..project import DefaultInstallDir, GitRepository, Project
from ...config.chericonfig import BuildType
from ...config.compilation_targets import CompilationTargets
from ...processutils import set_env
from ...utils import OSInfo


class X11Mixin:
    do_not_add_to_targets = True
    path_in_rootfs = "/usr/local"  # Always install X11 programs in /usr/local/bin to make X11 forwarding work
    default_build_type = BuildType.RELWITHDEBINFO
    cross_install_dir = DefaultInstallDir.ROOTFS_OPTBASE
    native_install_dir = DefaultInstallDir.DO_NOT_INSTALL  # Don't override the native installation
    supported_architectures = CompilationTargets.ALL_SUPPORTED_CHERIBSD_AND_HOST_TARGETS

    def setup(self):
        # noinspection PyUnresolvedReferences
        super().setup()
        assert isinstance(self, Project)
        # The build systems does not seem to add the fontconfig dependency
        rpath_dirs = self.target_info.additional_rpath_directories
        if rpath_dirs:
            self.COMMON_LDFLAGS.append("-Wl,-rpath," + ":".join(rpath_dirs))


class X11AutotoolsProjectBase(X11Mixin, CrossCompileAutotoolsProject):
    do_not_add_to_targets = True

    def __init__(self, config):
        super().__init__(config)
        self.configure_command = self.source_dir / "autogen.sh"


class X11MesonProject(X11Mixin, CrossCompileMesonProject):
    do_not_add_to_targets = True


class BuildXorgMacros(X11AutotoolsProjectBase):
    target = "xorg-macros"
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/util/macros.git")


# Like X11AutotoolsProjectBase but also adds xorg-macros as a dependency
class X11AutotoolsProject(X11AutotoolsProjectBase):
    do_not_add_to_targets = True
    dependencies = ["xorg-macros"]

    def setup(self):
        super().setup()
        self.configure_environment["ACLOCAL_PATH"] = BuildXorgMacros.get_install_dir(self) / "share/aclocal"
        # Avoid building documentation
        self.configure_args.extend(["--with-doxygen=no", "--enable-specs=no", "--enable-devel-docs=no"])

        if not self.compiling_for_host():
            self.configure_args.append("--with-sysroot=" + str(self.sdk_sysroot))
            # Needed for many of the projects but not all of them:
            self.configure_args.append("--enable-malloc0returnsnull")


class BuildXCBProto(X11AutotoolsProject):
    target = "xcbproto"
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/proto/xcbproto.git")


class BuildXorgProto(X11AutotoolsProject):
    target = "xorgproto"
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/proto/xorgproto.git")


class BuildLibXau(X11AutotoolsProject):
    target = "libxau"
    dependencies = ["xorgproto", "xorg-macros"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxau.git")


class BuildLibXCBPthreadStubs(X11AutotoolsProject):
    target = "xorg-pthread-stubs"
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/pthread-stubs.git")


class BuildLibXCB(X11AutotoolsProject):
    target = "libxcb"
    dependencies = ["xcbproto", "libxau", "xorg-pthread-stubs"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxcb.git")


class BuildLibXCBUtil(X11AutotoolsProject):
    target = "libxcb-util"
    dependencies = ["libxcb"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxcb-util.git")


class BuildLibXCBWM(X11AutotoolsProject):
    target = "libxcb-wm"
    dependencies = ["libxcb"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxcb-wm.git")


class BuildLibXCBImage(X11AutotoolsProject):
    target = "libxcb-image"
    dependencies = ["libxcb-util"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxcb-image.git")


class BuildLibXCBRenderUtil(X11AutotoolsProject):
    target = "libxcb-render-util"
    dependencies = ["libxcb"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxcb-render-util.git")


class BuildLibXCBCursor(X11AutotoolsProject):
    target = "libxcb-cursor"
    dependencies = ["libxcb-render-util", "libxcb-image"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxcb-cursor.git")

    def setup(self):
        super().setup()
        if not self.compiling_for_host():
            # Various underaligned capabilities in packed structs, hopefully not a problem at runtime
            self.cross_warning_flags += ["-Wno-error=cheri-capability-misuse"]


class BuildLibXCBKeysyms(X11AutotoolsProject):
    target = "libxcb-keysyms"
    dependencies = ["xorgproto"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxcb-keysyms.git")


class BuildLibXTrans(X11AutotoolsProject):
    target = "libxtrans"
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxtrans.git")


class BuildLibX11(X11AutotoolsProject):
    target = "libx11"
    dependencies = ["xorgproto", "libxcb", "libxtrans"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libx11.git")

    # pkg-config doesn't handle" "--sysroot very well, specify the path explicitly
    def setup(self):
        super().setup()
        self.configure_args.append("--with-keysymdefdir=" + str(self.install_dir / "include/X11"))
        # TODO: disable locale support to speed things up?
        # self.configure_args.extend(["--disable-xlocale", "--disable-xlocaledir"])
        if not self.compiling_for_host():
            # The build system gets confused when cross-compiling from macOS, tell it we don't want launchd support.
            self.configure_args.append("--without-launchd")
            # A few warnings in xlibi18n that don't affect correct execution. Fixing them would require
            # using uintptr_t and there currently isn't a typedef for that in libX11.
            self.cross_warning_flags += ["-Wno-error=cheri-capability-misuse"]


class BuildLibXext(X11AutotoolsProject):
    target = "libxext"
    dependencies = ["libx11"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxext.git")


class BuildLibXfixes(X11AutotoolsProject):
    target = "libxfixes"
    dependencies = ["libx11"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxfixes.git")


class BuildLibXi(X11AutotoolsProject):
    target = "libxi"
    dependencies = ["libxext", "libxfixes"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxi.git")


class BuildLibXrender(X11AutotoolsProject):
    target = "libxrender"
    dependencies = ["libx11"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxrender.git")


class BuildLibXrandr(X11AutotoolsProject):
    target = "libxrandr"
    dependencies = ["libxext", "libxrender"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxrandr.git")


# One of the simplest programs:
class BuildXEv(X11AutotoolsProject):
    target = "xev"
    dependencies = ["libxrandr"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/app/xev.git")


class BuildLibSM(X11AutotoolsProject):
    target = "libsm"
    dependencies = ["libx11", "libice"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libsm.git")


class BuildLibIce(X11AutotoolsProject):
    target = "libice"
    dependencies = ["libx11"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libice.git")

    def setup(self):
        super().setup()
        # TODO: fix the source code instead
        self.cross_warning_flags.append("-Wno-error=format")  # otherwise configure does not detect asprintf


class BuildLibXt(X11AutotoolsProject):
    target = "libxt"
    dependencies = ["libice", "libsm"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxt.git")


class BuildLibXDamage(X11AutotoolsProject):
    target = "libxdamage"
    dependencies = ["libx11", "libxfixes"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxdamage.git")


class BuildLibXmu(X11AutotoolsProject):
    target = "libxmu"
    dependencies = ["libxext", "libxrender", "libxt"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxmu.git")

    def setup(self):
        super().setup()
        # TODO: fix the source code instead
        self.cross_warning_flags.append("-Wno-error=cheri-capability-misuse")


class BuildXHost(X11AutotoolsProject):
    target = "xhost"
    dependencies = ["libxau", "libx11"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/app/xhost.git")


class BuildXAuth(X11AutotoolsProject):
    target = "xauth"
    dependencies = ["libx11", "libxau", "libxext", "libxmu", "xorgproto"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/app/xauth")


class BuildXEyes(X11AutotoolsProject):
    target = "xeyes"
    dependencies = ["libxi", "libxmu", "libxrender"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/app/xeyes.git")


class BuildLibXKBCommon(X11MesonProject):
    target = "libxkbcommon"
    dependencies = ["libx11"]
    repository = GitRepository("https://github.com/xkbcommon/libxkbcommon.git")

    def setup(self):
        # avoid wayland dep for now
        super().setup()
        self.configure_args.append("-Denable-wayland=false")
        # Don't build docs with Doxygen
        self.configure_args.append("-Denable-docs=false")
        # Avoid libxml2 dep
        self.configure_args.append("-Denable-xkbregistry=false")

    def process(self):
        newpath = os.getenv("PATH")
        if OSInfo.IS_MAC:
            # /usr/bin/bison on macOS is not compatible with this build system
            newpath = str(self.get_homebrew_prefix("bison")) + "/bin:" + newpath
        with set_env(PATH=newpath):
            super().process()


class BuildXorgFontUtil(X11AutotoolsProject):
    target = "xorg-font-util"
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/font/util.git")


class BuildPixman(X11MesonProject):
    target = "pixman"
    dependencies = ["libpng"]
    repository = GitRepository("https://gitlab.freedesktop.org/pixman/pixman.git",
                               temporary_url_override="https://gitlab.freedesktop.org/arichardson/pixman.git",
                               url_override_reason="https://gitlab.freedesktop.org/pixman/pixman/-/merge_requests/48")


class BuildLibFontenc(X11AutotoolsProject):
    target = "libfontenc"
    dependencies = ["xorg-font-util"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libfontenc.git")


class BuildLibXFont(X11AutotoolsProject):
    target = "libxfont"
    dependencies = ["libfontenc", "freetype2"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxfont.git")

    def setup(self):
        super().setup()
        if self.compiling_for_cheri():
            self.cross_warning_flags.append("-Wno-error=cheri-capability-misuse")


class BuildLibXFt(X11AutotoolsProject):
    target = "libxft"
    dependencies = ["fontconfig", "freetype2", "libxrender"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxft.git")


class BuildLibXTst(X11AutotoolsProject):
    target = "libxtst"
    dependencies = ["libxext", "libx11", "libxi"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxtst.git")
    builds_docbook_xml = True

    def setup(self):
        super().setup()
        if self.compiling_for_cheri():
            self.cross_warning_flags.append("-Wno-error=cheri-capability-misuse")


class BuildLibXKBFile(X11AutotoolsProject):
    target = "libxkbfile"
    dependencies = ["libx11"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxkbfile.git")


class BuildLibXScrnSaver(X11AutotoolsProject):
    target = "libxscrnsaver"
    dependencies = ["libx11", "libxext"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxscrnsaver.git")


class BuildLibJpegTurbo(CrossCompileCMakeProject):
    target = "libjpeg-turbo"
    repository = GitRepository("https://github.com/libjpeg-turbo/libjpeg-turbo.git",
                               old_urls=[b"https://github.com/arichardson/libjpeg-turbo.git"])

    def setup(self):
        super().setup()
        if self.compiling_for_aarch64(include_purecap=True):
            # self.add_cmake_options(NEON_INTRINSICS=True)
            self.add_cmake_options(WITH_SIMD=False)  # Tries to compile files in non-existent arm/aarch128 directory


class BuildTigerVNC(CrossCompileCMakeProject):
    target = "tigervnc"
    repository = GitRepository("https://github.com/TigerVNC/tigervnc")
    dependencies = ["pixman", "libxext", "libxfixes", "libxdamage", "libxtst", "libjpeg-turbo"]

    def __init__(self, config):
        super().__init__(config)
        if self.compiling_for_host():
            self.add_required_system_tool("fltk-config", homebrew="ftlk")

    def setup(self):
        super().setup()
        if not self.compiling_for_host():
            self.add_cmake_options(INSTALL_SYSTEMD_UNITS=False, ENABLE_NLS=False, BUILD_VIEWER=False)


class BuildXKeyboardConfig(X11MesonProject):
    target = "xkeyboard-config"
    dependencies = ["libx11"]
    repository = GitRepository("https://gitlab.freedesktop.org/xkeyboard-config/xkeyboard-config.git")

    def install(self, **kwargs):
        # work around `install script '/bin/sh -c ln -s base $DESTDIR/usr/local/share/X11/xkb/rules/xorg' exit code 1`
        for symlink in ("xorg", "xorg.lst", "xorg.xml"):
            self.delete_file(self.install_dir / "X11/xkb/rules" / symlink)
        super().install(**kwargs)


class BuildXKkbcomp(X11AutotoolsProject):
    target = "xkbcomp"
    dependencies = ["libx11"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/app/xkbcomp.git")


class BuildXProp(X11AutotoolsProject):
    target = "xprop"
    dependencies = ["libx11"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/app/xprop.git")


class BuildLibXCursor(X11AutotoolsProject):
    target = "libxcursor"
    dependencies = ["libx11", "libxfixes", "libxrender"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxcursor.git")


class BuildXBitMaps(X11AutotoolsProject):
    target = "xbitmaps"
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/data/bitmaps.git")


class BuildXSetRoot(X11AutotoolsProject):
    target = "xsetroot"
    dependencies = ["libx11", "libxmu", "libxcursor", "xbitmaps"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/app/xsetroot.git")


class BuildXVncServer(X11AutotoolsProject):
    target = "xvnc-server"
    # The actual XVnc source code is part of TigerVNC and not included in the xserver repository.
    # It also depends on build artifacts from an existing tigervnc build
    dependencies = ["libx11", "xorg-font-util", "libxrender", "libxfont", "libxkbfile", "tigervnc", "xkeyboard-config",
                    "xkbcomp"]
    # The tigervnc code requires the 1.20 release
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/xserver.git",
                               default_branch="server-1.20-branch", force_branch=True,
                               temporary_url_override="https://gitlab.freedesktop.org/arichardson/xserver.git",
                               url_override_reason=["https://gitlab.freedesktop.org/xorg/xserver/-/merge_requests/721",
                                                    "https://gitlab.freedesktop.org/xorg/xserver/-/merge_requests/720"])

    def install(self, **kwargs):
        """
        cheribuild.py run-<arch> --run-<arch>/extra-tcp-forwarding=5900=5900
        <qemu>: Xvnc -geometry 1024x768 -SecurityTypes=None
        <qemu>: DISPLAY=:0 xeyes

        <host> tigervnc localhost:5900
        """
        super().install()
        # Install a script to start the Xvnc so I don't have to remember the arguments
        # TODO: should we install a service that we can start with `service xvnc start`?
        self.write_file(self.install_dir / "bin/startxvnc", overwrite=True, mode=0o755,
                        contents="#!/bin/sh\nXvnc -geometry 1024x768 -SecurityTypes=None \"$@\"\n")

    def update(self):
        super().update()
        tigervnc_source = BuildTigerVNC.get_instance(self).source_dir
        if (self.source_dir / "hw").is_dir():
            self.create_symlink(tigervnc_source / "unix/xserver/hw/vnc", self.source_dir / "hw/vnc")
        if not (self.source_dir / ".tigervnc-patch-applied").exists():
            self.run_cmd("patch", "-p1", "-i", tigervnc_source / "unix/xserver120.patch", cwd=self.source_dir)
            self.write_file(self.source_dir / ".tigervnc-patch-applied", "applied", overwrite=True)

    def setup(self):
        super().setup()
        fonts_dir = Path("/", self.target_info.sysroot_install_prefix_relative, "share/fonts")
        self.configure_args.extend([
            "--without-dtrace", "--enable-static", "--disable-dri", "--disable-unit-tests",
            "--disable-xinerama", "--disable-xvfb", "--disable-xnest", "--disable-xorg",
            "--disable-dmx", "--disable-xwin", "--disable-xephyr", "--disable-kdrive",
            "--disable-libdrm",
            "--disable-config-dbus", "--disable-config-hal",
            "--disable-dri2", "--enable-install-libxf86config",
            "--disable-glx",  # "--enable-glx",
            "-with-default-font-path=catalogue:" + str(fonts_dir) + ",built-ins",
            "--with-serverconfig-path=" + str(self.install_prefix / "lib/X11"),
            "--disable-selective-werror",
            "--disable-xwayland",
            "--with-fontrootdir=" + str(fonts_dir),
            "--with-xkb-path=" + str(BuildXKeyboardConfig.get_instance(self).install_prefix / "share/X11/xkb"),
            "--with-xkb-bin-directory=" + str(BuildXKkbcomp.get_instance(self).install_prefix / "bin"),
        ])
        tigervnc = BuildTigerVNC.get_instance(self)
        self.make_args.set(TIGERVNC_SRCDIR=tigervnc.source_dir, TIGERVNC_BUILDDIR=tigervnc.build_dir)
        self.COMMON_LDFLAGS.append("-Wl,-rpath," + str(BuildFreeType2.get_instance(self).install_prefix / "lib"))
        if self.compiling_for_cheri():
            self.cross_warning_flags.append("-Wno-error=cheri-capability-misuse")


class BuildTWM(X11AutotoolsProject):
    # Simple window manager to use with XVnc (KWin has too many dependencies)
    target = "twm"
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/app/twm.git")
    dependencies = ["libx11", "libxt", "libsm", "libice", "libxext", "libxrandr", "libxmu"]

    def setup(self):
        super().setup()
        if self.compiling_for_cheri():
            self.cross_warning_flags.append("-Wno-error=cheri-capability-misuse")


class BuildLibXcomposite(X11AutotoolsProject):
    target = "libxcomposite"
    dependencies = ["libxfixes"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxcomposite.git")
    builds_docbook_xml = True


class BuildLibXpm(X11AutotoolsProject):
    target = "libxpm"
    dependencies = ["libx11", "libxt", "libxext"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxpm.git")

    def setup(self):
        super().setup()
        if self.compiling_for_cheri():
            self.cross_warning_flags.append("-Wno-error=cheri-capability-misuse")


# Slightly more functional window manager than TWM
class BuildIceWM(X11Mixin, CrossCompileCMakeProject):
    target = "icewm"
    dependencies = ["fontconfig", "libxcomposite", "libxdamage", "libpng", "libjpeg-turbo",
                    "libxpm", "libxft", "libxrandr"]
    repository = GitRepository("https://github.com/bbidulock/icewm",
                               old_urls=[b"https://github.com/arichardson/icewm"])

    def setup(self):
        super().setup()
        # /usr/local/bin/icewmbg --scaled=1 --center=1 --image /root/cherries.jpeg
        self.add_cmake_options(CONFIG_LIBPNG=True, CONFIG_LIBJPEG=True, CONFIG_IMLIB2=False, CONFIG_XPM=True)
        self.add_cmake_options(ENABLE_NLS=False, CONFIG_I18N=False)
