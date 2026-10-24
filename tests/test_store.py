from pathlib import Path

from repoagent.events import EventBus
from repoagent.models import RepoRunState, RunRequest, RunStatus
from repoagent.store import StateStore


def test_state_and_events_round_trip(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    state = RepoRunState(
        request=RunRequest(
            requirement="Add a useful feature",
            repository=str(tmp_path),
            create_pull_request=False,
        )
    )
    store.save_run(state)
    bus = EventBus(store)
    bus.emit(state.run_id, "test", "unit", "stored")

    loaded = store.get_run(state.run_id)
    assert loaded is not None
    assert loaded.status == RunStatus.QUEUED
    events = store.get_events(state.run_id)
    assert len(events) == 1
    assert events[0].message == "stored"
