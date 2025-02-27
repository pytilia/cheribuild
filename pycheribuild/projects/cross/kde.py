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
import tempfile
from pathlib import Path

from .crosscompileproject import CrossCompileAutotoolsProject, CrossCompileCMakeProject
from .freetype import BuildFontConfig, BuildFreeType2
from .qt5 import BuildQtBase, BuildSharedMimeInfo
from .wayland import BuildLibInput
from .x11 import BuildLibXCB
from ..project import DefaultInstallDir, GitRepository, MakeCommandKind, TargetAliasWithDependencies
from ...colour import AnsiColour, coloured
from ...config.chericonfig import BuildType
from ...config.compilation_targets import CompilationTargets
from ...config.loader import ComputedDefaultValue
from ...processutils import set_env
from ...utils import is_case_sensitive_dir, OSInfo


class KDECMakeProject(CrossCompileCMakeProject):
    do_not_add_to_targets = True
    default_install_dir = DefaultInstallDir.KDE_PREFIX
    default_build_type = BuildType.RELWITHDEBINFO
    supported_architectures = CompilationTargets.ALL_SUPPORTED_CHERIBSD_AND_HOST_TARGETS
    # Group all the frameworks source directories together
    default_source_dir = ComputedDefaultValue(
        function=lambda config, project: config.source_root / "kde-frameworks" / project.default_directory_basename,
        as_string=lambda cls: "$SOURCE_ROOT/kde-frameworks" + cls.default_directory_basename)

    tests_need_full_disk_image = False  # default to running with the full disk image
    _has_qt_designer_plugin = False
    _needs_newer_bison = False
    # Default to not building the tests since it saves a lot of build time
    has_optional_tests = True
    default_build_tests = False
    show_optional_tests_in_help = False

    @classmethod
    def dependencies(cls, config) -> "list[str]":
        result = super().dependencies(config)
        return result + ["qtbase", "extra-cmake-modules"]

    @property
    def ctest_script_extra_args(self):
        # Prefer the libraries in the build directory over the installed ones. This is needed when RPATH is not set
        # correctly, i.e. when built with CMake+Ninja on macOS with a version where
        # https://gitlab.kitware.com/cmake/cmake/-/merge_requests/6240 is not included.
        kde_prefix = self.install_prefix
        if self.tests_need_full_disk_image:
            return ["--test-setup-command", ". /build/prefix.sh && env | sort"]
        return ["--extra-library-path", "/build/bin", "--extra-library-path", "/build/lib",
                # Add the libraries from other frameworks
                "--extra-library-path", "/sysroot" + str(self.install_prefix) + "/lib:/sysroot/usr/lib:/sysroot/lib",
                # Also need the X11 libraries for most tests
                "--extra-library-path", "/sysroot" + str(BuildLibXCB.get_instance(self).install_prefix) + "/lib",
                # And of course QtCore/QtTest
                "--extra-library-path", "/sysroot" + str(BuildQtBase.get_instance(self).install_prefix) + "/lib",
                "--test-setup-command",
                "mkdir -p {} && ln -sn /sysroot{} {}".format(kde_prefix.parent, kde_prefix, kde_prefix),
                "--test-setup-command", ". /build/prefix.sh && env | sort"]

    def setup(self):
        super().setup()
        if self.target_info.is_macos():
            self.add_cmake_options(APPLE_SUPPRESS_X11_WARNING=True)
        # Skip the QtDesigner plugin for now, it won't be particularly useful
        if self._has_qt_designer_plugin:
            self.add_cmake_options(BUILD_DESIGNERPLUGIN=False)
        if not self.compiling_for_host():
            # We need native tools (e.g. desktoptojson/kconfig_compiler) for some projects
            native_project = BuildKCoreAddons.get_instance(self, cross_target=CompilationTargets.NATIVE)
            self.add_cmake_options(
                KF5_HOST_TOOLING=native_project.install_dir / native_project.target_info.default_libdir / "cmake")
            dep_names = " ".join(x.name for x in self._direct_dependencies(self.config, include_sdk_dependencies=False,
                                                                           include_toolchain_dependencies=False,
                                                                           explicit_dependencies_only=True))
            if "qtx11extras" in dep_names:
                self.warning("Adding include path as workaround for broken QtX11Extras")
                self.COMMON_FLAGS.append("-I" + str(BuildLibXCB.get_install_dir(self) / "include"))
        if OSInfo.IS_MAC and self._needs_newer_bison:
            # /usr/bin/bison on macOS is too old
            self.add_cmake_options(BISON_EXECUTABLE=self.get_homebrew_prefix("bison") / "bin/bison")
        if not is_case_sensitive_dir(self.build_dir):
            # Most KDE projects install CamelCase headers with the class name to one directory (e.g. <KIO/AuthInfo> and
            # the actual .h to another lowercase one (<kio/authinfo.h>). However, on a case-insensitive FS this results
            # in: non-portable path to file '<KIO/authinfo.h>'; specified path differs in case from file name on disk
            self.common_warning_flags.append("-Wno-nonportable-include-path")
        self.add_cmake_options(BUILD_TESTING=self.build_tests)
        # Avoid building documentation:
        self.add_cmake_options(CMAKE_DISABLE_FIND_PACKAGE_Doxygen=True)
        self.add_cmake_options(CMAKE_DISABLE_FIND_PACKAGE_KF5DocTools=True)

    @property
    def cmake_prefix_paths(self):
        return [self.install_dir, BuildQtBase.get_install_dir(self)] + super().cmake_prefix_paths

    def run_tests(self):
        self.info("To debug failing tests, you can increase verbosity by setting",
                  coloured(AnsiColour.yellow, 'export QT_LOGGING_RULES="*.debug=true"'))
        super().run_tests()


