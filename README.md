# Shirt-Color Dynamic Pricing — Proof of Concept

> **Demo / awareness project.** This shows how in-store "surveillance pricing" *could* work:
> a camera sees you coming, the store decides what you'll tolerate paying, and the shelf
> tag changes before you get there. It identifies people **only by shirt color**, never
> faces, and **stores nothing** about who walked by. Some demo profiles (fake employee
> discount, SNAP markup) are deliberately exploitative to show what this tech enables —
> doing that in a real store would be deceptive and, in many places, illegal.

```
 ONVIF camera ──RTSP──▶ Docker (OpenCV + FastAPI + web GUI) ◀──HTTP poll 1s── ESP32-S3-Box-3 (e-ink style tag)
```

## Demo customers (pre-loaded)

| Profile | Shirt | Story | Tag shows |
|---|---|---|---|
| A | Red | Price-sensitive, but has the rewards app and loves apples | $1.00 + 20% = **$1.20** |
| B | Blue | Never looks at prices | **$2.00** |
| C | Gray | Knows apples are ~50¢ | **$0.75** |
| D | Hi-vis neon green | Employee (20% "discount") + on SNAP | $1.00 +20% +10% = **$1.32**, shown as ~~$1.65~~ "EMPLOYEE PRICE -20%" |
| E | anything else | — | **$0.50** (product default) |

Everything is editable in the GUI: customers, shirt colors (color picker or upload a shirt
photo — it auto-picks the dominant color), products, base prices, markups, fake "was" discounts and badges.
Price changes hit the tag within ~1 second.

## 1. Run the server (Docker)

```bash
cp .env.example .env        # optional: set CAMERA_URL / ADMIN_PASSWORD
docker compose up -d --build
```
Open `http://<docker-host-ip>:8080`.

## 2. Connect the camera (IPC_523128M5MP_V2, ONVIF)

**Camera & Settings → Find camera stream (ONVIF)**: enter the camera IP, ONVIF port
(usually 80, 8899 or 8000), username and password → **Find streams** → **Use** the
lowest-resolution one → **Save settings**. Or paste an RTSP URL directly.

Use the sub-stream. 5 MP adds nothing for shirt colors and costs a lot of CPU.

## 3. Flash the ESP32-S3-Box-3

1. In ESPHome, add the keys from `esphome/secrets.yaml.example` to your `secrets.yaml`
   (your existing API key goes into `api_encryption_key`).
2. In `esphome/esp32-s3box-3-pricetag.yaml`, set `pricing_server` to the Docker host's IP:port.
3. Install. The tag shows **CONNECTING → ONLINE**. **OFFLINE** = it can't reach Docker
   (wrong IP, firewall on port 8080, or a different network/VLAN).

## 4. Tune detection (Live tab)

- Point the camera at the aisle approach; set the **aisle zone** (`x0,y0,x1,y1` as 0–1
  fractions, e.g. `0.2,0.3,0.8,1` = middle-bottom of the frame).
- Walk in wearing each shirt and watch the bars. A bar crossing the line = detection.
  Raise **Trigger fraction** if it fires too easily, lower it if it never fires.
- **Modes:** `motion` (default) only counts *moving* pixels, so a red sign or gray wall
  won't trigger it. `person` uses OpenCV's HOG detector and reads the torso color — better
  for full-body views. `zone` counts every pixel (only for a clean backdrop).
- **Gray** is the hardest color (floors, walls, shadows): keep `motion` mode, narrow the
  brightness ±, or give gray the lowest priority.
- **Hold price** controls how long the tag keeps the profile price after the shirt leaves.
- Use **Simulate a customer** for filming or for testing without a camera.

## Privacy design

- Frames are processed in RAM and discarded; the newest frame is the only one held.
- No detections, timestamps, counts or images are logged or stored. Restart = blank slate.
- The GUI preview is either a **color mask** (no camera image at all) or a **heavily
  pixelated** frame, and only exists while the Live tab is open. Faces are never shown.
- The database (`./data`) only holds config you typed in plus shirt reference photos you upload.
- Secrets live in `.env` and ESPHome `secrets.yaml` — both are git-ignored.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/display?product_id=0` | What the tag shows (ESP polls this) |
| GET | `/api/state` | Camera status, scores, active profile |
| POST | `/api/simulate` | `{"profile_id": 3, "seconds": 15}` (`null` clears) |
| CRUD | `/api/products`, `/api/profiles`, `/api/rules/{product}/{profile}` | Config |
| POST | `/api/profiles/{id}/photo` | Upload shirt photo → suggested color |
| GET/PUT | `/api/settings` | Camera + detection settings |
| POST | `/api/onvif/discover` | `{"host","port","user","password"}` → RTSP URLs |
