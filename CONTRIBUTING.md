# Contribution guidelines

Contributing to this project should be as easy and transparent as possible, whether it's:

- Reporting a bug
- Discussing the current state of the code
- Submitting a fix
- Proposing new features

## Github is used for everything

Github is used to host code, to track issues and feature requests, as well as accept pull requests.

Pull requests are the best way to propose changes to the codebase.

1. Fork the repo and create your branch from `main`.
2. If you've changed something, update the documentation.
3. Make sure your code lints (using `scripts/lint`).
4. Make sure the tests pass (using `scripts/test`) and add tests for your change; the fake Tesira server in `tests/fake_tesira.py` lets you test device behaviour without hardware.
5. Issue that pull request!

## Any contributions you make will be under the MIT Software License

In short, when you submit code changes, your submissions are understood to be under the same [MIT License](http://choosealicense.com/licenses/mit/) that covers the project. Feel free to contact the maintainers if that's a concern.

## Report bugs using Github's [issues](../../issues)

GitHub issues are used to track public bugs.
Report a bug by [opening a new issue](../../issues/new/choose); it's that easy!

## Write bug reports with detail, background, and sample code

**Great Bug Reports** tend to have:

- A quick summary and/or background
- Steps to reproduce
  - Be specific!
  - Give sample code if you can.
- What you expected would happen
- What actually happens
- Notes (possibly including why you think this might be happening, or stuff you tried that didn't work)

People *love* thorough bug reports. I'm not even kidding.

## Use a Consistent Coding Style

Use [black](https://github.com/ambv/black) to make sure the code follows the style.

## Test your code modification

The project ships a development container (`.devcontainer.json`) that installs
everything from `requirements-dev.txt`; open the repository in Visual Studio Code
and choose "Reopen in Container", or create a virtual environment locally and run
`scripts/setup`.

Run `scripts/test` to execute the pytest suite. The tests do not need a Tesira
or an MQTT broker: `tests/fake_tesira.py` emulates the device's telnet
behaviour, and the MQTT layer is replaced by a recording stub. If you have access
to a real Tesira, `python src/__init__.py --config config.yaml --loglevel debug`
shows every line exchanged with the device.

## License

By contributing, you agree that your contributions will be licensed under its MIT License.
