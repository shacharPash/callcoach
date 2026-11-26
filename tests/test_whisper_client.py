import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from callcoach import whisper
from callcoach.whisper import WhisperClient, WhisperServer, reap_orphans, write_pid_file


def parse_multipart(content_type: str, body: bytes) -> dict:
    boundary = content_type.split("boundary=")[1].encode()
    fields = {}
    for part in body.split(b"--" + boundary):
        head, sep, value = part.partition(b"\r\n\r\n")
        if not sep:
            continue
        name = re.search(rb'name="([^"]+)"', head).group(1).decode()
        value = value[:-2] if value.endswith(b"\r\n") else value
        fields[name] = value if name == "file" else value.decode()
    return fields


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        fields = parse_multipart(self.headers["Content-Type"], body)
        self.server.seen.append(fields)
        if fields.get("detect_language") == "true":
            resp = {"language": "english", "language_probabilities": {"en": 0.9, "he": 0.05}}
        elif fields.get("response_format") == "verbose_json":
            resp = {"segments": [{"start": 0.5, "end": 2.25, "text": " שלום "}, {"start": 3, "end": 4, "text": "  "},
                                 {"start": 4.0, "end": 6.5, "text": "מה שלומך"}]}
        else:
            resp = {"text": " שלום עולם \n"}
        data = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    srv.seen = []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


def client(srv):
    return WhisperClient(f"http://127.0.0.1:{srv.server_port}")


def test_segments_come_with_their_times_and_without_empty_ones(server):
    got = client(server).segments(b"\x00\x00" * 16000, "he", prompt="Okta, Acme")
    assert got == [(0.5, 2.25, "שלום"), (4.0, 6.5, "מה שלומך")]
    sent = server.seen[-1]
    assert sent["no_timestamps"] == "false" and sent["language"] == "he" and sent["prompt"] == "Okta, Acme"


def test_transcribe_sends_the_language_and_a_wav(server):
    assert client(server).transcribe(b"\x00\x00" * 1600, "he") == "שלום עולם"
    sent = server.seen[0]
    assert sent["language"] == "he" and sent["response_format"] == "json"
    assert sent["file"][:4] == b"RIFF"


def test_transcribe_sends_the_prompt_only_when_there_is_one(server):
    c = client(server)
    c.transcribe(bytes(3200), "he", prompt="SOC 2, Okta")
    assert server.seen[-1]["prompt"] == "SOC 2, Okta"
    c.transcribe(bytes(3200), "he")
    assert "prompt" not in server.seen[-1]


def test_detect_returns_probabilities(server):
    assert client(server).detect(b"\x00\x00" * 1600) == {"en": 0.9, "he": 0.05}
    assert server.seen[0]["response_format"] == "verbose_json"


def test_proxy_settings_are_ignored(server, monkeypatch):
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    assert client(server).transcribe(b"\x00\x00" * 1600, "en") == "שלום עולם"


def test_a_server_that_exits_while_loading_is_tried_three_times(tmp_path):
    runs = tmp_path / "runs"
    script = tmp_path / "fake-server"
    script.write_text(f'#!/bin/sh\necho run >> "{runs}"\nexit 3\n', encoding="utf-8")
    script.chmod(0o755)
    server = WhisperServer("he", tmp_path / "model.bin", binary=str(script), log_dir=tmp_path / "logs")
    server.start()
    with pytest.raises(RuntimeError, match="code 3"):
        server.wait_ready(timeout=10)
    assert len(runs.read_text(encoding="utf-8").splitlines()) == 3


SERVER = "/x/whisper-server -m model --host 127.0.0.1 --port {} -t 4 -nt"


def reap(run_dir, processes, *, alive=lambda pid: False, kill=None):
    """Runs reap_orphans against fake processes: pid -> (binary, command, start)."""
    killed = []

    def default_kill(pid, sig):
        killed.append(pid)

    reaped = reap_orphans(run_dir, alive=alive, binary_of=lambda pid: processes.get(pid, (None,) * 3)[0],
                          command_of=lambda pid: processes.get(pid, (None,) * 3)[1],
                          start_of=lambda pid: processes.get(pid, (None,) * 3)[2], kill=kill or default_kill)
    return reaped, killed


def test_orphaned_servers_are_stopped_and_live_ones_kept(tmp_path):
    write_pid_file(tmp_path, pid=111, owner=900, port=5001, start="S1")
    write_pid_file(tmp_path, pid=222, owner=901, port=5002, start="S2")
    write_pid_file(tmp_path, pid=333, owner=902, port=5003, start="S3")
    processes = {111: ("/x/whisper-server", SERVER.format(5001), "S1"),
                 222: ("/x/whisper-server", SERVER.format(5002), "S2"),
                 333: ("/usr/bin/vim", "/usr/bin/vim --port 5003", "S3")}
    reaped, killed = reap(tmp_path, processes, alive=lambda pid: pid == 901)
    assert killed == [111] and reaped == [111]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["whisper-222.json"]


