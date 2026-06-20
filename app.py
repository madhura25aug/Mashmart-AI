"""
app.py — Mashmart
Main server: Flask routing, concurrent mock scraping, covert 10% margin engine,
mock AI image inference, session-based auth, checkout + order persistence.
"""

import os
import time
import uuid
import random
import hashlib
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import (Flask, render_template, request, jsonify,
                   session, redirect, url_for)
import database as db

app = Flask(__name__, template_folder=".")

# ── Load .env key manually without external package dependency ──────────────
def load_env_key(key_name):
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith(key_name):
                    parts = line.strip().split("=", 1)
                    if len(parts) == 2:
                        val = parts[1].strip()
                        # strip quotes if present
                        if val.startswith('"') and val.endswith('"'):
                            val = val[1:-1]
                        elif val.startswith("'") and val.endswith("'"):
                            val = val[1:-1]
                        return val
    return os.environ.get(key_name)

app.secret_key = "mashmart_secret_2024_secure_key_xyz"

# ── Bootstrap DB on startup ──────────────────────────────────────────────────
db.init_db()


# ════════════════════════════════════════════════════════════════════════════
#  COVERT MARGIN ENGINE
#  The 10% markup is applied ONLY here on the server before any payload
#  is serialised and sent to the frontend. The base prices never leave Python.
# ════════════════════════════════════════════════════════════════════════════
def apply_markup(product: dict) -> dict:
    """Return a NEW dict with marked-up prices; raw bases are stripped."""
    marked = dict(product)
    marked["price_amazon"]   = int(round(product["base_amazon"]   * 1.10))
    marked["price_flipkart"] = int(round(product["base_flipkart"] * 1.10))
    marked["price_myntra"]   = int(round(product["base_myntra"]   * 1.10))
    # Strip raw fields so they are never serialised to client
    for k in ("base_amazon", "base_flipkart", "base_myntra"):
        marked.pop(k, None)
    return marked


def _mock_scrape_platform(platform: str, items: list) -> list:
    """
    Simulates a real scraper hitting a platform endpoint.
    Adds a tiny random delay to mimic network latency.
    """
    time.sleep(random.uniform(0.05, 0.15))  # mock network I/O
    return [(platform, item) for item in items]


# ════════════════════════════════════════════════════════════════════════════
#  AUTH ROUTES
# ════════════════════════════════════════════════════════════════════════════

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        data = request.get_json(silent=True) or request.form
        username = data.get("username", "").strip()
        password = data.get("password", "").strip()
        user = db.get_user_by_credentials(username, password)
        if user:
            session["user_id"]   = user["id"]
            session["username"]  = user["username"]
            session["role"]      = user["role"]
            session["email"]     = user["email"]
            if request.is_json:
                dest = "/admin" if user["role"] == "admin" else "/"
                return jsonify({"ok": True, "redirect": dest, "role": user["role"]})
            return redirect("/admin" if user["role"] == "admin" else "/")
        else:
            if request.is_json:
                return jsonify({"ok": False, "error": "Invalid credentials"}), 401
            error = "Invalid credentials"
    return render_template("login.html", error=error)


@app.route("/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or request.form
    uid = db.register_user(
        data.get("username", "").strip(),
        data.get("email", "").strip(),
        data.get("password", "").strip(),
        data.get("phone", ""),
        data.get("address", ""),
        data.get("pincode", ""),
    )
    if uid:
        return jsonify({"ok": True, "message": "Account created! Please login."})
    return jsonify({"ok": False, "error": "Username or email already exists."}), 409


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


# ════════════════════════════════════════════════════════════════════════════
#  CUSTOMER STOREFRONT
# ════════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template("index.html",
                           username=session.get("username"),
                           logged_in=bool(session.get("user_id")))


