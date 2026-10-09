# Argus development commands. Packaging is a separate step.
#
# uv is the toolchain for the tooling (see pyproject.toml): `make self-test` and
# the wheel run through `uv run`, which installs the project and the dev group.
# The driver itself still runs through the shell and the Python named in its
# configuration file.
PYTHON ?= python3
UV ?= uv
RUN := $(UV) run
PREFIX ?= $(HOME)/.local
BINDIR ?= $(PREFIX)/bin
CONFIG ?= config.json

.PHONY: install sync check dry-run self-test identifiers guard-faults wheel
install:
	install -d "$(DESTDIR)$(BINDIR)"
	ln -sf "$(CURDIR)/bin/argus" "$(DESTDIR)$(BINDIR)/argus"
	@printf 'Installed argus in %s; add this folder to PATH.\n' "$(BINDIR)"

sync:
	$(UV) sync

# A tick reads one configuration file, so the readiness report and the dry run
# read the file named by CONFIG. --self-test reads no file.
check:
	bin/argus --config "$(CONFIG)" --check

dry-run:
	bin/argus --config "$(CONFIG)" --dry-run

self-test:
	$(RUN) bin/argus --self-test
	$(RUN) python tests/test_config.py
	$(RUN) python tests/test_entrypoint.py
	$(RUN) python tests/test_conventions.py
	$(RUN) python tests/test_phase_notifications.py
	$(RUN) python tests/test_identifiers.py
	$(RUN) python -m argus.phase_group --self-test
	$(RUN) python -m argus.gitsync --self-test

# The published folder must carry no identifier: no home path, no numeric
# account or channel id, and no notification default or literal that is not a
# documented placeholder. It runs inside self-test too, so a new identifier
# cannot be added to the package quietly.
identifiers:
	$(RUN) python tests/test_identifiers.py

guard-faults:
	$(RUN) python tests/guard_faults.py

# The package: a wheel whose console script is `argus`, for `pip install .` and
# `uv tool install .`. `install` above stays the development link, so an edit is
# live without reinstalling. A wrong mapping produces a wheel with no files in
# it without an error, so this target checks the wheel it just built against
# the package folder.
wheel:
	$(UV) build --wheel
	@$(RUN) python scripts/check_wheel.py "$$(ls -1t dist/*.whl | head -1)"
