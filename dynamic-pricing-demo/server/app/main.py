import base64
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import onvif
from .live import LiveState
from .pricing import compute, money, tag_payload
from .store import PHOTO_DIR, Store
from .vision import Vision, dominant_color

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
STATIC = Path(__file__).parent / "static"

store = Store()
live = LiveState()
vision = Vision(store, live)


@asynccontextmanager
async def lifespan(_app):
    if os.environ.get("DISABLE_VISION") != "1":
        vision.start()
    yield


app = FastAPI(title="Shirt-Color Dynamic Pricing Demo", lifespan=lifespan)


# Optional basic auth for the admin GUI. The ESP endpoint stays open so the tag can poll it.
@app.middleware("http")
async def basic_auth(request: Request, call_next):
    path = request.url.path
    if ADMIN_PASSWORD and not (path.startswith("/api/display") or path == "/healthz"):
        ok = False
        hdr = request.headers.get("authorization", "")
        if hdr.startswith("Basic "):
            try:
                _, _, pw = base64.b64decode(hdr[6:]).decode().partition(":")
                ok = secrets.compare_digest(pw, ADMIN_PASSWORD)
            except Exception:
                ok = False
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="pricing-demo"'})
    return await call_next(request)


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ── What the ESP32 price tag polls ──────────────────────────────────────
@app.get("/api/display")
def display(product_id: int = 0):
    product = store.get_product(product_id) if product_id else store.display_product()
    if not product:
        return {"product": "NO PRODUCT", "unit": "", "price": "--", "was": "", "badge": "", "tag": ""}
    profile = live.current_profile(store.list_profiles())
    rule = store.get_rule(product["id"], profile["id"]) if profile else None
    show = store.get_settings().get("show_profile_on_tag") == "1"
    return tag_payload(product, profile, rule, show)


# ── Live state for the GUI ──────────────────────────────────────────────
@app.get("/api/state")
def state():
    oid, remaining = live.override()
    with live.lock:
        detected = list(live.detected)
    return {
        "camera_status": live.camera_status,
        "fps": live.fps,
        "scores": {str(k): round(v, 4) for k, v in dict(live.scores).items()},
        "detected": detected,
        "override_id": oid,
        "override_remaining": remaining,
        "mode": store.get_settings().get("mode"),
    }


@app.post("/api/simulate")
async def simulate(request: Request):
    d = await request.json()
    pid = d.get("profile_id")
    live.set_override(int(pid) if pid is not None else None, float(d.get("seconds", 15)))
    return {"ok": True}


@app.get("/api/preview.jpg")
def preview():
    live.request_preview()
    jpg = live.preview_jpeg
    if not jpg:
        return Response(status_code=204)
    return Response(jpg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


# ── Products ────────────────────────────────────────────────────────────
@app.get("/api/products")
def products():
    return store.list_products()


@app.post("/api/products")
async def add_product(request: Request):
    pid = store.create_product(await request.json())
    if len(store.list_products()) == 1:
        store.set_on_display(pid)
    return store.get_product(pid)


@app.put("/api/products/{pid}")
async def edit_product(pid: int, request: Request):
    if not store.update_product(pid, await request.json()):
        raise HTTPException(404)
    return store.get_product(pid)


@app.delete("/api/products/{pid}")
def del_product(pid: int):
    store.delete_product(pid)
    return {"ok": True}


# ── Shirt-color customer profiles ───────────────────────────────────────
@app.get("/api/profiles")
def profiles():
    return store.list_profiles()


@app.post("/api/profiles")
async def add_profile(request: Request):
    return store.get_profile(store.create_profile(await request.json()))


@app.put("/api/profiles/{pid}")
async def edit_profile(pid: int, request: Request):
    store.update_profile(pid, await request.json())
    return store.get_profile(pid)


@app.delete("/api/profiles/{pid}")
def del_profile(pid: int):
    p = store.get_profile(pid)
    if p and p["photo"]:
        (PHOTO_DIR / p["photo"]).unlink(missing_ok=True)
    store.delete_profile(pid)
    return {"ok": True}


@app.post("/api/profiles/{pid}/photo")
async def upload_photo(pid: int, file: UploadFile = File(...)):
    p = store.get_profile(pid)
    if not p:
        raise HTTPException(404)
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(413, "Photo too large (10 MB max)")
    suggested = dominant_color(data)
    if suggested is None:
        raise HTTPException(400, "Not an image")
    ext = Path(file.filename or "x.jpg").suffix.lower()
    ext = ext if ext in (".jpg", ".jpeg", ".png", ".webp") else ".jpg"
    name = f"profile{pid}_{uuid.uuid4().hex[:8]}{ext}"
    (PHOTO_DIR / name).write_bytes(data)
    if p["photo"]:
        (PHOTO_DIR / p["photo"]).unlink(missing_ok=True)
    store.update_profile(pid, {"photo": name})
    return {"photo": name, "suggested_hex": suggested}


# ── Pricing rules (product x profile) ───────────────────────────────────
@app.get("/api/rules")
def rules(product_id: int):
    product = store.get_product(product_id)
    if not product:
        raise HTTPException(404)
    out = []
    for r in store.list_rules(product_id):
        price, was = compute(product, r)
        out.append({**r, "final": money(price), "was": money(was) if was else ""})
    return out


@app.put("/api/rules/{product_id}/{profile_id}")
async def set_rule(product_id: int, profile_id: int, request: Request):
    store.set_rule(product_id, profile_id, await request.json())
    return {"ok": True}


@app.delete("/api/rules/{product_id}/{profile_id}")
def del_rule(product_id: int, profile_id: int):
    store.delete_rule(product_id, profile_id)
    return {"ok": True}


# ── Settings + camera helpers ───────────────────────────────────────────
@app.get("/api/settings")
def get_settings():
    return store.get_settings()


@app.put("/api/settings")
async def put_settings(request: Request):
    store.set_settings(await request.json())
    return store.get_settings()


@app.post("/api/onvif/discover")
async def onvif_discover(request: Request):
    d = await request.json()
    try:
        streams = onvif.find_streams(d.get("host", ""), d.get("port", 80), d.get("user", ""), d.get("password", ""))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    return {"streams": streams}


app.mount("/photos", StaticFiles(directory=str(PHOTO_DIR)), name="photos")
app.mount("/", StaticFiles(directory=str(STATIC), html=True), name="static")
