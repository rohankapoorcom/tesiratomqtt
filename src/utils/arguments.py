"""Custom action handlers for argparse."""

import argparse
import os


class EnvDefault(argparse.Action):
    """
    Utilizes an environment variable if present, otherwise takes the argument from CLI.

    Based off of https://stackoverflow.com/a/10551190.
    """

    def __init__(  # noqa: D107
        self,
        envvar: str,
        required: bool = True,  # noqa: FBT001, FBT002
        default: str | None = None,
        **kwargs: object,
    ) -> None:
        if envvar and envvar in os.environ:
            default = os.environ[envvar]
        if required and default:
            required = False
        super().__init__(default=default, required=required, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None) -> None:  # noqa: ANN001, ARG002, D102
        setattr(namespace, self.dest, values)
