"""Best-effort stage observations outside the frozen ASR producer."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import time

SCHEMA = "murmurmark.transcribe_compute_observation/v1"
PATTERNS = (
    ("primary_asr", "raw/chunks/*/*.run.log"),
    ("primary_asr", "raw/chunks/*/chunk_cache_report.json"),
    ("micro_current", "timeline-repair/micro_reasr/*.wav"),
    ("micro_current", "timeline-repair/micro_reasr/*.run.log"),
    ("micro_shadow", "timeline-repair-shadow_v2/micro_reasr/*.wav"),
    ("micro_shadow", "timeline-repair-shadow_v2/micro_reasr/*.run.log"),
    ("opening_repair", "opening-repair*/**/*.run.log"),
    ("publication", "resolved/transcribe_simple_report*.json"),
)


class Observer:
    def __init__(self, session: Path, *, clock=time.monotonic, poll_sec: float = 2.0):
        self.root = session / "derived/transcript-simple/whisper-cpp"
        self.clock = clock
        self.started = clock()
        self.poll_sec = poll_sec
        self.last_poll = self.started
        self.error = None
        self.seen = self._snapshot()
        self.changed = set()
        self.events = [{"at_sec": 0.0, "activity": "preparing_or_cache_validation"}]

    def _snapshot(self) -> dict:
        values = {}
        try:
            for activity, pattern in PATTERNS:
                for path in self.root.glob(pattern):
                    try:
                        stat = path.stat()
                    except FileNotFoundError:
                        continue
                    values[str(path.relative_to(self.root))] = (activity, stat.st_mtime_ns, stat.st_size)
                    if len(values) > 10000:
                        raise ValueError("observation_file_limit")
        except (OSError, ValueError) as error:
            self.error = type(error).__name__ + ": " + str(error)
            return {}
        return values

    def poll(self, *, force: bool = False) -> None:
        now = self.clock()
        if self.error or (not force and now - self.last_poll < self.poll_sec):
            return
        self.last_poll = now
        current = self._snapshot()
        if self.error:
            return
        changed = {path for path, stamp in current.items() if self.seen.get(path) != stamp}
        self.seen = current
        self.changed.update(changed)
        activities = sorted({current[path][0] for path in changed})
        if activities:
            activity = "+".join(activities)
            if activity != self.events[-1]["activity"]:
                self.events.append({"at_sec": round(now - self.started, 6), "activity": activity})

    def report(self, *, final: bool = False) -> dict:
        self.poll(force=final)
        elapsed = round(self.clock() - self.started, 6)
        windows = []
        for index, event in enumerate(self.events):
            end = self.events[index + 1]["at_sec"] if index + 1 < len(self.events) else elapsed
            windows.append({**event, "until_sec": end, "observed_window_sec": round(end - event["at_sec"], 6)})
        counts = Counter(self.seen[p][0] for p in self.changed if p in self.seen)
        return {"schema": SCHEMA, "status": "unavailable" if self.error else "observed",
                "error": self.error, "elapsed_sec": elapsed, "poll_sec": self.poll_sec,
                "last_observed_activity": self.events[-1]["activity"], "windows": windows,
                "changed_artifacts_by_activity": dict(sorted(counts.items())),
                "model_invocations": None, "micro_cache_hits": None,
                "measurement_scope": "artifact_activity_windows_not_model_timings",
                "limitations": ["old_unchanged_files_excluded", "artifact_reuse_is_not_a_decode",
                                "windows_include_waits_and_adjacent_work", "no_activity_does_not_prove_idle",
                                "concurrent_activities_not_additive", "producer_call_counters_unavailable"]}
