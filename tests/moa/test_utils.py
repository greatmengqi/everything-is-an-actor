from everything_is_an_actor.agents.task import TaskResult, TaskStatus


class TestFormatReferences:
    def test_success_only(self):
        from everything_is_an_actor.moa.utils import format_references

        results = [
            TaskResult(task_id="1", output="answer A"),
            TaskResult(task_id="2", output="answer B"),
        ]
        assert format_references(results) == "1. answer A\n2. answer B"

    def test_skips_failures_by_default(self):
        from everything_is_an_actor.moa.utils import format_references

        results = [
            TaskResult(task_id="1", output="answer A"),
            TaskResult(task_id="2", error="boom", status=TaskStatus.FAILED),
            TaskResult(task_id="3", output="answer C"),
        ]
        assert format_references(results) == "1. answer A\n3. answer C"

    def test_include_failures(self):
        from everything_is_an_actor.moa.utils import format_references

        results = [
            TaskResult(task_id="1", output="answer A"),
            TaskResult(task_id="2", error="boom", status=TaskStatus.FAILED),
        ]
        text = format_references(results, include_failures=True)
        assert "1. answer A" in text
        assert "2. [FAILED: boom]" in text

    def test_empty_results(self):
        from everything_is_an_actor.moa.utils import format_references

        assert format_references([]) == ""
