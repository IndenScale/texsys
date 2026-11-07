# Repository instructions

## Tex Studio

- **Tex Studio** is the local Web workbench implemented in this repository; it is not a separately installed macOS application. The repository name and CLI are `texsys`.
- To launch the HVAC workbench for the standard example project, run from the repository root:

  ```bash
  uv run texsys studio -p examples/hvac-dynamic-station
  ```

- The default local address is `http://127.0.0.1:8710`. Open it in Google Chrome when a browser window is requested. Do not look for or try to launch a standalone “Tex Studio” app.
- The CLI option `--open` uses the system default browser. Prefer opening the printed address explicitly in Google Chrome so browser choice follows the user's repository/browser constraints.
