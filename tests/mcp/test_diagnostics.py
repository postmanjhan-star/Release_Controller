import httpx

from app.integrations.drone.client import DroneClient
from app.integrations.drone.schemas import DroneBuild
from app.services.release_diagnostics_service import ReleaseDiagnosticsService


def test_failed_step_logs_are_bounded(operations):
    build = DroneBuild.from_drone(
        {
            "number": 42,
            "status": "failure",
            "started": 100,
            "finished": 110,
            "stages": [
                {
                    "number": 1,
                    "name": "quality",
                    "status": "failure",
                    "steps": [{"number": 2, "name": "pytest", "status": "failure", "exit_code": 1}],
                }
            ],
        }
    )
    operations.builds.get_build.side_effect = None
    operations.builds.get_build.return_value = build
    operations.builds.get_step_logs.return_value = "x" * 10001
    result = ReleaseDiagnosticsService(operations).get_failed_pipeline_summary("backend", 42)
    assert len(result["logs"]) == 10000
    assert result["log_truncated"]
    assert result["failed_steps"][0]["exit_code"] == 1
    assert build.started_at is not None


def test_fixed_drone_log_endpoint(monkeypatch):
    requests = []

    def request(method, url, **kwargs):
        requests.append((method, url))
        return httpx.Response(200, json=[{"out": "first\n"}, {"out": "second\n"}])

    monkeypatch.setattr(httpx, "request", request)
    result = DroneClient("http://drone.test", "test").get_step_logs("team", "api", 42, 1, 2)
    assert result == "first\nsecond\n"
    assert requests == [("GET", "http://drone.test/api/repos/team/api/builds/42/logs/1/2")]
