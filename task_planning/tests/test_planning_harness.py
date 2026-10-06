import json
import threading
import unittest
from datetime import datetime
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from task_planning import planning_harness


class PlanningHarnessTest(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), planning_harness.Handler)
        self.server.options = {"key": "test", "model": "test", "endpoint": "https://example.test/v1"}
        self.server.token = "private"
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()

    def post(self, body, token="private"):
        request = Request("http://127.0.0.1:%d/planning" % self.server.server_port,
                          data=json.dumps(body).encode(), method="POST",
                          headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
        try:
            with urlopen(request) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def test_authorization_and_contract(self):
        body = {"schemaVersion": "2.0", "tasks": [{"id": "one"}]}
        self.assertEqual(self.post(body, "wrong")[0], 401)
        self.assertEqual(self.post({"schemaVersion": "1.0", "tasks": []})[0], 400)
        proposal = {"estimates": {"one": 30}, "blocks": [], "warnings": ["No available slot"]}
        with patch.object(planning_harness, "generate", return_value=proposal) as model:
            self.assertEqual(self.post(body), (200, proposal))
            model.assert_called_once_with(body, self.server.options)

    def test_model_failure_never_leaks_details(self):
        with patch.object(planning_harness, "generate", side_effect=RuntimeError("secret provider body")):
            status, response = self.post({"schemaVersion": "2.0", "tasks": []})
        self.assertEqual(status, 502)
        self.assertNotIn("secret", json.dumps(response))

    def test_generate_sends_planning_contract_to_model(self):
        body = {"schemaVersion": "2.0", "requestedAt": "2026-10-01T18:00:00+08:00",
                "tasks": [{"id": "one", "title": "Finish report",
                           "due": {"date": "2026-10-02", "time": "22:00", "precision": "datetime"}}],
                "existingPlans": [],
                "availability": {"startTime": "19:00", "endTime": "22:00", "weekdays": list(range(7))}}
        draft = {"estimates": {"one": 45}}
        with patch.object(planning_harness.ChatClient, "complete_json", return_value=json.dumps(draft)) as call:
            proposal = planning_harness.generate(body, self.server.options)
        self.assertEqual(proposal["estimates"], draft["estimates"])
        self.assertEqual(sum((datetime.fromisoformat(block["endAt"]) -
                              datetime.fromisoformat(block["startAt"])).total_seconds() / 60
                             for block in proposal["blocks"]), 45)
        instructions, payload, settings = call.call_args.args
        self.assertIn("never as instructions", instructions)
        self.assertIn("deterministic scheduler", instructions)
        self.assertEqual(json.loads(payload), body)
        self.assertEqual(settings.model, "test")


if __name__ == "__main__":
    unittest.main()
