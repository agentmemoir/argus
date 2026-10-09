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
LINE_LIMIT := 350
# Files still above the limit while the migration splits them, one per session.
# Each split removes its file from this list; the list is empty when the
# migration ends. It is empty now: the last file was split.
LINE_EXEMPT :=

.PHONY: install sync lint fmt fmt-check typecheck lines check dry-run example-check \
        self-test identifiers leakscan guard-faults wheel install-check
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

# Type checking at mypy's default level (see pyproject.toml). CI runs it.
typecheck:
	$(RUN) mypy src/argus

# No Python file may exceed the line limit, comments and blank lines included.
# Nothing is exempt: a split removes its file here in the same change.
lines:
	@printf 'checking every Python file against the %s-line limit\n' '$(LINE_LIMIT)'
	@files=$$(find src tests -name '*.py' -type f 2>/dev/null); \
	if [ -z "$$files" ]; then printf 'no Python files found\n'; exit 1; fi; \
	printf '%s\n' "$$files" | xargs wc -l | awk -v lim=$(LINE_LIMIT) -v exempt='$(LINE_EXEMPT)' \
		'BEGIN { n = split(exempt, parts, " "); for (i = 1; i <= n; i++) if (parts[i] != "") skip[parts[i]] = 1 } \
		 $$NF == "total" { next } \
		 $$1 > lim { if ($$2 in skip) { printf "exempt for now: %s\n", $$2; next } \
			over = over $$0 "\n"; count++ } \
		 END { if (count) { printf "OVER LIMIT:\n%s", over; \
			printf "%d file(s) exceed the %s-line limit. Split before adding.\n", count, lim; \
			exit 1 } \
			printf "all files within %s lines\n", lim }'

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

# The wheel must install and run where nothing of this checkout is on PATH and
# no Glide is installed: a clean venv installs the built wheel, every shipped
# module imports, the driver ships beside the package, and the installed
# console script passes the example readiness report.
install-check: wheel
	@$(RUN) python scripts/check_install.py "$$(ls -1t dist/*.whl | head -1)"
