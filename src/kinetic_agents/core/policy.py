"""Versioned baseline instructions, independent of execution and scoring."""

from kinetic_agents.core.contracts import PolicyContext
from kinetic_agents.core.contracts import fingerprint

RESUME = (
    "\nThe executor has recovered your SAME task and recorded session. Read your own "
    "notes and scientific job statuses before further actions. Do not resubmit completed "
    "science or create a new request ID merely because a connection was interrupted. "
    "Original cumulative budgets and deadlines remain. Continue your task or record an honest stop_search."
)
CONTINUE = (
    "No stop_search has been recorded. Continue your research, wait for your pending "
    "science, or submit your current frontier and record an honest stop_search. "
    "This continuation supplies no scientific hypothesis or preferred candidate."
)


class NativeBaselinePolicy:
    """Exact legacy prompt behavior. No prescribed tool/candidate/experiment plan.

    This is an instruction-policy interface, not an implemented Bayesian search
    policy or RSI skill manager. Experimental policies must override identity().
    """

    policy_id = "native-autonomous-baseline"
    version = "1"

    def start(self, context: PolicyContext) -> str:
        if context.resumed:
            return context.continuation_notice + RESUME
        return context.task_text + "\nTask files: " + context.task_directory

    def continuation(self) -> str:
        return CONTINUE

    def identity(self) -> dict:
        return {
            "id": self.policy_id,
            "version": self.version,
            "instructions_sha256": fingerprint(
                {"resume": RESUME, "continue": CONTINUE, "start_suffix": "\nTask files: "}
            ),
        }
