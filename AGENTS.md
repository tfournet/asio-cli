# Agent Contributor Guide

Welcome! This repository houses the ConnectWise Asio automation TUI along with the OpenAPI specification and operator playbooks that back it. This guide explains how to set up your environment, iterate on features, and open high-quality pull requests.

## Know the Components
- `asio_app/` — interactive shell that wraps the Asio REST API.
- `openapi.yml` — authoritative API description; consumers and tooling rely on it.
- `example.md` — live runbook that documents real request/response flows.
- `requirements.txt` — Python dependencies for the TUI and supporting utilities.

## Quick Start
1. Install Python 3.11+ and `pip`.
2. Create a virtual environment in the repo root:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. Provide credentials locally in a `.env` file (never commit secrets):
   ```env
   ASIO_BASE_URL=https://openapi.service.itsupport247.net
   ASIO_CLIENT_ID=your_client_id
   ASIO_CLIENT_SECRET=your_client_secret
   ASIO_SCOPE="platform.companies.read platform.devices.read platform.custom_fields_values.read platform.rpa.resolve platform.sites.write platform.tickets.update platform.sites.read security.security360.write platform.policies.read platform.dataMapping.read platform.tickets.create platform.asset.read platform.deviceGroups.read platform.automation.read platform.automation.create platform.policies.create platform.custom_fields_definitions.write platform.tickets.read platform.agent.delete platform.policies.delete platform.policies.update platform.custom_fields_values.write platform.custom_fields_definitions.read platform.patching.read platform.agent-token.read platform.agent.read"
   ```
4. Launch the shell to validate your setup:
   ```bash
   python3 -m asio_app.tui
   ```

## Daily Development Workflow
- Create a feature branch before making changes.
- Keep the OpenAPI spec and TUI behavior in sync; new endpoints or schemas should appear in both `openapi.yml` and the UI flows that surface them.
- Document new manual steps or updated payloads in `example.md` so operators can reproduce the automation without the TUI.
- Run the quality gates before pushing:
  ```bash
  npx @redocly/openapi-cli lint openapi.yml
  npx openapi-format openapi.yml --outfile openapi.yml
  python3 -m asio_app.tui  # smoke check key commands
  ```
  (Install Node CLI helpers globally or via `npx` on demand.)

## Coding Standards
- Match the existing Python style: type hints, f-strings, and early returns when they clarify flow.
- Prefer composed helper methods over inline API logic in command handlers; keep request construction in `asio_app/api.py`.
- Follow current YAML formatting: two-space indentation, double-quoted strings, and `UpperCamelCase` operation/component identifiers.
- When introducing new commands or tables in the TUI, use Rich tables for structured output and update the built-in help text.

## Testing & Validation
- Treat linting the OpenAPI spec as a mandatory gate—no warnings in committed work.
- Manually exercise the TUI flows that map to your changes (`companies`, `endpoints`, `scripts`, `run`, etc.). Capture any inconsistencies you observe as follow-up issues if you cannot address them immediately.
- Use the call sequences in `example.md` (token → companies → endpoints → scripts → schedule → results) to double-check new API interactions, especially when introducing new scopes or payload requirements.

## Documentation Expectations
- Update `example.md` whenever request/response shapes, required scopes, or recommended troubleshooting steps change.
- Add diagrams, payload samples, or operational notes to a `docs/` subtree if they do not belong in the spec itself.
- Keep `README.md` aligned with new commands or setup steps so new agents can get started without surprises.

## Git Hygiene & Pull Requests
- Use Conventional Commit prefixes (`feat:`, `fix:`, `docs:`, etc.) with imperative summaries.
- Reference issue IDs or support tickets when relevant, and call out breaking changes in a dedicated block.
- PR descriptions should explain motivation, summarize functional changes, list impacted commands or endpoints, and attach screenshots of rendered docs when they materially change.

## Security & Secrets
- Do not commit `.env` files, API tokens, or customer-identifying data.
- Verify new OAuth scopes with the platform security team before publishing them in examples or docs.
- Scrub sensitive values from console output shared in issues or PR discussions.

## Support & Troubleshooting
- Review Rich console output for API errors; most exceptions bubble up from `requests` with useful context.
- Enable HTTP debugging only in a local branch and remove it before submitting a PR.
- When production behavior diverges from the spec, capture the raw request/response pair and open an issue so the spec and TUI can be corrected together.

Thanks for contributing and keeping the Asio automation experience sharp for every operator.
