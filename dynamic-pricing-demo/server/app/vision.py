"""Camera -> shirt-color detection. Everything here is in-memory only.

Pipeline (per processed frame):
  1. Grab the newest frame from the RTSP stream (older frames are dropped).
  2. Downscale to 480 px wide and convert to HSV.
  3. Pick the pixels that could be a person's clothing:
       motion  - moving foreground pixels inside the aisle zone (default)
       person  - torso box of people found by OpenCV's HOG person detector
       zone    - every pixel in the aisle zone (simplest, most false hits)
  4. For each shirt-color profile, measure the fraction of those pixels that
     match its color. Above the profile's threshold for N frames = "present".
  5. A profile stays active `hold_seconds` after its color disappears.

No frame, crop, color sample or detection result is ever written to disk or
the database. The optional GUI preview is either a color mask (no image) or
a heavily pixelated frame, and is only generated while someone is viewing it.
"""
import os
import threading
import time

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|timeout;5000000")

import cv2  # noqa: E402
import numpy as np  # noqa: E402

WORK_WIDTH = 480


def hex_to_bgr(hexs):
    h = (hexs or "#888888").lstrip("#")
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        r = g = b = 136
    return (b, g, r)


def hex_to_hsv(hexs):
    px = np.uint8([[hex_to_bgr(hexs)]])
    h, s, v = cv2.cvtColor(px, cv2.COLOR_BGR2HSV)[0, 0]
    return int(h), int(s), int(v)


def prepare_profile(p):
    h, s, v = hex_to_hsv(p["color_hex"])
    q = dict(p)
    q["_hsv"] = (h, s, v)
    q["_achromatic"] = s < 50 or v < 45   # gray / white / black shirts
    q["_bgr"] = hex_to_bgr(p["color_hex"])
    return q


def color_mask(hsv, p):
    """Boolean mask of pixels matching profile p's shirt color."""
    H = hsv[..., 0].astype(np.int16)
    S = hsv[..., 1].astype(np.int16)
    V = hsv[..., 2].astype(np.int16)
    th, ts, tv = p["_hsv"]
    if p["_achromatic"]:
        return (S <= p["gray_sat_max"]) & (np.abs(V - tv) <= p["gray_val_tol"])
    d = np.abs(H - th)
    d = np.minimum(d, 180 - d)  # hue wraps around (red sits at both ends)
    return (d <= p["hue_tol"]) & (S >= p["sat_min"]) & (V >= p["val_min"])


def parse_zone(z, w, h):
    try:
        x0, y0, x1, y1 = [min(max(float(v), 0.0), 1.0) for v in str(z).split(",")]
    except ValueError:
        x0, y0, x1, y1 = 0, 0, 1, 1
    if x1 <= x0 or y1 <= y0:
        x0, y0, x1, y1 = 0, 0, 1, 1
    return int(x0 * w), int(y0 * h), max(int(x1 * w), int(x0 * w) + 2), max(int(y1 * h), int(y0 * h) + 2)


