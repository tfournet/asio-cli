# Asio Commands TUI

The Asio Commands TUI provides an interactive shell for scheduling scripts against ConnectWise Asio endpoints. It wraps the flows captured in `example.md` and the endpoints defined in `openapi.yml`, so you can browse companies, inspect endpoints, and trigger automations without crafting raw HTTP calls.

## Prerequisites
- Python 3.11+
- API credentials stored in a `.env` file in the repository root:
  ```env
  ASIO_BASE_URL=https://openapi.service.itsupport247.net
  ASIO_CLIENT_ID=your_client_id
  ASIO_CLIENT_SECRET=your_client_secret
  ASIO_SCOPE="platform.companies.read platform.devices.read platform.custom_fields_values.read platform.rpa.resolve platform.sites.write platform.tickets.update platform.sites.read security.security360.write platform.policies.read platform.dataMapping.read platform.tickets.create platform.asset.read platform.deviceGroups.read platform.automation.read platform.automation.create platform.policies.create platform.custom_fields_definitions.write platform.tickets.read platform.agent.delete platform.policies.delete platform.policies.update platform.custom_fields_values.write platform.custom_fields_definitions.read platform.patching.read platform.agent-token.read platform.agent.read"
  ```
  Never commit the real secrets; keep the file local.

## Setup
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Running the TUI
Start the app from the repository root:
```bash
python3 -m asio_app.tui
```
Enable masked login and HTTP request/response tracing:
```bash
python3 -m asio_app.tui --debug
```
You will see an `asio>` prompt. Available commands:
- `companies` — list company IDs and names (prints raw payloads when `debug` mode is enabled).
- `endpoints <company>` — list managed endpoints by company ID, name, or friendly name.
- `scripts` — display available automation scripts.
- `run` — guided wizard to pick a company, endpoint (ID or friendly name), and script, then schedule it while streaming task status and reporting completion time.
- `summary <task_id>` — view task execution summary.
- `results <task_id> <instance_id>` — inspect detailed task outcomes.
- `scopecheck` — discover the largest scope set your credentials can use for token generation.
- `help`, `quit`, `exit` — helper commands.

The TUI prints API responses using Rich tables, so you can copy IDs into follow-up commands. Toggle `debug` within the shell to stream full HTTP requests/responses (with sensitive headers masked). For deeper troubleshooting or payload examples, refer back to `example.md`.
