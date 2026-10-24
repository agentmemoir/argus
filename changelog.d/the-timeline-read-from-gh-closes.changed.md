The timeline read from gh closes stdin, so gh cannot wait on the driver's terminal, and decodes its output as UTF-8 on every host. ([#179](https://github.com/agentmemoir/argus/issues/179))
