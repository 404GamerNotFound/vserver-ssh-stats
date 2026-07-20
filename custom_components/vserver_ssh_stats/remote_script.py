from pathlib import Path

REMOTE_SCRIPT = (Path(__file__).parent / "remote_collector.sh").read_text(encoding="utf-8")
