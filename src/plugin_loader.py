#!/usr/bin/python
###########################################################################
# Description:
#
# Copyright (c) 2018 Nokia
###########################################################################
import argparse
import itertools
import logging
import os
import traceback
from typing import TYPE_CHECKING, Callable, Dict, Iterator, List, Optional, Tuple, Union

import pkg_resources
from srlinux import iterators
from srlinux.asserts import full_class_name
from srlinux.collection_util import is_list_of
from srlinux.mgmt.cli import log
from srlinux.mgmt.cli.cli_plugin import CliPlugin
from srlinux.mgmt.cli.plugin_error import PluginError
from srlinux.mgmt.cli.syslog_observer import SyslogObserver
from srlinux.mgmt.cli.tools_plugin import ToolsPlugin

from .cli_loader import CliLoader
from .required_plugin import RequiredPlugin

if TYPE_CHECKING:
    from srlinux.mgmt.cli.cli_state import CliState


_DISTRO_PLUGIN_FOLDER = "/opt/srlinux/cli/plugins"
_GLOBAL_PLUGIN_FOLDER = "/etc/opt/srlinux/cli/plugins"
_HOME_PLUGIN_FOLDER = "~/cli/plugins"  # must start with ~/


class PluginEntryPointDist(object):
    def __init__(self, project_name: str):
        self.project_name = project_name


class PluginEntryPoint(object):
    def __init__(self, module_name: str, name: str, path: str):
        self.dist = PluginEntryPointDist(module_name)
        self.name = name
        self.path = path

    def load(self) -> Optional[Callable[[], CliPlugin]]:
        # Every plugin is fully loaded into python global namespace
        # Ideally we would load the plugin into separate plugin namespace globals and locals and then merge them into
        # global ones, however it has issues with plugin file imports
        # Workaround is to immediately load the plugin into globals and check if the class is replaced with a newer one
        # (based on its object id change) to determine if the file contains Plugin class and should be loaded into the
        # cli command tree
        old_plugin_id = id(globals().get("Plugin"))
        source = open(self.path).read()
        code_object = compile(source, self.path, "exec")
        exec(code_object, globals(), globals())
        new_plugin_id = id(globals().get("Plugin"))
        if old_plugin_id != new_plugin_id:
            # new cli Plugin class was loaded, will be added to cli plugins
            return globals()["Plugin"]
        else:
            # the loaded python file did not contain a cli Plugin class
            log.SRCLI_LOG_DEBUG(f"No cli plugin found in: {self.path}")
            return None

    def __str__(self) -> str:
        return f"{self.name} = {self.path}:Plugin"


class PluginFileLoader(object):
    def __init__(
        self, username: str, distro_folder: str, global_folder: str, home_folder: str
    ):
        self.username = username
        self.distro_folder = distro_folder
        self.global_folder = global_folder
        self.home_folder = home_folder

    def construct_entry_point(
        self, path: str, name: Optional[str] = None, module_name: str = "srlinux"
    ) -> PluginEntryPoint:
        """Constructs entry point like object (name is the basename of the path without extension unless specified)"""
        if not name:
            name = os.path.splitext(os.path.basename(path))[0]
        return PluginEntryPoint(module_name, name, path)

    def get_third_party_entry_points(self) -> Iterator[PluginEntryPoint]:
        """Returns list of entry-points for all files found in global and home folders"""
        for folder in [
            dir
            for dir in [self.distro_folder, self.global_folder, self.home_folder]
            if dir
        ]:
            if folder.startswith("~/"):
                folder = f"~{self.username}{folder[1:]}"
            expanded_folder = os.path.expandvars(os.path.expanduser(folder))
            for dirpath, dirnames, filenames in os.walk(expanded_folder):
                for f in filenames:
                    ext = os.path.splitext(f)[1]
                    if ext == ".py":
                        abspath = os.path.join(dirpath, f)
                        if os.path.isfile(abspath) and os.access(abspath, os.R_OK):
                            message = f"Loading custom plugin from: {abspath}"
                            logging.debug(message)
                            if SyslogObserver.has_global_instance():
                                SyslogObserver.get_global_instance().log_line(
                                    logging.INFO, message
                                )
                            yield self.construct_entry_point(abspath)


