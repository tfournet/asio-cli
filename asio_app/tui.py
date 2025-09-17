from __future__ import annotations

import argparse
import json
import shlex
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.pretty import Pretty
from rich.table import Table

from .api import AsioApiClient, RateLimitError


class AsioCommandsApp:
    """Interactive text UI for running automation against Asio endpoints."""

    def __init__(self, api: Optional[AsioApiClient] = None, *, login_debug: bool = False) -> None:
        self.console = Console()
        self.session = PromptSession()
        self.completer = WordCompleter(
            [
                "help",
                "companies",
                "endpoints",
                "scripts",
                "run",
                "summary",
                "results",
                "scopecheck",
                "debug",
                "quit",
                "exit",
            ],
            ignore_case=True,
        )
        self.debug_enabled = login_debug
        self.login_debug = login_debug
        self._companies_cache: Dict[str, Dict[str, Any]] = {}
        self._companies_by_name: Dict[str, str] = {}
        self._endpoints_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._endpoint_details: Dict[str, Dict[str, Any]] = {}
        self._scripts_cache: List[Dict[str, Any]] = []
        self._task_definitions: Optional[List[Dict[str, Any]]] = None
        if api is None:
            self.api = AsioApiClient(
                login_debug=login_debug,
                login_logger=self._login_debug,
                http_debug=self.debug_enabled,
                http_logger=self._http_debug,
            )
        else:
            self.api = api
        self._update_http_debug()

    # ------------------------------------------------------------------
    # Entry point
    def run(self) -> None:
        self._print_welcome()
        with patch_stdout(raw=True):
            while True:
                try:
                    command_line = self.session.prompt("asio> ", completer=self.completer)
                except (KeyboardInterrupt, EOFError):
                    self.console.print("\nExiting.")
                    break
                if not command_line.strip():
                    continue
                try:
                    self._dispatch(command_line)
                except EOFError:
                    self.console.print("Exiting.")
                    break
                except Exception as exc:  # noqa: BLE001 - show API errors to the operator
                    self.console.print(f"[red]Error:[/red] {exc}")

    # ------------------------------------------------------------------
    # Command dispatch
    def _dispatch(self, command_line: str) -> None:
        parts = shlex.split(command_line)
        if not parts:
            return
        command = parts[0].lower()
        args = parts[1:]

        if command in {"quit", "exit"}:
            raise EOFError
        if command == "help":
            self._print_help()
        elif command == "companies":
            self._handle_companies()
        elif command == "endpoints":
            self._handle_endpoints(args)
        elif command == "scripts":
            self._handle_scripts()
        elif command == "run":
            self._handle_run_wizard()
        elif command == "summary":
            self._handle_summary(args)
        elif command == "results":
            self._handle_results(args)
        elif command == "scopecheck":
            self._handle_scopecheck()
        elif command == "debug":
            self._handle_debug(args)
        else:
            self.console.print(f"Unknown command: {command}. Type 'help' for a list of commands.")

    # ------------------------------------------------------------------
    # Command handlers
    def _handle_companies(self) -> None:
        companies = self._load_companies(force_refresh=True)
        self._debug_print("companies", companies)
        if not companies:
            self.console.print("[yellow]No companies returned.[/yellow]")
            return
        table = Table(title="Companies")
        table.add_column("#")
        table.add_column("ID")
        table.add_column("Name")
        table.add_column("Friendly Name")
        for index, company in enumerate(companies, start=1):
            table.add_row(
                str(index),
                str(company.get("id", "")),
                str(company.get("name", "")),
                str(company.get("friendlyName", "")),
            )
        self.console.print(table)

    def _handle_endpoints(self, args: List[str]) -> None:
        if not args:
            self.console.print("Usage: endpoints <company>")
            return
        identifier = " ".join(args)
        company = self._resolve_company(identifier)
        if company is None:
            self.console.print(
                f"[red]Unknown company '{identifier}'. Run 'companies' to see available options.[/red]"
            )
            return
        company_id = str(company.get("id"))
        endpoints = self._load_endpoints(company_id, force_refresh=True)
        self._debug_print(f"endpoints:{company_id}", endpoints)
        if not endpoints:
            self.console.print(f"[yellow]No endpoints found for company {company_id}.[/yellow]")
            return
        table = Table(title=f"Endpoints for {company.get('friendlyName') or company.get('name') or company_id}")
        table.add_column("#")
        table.add_column("Endpoint ID")
        table.add_column("Friendly Name")
        table.add_column("Type")
        table.add_column("OS")
        table.add_column("Site ID")
        for index, endpoint in enumerate(endpoints, start=1):
            table.add_row(
                str(index),
                str(endpoint.get("endpointId", "")),
                str(endpoint.get("friendlyName", "")),
                str(endpoint.get("endpointType", "")),
                str(endpoint.get("osType", "")),
                str(endpoint.get("siteId", "")),
            )
        self.console.print(table)

    def _handle_scripts(self) -> None:
        scripts = self._load_scripts(force_refresh=True)
        self._debug_print("scripts", scripts)
        if not scripts:
            self.console.print("[yellow]No automation scripts returned.[/yellow]")
            return
        table = Table(title="Automation Scripts")
        table.add_column("#")
        table.add_column("Template ID")
        table.add_column("Name")
        table.add_column("Category")
        for index, script in enumerate(scripts, start=1):
            table.add_row(
                str(index),
                str(script.get("id", "")),
                str(script.get("name", "")),
                str(script.get("scriptCategory", "")),
            )
        self.console.print(table)

    def _handle_run_wizard(self) -> None:
        companies = self._load_companies()
        self._debug_print("run:companies", companies)
        if not companies:
            self.console.print("[yellow]No companies available.[/yellow]")
            return
        company = self._choose_item(
            "Select company",
            companies,
            lambda c: c.get("friendlyName") or c.get("name") or c.get("id"),
            alias_fn=self._company_aliases,
        )
        self._debug_print("run:selected_company", company)
        if not company:
            return
        endpoints = self._load_endpoints(str(company.get("id", "")))
        self._debug_print(f"run:endpoints:{company.get('id', '')}", endpoints)
        if not endpoints:
            self.console.print("[yellow]No endpoints for the selected company.[/yellow]")
            return
        endpoint = self._choose_item(
            "Select endpoint",
            endpoints,
            lambda e: f"{e.get('friendlyName') or e.get('endpointId')} | {e.get('endpointType')} | {e.get('osType')}",
            alias_fn=self._endpoint_aliases,
        )
        self._debug_print("run:selected_endpoint", endpoint)
        if not endpoint:
            return
        scripts = self._load_scripts()
        self._debug_print("run:scripts", scripts)
        if not scripts:
            self.console.print("[yellow]No scripts to schedule.[/yellow]")
            return
        script = self._choose_item(
            "Select script",
            scripts,
            lambda s: f"{s.get('name')} ({s.get('scriptCategory')})",
            alias_fn=self._script_aliases,
        )
        self._debug_print("run:selected_script", script)
        if not script:
            return
        default_name = script.get("name", "Automation Task")
        task_name = self.session.prompt(f"Task name [{default_name}]: ") or default_name
        user_parameters = self._collect_script_parameters(script)
        response = self.api.schedule_script(
            template_id=str(script.get("id")),
            template_type=str(script.get("templateType", "fusionscript")),
            endpoint_ids=[str(endpoint.get("endpointId"))],
            name=task_name,
            user_parameters=user_parameters,
        )
        self._debug_print("run:schedule_response", response)
        task_id = response.get("taskID") or response.get("taskId")
        self.console.print("[green]Task scheduled successfully.[/green]")
        if task_id:
            self.console.print(f"Task ID: {task_id}")
        self.console.print(response)
        if task_id:
            submitted_dt = self._parse_datetime(response.get("createdOn")) or datetime.now(timezone.utc)
            self._wait_for_task_completion(task_id, submitted_dt=submitted_dt)

    def _handle_summary(self, args: List[str]) -> None:
        if not args:
            self.console.print("Usage: summary <task_id>")
            return
        task_id = args[0]
        summary = self.api.get_task_instances_summary(task_id)
        self._debug_print(f"summary:{task_id}", summary)
        self._print_dict(summary, title=f"Task Summary for {task_id}")

    def _handle_results(self, args: List[str]) -> None:
        if len(args) < 2:
            self.console.print("Usage: results <task_id> <instance_id>")
            return
        task_id, instance_id = args[0], args[1]
        results = self.api.get_task_instance_results(task_id, instance_id)
        self._debug_print(f"results:{task_id}:{instance_id}", results)
        self._print_dict(results, title=f"Task Results for {instance_id}")

    def _handle_scopecheck(self) -> None:
        scope_str = self.api.config.scope.strip().strip('"')
        scopes = [scope for scope in scope_str.split() if scope]
        if not scopes:
            self.console.print("[yellow]No scopes configured in ASIO_SCOPE.[/yellow]")
            return

        self.console.print("Probing individual scopes...")
        probe_results: List[tuple[str, bool, Any]] = []
        allowed_scopes: List[str] = []
        for scope in scopes:
            ok, detail = self.api.test_scopes([scope])
            probe_results.append((scope, ok, detail))
            if ok:
                allowed_scopes.append(scope)

        table = Table(title="Individual Scope Results")
        table.add_column("Scope")
        table.add_column("Status")
        table.add_column("Detail")
        for scope, ok, detail in probe_results:
            status = "[green]allowed[/green]" if ok else "[red]denied[/red]"
            table.add_row(scope, status, self._scope_detail(detail))
        self.console.print(table)

        if not allowed_scopes:
            self.console.print("[red]No scopes could be used to obtain a token. Verify client provisioning.[/red]")
            return

        self.console.print("Building the largest working scope combination...")
        working_scopes: List[str] = []
        combo_results: List[tuple[str, bool, Any]] = []
        for scope in allowed_scopes:
            candidate = working_scopes + [scope]
            ok, detail = self.api.test_scopes(candidate)
            combo_results.append((scope, ok, detail))
            if ok:
                working_scopes.append(scope)

        combo_table = Table(title="Combination Check")
        combo_table.add_column("Scope Added")
        combo_table.add_column("Status")
        combo_table.add_column("Detail")
        for scope, ok, detail in combo_results:
            status = "[green]kept[/green]" if ok else "[red]removed[/red]"
            combo_table.add_row(scope, status, self._scope_detail(detail))
        self.console.print(combo_table)

        if working_scopes:
            working_str = " ".join(working_scopes)
            self.console.print(
                "[green]Suggested scope string:[/green] "
                f"{working_str}\nUpdate your ASIO_SCOPE or .env file accordingly."
            )
        else:
            self.console.print(
                "[yellow]All individually valid scopes conflicted when combined. "
                "Consider contacting the platform team for guidance.[/yellow]"
            )

    # ------------------------------------------------------------------
    # Utilities
    def _handle_debug(self, args: List[str]) -> None:
        if not args:
            self.debug_enabled = not self.debug_enabled
            state = "enabled" if self.debug_enabled else "disabled"
            self.console.print(f"Debugging {state}.")
            return
        option = args[0].lower()
        if option in {"on", "enable"}:
            self.debug_enabled = True
            self.console.print("Debugging enabled.")
        elif option in {"off", "disable"}:
            self.debug_enabled = False
            self.console.print("Debugging disabled.")
        elif option in {"status", "state"}:
            state = "enabled" if self.debug_enabled else "disabled"
            self.console.print(f"Debugging is currently {state}.")
        else:
            self.console.print("Usage: debug [on|off|status]")
            return
        self._update_http_debug()

    def _print_welcome(self) -> None:
        self.console.print("[bold]ConnectWise Asio Automation Shell[/bold]")
        self.console.print("Type 'help' to see available commands.")
        if self.login_debug:
            self.console.print("[dim]Login debugging enabled via --debug.[/dim]")
        if self.debug_enabled:
            self.console.print("[dim]Command output debugging is active.[/dim]")

    def _print_help(self) -> None:
        self.console.print(
            """
Commands:
  help                 Show this message.
  companies            List companies your credentials can access.
  endpoints <company>  List endpoints for a company (ID, name, or friendly name).
  scripts              List available automation scripts.
  run                  Interactive wizard to schedule a script, collect parameters, and watch results.
  summary <task_id>    Show execution summary for a task.
  results <task> <instance>  Show detailed results for a task instance.
  scopecheck           Discover the maximal scope set your credentials support.
  debug [on|off|status]      Toggle command output debugging.
  quit/exit            Leave the shell.
""".strip()
        )

    def _choose_item(
        self,
        prompt: str,
        items: List[Dict[str, Any]],
        label_fn: Callable[[Dict[str, Any]], str],
        *,
        alias_fn: Optional[Callable[[Dict[str, Any]], Iterable[str]]] = None,
    ) -> Optional[Dict[str, Any]]:
        options = []
        alias_map: Dict[str, List[Dict[str, Any]]] = {}
        for index, item in enumerate(items, start=1):
            options.append((index, item, label_fn(item)))
            labels = [label_fn(item)]
            if alias_fn:
                labels.extend(alias_fn(item) or [])
            for label in labels:
                if not label:
                    continue
                alias_key = str(label).strip().lower()
                if alias_key:
                    alias_map.setdefault(alias_key, []).append(item)
        table = Table(title=prompt)
        table.add_column("#")
        table.add_column("Description")
        for index, _, label in options:
            table.add_row(str(index), str(label))
        self.console.print(table)
        selection = self.session.prompt(f"{prompt} (enter number or blank to cancel): ")
        if not selection.strip():
            return None
        try:
            numeric = int(selection)
        except ValueError:
            key = selection.strip().lower()
            matches = alias_map.get(key)
            if not matches:
                self.console.print("[red]Invalid selection.[/red]")
                return None
            if len(matches) > 1:
                self.console.print(
                    "[red]Selection matched multiple entries. Please choose by number to disambiguate.[/red]"
                )
                return None
            return matches[0]
        for index, item, _ in options:
            if index == numeric:
                return item
        self.console.print("[red]Selection out of range.[/red]")
        return None

    def _print_dict(self, data: Any, *, title: str) -> None:
        if isinstance(data, dict):
            table = Table(title=title)
            table.add_column("Key")
            table.add_column("Value")
            for key, value in data.items():
                table.add_row(str(key), self._stringify(value))
            self.console.print(table)
        elif isinstance(data, list):
            table = Table(title=title)
            table.add_column("Index")
            table.add_column("Value")
            for index, value in enumerate(data):
                table.add_row(str(index), self._stringify(value))
            self.console.print(table)
        else:
            self.console.print(f"{title}: {data}")

    def _stringify(self, value: Any) -> str:
        if isinstance(value, (dict, list)):
            return str(value)
        return "" if value is None else str(value)

    def _debug_print(self, label: str, payload: Any) -> None:
        if not self.debug_enabled:
            return
        self.console.print(f"[dim]DEBUG {label}[/dim]")
        self.console.print(Pretty(payload, expand_all=True), style="dim")

    def _login_debug(self, message: str, payload: Optional[Any]) -> None:
        self.console.print(f"[dim]LOGIN DEBUG[/dim] {message}")
        if payload is not None:
            self.console.print(Pretty(payload, expand_all=True), style="dim")

    def _http_debug(self, phase: str, payload: Optional[Any]) -> None:
        self.console.print(f"[dim]HTTP {phase}[/dim]")
        if payload is not None:
            self.console.print(Pretty(payload, expand_all=True), style="dim")

    def _update_http_debug(self) -> None:
        if hasattr(self.api, "set_http_debug"):
            self.api.set_http_debug(self.debug_enabled, self._http_debug)

    def _scope_detail(self, detail: Any) -> str:
        if isinstance(detail, dict):
            if "error_description" in detail:
                return str(detail["error_description"])
            if "error" in detail:
                return str(detail["error"])
            return str({key: value for key, value in detail.items() if key not in {"access_token", "refresh_token"}})
        if isinstance(detail, list):
            return ", ".join(map(str, detail[:3])) + ("..." if len(detail) > 3 else "")
        if detail is None:
            return ""
        return str(detail)

    # ------------------------------------------------------------------
    # Cache helpers
    def _load_companies(self, *, force_refresh: bool = False) -> List[Dict[str, Any]]:
        if self._companies_cache and not force_refresh:
            return list(self._companies_cache.values())
        companies = self.api.list_companies()
        self._companies_cache = {}
        self._companies_by_name = {}
        for company in companies:
            company_id = str(company.get("id", ""))
            self._companies_cache[company_id] = company
            for key in ("name", "friendlyName"):
                value = company.get(key)
                if value:
                    self._companies_by_name[value.lower()] = company_id
        return companies

    def _resolve_company(self, identifier: str) -> Optional[Dict[str, Any]]:
        if not identifier:
            return None
        identifier = identifier.strip()
        if not identifier:
            return None
        companies = self._load_companies()
        lower = identifier.lower()
        if identifier in self._companies_cache:
            return self._companies_cache[identifier]
        if lower in self._companies_by_name:
            company_id = self._companies_by_name[lower]
            return self._companies_cache.get(company_id)
        # try index lookup (1-based) matching the last listing order
        if identifier.isdigit():
            index = int(identifier)
            if 1 <= index <= len(companies):
                company = companies[index - 1]
                company_id = str(company.get("id", ""))
                self._companies_cache[company_id] = company
                return company
        return None

    def _load_endpoints(self, company_id: str, *, force_refresh: bool = False) -> List[Dict[str, Any]]:
        if not force_refresh and company_id in self._endpoints_cache:
            return self._endpoints_cache[company_id]
        endpoints = self.api.list_company_endpoints(company_id)
        for endpoint in endpoints:
            endpoint_id = str(endpoint.get("endpointId", ""))
            if not endpoint_id:
                continue
            if not endpoint.get("friendlyName"):
                detail = self._get_endpoint_detail(endpoint_id)
                if detail:
                    friendly = detail.get("friendlyName") or detail.get("name")
                    if friendly:
                        endpoint["friendlyName"] = friendly
        self._endpoints_cache[company_id] = endpoints
        return endpoints

    def _load_scripts(self, *, force_refresh: bool = False) -> List[Dict[str, Any]]:
        if self._scripts_cache and not force_refresh:
            return self._scripts_cache
        scripts = self.api.list_scripts()
        self._scripts_cache = scripts
        return scripts

    def _get_endpoint_detail(self, endpoint_id: str) -> Optional[Dict[str, Any]]:
        if endpoint_id in self._endpoint_details:
            return self._endpoint_details[endpoint_id]
        while True:
            try:
                detail = self.api.get_endpoint_detail(endpoint_id)
                self._endpoint_details[endpoint_id] = detail
                return detail
            except RateLimitError as err:
                self._handle_rate_limit(err)
            except Exception as exc:  # noqa: BLE001
                self.console.print(
                    f"[red]Failed to fetch details for endpoint {endpoint_id}: {exc}[/red]"
                )
                return None

    def _handle_rate_limit(self, error: RateLimitError) -> None:
        wait_seconds = max(1, int(round(error.retry_after)))
        deadline = time.time() + wait_seconds
        self.console.print(
            f"[yellow]API rate limit reached. Retrying in {wait_seconds} seconds...[/yellow]"
        )
        last_reported = -1
        while True:
            remaining = int(round(deadline - time.time()))
            if remaining <= 0:
                break
            if remaining != last_reported:
                self.console.print(f"[yellow]{remaining} seconds remaining...[/yellow]")
                last_reported = remaining
            time.sleep(1)
        self.console.print("[yellow]Resuming requests.[/yellow]")

    def _company_aliases(self, company: Dict[str, Any]) -> List[str]:
        aliases = [
            str(company.get("id", "")),
            str(company.get("name", "")),
            str(company.get("friendlyName", "")),
        ]
        return [alias for alias in aliases if alias]

    def _endpoint_aliases(self, endpoint: Dict[str, Any]) -> List[str]:
        aliases = [
            str(endpoint.get("endpointId", "")),
            str(endpoint.get("friendlyName", "")),
            str(endpoint.get("name", "")),
        ]
        return [alias for alias in aliases if alias]

    def _script_aliases(self, script: Dict[str, Any]) -> List[str]:
        aliases = [
            str(script.get("id", "")),
            str(script.get("name", "")),
        ]
        return [alias for alias in aliases if alias]

    def _load_task_definitions(self) -> List[Dict[str, Any]]:
        if self._task_definitions is None:
            while True:
                try:
                    self._task_definitions = self.api.list_task_definitions()
                    break
                except RateLimitError as err:
                    self._handle_rate_limit(err)
                except Exception as exc:  # noqa: BLE001
                    self.console.print(f"[red]Failed to load task definitions: {exc}[/red]")
                    self._task_definitions = []
                    break
        return self._task_definitions

    def _find_task_definition_for_script(self, script: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        script_id = str(script.get("id", ""))
        script_name = str(script.get("name", ""))
        definitions = self._load_task_definitions()
        for definition in definitions:
            template_id = str(definition.get("templateID") or definition.get("templateId") or "")
            if template_id and template_id == script_id:
                return definition
        for definition in definitions:
            definition_id = str(definition.get("id", ""))
            if definition_id and definition_id == script_id:
                return definition
        for definition in definitions:
            if script_name and definition.get("name") == script_name:
                return definition
        return None

    def _collect_script_parameters(self, script: Dict[str, Any]) -> Optional[Any]:
        if not script.get("hasParameters"):
            return None
        definition = self._find_task_definition_for_script(script)
        schema = None
        sample = None
        if definition:
            schema = self._parse_json(definition.get("JSONSchema") or definition.get("jsonSchema"))
            sample = self._parse_json(definition.get("userParameters"))
        if schema and isinstance(schema, dict) and schema.get("properties"):
            return self._prompt_parameters_from_schema(schema, sample)
        return self._prompt_parameters_manual(sample)

    # ------------------------------------------------------------------
    # Task polling helpers
    def _wait_for_task_completion(
        self,
        task_id: str,
        *,
        poll_interval: float = 1.0,
        timeout: float = 600.0,
        submitted_dt: Optional[datetime] = None,
    ) -> None:
        self.console.print("[cyan]Waiting for task completion...[/cyan]")
        start = time.time()
        last_statuses: Dict[str, str] = {}
        terminal_statuses = {
            "success",
            "succeeded",
            "failed",
            "completed",
            "cancelled",
            "canceled",
            "error",
            "partial_success",
            "timeout",
        }
        pending_statuses = {"running", "waiting", "queued", "pending", "in_progress", "scheduled"}

        try:
            while True:
                if timeout and (time.time() - start) >= timeout:
                    self.console.print(
                        "[yellow]Timed out waiting for task completion. Use 'summary'/'results' commands manually.[/yellow]"
                    )
                    return

                try:
                    summary = self.api.get_task_instances_summary(task_id)
                except RateLimitError as err:
                    self._handle_rate_limit(err)
                    continue
                except Exception as exc:  # noqa: BLE001
                    self.console.print(f"[red]Failed to fetch task summary: {exc}[/red]")
                    return

                instances = self._extract_summary_instances(summary)
                if instances:
                    statuses = {}
                    for inst in instances:
                        instance_id = str(inst.get("taskInstanceId") or inst.get("Id") or "")
                        status = str(inst.get("OverallStatus") or inst.get("Status") or "").strip()
                        if instance_id:
                            statuses[instance_id] = status
                    self._report_status_changes(statuses, last_statuses)
                    last_statuses = statuses

                    if statuses and all(
                        (not status) or (status.lower() in terminal_statuses)
                        for status in statuses.values()
                    ):
                        self.console.print("[green]Task reached a terminal status.[/green]\\nFetching results...")
                        self._fetch_and_print_results(task_id, instances, submitted_dt)
                        return

                    if statuses and any(status.lower() in pending_statuses for status in statuses.values() if status):
                        time.sleep(poll_interval)
                        continue

                # If no instances or statuses, fall back to counts
                if self._summary_is_complete(summary):
                    self.console.print("[green]Task reached a terminal status.[/green]\\nFetching results...")
                    self._fetch_and_print_results(task_id, instances, submitted_dt)
                    return

                time.sleep(poll_interval)
        except KeyboardInterrupt:
            self.console.print(
                "[yellow]Stopped waiting for task completion. You can check later with 'summary' or 'results'.[/yellow]"
            )

    def _extract_summary_instances(self, summary: Any) -> List[Dict[str, Any]]:
        if isinstance(summary, dict):
            for key in ("Results", "results", "TaskInstances", "taskInstances"):
                value = summary.get(key)
                if isinstance(value, list):
                    return value
        return []

    def _summary_is_complete(self, summary: Any) -> bool:
        if not isinstance(summary, dict):
            return False
        counts = {
            key.lower(): summary.get(key)
            for key in summary.keys()
            if key.lower().endswith("count")
        }
        running = self._coerce_int(counts.get("runningcount") or counts.get("running_count"))
        waiting = self._coerce_int(counts.get("waitingcount") or counts.get("waiting_count"))
        scheduled = self._coerce_int(counts.get("scheduledcount") or counts.get("scheduled_count"))
        return running == 0 and waiting == 0 and scheduled == 0

    def _report_status_changes(self, statuses: Dict[str, str], last_statuses: Dict[str, str]) -> None:
        for instance_id, status in statuses.items():
            prev = last_statuses.get(instance_id)
            if status != prev:
                pretty_status = status or "(unknown)"
                self.console.print(f"[cyan]Instance {instance_id}: {pretty_status}[/cyan]")

    def _fetch_and_print_results(
        self,
        task_id: str,
        instances: List[Dict[str, Any]],
        submitted_dt: Optional[datetime],
    ) -> None:
        overall_elapsed: List[float] = []
        for inst in instances:
            instance_id = str(inst.get("taskInstanceId") or inst.get("Id") or "")
            if not instance_id:
                continue
            results: Optional[Dict[str, Any]] = None
            while True:
                try:
                    results = self.api.get_task_instance_results(task_id, instance_id)
                    break
                except RateLimitError as err:
                    self._handle_rate_limit(err)
                    continue
                except Exception as exc:  # noqa: BLE001
                    self.console.print(
                        f"[red]Failed to fetch results for instance {instance_id}: {exc}[/red]"
                    )
                    results = None
                    break
            if results is None:
                continue
            self._debug_print(f"task_results:{instance_id}", results)
            output = self._extract_instance_output(results)
            if output is not None:
                self.console.print(f"[cyan]Instance {instance_id} output:[/cyan]")
                self.console.print(output)
            title = f"Task Results for {instance_id}"
            self._print_dict(results, title=title)

            start_dt = self._determine_start_time(inst, results) or submitted_dt
            completion_dt = self._determine_completion_time(inst, results) or datetime.now(timezone.utc)
            if start_dt and completion_dt:
                duration_from_start = max(0.0, (completion_dt - start_dt).total_seconds())
                if submitted_dt:
                    elapsed_from_submit = max(0.0, (completion_dt - submitted_dt).total_seconds())
                    overall_elapsed.append(elapsed_from_submit)
                    self.console.print(
                        f"[green]Instance {instance_id} completed in {self._format_duration(elapsed_from_submit)} from submission.[/green]"
                    )
                else:
                    self.console.print(
                        f"[green]Instance {instance_id} completed in {self._format_duration(duration_from_start)}.[/green]"
                    )
        if submitted_dt and overall_elapsed:
            total = max(overall_elapsed)
            self.console.print(
                f"[green]Task completed in {self._format_duration(total)} from submission.[/green]"
            )

    def _coerce_int(self, value: Any) -> int:
        if value is None:
            return 0
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            value = value.strip()
            if value.isdigit():
                return int(value)
        return 0

    def _parse_json(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                return json.loads(text)
            except ValueError:
                return None
        return None

    def _determine_start_time(self, summary_instance: Dict[str, Any], results: Dict[str, Any]) -> Optional[datetime]:
        candidate_keys = [
            "ExecutedOn",
            "executedOn",
            "executionTime",
            "StartTime",
            "startTime",
        ]
        for key in candidate_keys:
            dt = self._parse_datetime(summary_instance.get(key))
            if dt:
                return dt
        entries = self._extract_results_entries(results)
        instance_id = str(summary_instance.get("taskInstanceId") or summary_instance.get("Id") or "")
        for entry in entries:
            entry_id = str(entry.get("taskInstanceId") or entry.get("instanceId") or "")
            if instance_id and entry_id and entry_id != instance_id:
                continue
            for key in ("executionTime", "executedOn", "startTime", "startedAt"):
                dt = self._parse_datetime(entry.get(key))
                if dt:
                    return dt
        return None

    def _determine_completion_time(
        self,
        summary_instance: Dict[str, Any],
        results: Dict[str, Any],
    ) -> Optional[datetime]:
        candidate_keys = [
            "CompletedOn",
            "completedOn",
            "completionTime",
            "CompletionTime",
            "ModifiedOn",
            "modifiedOn",
        ]
        for key in candidate_keys:
            dt = self._parse_datetime(summary_instance.get(key))
            if dt:
                return dt
        entries = self._extract_results_entries(results)
        instance_id = str(summary_instance.get("taskInstanceId") or summary_instance.get("Id") or "")
        for entry in entries:
            entry_id = str(entry.get("taskInstanceId") or entry.get("instanceId") or "")
            if instance_id and entry_id and entry_id != instance_id:
                continue
            for key in (
                "completedOn",
                "completionTime",
                "createdOn",
                "completedAt",
                "finishedAt",
            ):
                dt = self._parse_datetime(entry.get(key))
                if dt:
                    return dt
        return None

    def _extract_results_entries(self, results: Any) -> List[Dict[str, Any]]:
        if isinstance(results, dict):
            for key in ("Result", "Results", "items", "data"):
                value = results.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        if isinstance(results, list):
            return [item for item in results if isinstance(item, dict)]
        return []

    def _parse_datetime(self, value: Any) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            try:
                dt = datetime.fromisoformat(text)
            except ValueError:
                return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return dt
        return None

    def _format_duration(self, seconds: float) -> str:
        seconds = max(0, int(round(seconds)))
        minutes, sec = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        parts: List[str] = []
        if hours:
            parts.append(f"{hours}h")
        if minutes or hours:
            parts.append(f"{minutes}m")
        parts.append(f"{sec}s")
        return " ".join(parts)

    def _prompt_parameters_from_schema(self, schema: Dict[str, Any], sample: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            return self._prompt_parameters_manual(sample)
        required = set(schema.get("required", [])) if isinstance(schema.get("required"), list) else set()
        sample_values = sample if isinstance(sample, dict) else {}
        params: Dict[str, Any] = {}
        for name, prop in properties.items():
            if not isinstance(prop, dict):
                continue
            description = prop.get("description") or ""
            param_type = prop.get("type") or "string"
            enum = prop.get("enum") if isinstance(prop.get("enum"), list) else None
            default = sample_values.get(name, prop.get("default"))
            default_display = None
            if isinstance(default, (dict, list)):
                default_display = json.dumps(default)
            elif default is not None:
                default_display = str(default)
            while True:
                prompt_parts = [name]
                if description:
                    prompt_parts.append(f"- {description}")
                type_segment = param_type
                if enum:
                    type_segment += f", options: {', '.join(map(str, enum))}"
                prompt_parts.append(f"[{type_segment}]")
                if default_display is not None:
                    prompt_parts.append(f"(default: {default_display})")
                prompt_text = " ".join(prompt_parts) + ": "
                raw = self.session.prompt(prompt_text)
                if not raw.strip():
                    if default is not None:
                        value = default
                    elif name in required:
                        self.console.print(f"[red]{name} is required.[/red]")
                        continue
                    else:
                        value = None
                else:
                    try:
                        value = self._convert_parameter_value(raw, prop)
                    except ValueError as exc:
                        self.console.print(f"[red]{exc}[/red]")
                        continue
                if value is not None:
                    params[name] = value
                elif name in required:
                    self.console.print(f"[red]{name} is required.[/red]")
                    continue
                break
        extra_json = self.session.prompt("Additional parameters as JSON (leave blank to continue): ")
        if extra_json.strip():
            extra = self._parse_json(extra_json)
            if isinstance(extra, dict):
                params.update(extra)
            else:
                self.console.print("[yellow]Ignored additional parameters (invalid JSON).[/yellow]")
        return params or None

    def _prompt_parameters_manual(self, sample: Any) -> Optional[Any]:
        params: Dict[str, Any] = {}
        sample_dict = sample if isinstance(sample, dict) else None
        sample_list = sample if isinstance(sample, list) else None

        if sample_dict:
            self.console.print("[cyan]Provide values for the following parameters (press Enter to keep defaults).[/cyan]")
            for key, default in sample_dict.items():
                default_display = (
                    json.dumps(default)
                    if isinstance(default, (dict, list))
                    else str(default) if default is not None else None
                )
                prompt = f"{key}"
                if default_display is not None:
                    prompt += f" (default: {default_display})"
                prompt += ": "
                raw = self.session.prompt(prompt)
                if not raw.strip():
                    if default is not None:
                        params[key] = default
                    continue
                parsed = self._parse_json(raw)
                params[key] = parsed if parsed is not None else raw
        elif sample_list:
            pretty = json.dumps(sample_list, indent=2)
            self.console.print("[cyan]Sample parameter list:\n" + pretty + "[/cyan]")
            if self._prompt_yes_no("Use the sample list as-is?", default=False):
                return sample_list
            self.console.print("[cyan]Enter each list item (blank line to finish).[/cyan]")
            items: List[Any] = []
            while True:
                raw = self.session.prompt(f"Item {len(items) + 1}: ")
                if not raw.strip():
                    break
                parsed = self._parse_json(raw)
                items.append(parsed if parsed is not None else raw)
            return items or None
        else:
            self.console.print(
                "[cyan]This script requires parameters. Enter key/value pairs (blank name to finish).[/cyan]"
            )

        while True:
            if not self._prompt_yes_no("Add another parameter?", default=False):
                break
            name = self.session.prompt("Parameter name: ").strip()
            if not name:
                break
            value_raw = self.session.prompt(f"{name} value: ")
            parsed = self._parse_json(value_raw)
            params[name] = parsed if parsed is not None else value_raw

        if not params and sample_dict:
            return sample_dict
        return params or None

    def _convert_parameter_value(self, raw: str, schema: Dict[str, Any]) -> Any:
        param_type = schema.get("type") or "string"
        enum = schema.get("enum") if isinstance(schema.get("enum"), list) else None
        if enum:
            for option in enum:
                if raw == option:
                    return option
                if isinstance(option, (int, float)) and raw == str(option):
                    return option
            for option in enum:
                if isinstance(option, str) and raw.strip().lower() == option.lower():
                    return option
            raise ValueError(f"Value must be one of: {', '.join(map(str, enum))}")
        if param_type == "boolean":
            lowered = raw.strip().lower()
            if lowered in {"true", "t", "yes", "y", "1"}:
                return True
            if lowered in {"false", "f", "no", "n", "0"}:
                return False
            raise ValueError("Enter true/false")
        if param_type == "integer":
            try:
                return int(raw)
            except ValueError as exc:
                raise ValueError("Enter an integer") from exc
        if param_type == "number":
            try:
                return float(raw)
            except ValueError as exc:
                raise ValueError("Enter a numeric value") from exc
        if param_type == "array":
            parsed = self._parse_json(raw)
            if isinstance(parsed, list):
                return parsed
            raise ValueError("Enter a JSON array")
        if param_type == "object":
            parsed = self._parse_json(raw)
            if isinstance(parsed, dict):
                return parsed
            raise ValueError("Enter a JSON object")
        return raw

    def _prompt_yes_no(self, message: str, *, default: bool = False) -> bool:
        suffix = " [Y/n]" if default else " [y/N]"
        while True:
            raw = self.session.prompt(message + suffix + " ").strip().lower()
            if not raw:
                return default
            if raw in {"y", "yes"}:
                return True
            if raw in {"n", "no"}:
                return False
            self.console.print("[red]Please enter y or n.[/red]")

    def _extract_instance_output(self, results: Any) -> Optional[str]:
        entries = self._extract_results_entries(results)
        for entry in entries:
            for key in ("output", "resultDetails", "result", "stdout", "details", "logs"):
                payload = entry.get(key)
                if payload:
                    return self._stringify(payload)
        if isinstance(results, dict):
            for key in ("output", "resultDetails", "result", "stdout"):
                payload = results.get(key)
                if payload:
                    return self._stringify(payload)
        return None


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="ConnectWise Asio automation shell")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Display masked login flow and HTTP request/response debugging output.",
    )
    args = parser.parse_args(argv)

    app = AsioCommandsApp(login_debug=args.debug)
    app.run()


if __name__ == "__main__":
    main()
