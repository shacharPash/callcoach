import importlib.util
import json
import subprocess
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "tools" / "show.py"
spec = importlib.util.spec_from_file_location("show", TOOL)
show = importlib.util.module_from_spec(spec)
spec.loader.exec_module(show)


def test_lines_are_shown_with_who_said_them():
    assert show.show({"type": "line", "channel": "me", "text": "שלום"}) == "אני: שלום"
    assert show.show({"type": "line", "channel": "them", "text": "hi"}) == "הצד השני: hi"


def test_status_events_are_hidden():
    assert show.show({"type": "status", "state": "active"}) is None


def test_bad_input_is_skipped():
    events = ["not json", json.dumps({"type": "line"}), json.dumps([1]),
              json.dumps({"type": "line", "channel": "me", "text": "כן"})]
    out = subprocess.run([sys.executable, str(TOOL)], input="\n".join(events) + "\n",
                         capture_output=True, text=True, check=True).stdout
    assert out == "אני: כן\n"
