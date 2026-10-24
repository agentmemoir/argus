The tmux idle watcher ends when the process that started it is gone, so one run outside tmux, or after its tmux server died, no longer loops as an orphan.
