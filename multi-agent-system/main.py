from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from tools import prepare_workspace_copy, read_json, read_text_input, resolve_project_path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
BENCHMARK_DIR = PROJECT_ROOT / "benchmark"
RUN_DIR = PROJECT_ROOT / "run"
AGENT_ROLE_VALUES = [
    "requirements_analyst",
    "architect",
    "implementation_planner",
    "developer",
    "test_engineer",
    "defect_repairer",
    "release_manager",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run MAS benchmark tasks.")
    parser.add_argument("--benchmark", default="all", help="Benchmark id to run, or 'all'. Default: all.")
    parser.add_argument("--all-stages", action="store_true", help="Run the full multi-agent stage sequence. This is the default when --stage is omitted.")
    parser.add_argument("--stage", action="append", choices=AGENT_ROLE_VALUES, help="Run only this stage. Can be repeated.")
    parser.add_argument("--run-id", help="Run id. Defaults to a generated id.")
    parser.add_argument("--workspace", type=Path, help="Use an explicit workspace path instead of creating one under run/.")
    parser.add_argument("--task", help="Task text. If omitted, benchmark docs are used as context.")
    parser.add_argument("--task-file", type=Path, help="Read task text from a file.")
    parser.add_argument("--agents-config", type=Path, default=CONFIG_DIR / "agent.json", help="Path to agent config JSON.")
    parser.add_argument("--benchmarks-config", type=Path, default=CONFIG_DIR / "benchmark.json", help="Path to benchmark config JSON.")
    parser.add_argument("--model", default=os.getenv("OPENHANDS_MODEL") or os.getenv("OPENAI_MODEL") or os.getenv("LLM_MODEL"), help="LLM model for OpenHands runner.")
    parser.add_argument("--api-key", default=os.getenv("OPENHANDS_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY"), help="LLM API key.")
    parser.add_argument("--base-url", default=os.getenv("OPENHANDS_BASE_URL") or os.getenv("OPENAI_BASE_URL") or os.getenv("LLM_BASE_URL"), help="Optional LLM base URL.")
    parser.add_argument("--temperature", type=float, default=float(os.getenv("OPENHANDS_TEMPERATURE", "0.0")), help="Runner temperature.")
    parser.add_argument("--max-output-tokens", type=int, default=int(os.getenv("OPENHANDS_MAX_OUTPUT_TOKENS", "4096")), help="Max output tokens.")
    parser.add_argument("--max-iterations", type=int, default=int(os.getenv("OPENHANDS_MAX_ITERATIONS", "80")), help="Max OpenHands iterations per run.")
    parser.add_argument("--brief-agent", choices=AGENT_ROLE_VALUES, help="Run a single brief directly with this agent, without the multi-agent pipeline.")
    parser.add_argument("--brief", help="Brief text for --brief-agent.")
    parser.add_argument("--brief-file", type=Path, help="Read brief text from a file for --brief-agent.")
    return parser.parse_args()


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    args = parse_args()

    runner_config = build_runner_config(args)
    agents = load_agents(args.agents_config)

    if args.brief_agent:
        run_single_agent_brief(args, agents, runner_config)
        return
    if args.all_stages and args.stage:
        raise SystemExit("Use either --all-stages or --stage, not both.")

    benchmarks = select_benchmarks(load_benchmarks(args.benchmarks_config), args.benchmark)
    if not benchmarks:
        raise SystemExit(f"No benchmarks selected for '{args.benchmark}'.")
    if args.workspace and len(benchmarks) > 1:
        raise SystemExit("--workspace can only be used with one selected benchmark.")

    for benchmark in benchmarks:
        run_benchmark(args, benchmark, agents, runner_config)


def run_benchmark(
    args: argparse.Namespace,
    benchmark: dict[str, Any],
    agents: dict[Any, Any],
    runner_config: Any,
) -> None:
    from pipeline import Benchmark, LangGraphPipeline, OpenhandsRunner
    from tools import WorkspaceManager, new_uuid

    benchmark_model = Benchmark.model_validate(benchmark)
    benchmark_id = benchmark_model.id
    run_id = args.run_id or f"{benchmark_id}-{new_uuid()}"
    workspace_path = args.workspace or RUN_DIR / benchmark_id / run_id
    prepare_workspace(benchmark_id, workspace_path, explicit_workspace=args.workspace is not None)

    workspace = WorkspaceManager(workspace_path)
    runner = OpenhandsRunner(runner_config, workspace.root)
    stages = select_stages(None if args.all_stages else args.stage)
    task = read_text_input(args.task, args.task_file) or benchmark_task(benchmark, workspace.root)

    pipeline = LangGraphPipeline(
        runner=runner,
        workspace=workspace,
        agents=agents,
        stages=stages,
        validation_references=resolve_reference_files(benchmark),
        stage_validations={
            validation.stage: validation.enabled_methods()
            for validation in benchmark_model.stage_validations
        },
    )
    state = pipeline.run(task=task, run_id=run_id)

    print(f"benchmark={benchmark_id}")
    print(f"run_id={state.run_id}")
    print(f"workspace={workspace.root}")
    print(f"completed_stages={','.join(state.completed_stages)}")
    if state.validation_summary and state.validation_summary.weighted_score is not None:
        print(f"weighted_score={state.validation_summary.weighted_score:.4f}")
    print(f"artifact_manifest={workspace.resolve('.mas/artifact_manifest.json')}")
    print(f"final_report={workspace.resolve('.mas/final_report.md')}")


def run_single_agent_brief(
    args: argparse.Namespace,
    agents: dict[Any, Any],
    runner_config: Any,
) -> None:
    from pipeline import AgentRole, OpenhandsRunner
    from tools import WorkspaceManager, new_uuid

    role = AgentRole(args.brief_agent)
    brief = read_text_input(args.brief, args.brief_file) or read_text_input(args.task, args.task_file)
    if not brief:
        raise SystemExit("--brief-agent requires --brief, --brief-file, --task, or --task-file.")

    run_id = args.run_id or f"brief-{role.value}-{new_uuid()}"
    workspace_path = args.workspace or RUN_DIR / "brief" / run_id
    workspace = WorkspaceManager(workspace_path)
    workspace.ensure_layout()

    runner = OpenhandsRunner(runner_config, workspace.root)
    runner.run_task(agents[role], brief)

    print(f"agent={role.value}")
    print(f"run_id={run_id}")
    print(f"workspace={workspace.root}")


def build_runner_config(args: argparse.Namespace) -> Any:
    if not args.model:
        raise SystemExit("Missing model. Pass --model or set OPENHANDS_MODEL / OPENAI_MODEL / LLM_MODEL.")
    if not args.api_key:
        raise SystemExit("Missing API key. Pass --api-key or set OPENHANDS_API_KEY / OPENAI_API_KEY / LLM_API_KEY.")

    from pydantic import SecretStr
    from pipeline import OpenHandsRunnerConfig

    return OpenHandsRunnerConfig(
        model=args.model,
        api_key=SecretStr(args.api_key),
        base_url=args.base_url,
        temperature=args.temperature,
        max_output_tokens=args.max_output_tokens,
        max_iteration_per_run=args.max_iterations,
    )


def load_agents(path: Path) -> dict[Any, Any]:
    from pipeline import AgentDefinition, AgentRole, default_agent_definitions

    agents = default_agent_definitions()
    data = read_json(path, default={"agents": []})

    for item in data.get("agents", []):
        role_value = item.get("role")
        if not role_value:
            continue
        role = AgentRole(role_value)
        agents[role] = AgentDefinition(
            name=item.get("name") or role.value,
            system_prompt=item.get("system_prompt") or agents[role].system_prompt,
        )
    return agents


def load_benchmarks(path: Path) -> list[dict[str, Any]]:
    from pipeline import Benchmark

    configured = read_json(path, default={"benchmarks": []}).get("benchmarks", [])
    by_id = {item["id"]: item for item in configured if item.get("id")}

    for benchmark_path in sorted(path for path in BENCHMARK_DIR.iterdir() if path.is_dir()):
        by_id.setdefault(
            benchmark_path.name,
            {
                "id": benchmark_path.name,
                "benchmark_type": "unknown",
                "reference_response": {},
            },
        )
    return [Benchmark.model_validate(item).model_dump(mode="python") for item in by_id.values()]


def select_benchmarks(benchmarks: list[dict[str, Any]], selected: str) -> list[dict[str, Any]]:
    if selected == "all":
        return benchmarks
    return [benchmark for benchmark in benchmarks if benchmark.get("id") == selected]


def select_stages(stage_values: list[str] | None) -> list[Any]:
    from pipeline import AgentRole, DEFAULT_STAGES

    if not stage_values:
        return DEFAULT_STAGES
    selected_roles = [AgentRole(value) for value in stage_values]
    stages_by_role = {stage.role: stage for stage in DEFAULT_STAGES}
    return [stages_by_role[role] for role in selected_roles]


def prepare_workspace(benchmark_id: str, workspace_path: Path, explicit_workspace: bool) -> None:
    try:
        prepare_workspace_copy(BENCHMARK_DIR / benchmark_id, workspace_path, explicit_workspace=explicit_workspace)
    except (FileExistsError, FileNotFoundError) as exc:
        raise SystemExit(str(exc)) from exc


def benchmark_task(benchmark: dict[str, Any], workspace_root: Path) -> str:
    docs = []
    for relative_path in ("docs/requirements.md", "docs/architecture.md", "docs/implementation_plan.md"):
        path = workspace_root / relative_path
        if path.exists():
            docs.append(f"## {relative_path}\n{path.read_text(encoding='utf-8')}")

    header = (
        f"Run benchmark '{benchmark['id']}'"
        f" of type '{benchmark.get('benchmark_type', 'unknown')}'."
    )
    if not docs:
        return header
    return f"{header}\n\nUse these benchmark artifacts as the initial task context:\n\n" + "\n\n".join(docs)


def resolve_reference_files(benchmark: dict[str, Any]) -> dict[str, Any]:
    references = benchmark.get("reference_response", {})
    resolved: dict[str, Any] = {
        key: resolve_project_path(value, PROJECT_ROOT)
        for key, value in references.items()
        if value
    }
    swebench_config = benchmark.get("swebench") or benchmark.get("swe_bench")
    if swebench_config:
        resolved["swebench"] = swebench_config
    return resolved


if __name__ == "__main__":
    main()
