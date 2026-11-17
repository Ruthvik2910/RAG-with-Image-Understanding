import json
import logging
import math
from threading import RLock
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, List


EVALUATION_FILE = Path("./evaluation_history.jsonl")
_WRITE_LOCK = RLock()
logger = logging.getLogger(__name__)


def optional_bool(value):
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return None


def nonnegative_number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (ValueError, TypeError):
        return None


@dataclass
class EvaluationResult:
    question: str
    answer: str
    route: str = "unknown"

    retrieved_documents: int = 0
    relevant_documents: int = 0

    grounded: bool | None = None
    answers_question: bool | None = None

    retry_count: int | None = 0
    latency_seconds: float | None = 0.0

    sources: List[Dict[str, Any]] = field(default_factory=list)

    timestamp: str = field(
        default_factory=lambda: datetime.now(
            timezone.utc
        ).isoformat()
    )

    error: str = ""
    request_id: str = ""
    status: str = "completed"
    execution_success: bool | None = None

    def __post_init__(self):
        self.grounded = optional_bool(self.grounded)
        self.answers_question = optional_bool(self.answers_question)
        self.execution_success = optional_bool(self.execution_success)
        self.latency_seconds = nonnegative_number(self.latency_seconds)
        self.retry_count = nonnegative_number(self.retry_count)
        if self.retry_count is not None:
            self.retry_count = int(self.retry_count)
        self.retrieved_documents = int(nonnegative_number(self.retrieved_documents) or 0)
        self.relevant_documents = min(self.retrieved_documents, int(nonnegative_number(self.relevant_documents) or 0))
        if self.status in {"completed", "error", "failed"} and (self.status in {"error", "failed"} or self.execution_success is False or self.error):
            self.status = "failed"

    @property
    def retrieval_relevance_rate(self) -> float:
        if self.retrieved_documents == 0:
            return 0.0

        return (
            self.relevant_documents
            / self.retrieved_documents
        )

    @property
    def success(self) -> bool:
        return self.status == "completed" and self.execution_success is not False and not bool(self.error)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)

        data["retrieval_relevance_rate"] = (
            self.retrieval_relevance_rate
        )

        data["success"] = self.success

        return data


class EvaluationTracker:
    def __init__(
        self,
        storage_path: Path | str = EVALUATION_FILE,
    ) -> None:
        self.storage_path = Path(storage_path)
        self.results: List[EvaluationResult] = []

        self.load()

    def add(
        self,
        result: EvaluationResult,
    ) -> None:
        # An approval pause is operational telemetry, not a final evaluation.
        if result.status not in {"completed", "failed"}:
            return
        # Reload under the write lock so separate tracker instances cannot append
        # duplicate evaluations for the same resumed/completed request.
        with _WRITE_LOCK:
            if result.status not in {"completed", "failed"}:
                return
            self.load()
            if result.request_id and any(item.request_id == result.request_id for item in self.results):
                return
            self.save_result(result)
            self.results.append(result)

    def save_result(
        self,
        result: EvaluationResult,
    ) -> None:
        self.storage_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with self.storage_path.open(
            "a",
            encoding="utf-8",
        ) as file:
            json.dump(
                result.to_dict(),
                file,
                ensure_ascii=False,
            )
            file.write("\n")

    def load(self) -> None:
        with _WRITE_LOCK:
            self.results = []
            if not self.storage_path.exists():
                return
            allowed = {item.name for item in fields(EvaluationResult)}
            seen = set()
            with self.storage_path.open("r", encoding="utf-8") as file:
                for line in file:
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        # Older JSONL stores a computed `success`; retain explicit
                        # failure rather than discarding it and inferring success.
                        saved_success = data.pop("success", None)
                        if data.get("execution_success") is None:
                            data["execution_success"] = saved_success
                        result = EvaluationResult(**{key: value for key, value in data.items() if key in allowed})
                        if result.status not in {"completed", "failed"}:
                            continue
                        if result.request_id and result.request_id in seen:
                            continue
                        self.results.append(result)
                        if result.request_id:
                            seen.add(result.request_id)
                    except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
                        logger.warning("Skipped malformed evaluation history record in %s", self.storage_path)

    def clear(self) -> None:
        with _WRITE_LOCK:
            self.results = []
            self.storage_path.unlink(missing_ok=True)

    def summary(self) -> Dict[str, Any]:
        if not self.results:
            return {
                "total_questions": 0,
                "successful_questions": 0,
                "failed_questions": 0,
                "average_latency_seconds": 0.0,
                "grounded_rate": 0.0,
                "answer_quality_rate": 0.0,
                "average_retries": 0.0,
                "average_retrieval_relevance": 0.0,
                "grounded_evaluated_questions": 0,
                "quality_evaluated_questions": 0,
                "retrieval_evaluated_questions": 0,
            }

        total = len(self.results)

        successful = sum(
            result.success
            for result in self.results
        )

        # Unknown assessments are not failed assessments. Execution success is
        # independent of answer quality and grounding.
        grounded = [result.grounded for result in self.results if result.grounded is not None]
        quality = [result.answers_question for result in self.results if result.answers_question is not None]
        latencies = [result.latency_seconds for result in self.results if result.latency_seconds is not None]
        retries = [result.retry_count for result in self.results if result.retry_count is not None]
        retrieval_rates = [result.retrieval_relevance_rate for result in self.results if result.retrieved_documents > 0]

        def mean(values):
            return sum(values) / len(values) if values else 0.0

        return {
            "total_questions": total,
            "successful_questions": successful,
            "failed_questions": total - successful,
            "average_latency_seconds": mean(latencies),
            "grounded_rate": mean(grounded),
            "answer_quality_rate": mean(quality),
            "average_retries": mean(retries),
            "average_retrieval_relevance": mean(retrieval_rates),
            "grounded_evaluated_questions": len(grounded),
            "quality_evaluated_questions": len(quality),
            "retrieval_evaluated_questions": len(retrieval_rates),
        }



def measure_latency(
    func,
    *args,
    **kwargs,
):
    start = perf_counter()

    result = func(
        *args,
        **kwargs,
    )

    latency = perf_counter() - start

    return result, latency