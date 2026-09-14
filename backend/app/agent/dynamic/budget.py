"""Deterministic budget accounting for the dynamic Agent loop."""

from datetime import UTC, datetime

from app.agent.dynamic.models import BudgetDecision, BudgetUsage, RunBudget
from app.infrastructure.llm import TokenUsage


class BudgetLedger:
    """执行模型调用和 Tool 调用之前做 preflight，之后记录真实 usage。

    Budget 是 Agent Harness 的硬边界。模型可以看到剩余额度，但无权修改它；因此本类
    不接受任何 model-generated BudgetDecision，所有决定都由确定性代码计算。
    """

    def __init__(self, budget: RunBudget, usage: BudgetUsage) -> None:
        self.budget = budget
        self.usage = usage

    def preflight(self, operation: str) -> BudgetDecision:
        elapsed = (datetime.now(UTC) - self.usage.started_at).total_seconds()
        if elapsed >= self.budget.deadline_seconds:
            return BudgetDecision(
                allowed=False,
                terminal_reason="deadline_exceeded",
                detail="run deadline was reached",
            )
        if self.usage.steps >= self.budget.max_steps:
            return self._exhausted("max_steps")
        if (
            operation == "model"
            and self.usage.model_calls >= self.budget.max_model_calls
        ):
            return self._exhausted("max_model_calls")
        if operation == "tool" and self.usage.tool_calls >= self.budget.max_tool_calls:
            return self._exhausted("max_tool_calls")
        if self.usage.total_tokens >= self.budget.max_total_tokens:
            return self._exhausted("max_total_tokens")
        if self.usage.input_tokens >= self.budget.max_input_tokens:
            return self._exhausted("max_input_tokens")
        if self.usage.output_tokens >= self.budget.max_output_tokens:
            return self._exhausted("max_output_tokens")
        return BudgetDecision(allowed=True)

    def record_step(self) -> BudgetUsage:
        self.usage = self.usage.model_copy(update={"steps": self.usage.steps + 1})
        return self.usage

    def record_model(self, tokens: TokenUsage) -> BudgetDecision:
        self.usage = self.usage.model_copy(
            update={
                "model_calls": self.usage.model_calls + 1,
                "input_tokens": self.usage.input_tokens + tokens.input_tokens,
                "output_tokens": self.usage.output_tokens + tokens.output_tokens,
                "total_tokens": self.usage.total_tokens + tokens.total_tokens,
            }
        )
        if self.usage.input_tokens > self.budget.max_input_tokens:
            return self._exhausted("max_input_tokens")
        if self.usage.output_tokens > self.budget.max_output_tokens:
            return self._exhausted("max_output_tokens")
        if self.usage.total_tokens > self.budget.max_total_tokens:
            return self._exhausted("max_total_tokens")
        return BudgetDecision(allowed=True)

    def record_tool(self) -> BudgetUsage:
        self.usage = self.usage.model_copy(
            update={"tool_calls": self.usage.tool_calls + 1}
        )
        return self.usage

    def reserve_delegation(self, tokens: int) -> BudgetDecision:
        """Charge a child allocation to the parent before another Agent turn.

        Reservation intentionally charges the allocation rather than a best-effort
        estimate. This prevents concurrent or retried children from oversubscribing
        the Lead's Token budget even when provider usage arrives later.
        """

        self.usage = self.usage.model_copy(
            update={
                "delegated_tokens": self.usage.delegated_tokens + tokens,
                "total_tokens": self.usage.total_tokens + tokens,
            }
        )
        if self.usage.total_tokens > self.budget.max_total_tokens:
            return self._exhausted("max_total_tokens")
        return BudgetDecision(allowed=True)

    def record_replan(self) -> BudgetDecision:
        self.usage = self.usage.model_copy(
            update={"replans": self.usage.replans + 1}
        )
        if self.usage.replans > self.budget.max_replans:
            return BudgetDecision(
                allowed=False,
                terminal_reason="verification_failed",
                detail="maximum replan count was exceeded",
            )
        return BudgetDecision(allowed=True)

    @staticmethod
    def _exhausted(limit: str) -> BudgetDecision:
        return BudgetDecision(
            allowed=False,
            terminal_reason="budget_exhausted",
            detail=f"run exceeded {limit}",
        )