class Analyzer:
    """Stateful (background model / HOG) but stores nothing persistent."""

    def __init__(self):
        self.bg = None
        self.hog = None
        self.mode = None

    def reset(self, mode):
        self.mode = mode
        self.bg = cv2.createBackgroundSubtractorMOG2(history=900, varThreshold=25, detectShadows=True)
        if mode == "person" and self.hog is None:
            self.hog = cv2.HOGDescriptor()
            self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    def analyze(self, frame, profiles, settings, want_masks=False):
        mode = settings.get("mode", "motion")
        if mode != self.mode:
            self.reset(mode)
        h0, w0 = frame.shape[:2]
        scale = WORK_WIDTH / float(w0)
        small = cv2.resize(frame, (WORK_WIDTH, max(2, int(h0 * scale))), interpolation=cv2.INTER_AREA)
        small = cv2.GaussianBlur(small, (5, 5), 0)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        H, W = small.shape[:2]
        zx0, zy0, zx1, zy1 = parse_zone(settings.get("zone"), W, H)

        # regions: list of (x0, y0, x1, y1, candidate_mask_or_None, threshold_override)
        regions, boxes = [], []
        if mode == "person":
            gray = cv2.cvtColor(small[zy0:zy1, zx0:zx1], cv2.COLOR_BGR2GRAY)
            rects, _ = self.hog.detectMultiScale(gray, winStride=(8, 8), padding=(8, 8), scale=1.05)
            thr = float(settings.get("person_torso_fraction", 0.3))
            for (x, y, w, h) in rects:
                x, y = x + zx0, y + zy0
                tx0, tx1 = x + int(0.30 * w), x + int(0.70 * w)
                ty0, ty1 = y + int(0.25 * h), y + int(0.55 * h)
                boxes.append((x, y, x + w, y + h))
                regions.append((tx0, ty0, tx1, ty1, None, thr))
        elif mode == "zone":
            regions.append((zx0, zy0, zx1, zy1, None, None))
        else:  # motion
            fg = self.bg.apply(small)
            fg = np.where(fg == 255, 255, 0).astype(np.uint8)  # 127 = shadow, ignore
            fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
            fg = cv2.dilate(fg, np.ones((5, 5), np.uint8))
            regions.append((zx0, zy0, zx1, zy1, fg[zy0:zy1, zx0:zx1] > 0, None))

        scores = {p["id"]: 0.0 for p in profiles}
        hits = {p["id"]: False for p in profiles}
        masks = {} if want_masks else None
        for (x0, y0, x1, y1, cand, thr_override) in regions:
            sub = hsv[y0:y1, x0:x1]
            area = float(max(sub.shape[0] * sub.shape[1], 1))
            for p in profiles:
                m = color_mask(sub, p)
                if cand is not None:
                    m &= cand
                frac = float(m.sum()) / area
                thr = thr_override if thr_override is not None else p["min_fraction"]
                if frac > scores[p["id"]]:
                    scores[p["id"]] = frac
                if frac >= thr:
                    hits[p["id"]] = True
                if want_masks:
                    full = masks.setdefault(p["id"], np.zeros((H, W), bool))
                    full[y0:y1, x0:x1] |= m
        return {
            "scores": scores, "hits": hits, "masks": masks, "boxes": boxes,
            "zone": (zx0, zy0, zx1, zy1), "small": small if want_masks else None,
            "torsos": [(r[0], r[1], r[2], r[3]) for r in regions] if mode == "person" else [],
        }