@app.route("/api/search")
def api_search():
    """
    Parallel mock scraping across three platform workers.
    The 10% markup is injected here before JSON serialisation.
    """
    query    = request.args.get("q", "").strip()
    category = request.args.get("category", "All").strip()

    raw_items = db.get_all_products(
        category=category if category != "All" else None,
        search=query if query else None
    )

    # ── Run 3 mock platform scrapers concurrently ────────────────────────────
    results = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(_mock_scrape_platform, "amazon",   raw_items): "amazon",
            pool.submit(_mock_scrape_platform, "flipkart", raw_items): "flipkart",
            pool.submit(_mock_scrape_platform, "myntra",   raw_items): "myntra",
        }
        seen_ids = set()
        for future in as_completed(futures):
            for _, item in future.result():
                if item["id"] not in seen_ids:
                    seen_ids.add(item["id"])
                    results.append(apply_markup(item))

    # Sort by lowest marked-up Amazon price for best-deal prominence
    results.sort(key=lambda x: x["price_amazon"])
    return jsonify({"products": results, "count": len(results)})


@app.route("/api/upload-image", methods=["POST"])
def upload_image():
    """
    Real AI Vision endpoint using Gemini API.
    Reads image bytes in-memory and queries the model.
    Falls back to mock logic if the API key is missing or fails.
    """
    if "image" not in request.files:
        return jsonify({"error": "No image provided"}), 400

    file   = request.files["image"]
    _bytes = file.read()          # read into memory; no disk I/O

    api_key = load_env_key("GEMINI_API_KEY")
    if not api_key:
        return _fallback_upload_image(_bytes)

    # Detect extension for mimetype
    ext = file.filename.split(".")[-1].lower() if "." in file.filename else "jpeg"
    mime_type = f"image/{ext}" if ext in ["png", "jpeg", "jpg", "webp"] else "image/jpeg"
    
    image_b64 = base64_encode_bytes = hashlib.md5(_bytes).hexdigest() # just a placeholder, let's do real b64
    import base64
    image_b64 = base64.b64encode(_bytes).decode("utf-8")
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": (
                            "Analyze this product image. Identify what product it is, categorize it into one of these exact categories: 'Fashion', 'Mobile', 'Beauty', 'Electronics', 'Food'. Provide a concise search query (1-4 words) that can be used to find this product or similar products in a shopping store. Output your response ONLY as a valid JSON object with the keys: 'category' (string, matching one of the five categories), 'query' (string, the search query), 'labels' (array of strings, 3-4 descriptive keywords), and 'confidence' (float, between 0.0 and 1.0). Do not include any markdown formatting like ```json or ``` in the output."
                        )
                    },
                    {
                        "inlineData": {
                            "mimeType": mime_type,
                            "data": image_b64
                        }
                    }
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json"
        }
    }
    
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            text_response = res_data["candidates"][0]["content"]["parts"][0]["text"].strip()
            result = json.loads(text_response)
            return jsonify({
                "query": result.get("query", "product"),
                "category": result.get("category", "Fashion"),
                "confidence": result.get("confidence", 0.95),
                "labels": result.get("labels", ["Product", "Retail"]),
            })
    except Exception as e:
        print(f"Vision Gemini API error: {e}. Falling back to mock calculation.")
        return _fallback_upload_image(_bytes)

def _fallback_upload_image(_bytes):
    # Hash image bytes to deterministically pick a category
    digest   = hashlib.md5(_bytes).hexdigest()
    cats     = ["Fashion", "Mobile", "Beauty", "Electronics", "Food"]
    category = cats[int(digest[:2], 16) % len(cats)]

    sample_queries = {
        "Fashion":     "Sneakers",
        "Mobile":      "iPhone",
        "Beauty":      "Foundation",
        "Electronics": "Headphones",
        "Food":        "Tea",
    }
    return jsonify({
        "query":    sample_queries[category],
        "category": category,
        "confidence": round(random.uniform(0.87, 0.99), 2),
        "labels":  [category, "Product", "Retail"],
    })


