import logging
from datetime import datetime, timezone

from srlinux.data import Border, Data, TagValueFormatter
from srlinux.location import build_path
from srlinux.mgmt.cli import CliPlugin
from srlinux.mgmt.server.server_error import ServerError
from srlinux.schema import FixedSchemaRoot
from srlinux.syntax import Syntax

logger = logging.getLogger(__name__)
logger.level = logging.INFO


class Plugin(CliPlugin):
    """
    Adds `show uptime` command.

    Example output:
    ```
    --{ running }--[  ]--
    A:srl# show uptime
    ----------------------------------------------------------------------
    Uptime     : 0 days 6 hours 0 minutes 25 seconds
    Last Booted: 2024-10-24T03:31:50.561Z
    ----------------------------------------------------------------------
    ```
    """

    def load(self, cli, **_kwargs):
        cli.show_mode.add_command(
            syntax=Syntax(
                name="uptime",
                short_help="⌛ Show platform uptime",
                help="⌛ Show platform uptime in days, hours, minutes and seconds.",
                help_epilogue="📖 It is easy to wrap up your own CLI command. Learn more about SR Linux at https://learn.srlinux.dev",
            ),
            schema=self._get_schema(),
            callback=self._print,
        )

    def _get_schema(self):
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
        state,
        output,
        arguments,
        **_kwargs,
    ) -> None:
        self._fetch_state(state)
        data = self._populate_data(arguments)
        self._set_formatters(data)
        output.print_data(data)

    def _fetch_state(self, state):
        last_booted_path = build_path("/platform/chassis/last-booted")

        try:
            self._last_booted_data = state.server_data_store.get_data(
                last_booted_path, recursive=False
            )
        except ServerError:
            self._last_booted_data = None

    def _populate_data(self, arguments):
        data = Data(schema=arguments.schema)

        uptime_container = data.uptime.create()

        uptime_container.last_booted = (
            self._last_booted_data.platform.get().chassis.get().last_booted
        )

        uptime_container.uptime = _calculate_uptime(uptime_container.last_booted)

        return data

    def _set_formatters(self, data):
        data.set_formatter(
            schema="/uptime",
            formatter=Border(TagValueFormatter(), Border.Above | Border.Below),
        )


def _calculate_uptime(last_booted):
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

    # # Format human readable string
    t = f"{days} days {hours} hours {minutes} minutes {seconds} seconds"

    return t