def test_a_reused_pid_is_never_stopped(tmp_path):
    write_pid_file(tmp_path, pid=111, owner=900, port=5001, start="S1")
    write_pid_file(tmp_path, pid=112, owner=900, port=5002, start="S1")
    write_pid_file(tmp_path, pid=113, owner=900, port=5003, start="S1")
    write_pid_file(tmp_path, pid=114, owner=900)
    processes = {111: ("/x/whisper-server", SERVER.format(5009), "S1"),
                 112: ("/x/whisper-server", SERVER.format(5002), "S2"),
                 113: ("/bin/sh", "/bin/sh -c " + SERVER.format(5003), "S1"),
                 114: ("/x/whisper-server", SERVER.format(5004), "S1")}
    reaped, killed = reap(tmp_path, processes)
    assert killed == [] and reaped == [] and list(tmp_path.iterdir()) == []


def test_a_server_that_cannot_be_signalled_does_not_end_the_reap(tmp_path):
    write_pid_file(tmp_path, pid=111, owner=900, port=5001, start="S1")
    write_pid_file(tmp_path, pid=222, owner=900, port=5002, start="S2")
    processes = {111: ("/x/whisper-server", SERVER.format(5001), "S1"),
                 222: ("/x/whisper-server", SERVER.format(5002), "S2")}
    killed = []

    def kill(pid, sig):
        if pid == 111:
            raise PermissionError("not permitted")
        killed.append(pid)

    reaped, _ = reap(tmp_path, processes, kill=kill)
    assert reaped == [222] and killed == [222] and list(tmp_path.iterdir()) == []


def test_ps_runs_in_a_fixed_locale_and_time_zone(monkeypatch):
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw.get("env")))
        return type("Done", (), {"stdout": "out\n"})()

    monkeypatch.setattr(whisper.subprocess, "run", run)
    assert whisper._command_of(5) == "out" and whisper._binary_of(5) == "out"
    assert [cmd[-1] for cmd, _ in calls] == ["command=", "comm="]
    assert all(env and env["LC_ALL"] == "C" and env["TZ"] == "UTC" for _, env in calls)


def test_a_pid_file_holds_the_server_its_owner_port_and_start(tmp_path):
    path = write_pid_file(tmp_path, pid=5, owner=6, port=7, start="Sun Nov 15 10:00:00 2026")
    assert json.loads(path.read_text()) == {"pid": 5, "owner": 6, "port": 7, "start": "Sun Nov 15 10:00:00 2026"}


def test_the_pid_file_records_the_port_and_start_of_the_server(tmp_path, monkeypatch):
    monkeypatch.setattr(whisper, "process_start", lambda pid: "START")
    script = tmp_path / "fake-server"
    script.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    script.chmod(0o755)
    server = WhisperServer("he", tmp_path / "model.bin", binary=str(script), run_dir=tmp_path / "run")
    server.start()
    try:
        data = json.loads(server.pid_file.read_text())
        assert data == {"pid": server.proc.pid, "owner": os.getpid(), "port": server.port, "start": "START"}
    finally:
        server.stop()


def test_a_pid_file_that_cannot_be_written_does_not_stop_the_server(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("", encoding="utf-8")
    script = tmp_path / "fake-server"
    script.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    script.chmod(0o755)
    notes = []
    server = WhisperServer("he", tmp_path / "model.bin", binary=str(script), run_dir=blocker / "run",
                           on_note=lambda kind, **fields: notes.append((kind, fields)))
    server.start()
    try:
        assert server.proc is not None and server.pid_file is None
        [(kind, fields)] = notes
        assert kind == "pid_file_failed" and fields["server"] == "he" and fields["error"]
    finally:
        server.stop()


def test_a_server_keeps_one_pid_file_and_removes_it_on_stop(tmp_path):
    script = tmp_path / "fake-server"
    script.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    script.chmod(0o755)
    run_dir = tmp_path / "run"
    server = WhisperServer("he", tmp_path / "model.bin", binary=str(script), run_dir=run_dir)
    server.start()
    with pytest.raises(RuntimeError):
        server.wait_ready(timeout=10)
    assert len(list(run_dir.glob("whisper-*.json"))) == 1
    server.stop()
    assert list(run_dir.glob("whisper-*.json")) == []