# TODO: should generate the dependency graph from
#  https://invent.kde.org/sysadmin/repo-metadata/-/blob/master/dependencies/dependency-data-kf5-qt5
class BuildExtraCMakeModules(KDECMakeProject):
    target = "extra-cmake-modules"
    dependencies = []
    repository = GitRepository("https://invent.kde.org/frameworks/extra-cmake-modules.git")


class BuildPhonon(KDECMakeProject):
    target = "phonon"
    repository = GitRepository("https://invent.kde.org/libraries/phonon.git")


class BuildLibIntlLite(CrossCompileCMakeProject):
    target = "libintl-lite"
    repository = GitRepository("https://github.com/j-jorge/libintl-lite")

    def setup(self):
        super().setup()
        # We have to compile with -fPIC since this static library will be included in a shared library
        self.add_cmake_options(CMAKE_POSITION_INDEPENDENT_CODE=True)


# Full gettext should not be needed, libintl-lite should be sufficient
class BuildGettext(CrossCompileAutotoolsProject):
    target = "gettext"
    repository = GitRepository("https://git.savannah.gnu.org/git/gettext.git")
    make_kind = MakeCommandKind.GnuMake

    def setup(self):
        super().setup()
        self.configure_args.extend([
            "--enable-relocatable",
            "--disable-csharp",
            "--disable-java",
            "--disable-libasprintf",
            "--disable-openmp",
            "--without-emacs",
            "--with-included-gettext",
            "ac_cv_lib_rt_sched_yield=no"
        ])

    def configure(self, **kwargs):
        # gettext-runtime/intl
        if not (self.source_dir / "configure").exists():
            self.run_cmd(self.source_dir / "autogen.sh", cwd=self.source_dir)
        super().configure()

    def clean(self):
        if not (self.source_dir / "Makefile").exists():
            return None
        self.run_make("distclean", cwd=self.source_dir)

    def compile(self, **kwargs):
        self.run_make("all", cwd=self.build_dir / "gettext-runtime/intl")

    def install(self, **kwargs):
        self.run_make_install(cwd=self.build_dir / "gettext-runtime/intl")

    def process(self):
        new_env = dict()
        if OSInfo.IS_MAC:
            # /usr/bin/bison and /usr/bin/sed on macOS are not compatible with this build system
            new_env["PATH"] = ":".join([str(self.get_homebrew_prefix("gnu-sed") / "libexec/gnubin"),
                                        str(self.get_homebrew_prefix("bison") / "bin"),
                                        os.getenv("PATH")])
        with set_env(**new_env):
            super().process()


#
# Frameworks, tier1
#
# frameworks/syntax-highlighting: third-party/taglib
# frameworks/kwayland: kdesupport/plasma-wayland-protocols
class BuildBreezeIcons(KDECMakeProject):
    target = "breeze-icons"
    repository = GitRepository("https://invent.kde.org/frameworks/breeze-icons.git")