def get_mock_ai_response(user_message, products):
    import random
    msg = user_message.lower()
    
    # 1. Look for category mentions
    categories = ["fashion", "mobile", "beauty", "electronics", "food"]
    found_cat = None
    for cat in categories:
        if cat in msg:
            found_cat = cat.capitalize()
            break
            
    matching_products = []
    if found_cat:
        matching_products = [p for p in products if p["category"].lower() == found_cat.lower()]
    else:
        # Find products whose titles match words in user_message
        words = [w for w in msg.split() if len(w) > 3]
        for p in products:
            title_lower = p["title"].lower()
            if any(word in title_lower for word in words):
                matching_products.append(p)
                
    if not matching_products:
        # Check for cheapest / premium keywords
        if "cheap" in msg or "best deal" in msg or "lowest" in msg or "offer" in msg:
            sorted_prods = sorted(products, key=lambda x: min(x["price_amazon"], x["price_flipkart"], x["price_myntra"]))
            matching_products = sorted_prods[:3]
        elif "expensive" in msg or "premium" in msg or "costly" in msg:
            sorted_prods = sorted(products, key=lambda x: max(x["price_amazon"], x["price_flipkart"], x["price_myntra"]), reverse=True)
            matching_products = sorted_prods[:3]
        else:
            # Try general random selection
            matching_products = random.sample(products, min(3, len(products)))
            
    if not matching_products:
        return "I'm sorry, I couldn't find any matching products in our catalog. We offer a wide range of Fashion, Mobiles, Beauty products, Electronics, and Food items. Could you try asking about one of these?"
        
    reply = "Here are the best deals I found in the Mashmart catalog:\n\n"
    for p in matching_products[:3]:
        prices = {
            "Amazon": p["price_amazon"],
            "Flipkart": p["price_flipkart"],
            "Myntra": p["price_myntra"]
        }
        best_platform = min(prices, key=prices.get)
        best_price = prices[best_platform]
        
        reply += f"• **{p['title']}** ({p['category']}): Best deal is on **{best_platform}** at **₹{best_price:,}**. "
        other_prices = [f"{plat}: ₹{price:,}" for plat, price in prices.items() if plat != best_platform]
        reply += f"(Other prices: {', '.join(other_prices)})\n"
        
    reply += "\nLet me know if you would like help checking out any of these!"
    return reply


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """
    Real AI Shopping Assistant using Gemini API.
    Provides users with product suggestions, comparisons, and general assistance
    guided by the real SQLite product inventory catalog.
    """
    data = request.get_json(silent=True) or {}
    user_message = data.get("message", "").strip()
    if not user_message:
        return jsonify({"reply": "Hello! How can I help you find the best deal today?"})

    # Get all database products to feed as context
    products = db.get_all_products()
    products_context = []
    for p in products:
        marked = apply_markup(p)
        products_context.append({
            "id": marked["id"],
            "title": marked["title"],
            "category": marked["category"],
            "price_amazon": marked["price_amazon"],
            "price_flipkart": marked["price_flipkart"],
            "price_myntra": marked["price_myntra"],
            "rating": marked["rating"],
            "brand": marked.get("brand", ""),
            "description": marked.get("description", ""),
            "specifications": marked.get("specifications", "{}"),
            "reviews_count": marked.get("reviews_count", 0)
        })

    system_instruction = (
        "You are Mashmart AI, a premium, witty shopping assistant. "
        "Your task is to help users find the best deals and recommend products from our catalog. "
        "Here is the exact list of products currently available in the store (each product has marked-up prices for Amazon, Flipkart, and Myntra):\n"
        f"{json.dumps(products_context, indent=2)}\n\n"
        "Rules:\n"
        "1. Recommend ONLY products that exist in the catalog above. If a user asks for something we don't have, politely explain we don't carry it, but suggest the closest alternative we DO have.\n"
        "2. Compare prices between Amazon, Flipkart, and Myntra for the products. Point out which platform has the lowest price.\n"
        "3. Provide helpful, conversational shopping advice. Keep answers relatively concise (max 3-4 sentences).\n"
        "4. Return a clean, plain text response."
    )

    api_key = load_env_key("GEMINI_API_KEY")
    if not api_key:
        return jsonify({"reply": get_mock_ai_response(user_message, products_context)})

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": f"{system_instruction}\n\nUser Query: {user_message}"}]
            }
        ]
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            reply = res_data["candidates"][0]["content"]["parts"][0]["text"].strip()
            return jsonify({"reply": reply})
    except Exception as e:
        print(f"Chat Gemini API error: {e}. Falling back to local helper.")
        return jsonify({"reply": get_mock_ai_response(user_message, products_context)})



# ════════════════════════════════════════════════════════════════════════════
#  CHECKOUT & ORDER ROUTES
# ════════════════════════════════════════════════════════════════════════════

