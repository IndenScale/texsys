# Repository instructions

## Tex Studio

- **Tex Studio** is the local Web workbench implemented in this repository; it is not a separately installed macOS application. The repository name and CLI are `texsys`.
- **Run at most one Tex Studio instance per user.** Reuse the running instance at `http://127.0.0.1:8710` for development, review, and browser tests. Check for an existing Studio process and listener before launching. If an instance is already running or the port is occupied, do not start another instance on a different port, even for a temporary project or a production smoke test.
- **Acquire the exclusive launch lock before starting Studio.** Use an atomic `mkdir "/tmp/texsys-studio-$(id -u).lock"` for this user and keep the lock for the lifetime of the server. Record the owning session ID in the lock directory so other sessions can reach it. If the lock cannot be acquired or the running state cannot be verified, do not launch. Only release a lock you acquired, after its server has stopped.
- **Leave the existing `8710` instance under its owning session's control.** Never kill, stop, or restart another session's instance, even if its owner or lock cannot be identified. For changes that require a rebuild or restart, such as adding a dependency, notify the owning session through cross-session communication and describe what changed. The owning session is responsible for rebuilding and restarting on `8710`. Wait for its confirmation before verifying the change in the browser. If the owner cannot be reached or identified, report the blocker and leave the instance running.
- Start Tex Studio in **Vite HMR mode by default** for the standard HVAC example project. Run from the repository root:

  ```bash
  uv run texsys studio -p examples/hvac-dynamic-station --hmr
  ```

- HMR mode requires Node.js and the repository's installed Vite dependencies. If dependencies are missing, run `bun install --frozen-lockfile` and `uv run tools/sync_node_tools.py` from the repository root.
- Use the existing Google Chrome instance and Tex Studio tab for opening, reviewing, and testing the workbench. Do not start a separate Chrome process or isolated browser profile for automation. If the existing Chrome instance cannot be accessed, report the blocker. The CLI option `--open` uses the system default browser and must not be used.
