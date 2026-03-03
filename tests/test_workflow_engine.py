import unittest

from app.modules.workflow_engine import WorkflowEngine


class WorkflowEngineTests(unittest.TestCase):
    def test_parses_and_lists_workflows(self) -> None:
        engine = WorkflowEngine(
            {
                "enabled": True,
                "definitions": {
                    "Morning Boot": ["open chrome", "open vscode"],
                    "Focus": {"description": "Quiet mode", "steps": ["open notepad"]},
                },
            }
        )

        names = engine.list_workflows()
        self.assertEqual(["focus", "morning boot"], names)
        workflow = engine.get_workflow("morning boot")
        self.assertIsNotNone(workflow)
        if workflow is None:
            self.fail("Expected workflow to be parsed")
        self.assertEqual(("open chrome", "open vscode"), workflow.steps)

    def test_respects_max_steps_limit(self) -> None:
        engine = WorkflowEngine(
            {
                "enabled": True,
                "max_steps": 2,
                "definitions": {
                    "heavy": ["one", "two", "three"],
                },
            }
        )
        workflow = engine.get_workflow("heavy")
        self.assertIsNotNone(workflow)
        if workflow is None:
            self.fail("Expected workflow to exist")
        self.assertEqual(("one", "two"), workflow.steps)


if __name__ == "__main__":
    unittest.main()