# Pincode lookup table (city, state) used by checkout auto-fill
PINCODE_MAP = {
    "560001": ("Bengaluru", "Karnataka"),
    "110001": ("New Delhi", "Delhi"),
    "400001": ("Mumbai", "Maharashtra"),
    "600001": ("Chennai", "Tamil Nadu"),
    "700001": ("Kolkata", "West Bengal"),
    "500001": ("Hyderabad", "Telangana"),
    "380001": ("Ahmedabad", "Gujarat"),
    "411001": ("Pune", "Maharashtra"),
    "302001": ("Jaipur", "Rajasthan"),
    "226001": ("Lucknow", "Uttar Pradesh"),
}


@app.route("/api/pincode/<pincode>")
def pincode_lookup(pincode):
    """Instant pincode → city/state lookup for checkout form auto-fill."""
    info = PINCODE_MAP.get(pincode.strip())
    if info:
        return jsonify({"found": True, "city": info[0], "state": info[1]})
    # Generic fallback for unknown pincodes
    return jsonify({"found": False, "city": "", "state": ""})


@app.route("/api/checkout", methods=["POST"])
def checkout():
    """
    Sandbox checkout processor.
    Receives order data, calculates the covert margin, and persists to DB.
    """
    data = request.get_json()

    customer_name = data.get("name", "")
    phone         = data.get("phone", "")
    address_parts = [
        data.get("address", ""),
        data.get("city", ""),
        data.get("state", ""),
        data.get("pincode", ""),
    ]
    full_address = ", ".join(p for p in address_parts if p)

    cart_items = data.get("cart_items", [])
    user_id = session.get("user_id")
    order_id = "MM-" + str(uuid.uuid4()).upper()[:12]

    if cart_items:
        total_price = 0
        product_summaries = []
        platforms = set()
        
        for item in cart_items:
            title = item.get("product_title", "")
            plat = item.get("chosen_platform", "amazon")
            price = int(item.get("price", 0))
            qty = int(item.get("quantity", 1))
            
            subtotal = price * qty
            total_price += subtotal
            product_summaries.append(f"{title} ({qty}x)")
            platforms.add(plat.capitalize())
            
            item_base_price = int(round(price / 1.10))
            item_margin = price - item_base_price
            total_item_margin = item_margin * qty
            
            db.save_order(
                f"{order_id}-{uuid.uuid4().hex[:4].upper()}",
                title, plat, subtotal, total_item_margin,
                customer_name, phone, full_address
            )
            
        if user_id:
            db.clear_user_cart(user_id)
            
        session["last_order"] = {
            "order_id": order_id,
            "product": ", ".join(product_summaries),
            "platform": "/".join(platforms),
            "price": total_price,
            "name": customer_name
        }
    else:
        product_title   = data.get("product_title", "")
        chosen_platform = data.get("chosen_platform", "amazon")
        marked_price    = int(data.get("marked_price", 0))

        base_price    = int(round(marked_price / 1.10))
        margin_amount = marked_price - base_price

        db.save_order(
            order_id, product_title, chosen_platform,
            marked_price, margin_amount,
            customer_name, phone, full_address
        )

        session["last_order"] = {
            "order_id":    order_id,
            "product":     product_title,
            "platform":    chosen_platform,
            "price":       marked_price,
            "name":        customer_name,
        }

    return jsonify({"ok": True, "order_id": order_id})


# ════════════════════════════════════════════════════════════════════════════
#  CART MANAGEMENT ROUTES
# ════════════════════════════════════════════════════════════════════════════

@app.route("/api/cart", methods=["GET"])
def get_cart():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"ok": True, "cart": []})
    cart_items = db.get_user_cart(user_id)
    return jsonify({"ok": True, "cart": cart_items})

@app.route("/api/cart/add", methods=["POST"])
def add_cart_item():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"ok": True})
    data = request.get_json() or {}
    product_title = data.get("product_title", "")
    chosen_platform = data.get("chosen_platform", "amazon")
    price = int(data.get("price", 0))
    image_url = data.get("image_url", "")
    quantity = int(data.get("quantity", 1))
    
    db.add_to_user_cart(user_id, product_title, chosen_platform, price, image_url, quantity)
    return jsonify({"ok": True, "cart": db.get_user_cart(user_id)})

@app.route("/api/cart/update", methods=["POST"])
def update_cart_item():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"ok": True})
    data = request.get_json() or {}
    item_id = int(data.get("item_id", 0))
    quantity = int(data.get("quantity", 1))
    
    db.update_cart_item_quantity(user_id, item_id, quantity)
    return jsonify({"ok": True, "cart": db.get_user_cart(user_id)})

