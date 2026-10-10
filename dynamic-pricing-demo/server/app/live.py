"""In-memory live state. Nothing here is persisted; restart = gone."""
import threading
import time


class LiveState:
    def __init__(self):
        self.lock = threading.Lock()
        self.detected = []          # profile ids currently "present"
        self.scores = {}            # profile id -> matched fraction (latest frame only)
        self.camera_status = "starting"
        self.fps = 0.0
        self.override_id = None
        self.override_until = 0.0
        self.preview_jpeg = None
        self._preview_req = 0.0

    def set_detected(self, ids):
        with self.lock:
            self.detected = list(ids)

    def set_override(self, profile_id, seconds):
        with self.lock:
            self.override_id = profile_id
            self.override_until = time.time() + float(seconds) if profile_id is not None else 0

    def override(self):
        with self.lock:
            if self.override_id is not None and time.time() < self.override_until:
                return self.override_id, round(self.override_until - time.time(), 1)
            return None, 0

    def request_preview(self):
        self._preview_req = time.time()

    def preview_wanted(self):
        return time.time() - self._preview_req < 5

    def current_profile(self, profiles):
        """Override (simulate button) wins, else highest-priority detected profile."""
        by_id = {p["id"]: p for p in profiles if p["enabled"]}
        oid, _ = self.override()
        if oid in by_id:
            return by_id[oid]
        with self.lock:
            cands = [by_id[i] for i in self.detected if i in by_id]
        return max(cands, key=lambda p: p["priority"]) if cands else None
