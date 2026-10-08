# Argus development commands. Packaging is a separate step.
PYTHON ?= python3
PREFIX ?= $(HOME)/.local
BINDIR ?= $(PREFIX)/bin
CONFIG ?= config.json

.PHONY: install check dry-run self-test identifiers guard-faults wheel
install:
	install -d "$(DESTDIR)$(BINDIR)"
	ln -sf "$(CURDIR)/bin/argus" "$(DESTDIR)$(BINDIR)/argus"
	@printf 'Installed argus in %s; add this folder to PATH.\n' "$(BINDIR)"

# A tick reads one configuration file, so the readiness report and the dry run
# read the file named by CONFIG. --self-test reads no file.
check:
	bin/argus --config "$(CONFIG)" --check

dry-run:
	bin/argus --config "$(CONFIG)" --dry-run

self-test:
	bin/argus --self-test
	$(PYTHON) test_config.py
	$(PYTHON) test_entrypoint.py
	$(PYTHON) test_conventions.py
	$(PYTHON) test_phase_notifications.py
	$(PYTHON) test_identifiers.py
	$(PYTHON) gitsync.py --self-test

# The published folder must carry no identifier: no home path, no numeric
# account or channel id, and no notification default or literal that is not a
# documented placeholder. It runs inside self-test too, so a new identifier
# cannot be added to the package quietly.
identifiers:
	$(PYTHON) test_identifiers.py

guard-faults:
	$(PYTHON) scripts/guard_faults.py

# The package: a wheel whose console script is `argus`, for `pip install .` and
# `uv tool install .`. `install` above stays the development link, so an edit is
# live without reinstalling. The mapping lives in pyproject.toml, and a wrong
# mapping produces a wheel with no files in it without an error, so this target
# checks that the driver is in the wheel it just built.
wheel:
	uv build --wheel
	@$(PYTHON) check_wheel.py "$$(ls -1t dist/*.whl | head -1)"
