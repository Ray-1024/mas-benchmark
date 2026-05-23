from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from workspace import WorkspaceManager

if TYPE_CHECKING:
    from pipeline.runner import OpenHandsRunnerConfig


class LLMJudgeResult(BaseModel):
    stage: str
    metric: str = "llm_judge"
    score: float | None = None
    status: str
    details: dict[str, Any] = Field(default_factory=dict)


class LLMJudge:
    def __init__(
        self,
        config: OpenHandsRunnerConfig,
        workspace: WorkspaceManager,
        system_prompt: str | None = None,
    ) -> None:
        self.config = config
        self.workspace = workspace
        self.system_prompt = system_prompt or (
            "You are an impartial software artifact evaluator. "
            "Evaluate only the provided artifact content against the prompt. "
            "Return strict JSON with keys: score, status, reasoning, findings. "
            "score must be a number from 0.0 to 1.0 or null. "
            "status must be one of: passed, failed, skipped, error."
        )

    def validate_artifacts(
        self,
        artifact_paths: list[str | Path],
        prompt: str,
        *,
        stage: str,
        metric: str = "llm_judge",
        max_chars_per_file: int = 10_000,
    ) -> LLMJudgeResult:
        contents: list[str] = []
        details: dict[str, Any] = {
            "artifact_paths": [],
            "prompt": prompt,
            "truncated": {},
        }

        for artifact_path in artifact_paths:
            path = self._resolve_artifact_path(artifact_path)
            relative_path = path.relative_to(self.workspace.root).as_posix()
            details["artifact_paths"].append(relative_path)
            if not path.exists():
                return LLMJudgeResult(
                    stage=stage,
                    metric=metric,
                    score=0.0,
                    status="failed",
                    details={**details, "missing_artifact": relative_path},
                )
            if not path.is_file():
                return LLMJudgeResult(
                    stage=stage,
                    metric=metric,
                    score=None,
                    status="error",
                    details={**details, "error": f"Artifact path is not a file: {relative_path}"},
                )

            content = path.read_text(encoding="utf-8", errors="replace")
            details["truncated"][relative_path] = len(content) > max_chars_per_file
            contents.append(
                f"Artifact: {relative_path}\n"
                "```text\n"
                f"{content[:max_chars_per_file]}\n"
                "```"
            )

        return self.validate_text(
            content="\n\n".join(contents),
            prompt=prompt,
            stage=stage,
            metric=metric,
            details=details,
        )

    def validate_artifact(
        self,
        artifact_path: str | Path,
        prompt: str,
        *,
        stage: str,
        metric: str = "llm_judge",
        max_chars: int = 20_000,
    ) -> LLMJudgeResult:
        path = self._resolve_artifact_path(artifact_path)
        details: dict[str, Any] = {
            "artifact_path": path.relative_to(self.workspace.root).as_posix(),
            "prompt": prompt,
        }

        if not path.exists():
            return LLMJudgeResult(
                stage=stage,
                metric=metric,
                score=0.0,
                status="failed",
                details={**details, "missing_artifact": details["artifact_path"]},
            )
        if not path.is_file():
            return LLMJudgeResult(
                stage=stage,
                metric=metric,
                score=None,
                status="error",
                details={**details, "error": "Artifact path is not a file"},
            )

        content = path.read_text(encoding="utf-8", errors="replace")
        details["truncated"] = len(content) > max_chars
        return self.validate_text(
            content=content[:max_chars],
            prompt=prompt,
            stage=stage,
            metric=metric,
            details=details,
        )

    def validate_text(
        self,
        content: str,
        prompt: str,
        *,
        stage: str,
        metric: str = "llm_judge",
        details: dict[str, Any] | None = None,
    ) -> LLMJudgeResult:
        result_details = details or {"prompt": prompt}
        try:
            response_text = self._call_llm(
                [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": self._user_prompt(prompt, content)},
                ]
            )
            parsed = self._parse_json_response(response_text)
            score = parsed.get("score")
            if score is not None:
                score = max(0.0, min(1.0, float(score)))
            status = str(parsed.get("status") or self._status_from_score(score))
            return LLMJudgeResult(
                stage=stage,
                metric=metric,
                score=score,
                status=status,
                details={
                    **result_details,
                    "reasoning": parsed.get("reasoning"),
                    "findings": parsed.get("findings", []),
                    "raw_response": response_text,
                },
            )
        except Exception as exc:
            return LLMJudgeResult(
                stage=stage,
                metric=metric,
                score=None,
                status="error",
                details={**result_details, "error": str(exc)},
            )

    def _call_llm(self, messages: list[dict[str, str]]) -> str:
        llm = self._create_llm()
        for method_name in ("completion", "chat_completion"):
            method = getattr(llm, method_name, None)
            if method is None:
                continue
            response = method(messages=messages)
            return self._response_text(response)

        invoke = getattr(llm, "invoke", None)
        if invoke is not None:
            return self._response_text(invoke(messages))

        if callable(llm):
            return self._response_text(llm(messages))

        raise RuntimeError("Unsupported OpenHands LLM interface")

    def _create_llm(self) -> Any:
        from openhands.sdk import LLM

        return LLM(
            model=self.config.model,
            base_url=self.config.base_url,
            api_key=self.config.api_key,
            temperature=self.config.temperature,
            max_output_tokens=self.config.max_output_tokens,
        )

    def _response_text(self, response: Any) -> str:
        if isinstance(response, str):
            return response
        if isinstance(response, dict):
            return self._response_text_from_dict(response)

        content = getattr(response, "content", None)
        if content is not None:
            return str(content)

        choices = getattr(response, "choices", None)
        if choices:
            return self._response_text_from_choice(choices[0])

        return str(response)

    def _response_text_from_dict(self, response: dict[str, Any]) -> str:
        if "content" in response:
            return str(response["content"])
        choices = response.get("choices")
        if choices:
            return self._response_text_from_choice(choices[0])
        return json.dumps(response, ensure_ascii=False)

    def _response_text_from_choice(self, choice: Any) -> str:
        if isinstance(choice, dict):
            message = choice.get("message")
            if isinstance(message, dict) and "content" in message:
                return str(message["content"])
            if "text" in choice:
                return str(choice["text"])

        message = getattr(choice, "message", None)
        if message is not None:
            content = getattr(message, "content", None)
            if content is not None:
                return str(content)

        text = getattr(choice, "text", None)
        if text is not None:
            return str(text)
        return str(choice)

    def _parse_json_response(self, response: str) -> dict[str, Any]:
        try:
            parsed = json.loads(response)
        except json.JSONDecodeError:
            start = response.find("{")
            end = response.rfind("}")
            if start == -1 or end == -1 or end <= start:
                raise ValueError(f"LLM judge response is not JSON: {response}") from None
            parsed = json.loads(response[start : end + 1])

        if not isinstance(parsed, dict):
            raise ValueError("LLM judge response must be a JSON object")
        return parsed

    def _resolve_artifact_path(self, artifact_path: str | Path) -> Path:
        path = Path(artifact_path)
        if path.is_absolute():
            candidate = path.resolve()
        else:
            candidate = (self.workspace.root / path).resolve()

        try:
            candidate.relative_to(self.workspace.root)
        except ValueError as exc:
            raise ValueError(f"Artifact path escapes workspace root: {artifact_path}") from exc
        return candidate

    def _user_prompt(self, prompt: str, content: str) -> str:
        return (
            f"Evaluation prompt:\n{prompt}\n\n"
            "Artifact content:\n"
            "```text\n"
            f"{content}\n"
            "```\n\n"
            "Return JSON only."
        )

    def _status_from_score(self, score: float | None) -> str:
        if score is None:
            return "skipped"
        return "passed" if score >= 0.8 else "failed"
