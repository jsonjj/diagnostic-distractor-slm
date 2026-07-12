#!/usr/bin/env python3
"""Development-only verifier bridge for the Wayline live demo.

The bridge is deliberately narrow:
* loopback-only standard-library HTTP server;
* calls an already-running pinned llama-server;
* exposes only /health and /generate;
* returns answers only when all three misconception/computation/answer bindings
  pass the project's strict checker;
* otherwise returns 422 so Unity uses its sealed deterministic fallback.

This does not replace the production packaged Forge runtime or weaken its
descriptor-binding/reviewed-cache gates.
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request


REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from src.consistency import computation_consistent  # noqa: E402
from src.prompts import SYSTEM_PROMPT, build_user, parse_distractors  # noqa: E402
from src.text_utils import normalize_answer  # noqa: E402


MAX_REQUEST_BYTES = 16_384
MAX_MODEL_RESPONSE_BYTES = 1_048_576


def _model_chat(
    llama_url: str,
    *,
    question: str,
    correct: str,
    topic: str,
    attempt: int,
) -> str:
    payload = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user(question, correct, topic)},
        ],
        "temperature": 0.0 if attempt == 0 else min(0.35, 0.12 * attempt),
        "seed": 7411 + attempt,
        "max_tokens": 512,
    }
    request = urllib.request.Request(
        llama_url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        raw = response.read(MAX_MODEL_RESPONSE_BYTES + 1)
    if len(raw) > MAX_MODEL_RESPONSE_BYTES:
        raise ValueError("model response too large")
    decoded = json.loads(raw)
    return decoded["choices"][0]["message"]["content"]


def generate_verified(
    llama_url: str,
    *,
    question: str,
    correct: str,
    topic: str,
) -> list[str] | None:
    correct_norm = normalize_answer(correct)
    # Two bounded attempts keep the Unity loading screen under its 120-second
    # timeout. Any remaining miss returns 422 and the sealed deterministic batch
    # appears immediately instead of leaving the learner waiting.
    for attempt in range(2):
        try:
            raw = _model_chat(
                llama_url,
                question=question,
                correct=correct,
                topic=topic,
                attempt=attempt,
            )
            distractors = parse_distractors(raw)
            if len(distractors) != 3:
                continue
            answers = [normalize_answer(item.get("answer", "")) for item in distractors]
            if (
                any(not answer or len(answer) > 64 for answer in answers)
                or correct_norm in answers
                or len(set(answers)) != 3
                or any(
                    not item.get("misconception", "").strip()
                    or computation_consistent(
                        item.get("computation", ""),
                        item.get("answer", ""),
                        question=question,
                    )
                    is not True
                    for item in distractors
                )
            ):
                continue
            return [str(item["answer"]).strip() for item in distractors]
        except (KeyError, TypeError, ValueError, urllib.error.URLError, TimeoutError):
            continue
    return None


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "WaylineLiveBridge/1"

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self._json(404, {"status": "not_found"})
            return
        self._json(200, {"status": "ready"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/generate":
            self._json(404, {"status": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_REQUEST_BYTES:
            self._json(400, {"status": "invalid_request"})
            return
        try:
            body = json.loads(self.rfile.read(length))
            if set(body) != {"question", "correct", "topic"}:
                raise ValueError
            question = body["question"]
            correct = body["correct"]
            topic = body["topic"]
            if not all(
                isinstance(value, str) and value.strip() and len(value) <= 2048
                for value in (question, correct, topic)
            ):
                raise ValueError
        except (json.JSONDecodeError, ValueError, TypeError):
            self._json(400, {"status": "invalid_request"})
            return

        answers = generate_verified(
            self.server.llama_url,  # type: ignore[attr-defined]
            question=question,
            correct=correct,
            topic=topic,
        )
        if answers is None:
            print("live_bridge: generation rejected; Unity will use deterministic fallback")
            self._json(422, {"status": "verification_failed"})
            return
        print(f"live_bridge: verified 3 distractors for {question[:60]!r}")
        self._json(200, {"verified": True, "answers": answers})

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _json(self, status: int, payload: dict[str, object]) -> None:
        raw = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llama-url", default="http://127.0.0.1:8081")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()
    if args.host != "127.0.0.1":
        raise SystemExit("bridge host must be IPv4 loopback")
    server = ThreadingHTTPServer((args.host, args.port), BridgeHandler)
    server.llama_url = args.llama_url  # type: ignore[attr-defined]
    print(f"live_bridge: ready on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 130
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