@app.route("/api/cart/remove", methods=["POST"])
def remove_cart_item():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"ok": True})
    data = request.get_json() or {}
    item_id = int(data.get("item_id", 0))
    
    db.remove_from_cart(user_id, item_id)
    return jsonify({"ok": True, "cart": db.get_user_cart(user_id)})

@app.route("/api/cart/sync", methods=["POST"])
def sync_cart():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"ok": False, "error": "User not logged in"}), 401
    data = request.get_json() or {}
    guest_items = data.get("cart", [])
    
    db.sync_guest_cart(user_id, guest_items)
    return jsonify({"ok": True, "cart": db.get_user_cart(user_id)})


@app.route("/success")
def success():
    order = session.get("last_order", {})
    return render_template("success.html", order=order)


@app.route("/api/my-orders")
def my_orders():
    """Returns order history for the logged-in customer's phone number."""
    phone = request.args.get("phone", "")
    if not phone:
        return jsonify({"orders": []})
    orders = db.get_orders_by_phone(phone)
    return jsonify({"orders": orders})


# ════════════════════════════════════════════════════════════════════════════
#  BANNER & CATEGORY VIDEO ROUTES
# ════════════════════════════════════════════════════════════════════════════

@app.route("/api/banners", methods=["GET"])
def get_banners():
    banners = db.get_all_banners()
    return jsonify({"ok": True, "banners": banners})

@app.route("/api/admin/banners/add", methods=["POST"])
def add_banner():
    # Helper checking admin session
    if session.get("role") != "admin":
        return jsonify({"ok": False, "error": "Unauthorized"}), 403
        
    if "image" in request.files:
        file = request.files["image"]
        if file.filename != "":
            upload_folder = os.path.join(app.root_path, "static", "uploads")
            os.makedirs(upload_folder, exist_ok=True)
            ext = os.path.splitext(file.filename)[1].lower()
            if ext in [".jpg", ".jpeg", ".png", ".webp", ".gif"]:
                filename = f"{uuid.uuid4().hex}{ext}"
                file_path = os.path.join(upload_folder, filename)
                file.save(file_path)
                image_url = f"/static/uploads/{filename}"
                db.add_banner(image_url)
                return jsonify({"ok": True, "banners": db.get_all_banners()})
    
    data = request.get_json(silent=True) or {}
    image_url = data.get("image_url", "").strip()
    if not image_url:
        return jsonify({"ok": False, "error": "No image or image_url provided"}), 400
        
    db.add_banner(image_url)
    return jsonify({"ok": True, "banners": db.get_all_banners()})

@app.route("/api/admin/banners/delete/<int:bid>", methods=["DELETE"])
def delete_banner(bid):
    if session.get("role") != "admin":
        return jsonify({"ok": False, "error": "Unauthorized"}), 403
    db.delete_banner(bid)
    return jsonify({"ok": True, "banners": db.get_all_banners()})

@app.route("/api/category-videos", methods=["GET"])
def get_category_videos():
    videos = db.get_all_category_videos()
    return jsonify({"ok": True, "videos": videos})