class PluginLoader(object):
    @classmethod
    def create(cls, show_reports=False, home_plugins=False) -> "PluginLoader":
        def _is_show_report(
            entry_point: Union[pkg_resources.EntryPoint, PluginEntryPoint],
        ) -> bool:
            return ".plugins.reports." in str(entry_point)

        log.SRCLI_LOG_DEBUG("Walking entry points")
        entry_points = pkg_resources.iter_entry_points("srlinux.cli.plugin")

        if show_reports:
            filtered = (
                entry_point
                for entry_point in entry_points
                if _is_show_report(entry_point)
            )
        else:
            filtered = (
                entry_point
                for entry_point in entry_points
                if not _is_show_report(entry_point)
            )

        return PluginLoader(filtered, show_reports)

    @classmethod
    def create_third_party_plugins(
        cls, username, distro_plugins=False, global_plugins=False, user_plugins=False
    ) -> "PluginLoader":
        # add third party plugin entry points (walk all files in specified diretories)
        plugin_file_loader = PluginFileLoader(
            username,
            _DISTRO_PLUGIN_FOLDER if distro_plugins else "",
            _GLOBAL_PLUGIN_FOLDER if global_plugins else "",
            _HOME_PLUGIN_FOLDER if user_plugins else "",
        )
        third_party_entry_points = plugin_file_loader.get_third_party_entry_points()

        return PluginLoader(third_party_entry_points, False)

    def __init__(
        self,
        entry_points: Iterator[Union[pkg_resources.EntryPoint, PluginEntryPoint]],
        ignore_missing,
    ):
        self._ignore_missing = ignore_missing
        self._plugins = self._create_plugins(entry_points)

    def _create_plugins(
        self, entry_points: Iterator[Union[pkg_resources.EntryPoint, PluginEntryPoint]]
    ) -> List["_Plugin"]:
        log.SRCLI_LOG_DEBUG("Importing plugins")
        plugin_list = []
        for entry_point in entry_points:
            try:
                plugin = _Plugin.create_from_entry_point(entry_point)
                if plugin:
                    plugin_list.append(plugin)
            except ModuleNotFoundError as e:
                if not self._ignore_missing:
                    notify_plugin_exception(
                        f"Error: Importing plugin '{entry_point}'", e
                    )
                else:
                    log.SRCLI_LOG_DEBUG(f"Ignoring missing plugin '{entry_point}': {e}")
            except BaseException as e:
                notify_plugin_exception(f"Error: Importing plugin '{entry_point}'", e)

        log.SRCLI_LOG_DEBUG("Imported all plugins")
        return plugin_list

    def add_command_line_arguments(self, parser: argparse.ArgumentParser) -> None:
        for plugin in self._plugins:
            plugin.plugin.add_command_line_arguments(parser)

    def on_tools_load(self, state: "CliState") -> bool:
        try:
            ordered_plugins = self.get_ordered_plugins()
        except BaseException as e:
            notify_plugin_exception(
                "Error: Ordering of plugins before tools load failed", e
            )
            ordered_plugins = self._plugins

        result = True
        log.SRCLI_LOG_DEBUG("Loading tools plugins")
        for plugin in ordered_plugins:
            if not isinstance(plugin.plugin, ToolsPlugin):
                continue
            tools_plugin: ToolsPlugin = plugin.plugin

            log.SRCLI_LOG_DEBUG(f"  Loading tools plugin '{plugin.name}'")
            try:
                tools_plugin.on_tools_load(state=state)
            except BaseException as e:
                result = False
                if plugin.name == "tools_mode":
                    log.SRCLI_LOG_DEBUG(
                        "Skipping loading of tools plugins (tools mode schema initialization failed)"
                    )
                    return result
                else:
                    notify_plugin_exception(
                        f"Error: Loading tools plugin '{plugin.name}'", e
                    )

        log.SRCLI_LOG_DEBUG("Loaded all tools plugins")
        return result

    def on_start(self, state: "CliState") -> None:
        log.SRCLI_LOG_DEBUG("Starting plugins")
        for plugin in self._plugins:
            log.SRCLI_LOG_DEBUG(f"  Starting plugin '{plugin.name}'")
            try:
                plugin.plugin.on_start(state=state)
            except BaseException as e:
                notify_plugin_exception(f"Error: Starting plugin '{plugin.name}'", e)

        log.SRCLI_LOG_DEBUG(" Started all plugins")

    def get_ordered_plugins(self) -> List["_Plugin"]:
        """Order the entry points in an order that satisfies their requirements"""
        graph = _PluginGraph()
        graph.add_all(self._plugins)

        return graph.get_ordered_plugins()

    def load(
        self, arguments: argparse.Namespace, cli_loader: Optional[CliLoader] = None
    ) -> CliLoader:
        """
        'arguments' is the argparse.Namespace instance representing
        the command-line arguments passed in when starting the CLI.
        This will be passed to all 'load' calls
        """
        log.SRCLI_LOG_DEBUG("Loading plugins")
        cli = cli_loader or CliLoader()
        arguments = arguments

        try:
            ordered_plugins = self.get_ordered_plugins()
        except BaseException as e:
            notify_plugin_exception("Error: Ordering of plugins before load failed", e)
            ordered_plugins = self._plugins

        for plugin in ordered_plugins:
            log.SRCLI_LOG_DEBUG(f"  Loading plugin '{plugin.name}'")
            try:
                self._load(plugin, cli=cli, arguments=arguments)
            except BaseException as e:
                notify_plugin_exception(f"Error: Loading plugin '{plugin.name}'", e)
        log.SRCLI_LOG_DEBUG("Loaded all plugins")
        return cli

    def _load(
        self, plugin: "_Plugin", cli: CliLoader, arguments: argparse.Namespace
    ) -> None:
        plugin.plugin.load(cli, arguments=arguments)


