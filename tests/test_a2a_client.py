from uuid import uuid4

from repoagent.a2a_client import A2AValidationClient


def test_extracts_structured_validation_artifact() -> None:
    task = {
        "id": str(uuid4()),
        "status": {"state": "TASK_STATE_COMPLETED"},
        "artifacts": [
            {
                "name": "validation-report",
                "parts": [
                    {
                        "data": {
                            "passed": True,
                            "attempt": 1,
                            "command_results": [],
                            "summary": "Passed",
                            "recommended_actions": [],
                        }
                    }
                ],
            }
        ],
    }
    report = A2AValidationClient._extract_report(task)
    assert report.passed
    assert report.summary == "Passed"
