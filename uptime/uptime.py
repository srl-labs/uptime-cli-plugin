import argparse
import logging
from datetime import datetime, timezone
from typing import cast

from srlinux.data import Border, Data, TagValueFormatter
from srlinux.data.data import DataChildrenOfType
from srlinux.location import build_path
from srlinux.mgmt.cli import CliPlugin, CommandNodeWithArguments
from srlinux.mgmt.cli.cli_loader import CliLoader
from srlinux.mgmt.cli.cli_output import CliOutput
from srlinux.mgmt.cli.cli_state import CliState
from srlinux.mgmt.server.server_error import ServerError
from srlinux.schema import FixedSchemaRoot, SchemaNode
from srlinux.schema.fixed_schema import FixedSchemaNode
from srlinux.syntax import Syntax

logger = logging.getLogger(__name__)
logger.level = logging.INFO


class Plugin(CliPlugin):
    """
    Adds `show uptime` command.

    Example output:

        --{ running }--[  ]--
        A:srl# show uptime
        ----------------------------------------------------------------------
        Uptime     : 0 days 6 hours 0 minutes 25 seconds
        Last Booted: 2024-10-24T03:31:50.561Z
        ----------------------------------------------------------------------

    """

    def load(self, cli: CliLoader, arguments: argparse.Namespace) -> None:
        cli.show_mode.add_command(
            syntax=self._syntax(),
            schema=self._schema(),
            callback=self._print,
        )

    def _syntax(self) -> Syntax:
        return Syntax(
            name="uptime",
            short_help="⌛ Show platform uptime",
            help="⌛ Show platform uptime in days, hours, minutes and seconds.",
            help_epilogue="📖 It is easy to wrap up your own CLI command. Learn more about SR Linux at https://learn.srlinux.dev",
        )

    def _schema(self) -> FixedSchemaNode:
        root = FixedSchemaRoot()
        root.add_child(
            "uptime",
            fields=[
                "Uptime",
                "Last Booted",
            ],
        )
        return root

    def _print(
        self,
        state: CliState,
        output: CliOutput,
        arguments: CommandNodeWithArguments,
        **kwargs,
    ) -> None:
        self._fetch_state(state)
        data = self._populate_data(arguments)
        self._set_formatters(data)
        output.print_data(data)

    def _fetch_state(self, state: CliState):
        last_booted_path = build_path("/system/information/last-booted")

        try:
            self._last_booted_data = state.server_data_store.get_data(
                last_booted_path, recursive=False
            )
            logger.debug(self._last_booted_data.to_debug_string())
        except ServerError:
            self._last_booted_data = None

    def _populate_data(self, arguments: CommandNodeWithArguments):
        data = Data(schema=cast(SchemaNode, arguments.schema))
        if not isinstance(data.uptime, DataChildrenOfType):
            raise ValueError("Uptime is not a container")

        uptime_container = data.uptime.create()

        if not self._last_booted_data:
            raise ValueError("Last booted data not available")

        if not isinstance(self._last_booted_data.system, DataChildrenOfType):
            raise ValueError("System is not a container")

        system = self._last_booted_data.system.get()
        if not isinstance(system.information, DataChildrenOfType):
            raise ValueError("Information is not a container")

        sys_information = system.information.get()

        if not isinstance(sys_information.last_booted, str):
            raise ValueError("Last booted is not a leaf")

        uptime_container.last_booted = sys_information.last_booted

        uptime_container.uptime = _calculate_uptime(str(uptime_container.last_booted))

        return data

    def _set_formatters(self, data: Data):
        data.set_formatter(
            schema="/uptime",
            formatter=Border(TagValueFormatter(), Border.Above | Border.Below),
        )


def _calculate_uptime(last_booted: str) -> str:
    """
    Calculate uptime in human-readable form from the last booted time.
    """
    boot_time = datetime.strptime(last_booted, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=timezone.utc
    )
    current_time = datetime.now(timezone.utc)
    uptime_delta = current_time - boot_time

    # Calculate time components
    days = uptime_delta.days
    hours = uptime_delta.seconds // 3600
    minutes = (uptime_delta.seconds % 3600) // 60
    seconds = uptime_delta.seconds % 60

    # Format human readable string
    t = f"{days} days {hours} hours {minutes} minutes {seconds} seconds"

    return t
