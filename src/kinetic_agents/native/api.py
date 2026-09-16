"""Codex Responses backend with the same scoped team tools/live capture as subscription."""

from kinetic_agents.native.client import NativeClient
from kinetic_agents.native.subscription import SubscriptionTeamClient
from kinetic_agents.native.usage import SubscriptionTeamState
from kinetic_agents.observability.transcript import LiveTranscript
from kinetic_agents.core.storage import atomic


class APITeamClient(SubscriptionTeamClient):
    provider = "configured_api"
    host_scoped_workers = True

    def _transport_init(
        self, work, task, state, model, effort, account, token, tools, *, baseline_config
    ):
        self.account, self.gateway_token = account, token
        self.transcript = LiveTranscript(getattr(self, "transcript_root", state.parent))
        try:
            NativeClient.__init__(
                self,
                work,
                task,
                state,
                model,
                effort,
                account,
                token,
                tools,
                baseline_config=baseline_config,
            )
        except BaseException:
            self.transcript.close()
            raise
        self.subscription_stop = None
        self.subscription_save = lambda name, value: atomic(self.state / name, value)
        self.team_observations = getattr(
            self, "shared_observations", None
        ) or SubscriptionTeamState(
            self.state, self.team.service.store.identity, billing="configured_API"
        )

    def prepare_account(self):
        # No account/login RPC, no managed OAuth, no catalog alias substitution.
        return None

    def prepare_worker_account(self, client):
        return None

    def create_researcher(self, state):
        return APIResearcher(self, state)

    def handle_message(self, message):
        if str(message.get("method", "")).startswith("account/"):
            if "id" in message:
                raise PermissionError("API run must not enter subscription authentication")
            # app-server emits account/updated even for an API-only fresh home.
            # It is a notification, not an auth refresh request or model call.
            return
        return super().handle_message(message)


from kinetic_agents.native.workers import ResearcherClient


class APIResearcher(ResearcherClient, APITeamClient):
    def _transport_init(
        self, work, task, state, model, effort, account, token, tools, *, baseline_config
    ):
        return APITeamClient._transport_init(
            self,
            work,
            task,
            state,
            model,
            effort,
            account,
            self.parent.gateway_token,
            tools,
            baseline_config=baseline_config,
        )

    prepare_account = APITeamClient.prepare_account
