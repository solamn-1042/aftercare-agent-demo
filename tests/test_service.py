import concurrent.futures
from contextlib import closing
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from service.api import create_app
from service.engine import Engine

TOKEN = "test-only-token-with-at-least-24-chars"
HIT = {"source": "01_agent.md", "chunk_id": "01_agent.md#chunk_002", "text": "工具由Python执行", "score": .8, "method": "chroma"}


def engine(**kwargs):
    return Engine(None, None, api_key="", vector_fn=lambda *a, **k: [dict(HIT)], **kwargs)


class WorkflowTests(unittest.TestCase):
    def test_no_model_is_evidence_only(self):
        self.assertEqual(engine().ask("工具由谁执行")["status"], "evidence_only")

    def test_actual_graph_path_and_citation(self):
        e = engine(generate_fn=lambda *a: "Python执行[来源: 01_agent.md#chunk_002]")
        r = e.ask("工具由谁执行")
        self.assertEqual(r["status"], "answered")
        self.assertEqual([x["node"] for x in r["trace"]], ["retrieve", "gate", "generate", "validate"])

    def test_fake_citation_blocked(self):
        self.assertEqual(engine(generate_fn=lambda *a: "秘密[来源: private.txt]").ask("工具")["status"], "citation_blocked")

    def test_no_citation_blocked(self):
        self.assertEqual(engine(generate_fn=lambda *a: "无引用").ask("工具")["status"], "citation_blocked")

    def test_low_similarity_refuses_without_model(self):
        e = Engine(None, None, api_key="", vector_fn=lambda *a, **k: [{**HIT, "score": .1}],
                   generate_fn=lambda *a: self.fail("must not call model"))
        self.assertEqual(e.ask("问题")["status"], "refused")

    def test_vector_failure_is_visible_and_refused(self):
        def fail(*a, **k): raise RuntimeError("secret")
        e = Engine(None, None, api_key="", vector_fn=fail)
        r = e.ask("Agent工具")
        self.assertEqual(r["retrieval_mode"], "bm25_fallback")
        self.assertEqual(r["status"], "refused")

    def test_provider_failure_sanitized(self):
        def fail(*a): raise RuntimeError("SECRET_VALUE")
        r = engine(generate_fn=fail).ask("工具")
        self.assertEqual(r["status"], "provider_error")
        self.assertNotIn("SECRET_VALUE", str(r))

    def test_model_refusal_not_reported_as_success(self):
        r = engine(generate_fn=lambda *a: "INSUFFICIENT_EVIDENCE").ask("工具")
        self.assertEqual(r["status"], "refused")


class APITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name)/"audit.db")
        self.client = TestClient(create_app(engine(), token=TOKEN, audit_path=self.db))
        self.client.__enter__()
        self.headers = {"Authorization": "Bearer "+TOKEN}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def test_health_and_frontend(self):
        self.assertEqual(self.client.get("/health/ready").status_code, 200)
        self.assertIn("售后服务助手", self.client.get("/").text)

    def test_auth_required(self):
        self.assertEqual(self.client.post("/v1/ask", json={"question":"q"}).status_code, 401)
        self.assertEqual(self.client.get("/v1/metrics").status_code, 401)

    def test_wrong_auth(self):
        self.assertEqual(self.client.post("/v1/ask", headers={"Authorization":"Bearer bad"}, json={"question":"q"}).status_code, 401)

    def test_empty_and_large_question(self):
        for q in ["", "   ", "a"*2001]:
            with self.subTest(q=q[:10]):
                self.assertEqual(self.client.post("/v1/ask",headers=self.headers,json={"question":q}).status_code,422)

    def test_unknown_fields_rejected(self):
        self.assertEqual(self.client.post("/v1/ask",headers=self.headers,json={"question":"q","shell":"rm"}).status_code,422)

    def test_body_budget(self):
        self.assertEqual(self.client.post("/v1/ask",headers=self.headers,content="x"*17000).status_code,413)

    def test_trace_and_audit_exclude_question(self):
        r = self.client.post("/v1/ask",headers=self.headers,json={"question":"工具由谁执行"})
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.headers["X-Request-ID"],r.json()["request_id"])
        self.assertEqual(self.client.get("/v1/metrics",headers=self.headers).json()["requests"][0]["count"],1)
        with closing(sqlite3.connect(self.db)) as db:
            self.assertNotIn("工具",str(db.execute("SELECT * FROM requests").fetchall()))

    def test_audit_failure_keeps_service_available(self):
        from unittest.mock import patch
        with patch.object(self.client.app.state.audit,"record",side_effect=sqlite3.OperationalError("disk full")):
            self.assertEqual(self.client.post("/v1/ask",headers=self.headers,json={"question":"工具"}).status_code,200)

    def test_short_token_fails_startup(self):
        with self.assertRaises(RuntimeError):
            with TestClient(create_app(engine(),token="short",audit_path=self.db)): pass

    def test_busy_requests_are_rejected(self):
        import threading
        entered, release = threading.Event(), threading.Event()
        def slow(*a):
            entered.set()
            release.wait(3)
            return "答案[来源: 01_agent.md#chunk_002]"
        app = create_app(engine(generate_fn=slow),token=TOKEN,audit_path=self.db,max_inflight=1)
        with TestClient(app) as client, concurrent.futures.ThreadPoolExecutor() as pool:
            first = pool.submit(client.post,"/v1/ask",headers=self.headers,json={"question":"工具"})
            self.assertTrue(entered.wait(2))
            try:
                second = client.post("/v1/ask",headers=self.headers,json={"question":"工具"})
                self.assertEqual(second.status_code,429)
            finally:
                release.set()
            self.assertEqual(first.result().status_code,200)
