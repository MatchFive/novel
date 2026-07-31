import unittest

from app.agents.workflows.models import WorkflowDefinition, WorkflowStep


class TestWorkflowModels(unittest.TestCase):
    def test_definition_serializes(self):
        definition = WorkflowDefinition(
            name="test",
            description="A test workflow",
            steps=[
                WorkflowStep(name="load", fn="test.load"),
                WorkflowStep(name="save", fn="test.save", depends_on=["load"]),
            ],
        )
        data = definition.model_dump()
        self.assertEqual(data["name"], "test")
        self.assertEqual(len(data["steps"]), 2)


if __name__ == "__main__":
    unittest.main()
