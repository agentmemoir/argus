# The Example

This directory is a worked example of the configuration Argus reads, and the small project that configuration points at. It is one layout among many: Argus names no project structure of its own, so copy these files, change the paths, and nothing here is required.

```
examples/
  argus-config.jsonc                     # the configuration a tick reads
  machine.id                             # this machine's name, as the config expects
  check-bin/glide                        # a stand-in used only by `make example-check`
  project/pipeline.yaml                  # the pipeline a tick runs
  project/stages/build.md                # the prompt for its one step
  project/plans/phase-000001-example.md  # one work item
```

## Checking It

From the repository root:

```sh
make example-check
```

That runs the readiness report, which prints every configured value with the flag-or-file source it came from. The exit status is the verdict. It needs no Glide installed: `check-bin/glide` stands in for the `glide` command the configuration names, because the report only asks whether the command resolves. A real project points `commands.glide` at its own Glide command instead.

The configuration writes its run state under `examples/runs/`, which git ignores. To render the example pipeline with a real Glide, without running an agent:

```sh
glide run --pipeline examples/project/pipeline.yaml \
  --input ticket=1 --input document=examples/project/plans/phase-000001-example.md \
  --repo examples/project --dry-run
```

## Copying It

Copy `argus-config.jsonc`, `machine.id`, and the `project/` folder next to your own pipeline and work items, then change every path. The configuration rules are in [docs/configuration.md](../docs/configuration.md), and the fields that name a command are yours to fill with your own toolchain.