def render_preview(result, profiles, preview_mode):
    small = result["small"]
    H, W = small.shape[:2]
    if preview_mode == "pixelated":
        tiny = cv2.resize(small, (max(W // 16, 1), max(H // 16, 1)), interpolation=cv2.INTER_AREA)
        canvas = cv2.resize(tiny, (W, H), interpolation=cv2.INTER_NEAREST)
        canvas = (canvas * 0.6).astype(np.uint8)
    else:
        canvas = np.full((H, W, 3), 18, np.uint8)
    for p in profiles:
        m = result["masks"].get(p["id"])
        if m is not None:
            canvas[m] = p["_bgr"]
    zx0, zy0, zx1, zy1 = result["zone"]
    cv2.rectangle(canvas, (zx0, zy0), (zx1 - 1, zy1 - 1), (255, 255, 255), 1)
    for (x0, y0, x1, y1) in result["boxes"]:
        cv2.rectangle(canvas, (x0, y0), (x1, y1), (0, 200, 255), 1)
    for (x0, y0, x1, y1) in result["torsos"]:
        cv2.rectangle(canvas, (x0, y0), (x1, y1), (255, 255, 0), 1)
    ok, buf = cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return buf.tobytes() if ok else None


def dominant_color(image_bytes):
    """Suggest a profile color from an uploaded shirt photo (center crop k-means)."""
    img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None
    h, w = img.shape[:2]
    crop = img[h // 4: 3 * h // 4, w // 4: 3 * w // 4]
    px = cv2.resize(crop, (64, 64), interpolation=cv2.INTER_AREA).reshape(-1, 3).astype(np.float32)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
    _, labels, centers = cv2.kmeans(px, 3, None, crit, 3, cv2.KMEANS_PP_CENTERS)
    b, g, r = centers[np.bincount(labels.flatten()).argmax()].astype(int)
    return "#{:02X}{:02X}{:02X}".format(int(r), int(g), int(b))


class FrameGrabber(threading.Thread):
    """Continuously reads the stream and keeps ONLY the newest frame in RAM."""

    def __init__(self):
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.frame = None
        self.frame_id = 0
        self.status = "no camera URL set"
        self._want = ""

    def set_url(self, url):
        self._want = (url or "").strip()

    def run(self):
        cap, cur, fails = None, None, 0
        while True:
            want = self._want
            if want != cur:
                if cap is not None:
                    cap.release()
                cap, cur, fails = None, want, 0
                with self.lock:
                    self.frame = None
            if not cur:
                self.status = "no camera URL set"
                time.sleep(1)
                continue
            if cap is None:
                self.status = "connecting"
                cap = cv2.VideoCapture(cur, cv2.CAP_FFMPEG)
                if not cap.isOpened():
                    cap.release()
                    cap = None
                    self.status = "cannot open stream - retrying"
                    time.sleep(3)
                    continue
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                self.status = "streaming"
            ok, frame = cap.read()
            if not ok:
                fails += 1
                if fails > 25:
                    cap.release()
                    cap = None
                    with self.lock:
                        self.frame = None
                    self.status = "stream lost - reconnecting"
                    time.sleep(2)
                else:
                    time.sleep(0.05)
                continue
            fails = 0
            with self.lock:
                self.frame = frame
                self.frame_id += 1


class Vision(threading.Thread):
    def __init__(self, store, live):
        super().__init__(daemon=True)
        self.store, self.live = store, live
        self.grabber = FrameGrabber()
        self.analyzer = Analyzer()
        self.tracks = {}
        self._ver = -1
        self.profiles, self.settings = [], {}

    def _reload(self):
        if self.store.version == self._ver:
            return
        self._ver = self.store.version
        self.settings = self.store.get_settings()
        self.profiles = [prepare_profile(p) for p in self.store.list_profiles() if p["enabled"]]
        self.grabber.set_url(self.settings.get("camera_url", ""))

    def _update_tracks(self, hits):
        now = time.time()
        confirm = max(int(float(self.settings.get("confirm_frames", 3))), 1)
        hold = float(self.settings.get("hold_seconds", 4))
        active = []
        for p in self.profiles:
            t = self.tracks.setdefault(p["id"], {"hits": 0, "last": 0.0})
            if hits is not None:
                if hits.get(p["id"]):
                    t["hits"] += 1
                    if t["hits"] >= confirm:
                        t["last"] = now
                else:
                    t["hits"] = 0
            if t["last"] and now - t["last"] <= hold:
                active.append(p["id"])
        self.live.set_detected(active)

    def run(self):
        self.grabber.start()
        last_id, frames, t_fps = -1, 0, time.time()
        while True:
            try:
                self._reload()
                period = 1.0 / max(float(self.settings.get("target_fps", 6)), 0.5)
                t0 = time.time()
                with self.grabber.lock:
                    frame, fid = self.grabber.frame, self.grabber.frame_id
                self.live.camera_status = self.grabber.status
                if frame is None or fid == last_id:
                    self._update_tracks(None)
                    if frame is None:
                        self.live.scores = {}
                    time.sleep(0.05)
                    continue
                last_id = fid
                pmode = self.settings.get("preview_mode", "mask")
                want_preview = pmode != "off" and self.live.preview_wanted()
                res = self.analyzer.analyze(frame, self.profiles, self.settings, want_masks=want_preview)
                del frame
                self.live.scores = res["scores"]
                self._update_tracks(res["hits"])
                self.live.preview_jpeg = render_preview(res, self.profiles, pmode) if want_preview else None
                frames += 1
                if time.time() - t_fps >= 2:
                    self.live.fps = round(frames / (time.time() - t_fps), 1)
                    frames, t_fps = 0, time.time()
                time.sleep(max(0.0, period - (time.time() - t0)))
            except Exception as e:  # keep the loop alive no matter what
                self.live.camera_status = f"vision error: {e}"
                time.sleep(1)
