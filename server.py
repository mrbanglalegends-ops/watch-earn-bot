import hashlib, hmac, json, os, sqlite3, time
from urllib.parse import parse_qsl
from flask import Flask, request, jsonify, send_from_directory, abort

BOT_TOKEN = os.environ["BOT_TOKEN"]          # BotFather থেকে পাওয়া টোকেন
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))  # আপনার টেলিগ্রাম user id

REWARD = 0.002          # প্রতি অ্যাড দেখলে ইউজার যত পাবে ($) - Monetag থেকে আপনার আয়ের চেয়ে কম রাখুন
COOLDOWN = 35           # দুই অ্যাডের মাঝে কমপক্ষে কত সেকেন্ড
DAILY_LIMIT = 30        # দিনে সর্বোচ্চ কয়টা অ্যাড
MIN_WITHDRAW = 1.0      # সর্বনিম্ন উইথড্র ($)

app = Flask(__name__, static_folder=".")
DB = "data.db"


def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


with db() as c:
    c.execute("""CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY, name TEXT, balance REAL DEFAULT 0,
        last_ad REAL DEFAULT 0, day TEXT DEFAULT '', day_count INTEGER DEFAULT 0,
        total_ads INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS withdrawals(
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount REAL,
        method TEXT, account TEXT, status TEXT DEFAULT 'pending', created REAL)""")


def verify_init_data(init_data):
    """টেলিগ্রামের initData সঠিক কিনা যাচাই করে - ভুয়া রিকোয়েস্ট আটকায়"""
    data = dict(parse_qsl(init_data, keep_blank_values=True))
    received = data.pop("hash", None)
    if not received:
        return None
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received):
        return None
    if time.time() - int(data.get("auth_date", 0)) > 86400:
        return None
    return json.loads(data["user"])


def get_user():
    u = verify_init_data(request.json.get("initData", ""))
    if not u:
        abort(401)
    with db() as c:
        c.execute("INSERT OR IGNORE INTO users(id,name) VALUES(?,?)",
                  (u["id"], u.get("first_name", "")))
        return dict(c.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone())


def state(u):
    today = time.strftime("%Y-%m-%d")
    used = u["day_count"] if u["day"] == today else 0
    return {"balance": round(u["balance"], 4), "left": DAILY_LIMIT - used,
            "total_ads": u["total_ads"], "reward": REWARD,
            "min_withdraw": MIN_WITHDRAW, "user_id": u["id"]}


@app.get("/")
def index():
    return send_from_directory(".", "index.html")


@app.post("/api/me")
def me():
    return jsonify(state(get_user()))


@app.post("/api/reward")
def reward():
    u = get_user()
    now, today = time.time(), time.strftime("%Y-%m-%d")
    count = u["day_count"] if u["day"] == today else 0
    if now - u["last_ad"] < COOLDOWN:
        return jsonify(error="একটু অপেক্ষা করে আবার চেষ্টা করুন"), 429
    if count >= DAILY_LIMIT:
        return jsonify(error="আজকের লিমিট শেষ, কাল আবার আসুন"), 429
    with db() as c:
        c.execute("""UPDATE users SET balance=balance+?, last_ad=?, day=?, day_count=?,
                     total_ads=total_ads+1 WHERE id=?""",
                  (REWARD, now, today, count + 1, u["id"]))
        u = dict(c.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone())
    return jsonify(state(u))


@app.post("/api/withdraw")
def withdraw():
    u = get_user()
    method = request.json.get("method", "")[:30]
    account = request.json.get("account", "")[:60]
    if u["balance"] < MIN_WITHDRAW:
        return jsonify(error=f"সর্বনিম্ন উইথড্র ${MIN_WITHDRAW}"), 400
    if not method or not account:
        return jsonify(error="মেথড ও নম্বর দিন"), 400
    with db() as c:
        c.execute("INSERT INTO withdrawals(user_id,amount,method,account,created) VALUES(?,?,?,?,?)",
                  (u["id"], u["balance"], method, account, time.time()))
        c.execute("UPDATE users SET balance=0 WHERE id=?", (u["id"],))
        u = dict(c.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone())
    return jsonify(state(u))


@app.get("/admin/withdrawals")
def admin_list():
    # সহজ অ্যাডমিন ভিউ: /admin/withdrawals?key=আপনার_BOT_TOKEN এর শেষ ১০ অক্ষর
    if request.args.get("key") != BOT_TOKEN[-10:]:
        abort(403)
    with db() as c:
        rows = c.execute("SELECT * FROM withdrawals WHERE status='pending'").fetchall()
    return jsonify([dict(r) for r in rows])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