class BuildAttica(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/attica.git")


class BuildKArchive(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/karchive.git")


class BuildKCodecs(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kcodecs.git")


class BuildKCoreAddons(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kcoreaddons.git",
                               temporary_url_override="https://invent.kde.org/arichardson/kcoreaddons.git",
                               url_override_reason="A few minor fixes/speedups that haven't been merged yet")

    def setup(self):
        super().setup()
        # Install prefix.sh for KCoreAddons only (could do it for all projects but there is no point overwriting it)
        self.add_cmake_options(KDE_INSTALL_PREFIX_SCRIPT=True)
        shared_mime_install_dir = BuildSharedMimeInfo.get_install_dir(self, cross_target=CompilationTargets.NATIVE)
        self.add_cmake_options(UPDATE_MIME_DATABASE_EXECUTABLE=shared_mime_install_dir / "bin/update-mime-database")
        self.make_args.set_env(UPDATE_MIME_DATABASE_EXECUTABLE=shared_mime_install_dir / "bin/update-mime-database")

    def install(self, **kwargs):
        super().install(**kwargs)
        # update_xdg_mimetypes() is not run if DESTDIR is set.
        # See https://invent.kde.org/frameworks/extra-cmake-modules/-/merge_requests/151
        shared_mime_info = BuildSharedMimeInfo.get_instance(self)
        native_smi_dir = BuildSharedMimeInfo.get_install_dir(self, cross_target=CompilationTargets.NATIVE)
        self.run_cmd(native_smi_dir / "bin/update-mime-database", "-V", self.install_dir / "share/mime")
        if not self.compiling_for_host():
            # TODO: should probably just install Qt and KDE files in the same directory
            install_prefix = self.install_prefix
            qt_dir = BuildQtBase.get_instance(self).install_prefix
            self.write_file(self.rootfs_path / "usr/local/bin/kde-shell-x11", overwrite=True, mode=0o755,
                            contents=f"""#!/bin/sh
set -xe
export DISPLAY=:0
export QT_QPA_PLATFORM=xcb
if [ ! -f "{shared_mime_info.install_prefix / "share/mime/mime.cache"}" ]; then
    echo "MIME database cache is missing, run cheribuild.py {shared_mime_info.target}!"
    false;
fi
# Add the Qt install directory to $PATH if it isn't yet:
qtbindir="{qt_dir}/bin"
if [ "${{PATH#*$qtbindir}}" = "$PATH" ]; then
  echo "Qt bin dir is not in PATH, adding it"
  export "PATH=$qtbindir:$PATH"
fi
qtsharedir="{qt_dir}/share"
XDG_DATA_DIRS=${{XDG_DATA_DIRS:-/usr/local/share/:/usr/share/}}
if [ "${{XDG_DATA_DIRS#*$qtsharedir}}" = "$XDG_DATA_DIRS" ]; then
  echo "Qt share/ dir is not in XDG_DATA_DIRS, adding it"
  export "XDG_DATA_DIRS=$qtsharedir:$XDG_DATA_DIRS"
fi
qtconfigdir="{qt_dir}/etc/xdg"
XDG_CONFIG_DIRS=${{XDG_CONFIG_DIRS:-/usr/local/etc/xdg:/etc/xdg}}
if [ -d "${{qtconfigdir}}" ] && [ "${{XDG_CONFIG_DIRS#*$qtconfigdir}}" = "XDG_CONFIG_DIRS" ]; then
  echo "Qt share/ dir is not in XDG_CONFIG_DIRS, adding it"
  export "XDG_CONFIG_DIRS=$qtsharedir:$XDG_CONFIG_DIRS"
fi
. {install_prefix}/prefix.sh
# Create all the XDG data directories if they don't exist
# Silence "QStandardPaths: XDG_RUNTIME_DIR not set, defaulting to '/tmp/runtime-root'"
if [ -z "$XDG_RUNTIME_DIR" ]; then
    XDG_RUNTIME_DIR=/tmp/$USER-runtime
    test -d "$XDG_RUNTIME_DIR" || mkdir -m 0700 "$XDG_RUNTIME_DIR"
fi
# Create the default XDG_* directories if the env vars aren't set:
# https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html
test -z "$XDG_CONFIG_HOME" && mkdir -p $HOME/.config
test -z "$XDG_DATA_HOME" && mkdir -p $HOME/.local/share
test -z "$XDG_STATE_HOME" && mkdir -p $HOME/.local/state
test -z "$XDG_CACHE_HOME" && mkdir -p $HOME/.cache
env | sort
set +xe
# Provide a resonable logging rules default
# TODO: should we write a QtProject/qtlogging.ini file?
# To debug logging rules we can set QT_LOGGING_DEBUG=1
printf "To get debug output from application you can run:\n\t export \\"QT_LOGGING_RULES=%s%s%s%s\\"\\n" \
    "*.debug=true;qt.qpa.*.debug=false;qt.text.*.debug=false;qt.accessibility.*.debug=false;" \
    "qt.gui.shortcutmap=false;qt.quick.*.debug=false;qt.scenegraph.*.debug=false;qt.v4.*.debug=false;" \
    "qt.qml.gc.*.debug=false;" \
    "kf.coreaddons.desktopparser.*.debug=false;"
# Running with the default SHELL=/bin/csh breaks gdb since GDB start all programs with $SHELL
# by default and csh "helpfully" decides to reset $PATH to the default.
export SHELL=/bin/sh
exec sh
""")
            self.write_file(self.rootfs_path / "usr/local/bin/kde-shell-x11-smbfs", overwrite=True, mode=0o755,
                            contents=f"""#!/bin/sh
set -xe
if df -t smbfs,nfs "{install_prefix}" >/dev/null 2>/dev/null; then
    echo "{install_prefix} is already mounted from the host, skipping"
else
    mv "{install_prefix}" "{install_prefix}-old"
    qemu-mount-rootfs.sh
    ln -sfn "/nfsroot/{install_prefix}" "{install_prefix}"
fi
set +xe
exec /usr/local/bin/kde-shell-x11
""")


class BuildKConfig(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kconfig.git")


class BuildKDBusAddons(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kdbusaddons.git")

    @classmethod
    def dependencies(cls, config) -> "list[str]":
        if cls.get_crosscompile_target(config).target_info_cls.is_macos():
            return super().dependencies(config)
        return super().dependencies(config) + ["qtx11extras"]


class BuildKGuiAddons(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kguiaddons.git")

    @classmethod
    def dependencies(cls, config) -> "list[str]":
        if cls.get_crosscompile_target(config).target_info_cls.is_macos():
            return super().dependencies(config)
        return super().dependencies(config) + ["qtx11extras"]

    def setup(self):
        super().setup()
        # TODO: wayland support
        self.add_cmake_options(WITH_WAYLAND=False)


class BuildKItemViews(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kitemviews.git")
    _has_qt_designer_plugin = True


class BuildKItemModels(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kitemmodels.git")


class BuildKI18N(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/ki18n.git")

    @classmethod
    def dependencies(cls, config) -> "list[str]":
        return super().dependencies(config) + ["libintl-lite"]

    def setup(self):
        super().setup()
        # Avoid QtQml dependency since we don't really care about translations right now
        self.add_cmake_options(BUILD_WITH_QML=False)


class BuildKWidgetsAddons(KDECMakeProject):
    target = "kwidgetsaddons"
    repository = GitRepository("https://invent.kde.org/frameworks/kwidgetsaddons.git")
    _has_qt_designer_plugin = True


class BuildKWindowSystem(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kwindowsystem.git")

    @classmethod
    def dependencies(cls, config) -> "list[str]":
        if cls.get_crosscompile_target(config).target_info_cls.is_macos():
            return super().dependencies(config)
        return super().dependencies(config) + ["qtx11extras", "libxfixes", "libxrender"]


class BuildLibQREncode(KDECMakeProject):
    target = "libqrencode"
    repository = GitRepository("https://github.com/fukuchi/libqrencode")

    def setup(self):
        super().setup()
        # We have to compile with -fPIC since this static library will be included in a shared library
        self.add_cmake_options(CMAKE_POSITION_INDEPENDENT_CODE=True)


class BuildPrison(KDECMakeProject):
    target = "prison"
    dependencies = ["libqrencode"]
    repository = GitRepository("https://invent.kde.org/frameworks/prison.git")


class BuildSolid(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/solid.git")
    # XXX: https://foss.heptapod.net/bsdutils/bsdisks for the DBus API
    _needs_newer_bison = True


class BuildSonnet(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/sonnet.git")
    # TODO: should probably install a spell checker:
    # -- The following OPTIONAL packages have not been found:
    # * ASPELL, Spell checking support via Aspell, <http://aspell.net/>
    # * HSPELL, Spell checking support for Hebrew, <http://ivrix.org.il/projects/spell-checker/>
    # * HUNSPELL, Spell checking support via Hunspell, <http://hunspell.sourceforge.net/>
    # * VOIKKO, Spell checking support via Voikko, <http://voikko.puimula.org/>
    _has_qt_designer_plugin = True


#
# Frameworks, tier2
#


class BuildKAuth(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kauth.git")
    dependencies = ["kcoreaddons", "kcoreaddons-native"]  # optional: "polkit-qt-1"


class BuildKCompletion(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kcompletion.git")
    dependencies = ["kconfig", "kconfig-native", "kwidgetsaddons"]
    _has_qt_designer_plugin = True


class BuildKCrash(KDECMakeProject):
    dependencies = ["kcoreaddons", "kcoreaddons-native", "qtx11extras", "kwindowsystem"]
    repository = GitRepository("https://invent.kde.org/frameworks/kcrash.git")


class BuildKJobWidgets(KDECMakeProject):
    dependencies = ["kcoreaddons", "kcoreaddons-native", "kwidgetsaddons", "qtx11extras"]
    repository = GitRepository("https://invent.kde.org/frameworks/kjobwidgets.git")


# class BuildKDocTools(KDECMakeProject):
#     dependencies = ["karchive", "ki18n"]
#     repository = GitRepository("https://invent.kde.org/frameworks/kdoctools.git")


class BuildKNotifications(KDECMakeProject):
    # frameworks/knotifications: third-party/libdbusmenu-qt
    repository = GitRepository("https://invent.kde.org/frameworks/knotifications.git")

    @classmethod
    def dependencies(cls, config) -> "list[str]":
        result = ["qtdeclarative", "kwindowsystem", "kconfig", "kconfig-native", "kcoreaddons", "kcoreaddons-native",
                  "phonon"]
        if cls.get_crosscompile_target(config).target_info_cls.is_macos():
            return result + ["qtmacextras"]
        return result + ["qtx11extras"]


class BuildKPackage(KDECMakeProject):
    dependencies = ["karchive", "ki18n", "kcoreaddons", "kcoreaddons-native"]
    repository = GitRepository("https://invent.kde.org/frameworks/kpackage.git",
                               temporary_url_override="https://invent.kde.org/arichardson/kpackage.git",
                               url_override_reason="Various cross-compilation fixes - TODO: clean&submit MR")


class BuildKSyndication(KDECMakeProject):
    dependencies = ["kcodecs"]
    repository = GitRepository("https://invent.kde.org/frameworks/syndication.git")


class BuildKImageFormats(KDECMakeProject):
    target = "kimageformats"
    repository = GitRepository("https://invent.kde.org/frameworks/kimageformats.git")
    dependencies = ["karchive"]


class BuildKUnitConversion(KDECMakeProject):
    target = "kunitconversion"
    dependencies = ["ki18n", "kconfig"]
    repository = GitRepository("https://invent.kde.org/frameworks/kunitconversion.git")


#
# Frameworks, tier3
#
class BuildKBookmarks(KDECMakeProject):
    dependencies = ["kconfigwidgets", "kcodecs", "kiconthemes", "kxmlgui"]
    repository = GitRepository("https://invent.kde.org/frameworks/kbookmarks.git")


class BuildKCMUtils(KDECMakeProject):
    dependencies = ["kitemviews", "kconfigwidgets", "kservice", "kxmlgui", "kdeclarative", "kauth"]
    repository = GitRepository("https://invent.kde.org/frameworks/kcmutils.git")


class BuildKConfigWidgets(KDECMakeProject):
    dependencies = ["kauth", "kcoreaddons", "kcodecs", "kconfig", "kguiaddons", "ki18n", "kwidgetsaddons",
                    "kconfig-native"]
    repository = GitRepository("https://invent.kde.org/frameworks/kconfigwidgets.git")
    _has_qt_designer_plugin = True


# frameworks/kdav: frameworks/kio
# frameworks/kdesignerplugin: frameworks/kcoreaddons
# frameworks/kdesignerplugin: frameworks/kconfig
# frameworks/kdesignerplugin: frameworks/kdoctools
# frameworks/kemoticons: frameworks/karchive
# frameworks/kemoticons: frameworks/kservice
# frameworks/kjs: frameworks/kdoctools
class BuildKNewStuff(KDECMakeProject):
    dependencies = ["attica", "kitemviews", "kiconthemes", "ktextwidgets", "kxmlgui",
                    "solid", "kio", "kbookmarks", "kpackage", "kpackage-native", "ksyndication", "kirigami"]
    repository = GitRepository("https://invent.kde.org/frameworks/knewstuff.git")
    _needs_newer_bison = True


class BuildKService(KDECMakeProject):
    dependencies = ["kconfig", "kcoreaddons", "kcrash", "kdbusaddons", "ki18n",
                    "kcoreaddons-native",  # desktoptojson
                    "kconfig-native",  # kconfig_compiler
                    ]
    repository = GitRepository("https://invent.kde.org/frameworks/kservice.git")
    _needs_newer_bison = True


class BuildKTextWidgets(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/ktextwidgets.git")
    dependencies = ["sonnet", "kcompletion", "kconfigwidgets", "kwidgetsaddons"]
    _has_qt_designer_plugin = True


class BuildKParts(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kparts.git")
    dependencies = ["kio", "kxmlgui", "ktextwidgets", "knotifications"]
    _has_qt_designer_plugin = True


class BuildKIconThemes(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kiconthemes.git")
    dependencies = ["kconfigwidgets", "kwidgetsaddons", "kitemviews", "karchive", "ki18n", "breeze-icons", "qtsvg"]
    _has_qt_designer_plugin = True


class BuildKGlobalAccel(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kglobalaccel.git")

    @classmethod
    def dependencies(cls, config) -> "list[str]":
        result = ["kconfig", "kconfig-native", "kcrash", "kdbusaddons", "kwindowsystem"]
        if not cls.get_crosscompile_target(config).target_info_cls.is_macos():
            result += ["qtx11extras", "libxcb"]
        return result


class BuildKXMLGUI(KDECMakeProject):
    dependencies = ["kitemviews", "kconfig", "kconfig-native", "kglobalaccel",
                    "kconfigwidgets", "ki18n", "kiconthemes",
                    "ktextwidgets", "kwidgetsaddons", "kwindowsystem"]
    repository = GitRepository("https://invent.kde.org/frameworks/kxmlgui.git")
    _has_qt_designer_plugin = True


class BuildKDeclarative(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kdeclarative.git")
    dependencies = ["kpackage", "kpackage-native", "kio", "kiconthemes", "knotifications", "qtdeclarative", "kio"]
    _has_qt_designer_plugin = True

    def setup(self):
        super().setup()
        # We build Qt wihtout OpenGL support, so we shouldn't build the OpenGL code.
        self.add_cmake_options(CMAKE_DISABLE_FIND_PACKAGE_epoxy=True)


class BuildKInit(KDECMakeProject):
    target = "kinit"
    dependencies = ["kio", "kservice", "kcrash", "kjobwidgets", "solid", "kdbusaddons", "kwindowsystem", "libx11",
                    "libxcb"]
    repository = GitRepository("https://invent.kde.org/frameworks/kinit.git")


class BuildKNotifyConfig(KDECMakeProject):
    target = "knotifyconfig"
    dependencies = ["kio", "ki18n", "knotifications"]
    repository = GitRepository("https://invent.kde.org/frameworks/knotifyconfig.git")


class BuildKDED(KDECMakeProject):
    target = "kded"
    dependencies = ["kservice", "kcrash", "kdbusaddons"]
    repository = GitRepository("https://invent.kde.org/frameworks/kded.git")


class BuildKIO(KDECMakeProject):
    target = "kio"
    dependencies = ["kauth", "kdbusaddons", "ki18n", "kguiaddons", "kconfigwidgets", "kitemviews", "kcoreaddons",
                    "kwidgetsaddons", "kservice", "karchive", "qtx11extras", "solid",
                    "kjobwidgets", "kiconthemes", "kwindowsystem", "kcrash", "kcompletion", "ktextwidgets",
                    "kxmlgui", "kbookmarks", "kconfig", "kconfig-native", "knotifications", "kded",
                    # optional: "kwallet"
                    ]
    repository = GitRepository("https://invent.kde.org/frameworks/kio.git")
    _has_qt_designer_plugin = True


# frameworks/kmediaplayer: frameworks/ki18n
# frameworks/kmediaplayer: frameworks/kparts
# frameworks/kmediaplayer: frameworks/kxmlgui
# frameworks/kdewebkit: frameworks/kcoreaddons
# frameworks/kdewebkit: frameworks/kwallet
# frameworks/kdewebkit: frameworks/kio
# frameworks/kdewebkit: frameworks/knotifications
# frameworks/kdewebkit: frameworks/kparts
# frameworks/kdesu: frameworks/kcoreaddons
# frameworks/kdesu: frameworks/kservice
# frameworks/kdesu: frameworks/kpty
# frameworks/ktexteditor: frameworks/karchive
# frameworks/ktexteditor: frameworks/kconfig
# frameworks/ktexteditor: frameworks/kguiaddons
# frameworks/ktexteditor: frameworks/ki18n
# frameworks/ktexteditor: frameworks/kjobwidgets
# frameworks/ktexteditor: frameworks/kio
# frameworks/ktexteditor: frameworks/kparts
# frameworks/ktexteditor: frameworks/sonnet
# frameworks/ktexteditor: frameworks/kxmlgui
# frameworks/ktexteditor: frameworks/syntax-highlighting
# frameworks/kwallet: frameworks/kconfig
# frameworks/kwallet: frameworks/kcoreaddons
# frameworks/kwallet: frameworks/kdbusaddons
# frameworks/kwallet: frameworks/kiconthemes
# frameworks/kwallet: frameworks/ki18n
# frameworks/kwallet: frameworks/knotifications
# frameworks/kwallet: frameworks/kservice
# frameworks/kwallet: frameworks/kwindowsystem
# frameworks/kwallet: frameworks/kwidgetsaddons
# frameworks/kwallet: third-party/gpgme
# frameworks/purpose: frameworks/kcoreaddons
# frameworks/purpose: frameworks/kconfig
# frameworks/purpose: frameworks/ki18n
# frameworks/purpose: frameworks/kio
# frameworks/purpose: frameworks/kirigami
# frameworks/kxmlrpcclient: frameworks/kio
# frameworks/kcontacts: frameworks/kcoreaddons
# frameworks/kcontacts: frameworks/ki18n
# frameworks/kcontacts: frameworks/kconfig
# frameworks/kcontacts: frameworks/kcodecs
# frameworks/baloo: frameworks/kfilemetadata
# frameworks/baloo: frameworks/kcoreaddons
# frameworks/baloo: frameworks/kconfig
# frameworks/baloo: frameworks/kdbusaddons
# frameworks/baloo: frameworks/ki18n
# frameworks/baloo: frameworks/kidletime
# frameworks/baloo: frameworks/solid
# frameworks/baloo: frameworks/kcrash
# frameworks/baloo: frameworks/kio
class BuildKPeople(KDECMakeProject):
    target = "kpeople"
    repository = GitRepository("https://invent.kde.org/frameworks/kpeople.git")
    dependencies = ["kcoreaddons", "kcoreaddons-native", "kwidgetsaddons", "ki18n", "kitemviews"]


class BuildKSyntaxHighlighting(KDECMakeProject):
    # This includes e.g. the thumbnail provider for dolphin
    target = "ksyntaxhighlighting"
    needs_native_build_for_crosscompile = True
    repository = GitRepository("https://invent.kde.org/frameworks/syntax-highlighting.git")

    def setup(self):
        super().setup()
        if not self.compiling_for_host():
            native_build = self.get_instance(self, cross_target=CompilationTargets.NATIVE).build_dir
            self.add_cmake_options(KATEHIGHLIGHTINGINDEXER_EXECUTABLE=native_build / "bin/katehighlightingindexer")


class BuildKioExtras(KDECMakeProject):
    # This includes e.g. the thumbnail provider for dolphin
    target = "kio-extras"
    dependencies = ["kio", "ksyntaxhighlighting"]
    repository = GitRepository("https://invent.kde.org/network/kio-extras.git",
                               temporary_url_override="https://invent.kde.org/arichardson/kio-extras.git",
                               url_override_reason="https://invent.kde.org/network/kio-extras/-/merge_requests/110")


class BuildKFileMetadata(KDECMakeProject):
    # This includes e.g. the thumbnail provider for dolphin
    target = "kfilemetadata"
    # TODO: depend on poppler for PDF medatadata
    dependencies = ["karchive", "kconfig", "ki18n", "karchive", "poppler"]
    repository = GitRepository("https://invent.kde.org/frameworks/kfilemetadata.git")


class BuildKActivities(KDECMakeProject):
    target = "kactivities"
    dependencies = ["kio", "kwindowsystem", "kcoreaddons", "kconfig"]
    repository = GitRepository("https://invent.kde.org/frameworks/kactivities.git")

    def setup(self):
        super().setup()
        self.add_cmake_options(KACTIVITIES_LIBRARY_ONLY=True)  # avoid dependency on boost


class BuildKActivitiesStats(KDECMakeProject):
    target = "kactivities-stats"
    dependencies = ["kactivities"]
    repository = GitRepository("https://invent.kde.org/frameworks/kactivities-stats.git",
                               force_branch=True, default_branch="work/adridg/reduce-boost")  # avoid boost dep


class BuildKirigami(KDECMakeProject):
    target = "kirigami"
    dependencies = ["qtquickcontrols2", "extra-cmake-modules", "qtgraphicaleffects"]
    repository = GitRepository("https://invent.kde.org/frameworks/kirigami.git",
                               temporary_url_override="https://invent.kde.org/arichardson/kirigami.git",
                               url_override_reason="Needs some compilation fixes for -no-opengl QtBase")
    # TODO: DISABLE_DBUS=True?


class BuildPlasmaFramework(KDECMakeProject):
    target = "plasma-framework"
    dependencies = ["kio", "kconfigwidgets", "kactivities", "kdbusaddons", "kglobalaccel", "kpackage", "kdeclarative",
                    "qtquickcontrols", "qtquickcontrols2", "kxmlgui", "threadweaver", "kirigami"]
    repository = GitRepository("https://invent.kde.org/frameworks/plasma-framework.git",
                               temporary_url_override="https://invent.kde.org/arichardson/plasma-framework.git",
                               url_override_reason="Needs some compilation fixes for -no-opengl QtBase")


class BuildKRunner(KDECMakeProject):
    target = "krunner"
    dependencies = ["kio", "solid", "kconfig", "kcompletion", "kservice", "threadweaver", "ki18n", "plasma-framework"]
    repository = GitRepository("https://invent.kde.org/frameworks/krunner.git",)


class BuildKDecoration(KDECMakeProject):
    target = "kdecoration"
    repository = GitRepository("https://invent.kde.org/plasma/kdecoration.git")
    dependencies = ["ki18n"]


class BuildKFrameworkIntegration(KDECMakeProject):
    target = "kframeworkintegration"
    repository = GitRepository("https://invent.kde.org/frameworks/frameworkintegration")
    dependencies = ["knewstuff"]


class BuildBreezeStyle(KDECMakeProject):
    target = "breeze"
    repository = GitRepository("https://invent.kde.org/plasma/breeze.git")
    dependencies = ["kdecoration", "kconfig", "kcoreaddons", "kguiaddons", "kiconthemes", "kconfigwidgets",
                    "kwindowsystem", "kframeworkintegration"]


class BuildKIdleTime(KDECMakeProject):
    target = "kidletime"
    repository = GitRepository("https://invent.kde.org/frameworks/kidletime.git")

    @classmethod
    def dependencies(cls, config) -> "list[str]":
        result = super().dependencies(config)
        if not cls.get_crosscompile_target(config).is_native():
            result.extend(["libxext", "libxcb", "qtx11extras"])
        return result


class BuildKScreenLocker(KDECMakeProject):
    target = "kscreenlocker"
    repository = GitRepository("https://invent.kde.org/plasma/kscreenlocker.git",
                               temporary_url_override="https://invent.kde.org/arichardson/kscreenlocker.git",
                               url_override_reason="https://invent.kde.org/plasma/kscreenlocker/-/merge_requests/41")
    dependencies = ["kwindowsystem", "kxmlgui", "kwindowsystem", "kidletime", "libxcb"]

    def setup(self):
        super().setup()
        # TODO: build wayland bits
        self.add_cmake_options(KSCREENLOCKER_BUILD_WAYLAND=False)


class BuildKWin(KDECMakeProject):
    target = "kwin"
    repository = GitRepository("https://invent.kde.org/plasma/kwin.git",
                               temporary_url_override="https://invent.kde.org/arichardson/kwin.git",
                               url_override_reason="Needs lots of ifdefs for -no-opengl QtBase and no-wayland")
    dependencies = ["kdecoration", "qtx11extras", "breeze", "kcmutils", "kscreenlocker", "libinput", "qttools"]

    def setup(self):
        super().setup()
        # TODO: build wayland backend
        self.add_cmake_options(KWIN_BUILD_WAYLAND=False)
        if self.target_info.is_freebsd():
            # To get linux/input.h on FreeBSD
            if not BuildLibInput.get_source_dir(self).exists():
                self.warning("Need to clone libinput first to get linux/input.h compat header.")
                self.ask_for_confirmation("Would you like to clone it now?")
                BuildLibInput.get_instance(self).update()
            self.COMMON_FLAGS.append("-isystem" + str(BuildLibInput.get_source_dir(self) / "include"))


class BuildLibKScreen(KDECMakeProject):
    target = "libkscreen"
    repository = GitRepository("https://invent.kde.org/plasma/libkscreen.git",
                               temporary_url_override="https://invent.kde.org/arichardson/libkscreen.git",
                               url_override_reason="Support for no-wayland")
    dependencies = ["qtx11extras"]

    def setup(self):
        super().setup()
        self.add_cmake_options(LIBKSCREEN_BUILD_WAYLAND=False)


class BuildLibKSysguard(KDECMakeProject):
    target = "libksysguard"
    repository = GitRepository("https://invent.kde.org/plasma/libksysguard.git")
    dependencies = ["kio"]


# class BuildKQuickCharts(KDECMakeProject):
#     # Needs openGL!
#     target = "kquickcharts"
#     repository = GitRepository("https://invent.kde.org/frameworks/kquickcharts.git")
#     dependencies = ["qtquickcontrols2"]


class BuildPlasmaWorkspace(KDECMakeProject):
    target = "plasma-workspace"
    repository = GitRepository("https://invent.kde.org/plasma/plasma-workspace.git",
                               temporary_url_override="https://invent.kde.org/arichardson/plasma-workspace.git",
                               url_override_reason="Lots of no-wayland changes etc.")
    dependencies = ["xprop", "xsetroot", "plasma-framework", "kwin", "breeze", "kidletime", "kitemmodels", "kcmutils",
                    "knotifyconfig", "kded", "kinit", "kscreenlocker", "libkscreen", "libxft", "libxtst", "kpeople",
                    "kparts", "prison", "krunner", "kactivities-stats", "libksysguard", "kunitconversion"]
    # needs OpenGL: "kquickcharts"

    def setup(self):
        super().setup()
        self.add_cmake_options(CHERI_DEMO=True)  # TODO: build everything


class BuildQQC2DesktopStyle(KDECMakeProject):
    target = "qqc2-desktop-style"
    repository = GitRepository("https://invent.kde.org/frameworks/qqc2-desktop-style.git")
    dependencies = ["kirigami", "kiconthemes", "kconfigwidgets", "qtx11extras"]


class BuildQQC2BreezeStyle(KDECMakeProject):
    target = "qqc2-breeze-style"
    repository = GitRepository("https://invent.kde.org/plasma/qqc2-breeze-style.git")
    dependencies = ["kirigami", "kiconthemes", "kconfigwidgets", "qtx11extras", "breeze"]


class BuildPlasmaDesktop(KDECMakeProject):
    target = "plasma-desktop"
    repository = GitRepository(
        "https://invent.kde.org/plasma/plasma-desktop.git",
        temporary_url_override="https://invent.kde.org/arichardson/plasma-desktop.git",
        url_override_reason="needs e.g. https://invent.kde.org/plasma/plasma-desktop/-/merge_requests/532")
    dependencies = ["plasma-workspace", "qqc2-desktop-style", "libxkbfile", "xkeyboard-config"]


class BuildSystemSettings(KDECMakeProject):
    target = "systemsettings"
    repository = GitRepository("https://invent.kde.org/plasma/systemsettings.git",
                               default_branch="master", force_branch=True)
    dependencies = ["plasma-workspace"]


class BuildDoplhin(KDECMakeProject):
    target = "dolphin"
    dependencies = ["kparts", "kxmlgui", "knewstuff", "kio", "kcmutils", "kio-extras", "kfilemetadata"]
    repository = GitRepository("https://invent.kde.org/system/dolphin.git")


class BuildLibPng(CrossCompileCMakeProject):
    repository = GitRepository("https://github.com/glennrp/libpng",
                               temporary_url_override="https://github.com/CTSRD-CHERI/libpng",
                               url_override_reason="Needs https://github.com/glennrp/libpng/pull/386",
                               default_branch="libpng16", force_branch=True)
    target = "libpng"
    # The tests take a really long time to run (~2.5 hours on purecap RISC-V)
    ctest_script_extra_args = ("--test-timeout", 5 * 60 * 60)

    def setup(self):
        super().setup()
        if not self.compiling_for_host():
            # The CTest test script mounts the cmake install dir under /cmake
            self.add_cmake_options(TEST_CMAKE_COMMAND="/cmake/bin/cmake")
        if self.compiling_for_aarch64(include_purecap=True):
            # work around:  undefined reference to png_do_expand_palette_rgb8_neon [--no-allow-shlib-undefined]
            self.COMMON_FLAGS.append("-DPNG_ARM_NEON_OPT=0")


class BuildLCMS2(CrossCompileAutotoolsProject):
    repository = GitRepository("https://github.com/mm2/Little-CMS")
    target = "lcms2"

    def process(self):
        if OSInfo.IS_MAC:
            with tempfile.TemporaryDirectory() as td:
                # Work around awful autotools build system
                libtool_prefix = self.get_homebrew_prefix("libtool")
                self.create_symlink(libtool_prefix / "bin/glibtool", Path(td) / "libtool", relative=False)
                self.create_symlink(libtool_prefix / "bin/glibtoolize", Path(td) / "libtoolize", relative=False)
                with set_env(PATH=td + ":" + os.getenv("PATH", "")):
                    super().process()
        else:
            super().process()


class BuildExiv2(CrossCompileCMakeProject):
    repository = GitRepository("https://github.com/Exiv2/exiv2")
    target = "exiv2"
    dependencies = ["libexpat"]


class BuildGwenview(KDECMakeProject):
    target = "gwenview"
    dependencies = ["qtsvg", "kitemmodels", "kio", "kparts", "lcms2", "libpng", "exiv2"]
    repository = GitRepository("https://invent.kde.org/graphics/gwenview.git")


class BuildOpenJPEG(CrossCompileCMakeProject):
    target = "openjpeg"
    dependencies = ["lcms2", "libpng"]
    native_install_dir = DefaultInstallDir.BOOTSTRAP_TOOLS
    repository = GitRepository("https://github.com/uclouvain/openjpeg.git")

    def setup(self):
        super().setup()
        # TODO: upstream a fix to use PC_STATIC_LCMS2_LIBRARY_DIRS
        self.COMMON_LDFLAGS.append("-L" + str(BuildLCMS2.get_install_dir(self) / "lib"))


class BuildPoppler(CrossCompileCMakeProject):
    target = "poppler"
    dependencies = ["freetype2", "fontconfig", "openjpeg", "qtbase"]
    repository = GitRepository("https://gitlab.freedesktop.org/poppler/poppler.git",
                               temporary_url_override="https://gitlab.freedesktop.org/arichardson/poppler.git",
                               url_override_reason="cross-compilation fixes")

    @property
    def pkgconfig_dirs(self) -> "list[str]":
        return BuildFreeType2.get_instance(self).installed_pkgconfig_dirs() + \
               BuildFontConfig.get_instance(self).installed_pkgconfig_dirs() + self.target_info.pkgconfig_dirs

    def setup(self):
        super().setup()
        # Avoid boost dependency:
        self.add_cmake_options(ENABLE_BOOST=False)
        self.add_cmake_options(TESTDATADIR=self.source_dir / "testdata")

    def update(self):
        super().update()
        # Also clone the test data for unit tests
        test_repo = GitRepository("https://gitlab.freedesktop.org/poppler/test.git")
        test_repo.update(self, src_dir=self.source_dir / "testdata")

    @property
    def ctest_script_extra_args(self):
        return ["--extra-library-path", "/build/bin",
                "--extra-library-path", "/build/lib",
                "--extra-library-path", "/sysroot" + str(self.install_prefix) + "/lib:/sysroot/usr/lib:/sysroot/lib",
                "--extra-library-path", "/sysroot" + str(BuildQtBase.get_instance(self).install_prefix) + "/lib"]


class BuildThreadWeaver(KDECMakeProject):
    target = "threadweaver"
    repository = GitRepository("https://invent.kde.org/frameworks/threadweaver.git",
                               force_branch=True, default_branch="work/arichardson/cheri",
                               url_override_reason="https://invent.kde.org/frameworks/threadweaver/-/merge_requests/5")


# Doesn't build on FreeBSD properly:
# /Users/alex/cheri/kde-frameworks/kpty/src/kpty.cpp:72:10: fatal error: 'utmp.h' file not found
class BuildKPty(KDECMakeProject):
    target = "kpty"
    repository = GitRepository("https://invent.kde.org/frameworks/kpty",
                               temporary_url_override="https://invent.kde.org/arichardson/kpty",
                               url_override_reason="https://invent.kde.org/frameworks/kpty/-/merge_requests/12")

    def setup(self):
        super().setup()
        if not self.compiling_for_host():
            self.add_cmake_options(UTEMPTER_EXECUTABLE="/usr/libexec/ulog-helper")


class BuildOkular(KDECMakeProject):
    target = "okular"
    dependencies = ["poppler", "threadweaver", "kparts", "kio", "kiconthemes"]  # ktpy
    repository = GitRepository("https://invent.kde.org/graphics/okular.git")

    def setup(self):
        super().setup()
        self.add_cmake_options(ALLOW_OPTIONAL_DEPENDENCIES=True)


class BuildKDEX11Desktop(TargetAliasWithDependencies):
    target = "kde-x11-desktop"
    supported_architectures = CompilationTargets.ALL_SUPPORTED_CHERIBSD_AND_HOST_TARGETS
    dependencies = ["plasma-desktop", "dolphin", "okular", "gwenview", "systemsettings", "xvnc-server",
                    "xeyes", "twm", "xev"]  # Add some basic X11 things as a fallback