class _Plugin(object):
    @classmethod
    def create_from_entry_point(
        cls, entry_point: Union[pkg_resources.EntryPoint, PluginEntryPoint]
    ) -> Optional["_Plugin"]:
        log.SRCLI_LOG_DEBUG(f"  Importing plugin '{entry_point}'")
        if not entry_point.dist:
            raise PluginError(f"Invalid entry point '{entry_point}'")

        module_name = entry_point.dist.project_name
        name = entry_point.name
        try:
            plugin = cls._create_plugin(entry_point)
            if not plugin:
                return None
            return _Plugin(module_name=module_name, name=name, plugin=plugin)
        except PluginError as e:
            raise PluginError(f"Failed to load module '{module_name}.{name}': {e}")

    @classmethod
    def _create_plugin(
        cls, entry_point: Union[pkg_resources.EntryPoint, PluginEntryPoint]
    ) -> Optional[CliPlugin]:
        module = entry_point.load()
        if module:
            try:
                result = module()
                assert isinstance(result, CliPlugin)
                return result
            except Exception:
                raise PluginError(
                    f"Entry point '{entry_point}' must be a callable that returns a CliPlugin instance"
                )
        return None

    def __init__(self, module_name: str, name: str, plugin: CliPlugin):
        self.module_name = module_name
        self.name = name
        self.plugin = plugin

    @property
    def key(self) -> Tuple[str, str]:
        return (self.module_name, self.name)

    @property
    def requirements(self) -> Iterator[RequiredPlugin]:
        #  support unspecified module name (recreate new requirements with self.module_name instead of '')
        return (
            RequiredPlugin(module=p.module or self.module_name, plugin=p.name)
            for p in self.plugin.get_required_plugins()
        )

    def check_required_plugin_types(self) -> None:
        if not is_list_of(self.plugin.get_required_plugins(), RequiredPlugin):
            raise PluginError(
                f"Plugin '{str(self)}' get_required_plugins() should return a list of RequiredPlugin instances, "
                f"but I got {full_class_name(self.plugin.get_required_plugins())}"
            )

    def __str__(self) -> str:
        return f"{self.module_name}.{self.name}"

    def __repr__(self) -> str:
        return f"Plugin({str(self)}, {self.requirements})"


class _PluginGraph(object):
    """
    Will order the plugins based on their requirements.
    Based on https://stackoverflow.com/questions/14242295/build-a-dependency-graph-in-python
    """

    def __init__(self) -> None:
        self._plugins: Dict[Tuple[str, str], _Plugin] = {}

    def add_all(self, plugins: List[_Plugin]) -> None:
        for plugin in plugins:
            self.add(plugin)

    def add(self, plugin: _Plugin) -> None:
        self._plugins[plugin.key] = plugin

    def _get_sorted_plugins(self) -> List[_Plugin]:
        ordered_plugins = list()
        ordered_set = set()
        while True:
            appended = False
            for key, plugin in self._plugins.items():
                if key in ordered_set:
                    continue
                unresolved_dependencies = False
                for requirement in plugin.requirements:
                    if requirement.key not in ordered_set:
                        unresolved_dependencies = True
                        break
                if not unresolved_dependencies:
                    ordered_set.add(key)
                    ordered_plugins.append(plugin)
                    appended = True
            if not appended:
                if len(ordered_plugins) != len(self._plugins):
                    cycle = list()
                    for key, plugin in self._plugins.items():
                        if key not in ordered_set:
                            cycle.append(plugin)

                    raise PluginError(
                        f"Cyclic dependency between plugins detected: "
                        f"{[plugin.key[1] for plugin in cycle]}"
                    )
                break
        return ordered_plugins

    def get_ordered_plugins(self) -> List[_Plugin]:
        self._check_required_plugin_types()
        self._check_for_non_existing_requirements()
        return self._get_sorted_plugins()

    def _check_required_plugin_types(self) -> None:
        for plugin in self._plugins.values():
            plugin.check_required_plugin_types()

    def _check_for_non_existing_requirements(self) -> None:
        non_existing_requirement = iterators.first(
            (
                (plugin, requirement)
                for plugin in self._plugins.values()
                for requirement in plugin.requirements
                if requirement.key not in self._plugins
            ),
            default=None,
        )
        if non_existing_requirement:
            plugin, requirement = non_existing_requirement
            raise PluginError(
                f"Plugin '{str(plugin)}' requires non-existing plugin '{str(requirement)}'"
            )

    @property
    def _roots(self) -> Iterator[Tuple[str, str]]:
        return (
            key for key, plugin in self._plugins.items() if not any(plugin.requirements)
        )


def notify_plugin_exception(message: str, exception: BaseException) -> None:
    debug_string = f"{message}: {type(exception).__name__}: {exception}"
    if log.isLogEnabledFor(logging.DEBUG):
        debug_string += "\nTraceback (most recent call last):"
        for line in traceback.format_tb(exception.__traceback__):
            debug_string += f"{line}"
    logging.error(debug_string)
    log.SRCLI_LOG_ERROR(debug_string)
