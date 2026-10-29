import json
import unittest

from app import judges


def sse(*chunks):
    out = []
    for c in chunks:
        out.append("data: " + json.dumps({"choices": [{"delta": {"content": c}}]}))
    out.append("data: [DONE]")
    return "\n\n".join(out)


class ParserTests(unittest.TestCase):
    def test_plain_json_body(self):
        body = json.dumps({"choices": [{"message": {"content": '{"score":91,"pass":true,"reason":"ok"}'}}]})
        self.assertIn("score", judges._collect_text(body))

    def test_json_with_trailing_done_marker(self):
        body = json.dumps({"choices": [{"message": {"content": '{"score":55,"pass":false,"reason":"x"}'}}]}) + "data: [DONE]\n\n"
        payload = judges.extract_json(judges._collect_text(body))
        self.assertEqual(payload["score"], 55)

    def test_sse_chunks_are_concatenated(self):
        raw = sse('{"score":', "88", ',"pass":true,"reason":"bagus"}')
        payload = judges.extract_json(judges._collect_text(raw))
        self.assertEqual(payload["score"], 88)
        self.assertTrue(payload["pass"])

    def test_json_inside_code_fence(self):
        text = '```json\n{"score": 70, "pass": true, "reason": "layak"}\n```'
        self.assertEqual(judges.extract_json(text)["score"], 70)

    def test_garbage_raises(self):
        with self.assertRaises(judges.JudgeError):
            judges.extract_json("maaf saya tidak bisa membantu")

    def test_error_payload_raises(self):
        raw = json.dumps({"error": {"message": "Missing API key"}})
        with self.assertRaises(judges.JudgeError):
            judges._collect_text(raw)


class PromptTests(unittest.TestCase):
    def test_event_prompt_contains_concept(self):
        prompt = judges.build_prompt("event", {"concept": "Ide 66: Earth Hour 2027"})
        self.assertIn("Earth Hour 2027", prompt)
        self.assertIn("JSON", prompt)

    def test_meta_prompt_contains_title_and_keywords(self):
        prompt = judges.build_prompt("meta", {"title": "Glass bulb", "keywords": "bulb, forest"})
        self.assertIn("Glass bulb", prompt)
        self.assertIn("bulb, forest", prompt)

    def test_unknown_judge_falls_back(self):
        self.assertIn("KELAYAKAN JUAL", judges.build_prompt("tidak-ada", {}))


if __name__ == "__main__":
    unittest.main()
