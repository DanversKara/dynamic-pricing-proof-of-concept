"""SQLite-backed configuration store.

Only *configuration* lives here (products, shirt-color profiles, pricing
rules, settings). Camera frames and detections are NEVER written to the
database or disk - they live in memory only (see vision.py / live.py).
"""
import json
import os
import sqlite3
import threading
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
PHOTO_DIR = DATA_DIR / "photos"

SCHEMA = """
CREATE TABLE IF NOT EXISTS products(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    unit TEXT NOT NULL DEFAULT 'each',
    default_price REAL NOT NULL,
    on_display INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS profiles(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    color_hex TEXT NOT NULL DEFAULT '#888888',
    hue_tol INTEGER NOT NULL DEFAULT 10,
    sat_min INTEGER NOT NULL DEFAULT 90,
    val_min INTEGER NOT NULL DEFAULT 60,
    gray_sat_max INTEGER NOT NULL DEFAULT 40,
    gray_val_tol INTEGER NOT NULL DEFAULT 40,
    min_fraction REAL NOT NULL DEFAULT 0.012,
    priority INTEGER NOT NULL DEFAULT 10,
    enabled INTEGER NOT NULL DEFAULT 1,
    photo TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS rules(
    product_id INTEGER NOT NULL,
    profile_id INTEGER NOT NULL,
    base_price REAL,
    markups TEXT NOT NULL DEFAULT '[]',
    shown_discount_pct REAL NOT NULL DEFAULT 0,
    badge TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(product_id, profile_id)
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

DEFAULT_SETTINGS = {
    "camera_url": os.environ.get("CAMERA_URL", ""),
    "mode": "motion",            # motion | person | zone
    "zone": "0,0,1,1",           # x0,y0,x1,y1 as fractions of the frame
    "hold_seconds": "4",         # keep a profile price this long after the shirt leaves
    "confirm_frames": "3",       # consecutive hits needed before switching price
    "target_fps": "6",
    "person_torso_fraction": "0.30",
    "preview_mode": "mask",      # off | mask | pixelated
    "show_profile_on_tag": "1",
}

PROFILE_FIELDS = {
    "code": str, "name": str, "description": str, "color_hex": str,
    "hue_tol": int, "sat_min": int, "val_min": int, "gray_sat_max": int,
    "gray_val_tol": int, "min_fraction": float, "priority": int,
    "enabled": int, "photo": str,
}


class Store:
    def __init__(self, db_path=None):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        PHOTO_DIR.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(db_path or DATA_DIR / "pricing.db"), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.version = 1
        self._seed()

    # ── helpers ──────────────────────────────────────────────────────────
    def _exec(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            self.version += 1
            return cur

    def _all(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def _one(self, sql, args=()):
        rows = self._all(sql, args)
        return rows[0] if rows else None

    # ── demo seed data ───────────────────────────────────────────────────
    def _seed(self):
        if self._one("SELECT id FROM products LIMIT 1"):
            return
        apple = self.create_product({"name": "Apple", "unit": "each", "default_price": 0.50})
        self.set_on_display(apple)
        seeds = [
            dict(code="A", name="Red shirt", color_hex="#C62828", hue_tol=8, sat_min=120, val_min=60, priority=30,
                 description="Price-sensitive; often puts items back at checkout. Has the rewards app and loves this product.",
                 rule=dict(base_price=1.00, markups=[{"label": "Rewards app: loves this product", "percent": 20}])),
            dict(code="B", name="Blue shirt", color_hex="#1565C0", hue_tol=12, sat_min=100, val_min=50, priority=20,
                 description="High spender; never looks at prices.",
                 rule=dict(base_price=2.00)),
            dict(code="C", name="Gray shirt", color_hex="#808080", gray_sat_max=40, gray_val_tol=40, priority=10,
                 description="Buys this apple every time and knows it is usually about 50 cents.",
                 rule=dict(base_price=0.75)),
            dict(code="D", name="Hi-vis neon green vest", color_hex="#C6FF00", hue_tol=12, sat_min=120, val_min=120, priority=40,
                 description="Store employee (20% staff discount) and on food stamps.",
                 rule=dict(base_price=1.00,
                           markups=[{"label": "Claw back employee discount", "percent": 20},
                                    {"label": "SNAP / food stamps", "percent": 10}],
                           shown_discount_pct=20, badge="EMPLOYEE PRICE -20%")),
        ]
        for s in seeds:
            rule = s.pop("rule")
            pid = self.create_profile(s)
            self.set_rule(apple, pid, rule)

    # ── products ─────────────────────────────────────────────────────────
    def list_products(self):
        return self._all("SELECT * FROM products ORDER BY id")

    def get_product(self, pid):
        return self._one("SELECT * FROM products WHERE id=?", (pid,))

    def display_product(self):
        return (self._one("SELECT * FROM products WHERE on_display=1 ORDER BY id LIMIT 1")
                or self._one("SELECT * FROM products ORDER BY id LIMIT 1"))

    def create_product(self, d):
        cur = self._exec("INSERT INTO products(name, unit, default_price) VALUES(?,?,?)",
                         (str(d.get("name") or "Product"), str(d.get("unit") or "each"),
                          float(d.get("default_price") or 0)))
        return cur.lastrowid

    def update_product(self, pid, d):
        cur = self.get_product(pid)
        if not cur:
            return False
        self._exec("UPDATE products SET name=?, unit=?, default_price=? WHERE id=?",
                   (str(d.get("name", cur["name"])), str(d.get("unit", cur["unit"])),
                    float(d.get("default_price", cur["default_price"])), pid))
        if d.get("on_display"):
            self.set_on_display(pid)
        return True

    def set_on_display(self, pid):
        with self.lock:
            self.db.execute("UPDATE products SET on_display = CASE WHEN id=? THEN 1 ELSE 0 END", (pid,))
            self.db.commit()
            self.version += 1

    def delete_product(self, pid):
        self._exec("DELETE FROM rules WHERE product_id=?", (pid,))
        self._exec("DELETE FROM products WHERE id=?", (pid,))

    # ── profiles ─────────────────────────────────────────────────────────
    def list_profiles(self):
        return self._all("SELECT * FROM profiles ORDER BY priority DESC, id")

    def get_profile(self, pid):
        return self._one("SELECT * FROM profiles WHERE id=?", (pid,))

    def _clean_profile(self, d):
        out = {}
        for k, typ in PROFILE_FIELDS.items():
            if k in d and d[k] is not None:
                try:
                    out[k] = typ(d[k]) if typ is not int else int(float(d[k]))
                except (TypeError, ValueError):
                    pass
        if "color_hex" in out:
            h = out["color_hex"].strip().lstrip("#")
            out["color_hex"] = "#" + (h[:6].upper() if len(h) >= 6 else "888888")
        return out

    def create_profile(self, d):
        d = self._clean_profile({"name": "New profile", **d})
        keys = list(d)
        cur = self._exec(f"INSERT INTO profiles({','.join(keys)}) VALUES({','.join('?' * len(keys))})",
                         [d[k] for k in keys])
        return cur.lastrowid

    def update_profile(self, pid, d):
        d = self._clean_profile(d)
        if not d:
            return
        self._exec(f"UPDATE profiles SET {', '.join(k + '=?' for k in d)} WHERE id=?", [*d.values(), pid])

    def delete_profile(self, pid):
        self._exec("DELETE FROM rules WHERE profile_id=?", (pid,))
        self._exec("DELETE FROM profiles WHERE id=?", (pid,))

    # ── pricing rules ────────────────────────────────────────────────────
    def list_rules(self, product_id=None):
        sql, args = "SELECT * FROM rules", ()
        if product_id is not None:
            sql, args = sql + " WHERE product_id=?", (product_id,)
        rows = self._all(sql, args)
        for r in rows:
            r["markups"] = json.loads(r["markups"] or "[]")
        return rows

    def get_rule(self, product_id, profile_id):
        r = self._one("SELECT * FROM rules WHERE product_id=? AND profile_id=?", (product_id, profile_id))
        if r:
            r["markups"] = json.loads(r["markups"] or "[]")
        return r

    def set_rule(self, product_id, profile_id, d):
        markups = [{"label": str(m.get("label", "")), "percent": float(m.get("percent") or 0)}
                   for m in (d.get("markups") or []) if isinstance(m, dict)]
        base = d.get("base_price")
        base = None if base in (None, "") else float(base)
        self._exec("""INSERT INTO rules(product_id, profile_id, base_price, markups, shown_discount_pct, badge)
                      VALUES(?,?,?,?,?,?)
                      ON CONFLICT(product_id, profile_id) DO UPDATE SET
                        base_price=excluded.base_price, markups=excluded.markups,
                        shown_discount_pct=excluded.shown_discount_pct, badge=excluded.badge""",
                   (product_id, profile_id, base, json.dumps(markups),
                    float(d.get("shown_discount_pct") or 0), str(d.get("badge") or "")))

    def delete_rule(self, product_id, profile_id):
        self._exec("DELETE FROM rules WHERE product_id=? AND profile_id=?", (product_id, profile_id))

    # ── settings ─────────────────────────────────────────────────────────
    def get_settings(self):
        s = dict(DEFAULT_SETTINGS)
        for r in self._all("SELECT key, value FROM settings"):
            s[r["key"]] = r["value"]
        return s

    def set_settings(self, d):
        for k, v in d.items():
            if k in DEFAULT_SETTINGS:
                self._exec("INSERT INTO settings(key, value) VALUES(?,?) "
                           "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))
