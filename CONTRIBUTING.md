# Contributing to VServer SSH Stats

Thank you for your interest in contributing to **VServer SSH Stats**! We welcome improvements to the integration, documentation, and translations. The following guidelines help us review and merge contributions quickly and consistently.

## Getting Started
- **Discuss first**: Before starting larger work, please open a GitHub issue or discussion to align on the problem and proposed solution.
- **Set up Home Assistant**: Use a local Home Assistant Core/Container or a development VM to test changes. Follow the setup steps in the [README](README.md).
- **Python environment**: This integration targets Python 3.11+, matching Home Assistant's requirements. Create a virtual environment and install development dependencies with `pip install -r requirements_test.txt` if available, or mirror the versions used by Home Assistant.

## Reporting Bugs
- Use the *Bug report* issue template.
- Include Home Assistant version, integration version, installation method (HACS/manual), logs, and reproduction steps.
- Redact secrets such as IPs, usernames, and SSH keys before sharing logs.

## Proposing Enhancements
- Use the *Feature request* template and describe the use case, proposed behaviour, and any alternatives considered.
- Keep the Home Assistant design guidelines in mind: avoid long-running synchronous I/O in the event loop and prefer config flows for user configuration.

## Development Guidelines
- Keep code style consistent with the existing project. Home Assistant integrations use `ruff` and `black` defaults; please run them before opening a PR if you add Python code.
- Update translations in the `custom_components/vserver_ssh_stats/translations/` directory when introducing new strings.
- Update documentation (README files or docs) and screenshots if user-facing behaviour changes.
- Add or update tests under `tests/` if applicable. If tests are not available, describe manual verification steps in your PR.

## Commit & Pull Request Process
1. Fork the repository and create a feature branch from `main`.
2. Make your changes and ensure they build/lint locally.
3. Commit using clear messages (e.g. `feat: add SSH port option to config flow`).
4. Push your branch and open a Pull Request against `main`.
5. Fill in the PR template, describing the change, motivation, and testing performed.
6. Respond to review feedback promptly. We squash-merge most contributions.

## Release Coordination
Maintainers use `scripts/bump_version.py` to prepare releases and update `CHANGELOG.md`. Contributors do not need to run release scripts unless explicitly requested.

Thank you for helping improve VServer SSH Stats!