@app.route("/api/admin/category-videos", methods=["POST"])
def update_category_video():
    if session.get("role") != "admin":
        return jsonify({"ok": False, "error": "Unauthorized"}), 403
        
    video_url = ""
    image_url = ""
    
    # 1. Handle file uploads from multipart form data
    if "video" in request.files:
        file = request.files["video"]
        if file.filename != "":
            upload_folder = os.path.join(app.root_path, "static", "uploads")
            os.makedirs(upload_folder, exist_ok=True)
            ext = os.path.splitext(file.filename)[1].lower()
            if ext in [".mp4", ".webm", ".ogg"]:
                filename = f"{uuid.uuid4().hex}{ext}"
                file_path = os.path.join(upload_folder, filename)
                file.save(file_path)
                video_url = f"/static/uploads/{filename}"

    if "image" in request.files:
        file = request.files["image"]
        if file.filename != "":
            upload_folder = os.path.join(app.root_path, "static", "uploads")
            os.makedirs(upload_folder, exist_ok=True)
            ext = os.path.splitext(file.filename)[1].lower()
            if ext in [".jpg", ".jpeg", ".png", ".webp", ".gif"]:
                filename = f"{uuid.uuid4().hex}{ext}"
                file_path = os.path.join(upload_folder, filename)
                file.save(file_path)
                image_url = f"/static/uploads/{filename}"

    # 2. Extract text fields from form or json
    category = request.form.get("category", "").strip()
    if not video_url:
        video_url = request.form.get("video_url", "").strip()
    if not image_url:
        image_url = request.form.get("image_url", "").strip()

    # Fallback to JSON payload if request is json
    if not category:
        data = request.get_json(silent=True) or {}
        category = data.get("category", "").strip()
        if not video_url:
            video_url = data.get("video_url", "").strip()
        if not image_url:
            image_url = data.get("image_url", "").strip()

    if not category:
        return jsonify({"ok": False, "error": "Category is required"}), 400

    # Retrieve current video URL or image URL to preserve existing values during partial updates
    current_videos = db.get_all_category_videos()
    current_record = next((v for v in current_videos if v["category"].lower() == category.lower()), None)
    
    if current_record:
        if not video_url:
            video_url = current_record["video_url"]
        if not image_url:
            image_url = current_record.get("image_url", "")

    if not video_url:
        return jsonify({"ok": False, "error": "Video source is required"}), 400
        
    db.update_category_video(category, video_url, image_url if image_url else None)
    return jsonify({"ok": True, "videos": db.get_all_category_videos()})


# ════════════════════════════════════════════════════════════════════════════
#  ADMIN PANEL
# ════════════════════════════════════════════════════════════════════════════

def require_admin(f):
    """Decorator that guards admin routes."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("role") != "admin":
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated


@app.route("/admin")
@require_admin
def admin_dash():
    stats = db.get_dashboard_stats()
    return render_template("admin_dash.html", stats=stats,
                           username=session.get("username"))


@app.route("/admin/products")
@require_admin
def admin_products():
    products = db.get_all_products()
    return render_template("admin_prod.html", products=products,
                           username=session.get("username"))


@app.route("/api/admin/stats")
@require_admin
def admin_stats_api():
    return jsonify(db.get_dashboard_stats())


@app.route("/api/admin/upload-image", methods=["POST"])
@require_admin
def admin_upload_image():
    if "image" not in request.files:
        return jsonify({"ok": False, "error": "No image provided"}), 400
    file = request.files["image"]
    if file.filename == "":
        return jsonify({"ok": False, "error": "No selected file"}), 400
    
    # Ensure static/uploads folder exists
    upload_folder = os.path.join(app.root_path, "static", "uploads")
    os.makedirs(upload_folder, exist_ok=True)
    
    # Generate unique filename
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in [".jpg", ".jpeg", ".png", ".webp", ".gif"]:
        return jsonify({"ok": False, "error": "Invalid image format (supported: jpg, jpeg, png, webp, gif)"}), 400
        
    filename = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(upload_folder, filename)
    file.save(file_path)
    
    return jsonify({"ok": True, "image_url": f"/static/uploads/{filename}"})


@app.route("/api/add-product", methods=["POST"])
@require_admin
def add_product():
    data = request.get_json()
    nid = db.add_product(
        data["title"], data["category"], data.get("image_url", ""),
        int(data["base_amazon"]), int(data["base_flipkart"]),
        int(data["base_myntra"]), float(data.get("rating", 4.0)),
        data.get("brand", ""), data.get("description", ""),
        data.get("specifications", "{}"), int(data.get("reviews_count", 0))
    )
    return jsonify({"ok": True, "id": nid})


@app.route("/api/edit-product", methods=["POST"])
@require_admin
def edit_product():
    data = request.get_json()
    db.update_product(
        int(data["id"]), data["title"], data["category"], data.get("image_url", ""),
        int(data["base_amazon"]), int(data["base_flipkart"]),
        int(data["base_myntra"]), float(data.get("rating", 4.0)),
        data.get("brand", ""), data.get("description", ""),
        data.get("specifications", "{}"), int(data.get("reviews_count", 0))
    )
    return jsonify({"ok": True})


@app.route("/api/delete-product/<int:pid>", methods=["DELETE"])
@require_admin
def delete_product(pid):
    db.delete_product(pid)
    return jsonify({"ok": True})


# ════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)