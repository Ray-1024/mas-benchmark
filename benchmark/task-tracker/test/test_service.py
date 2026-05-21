from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from task_tracker.errors import AuthenticationError, AuthorizationError, NotFoundError, ValidationError
from task_tracker.service import TaskTrackerService


class TaskTrackerServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = TaskTrackerService(secret_key="test-secret")
        self.user = self.service.register_user("owner@example.com", "owner", "password123")
        self.other = self.service.register_user("member@example.com", "member", "password123")

    def test_register_login_and_token_profile(self) -> None:
        login = self.service.login("owner@example.com", "password123")

        self.assertEqual(login["token_type"], "bearer")
        self.assertEqual(self.service.user_from_token(login["access_token"])["id"], self.user["id"])

        with self.assertRaises(AuthenticationError):
            self.service.login("owner@example.com", "wrong-password")

    def test_board_column_task_workflow(self) -> None:
        board = self.service.create_board(self.user["id"], "Product Board", "Roadmap")
        todo = self.service.create_column(self.user["id"], board["id"], "To Do")
        doing = self.service.create_column(self.user["id"], board["id"], "Doing")
        task = self.service.create_task(
            self.user["id"],
            todo["id"],
            "Build API",
            description="Create REST endpoints",
            priority="High",
            labels=["feature"],
        )

        moved = self.service.move_task(self.user["id"], task["id"], doing["id"], position=0)
        loaded_board = self.service.get_board(self.user["id"], board["id"])

        self.assertEqual(moved["column_id"], doing["id"])
        self.assertEqual(loaded_board["columns"][1]["tasks"][0]["title"], "Build API")
        self.assertEqual(loaded_board["columns"][1]["tasks"][0]["priority"], "High")

    def test_search_filter_sort_and_activity(self) -> None:
        board = self.service.create_board(self.user["id"], "Engineering")
        column = self.service.create_column(self.user["id"], board["id"], "Backlog")
        self.service.create_task(self.user["id"], column["id"], "Fix login bug", priority="High", labels=["bug"])
        self.service.create_task(self.user["id"], column["id"], "Write docs", priority="Low", labels=["docs"])

        results = self.service.search_tasks(self.user["id"], board["id"], query="login", priority="High", labels=["bug"])
        activity = self.service.activity_for_task(self.user["id"], results[0]["id"])

        self.assertEqual([task["title"] for task in results], ["Fix login bug"])
        self.assertEqual(activity[0]["action"], "task_created")

    def test_comments_labels_and_reorder(self) -> None:
        board = self.service.create_board(self.user["id"], "Ops")
        todo = self.service.create_column(self.user["id"], board["id"], "To Do")
        done = self.service.create_column(self.user["id"], board["id"], "Done")
        first = self.service.create_task(self.user["id"], todo["id"], "First")
        second = self.service.create_task(self.user["id"], todo["id"], "Second")

        reordered_columns = self.service.reorder_columns(self.user["id"], board["id"], [done["id"], todo["id"]])
        reordered_tasks = self.service.reorder_tasks(self.user["id"], todo["id"], [second["id"], first["id"]])
        comment = self.service.add_comment(self.user["id"], second["id"], "Looks good")
        labeled = self.service.add_label(self.user["id"], second["id"], "enhancement", "#00ff00")

        self.assertEqual([column["name"] for column in reordered_columns], ["Done", "To Do"])
        self.assertEqual([task["title"] for task in reordered_tasks], ["Second", "First"])
        self.assertEqual(comment["content"], "Looks good")
        self.assertEqual(labeled["labels"][0]["name"], "enhancement")

    def test_authorization_and_soft_delete(self) -> None:
        board = self.service.create_board(self.user["id"], "Private")

        with self.assertRaises(AuthorizationError):
            self.service.get_board(self.other["id"], board["id"])

        self.service.add_board_member(self.user["id"], board["id"], self.other["id"])
        self.assertEqual(self.service.get_board(self.other["id"], board["id"])["id"], board["id"])

        self.service.delete_board(self.user["id"], board["id"])
        with self.assertRaises(NotFoundError):
            self.service.get_board(self.user["id"], board["id"])

    def test_delete_column_requires_task_reassignment(self) -> None:
        board = self.service.create_board(self.user["id"], "Board")
        todo = self.service.create_column(self.user["id"], board["id"], "To Do")
        done = self.service.create_column(self.user["id"], board["id"], "Done")
        task = self.service.create_task(self.user["id"], todo["id"], "Move me")

        with self.assertRaises(ValidationError):
            self.service.delete_column(self.user["id"], todo["id"])

        self.service.delete_column(self.user["id"], todo["id"], move_tasks_to=done["id"])
        self.assertEqual(self.service.get_board(self.user["id"], board["id"])["columns"][0]["tasks"][0]["id"], task["id"])


if __name__ == "__main__":
    unittest.main()
