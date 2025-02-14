from srlinux.mgmt.cli import CliPlugin


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
        pass
