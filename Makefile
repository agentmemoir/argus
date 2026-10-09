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

.PHONY: install sync lint fmt fmt-check check dry-run example-check self-test \
        identifiers leakscan guard-faults wheel
install:
	install -d "$(DESTDIR)$(BINDIR)"
	ln -sf "$(CURDIR)/bin/argus" "$(DESTDIR)$(BINDIR)/argus"
	@printf 'Installed argus in %s; add this folder to PATH.\n' "$(BINDIR)"

sync:
	$(UV) sync

# Lint and the one formatter (ruff, 88 columns). CI runs lint and fmt-check.
lint:
	$(RUN) ruff check .

fmt:
	$(RUN) ruff format .

fmt-check:
	$(RUN) ruff format --check .

# A tick reads one configuration file, so the readiness report and the dry run
# read the file named by CONFIG. --self-test reads no file.
check:
	bin/argus --config "$(CONFIG)" --check

dry-run:
	bin/argus --config "$(CONFIG)" --dry-run

# The example must load and pass the readiness report on any machine. A real
# project names its own Glide command; `examples/check-bin` puts a stand-in
# first on PATH, because --check only asks whether the command resolves and a
# machine with no Glide installed must still be able to check the example. The
# runs folder is created here: it is git-ignored, and the driver needs the
# configured run-state folder to exist before it starts.
example-check:
	@mkdir -p examples/runs
	@PATH="$(CURDIR)/examples/check-bin:$$PATH" $(RUN) bin/argus --config examples/argus-config.jsonc --check

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

# The published tree must carry no host path, no credential assignment, and no
# run state. The tests own the patterns; this target is how a push check and CI
# run them.
leakscan:
	$(RUN) pytest -q tests/test_no_host_leak.py tests/test_leak_patterns.py

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
