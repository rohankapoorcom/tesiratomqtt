"""Custom action handlers for argparse."""

import argparse
import os


class EnvDefault(argparse.Action):
    """
    Utilizes an environment variable if present, otherwise takes the command line argument.

    Based off of https://stackoverflow.com/a/10551190.
    """

    def __init__(self, envvar, required=True, default=None, **kwargs):
        if envvar and envvar in os.environ:
            default = os.environ[envvar]
        if required and default:
            required = False
        super().__init__(default=default, required=required, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values)
