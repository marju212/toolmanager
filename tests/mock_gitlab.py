#!/usr/bin/env python3
"""
Mock GitLab API server for testing release.sh.

Simulates the following endpoints:
  GET  /api/v4/projects/:id              - Get project by ID or encoded path
  PUT  /api/v4/projects/:id              - Update project (default branch)
  POST /api/v4/projects/:id/merge_requests - Create merge request

Usage:
  python3 mock_gitlab.py [--port PORT] [--state-dir DIR]

The server writes its port to <state-dir>/port once listening, so tests can
discover the assigned port when using --port 0.

Behavior can be controlled per-request via the X-Mock-Scenario header or
globally via files in <state-dir>/:
  fail_auth        - Return 401 on next request
  fail_not_found   - Return 404 on next project lookup
  fail_server      - Return 500 on next request
"""

import argparse
import json
import os
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote


class MockState:
    """Shared state across requests."""

    def __init__(self, state_dir):
        self.state_dir = state_dir
        self.requests = []  # List of (method, path, headers, body) tuples
        self.project = {
            "id": 12345,
            "name": "test-project",
            "path_with_namespace": "group/test-project",
            "default_branch": "main",
            "web_url": "https://gitlab.example.com/group/test-project",
        }
        self.merge_request_counter = 0
        self.lock = threading.Lock()

    def record_request(self, method, path, headers, body):
        with self.lock:
            self.requests.append({
                "method": method,
                "path": path,
                "headers": dict(headers),
                "body": body,
            })

    def check_scenario(self, name):
        """Check if a scenario file exists and remove it (one-shot trigger)."""
        path = os.path.join(self.state_dir, name)
        if os.path.exists(path):
            os.unlink(path)
            return True
        return False

    def dump_requests(self):
        """Write all recorded requests to state dir for test inspection."""
        path = os.path.join(self.state_dir, "requests.json")
        with self.lock:
            with open(path, "w") as f:
                json.dump(self.requests, f, indent=2, default=str)


class GitLabHandler(BaseHTTPRequestHandler):
    """Handles mock GitLab API requests."""

    state: MockState  # Set by factory

    def log_message(self, format, *args):
        """Suppress default stderr logging."""
        pass

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > 0:
            return self.rfile.read(length).decode("utf-8")
        return ""

    def _send_json(self, code, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _check_auth(self):
        token = self.headers.get("PRIVATE-TOKEN", "")
        if not token:
            self._send_json(401, {"message": "401 Unauthorized"})
            return False
        if self.state.check_scenario("fail_auth"):
            self._send_json(401, {"message": "401 Unauthorized - token expired"})
            return False
        return True

    def _check_global_failures(self):
        if self.state.check_scenario("fail_server"):
            self._send_json(500, {"message": "500 Internal Server Error"})
            return True
        # Persistent variant — stays active until file is manually removed
        fail_always = os.path.join(self.state.state_dir, "fail_server_always")
        if os.path.exists(fail_always):
            self._send_json(500, {"message": "500 Internal Server Error"})
            return True
        return False

    def _route(self, method):
        body = self._read_body() if method in ("POST", "PUT", "PATCH") else ""
        self.state.record_request(method, self.path, self.headers, body)

        if not self._check_auth():
            return
        if self._check_global_failures():
            return

        # Strip /api/v4 prefix
        path = self.path
        if path.startswith("/api/v4"):
            path = path[len("/api/v4"):]

        # Route: GET /projects/:id_or_path
        if method == "GET" and path.startswith("/projects/"):
            self._handle_get_project(path)
        # Route: PUT /projects/:id_or_path
        elif method == "PUT" and path.startswith("/projects/"):
            self._handle_update_project(path, body)
        # Route: POST /projects/:id_or_path/merge_requests
        elif method == "POST" and "/merge_requests" in path:
            self._handle_create_mr(path, body)
        else:
            self._send_json(404, {"message": f"404 Not Found: {method} {self.path}"})

    def _handle_get_project(self, path):
        if self.state.check_scenario("fail_not_found"):
            self._send_json(404, {"message": "404 Project Not Found"})
            return

        # Extract project identifier (could be numeric ID or URL-encoded path)
        parts = path.split("/")  # /projects/<identifier>
        if len(parts) >= 3:
            identifier = unquote(parts[2])
        else:
            self._send_json(404, {"message": "404 Not Found"})
            return

        # Match by ID or path
        project = self.state.project
        if (str(project["id"]) == identifier or
                project["path_with_namespace"] == identifier):
            self._send_json(200, project)
        else:
            self._send_json(404, {
                "message": f"404 Project Not Found: {identifier}"
            })

    def _handle_update_project(self, path, body):
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._send_json(400, {"message": "400 Bad Request: invalid JSON"})
            return

        if "default_branch" in data:
            self.state.project["default_branch"] = data["default_branch"]

        self._send_json(200, self.state.project)

    def _handle_create_mr(self, path, body):
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._send_json(400, {"message": "400 Bad Request: invalid JSON"})
            return

        self.state.merge_request_counter += 1
        mr_id = self.state.merge_request_counter

        mr = {
            "id": mr_id,
            "iid": mr_id,
            "title": data.get("title", f"MR !{mr_id}"),
            "description": data.get("description", ""),
            "source_branch": data.get("source_branch", ""),
            "target_branch": data.get("target_branch", "main"),
            "state": "opened",
            "web_url": f"{self.state.project['web_url']}/-/merge_requests/{mr_id}",
            "remove_source_branch": data.get("remove_source_branch", False),
        }
        self._send_json(201, mr)

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._route("PUT")


def make_handler(state):
    """Create a handler class bound to the given state."""

    class BoundHandler(GitLabHandler):
        pass

    BoundHandler.state = state
    return BoundHandler


def main():
    parser = argparse.ArgumentParser(description="Mock GitLab API server")
    parser.add_argument("--port", type=int, default=0, help="Port (0 = auto)")
    parser.add_argument("--state-dir", default="/tmp/mock_gitlab",
                        help="Directory for state files")
    args = parser.parse_args()

    os.makedirs(args.state_dir, exist_ok=True)

    state = MockState(args.state_dir)
    handler = make_handler(state)
    server = HTTPServer(("127.0.0.1", args.port), handler)

    actual_port = server.server_address[1]

    # Write port file so tests can discover it
    port_file = os.path.join(args.state_dir, "port")
    with open(port_file, "w") as f:
        f.write(str(actual_port))

    # Write PID file for cleanup
    pid_file = os.path.join(args.state_dir, "pid")
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))

    print(f"Mock GitLab API listening on http://127.0.0.1:{actual_port}", flush=True)
    print(f"State dir: {args.state_dir}", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        state.dump_requests()
        server.server_close()


if __name__ == "__main__":
    main()
