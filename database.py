"""
database.py — Mashmart
Persistent SQLite data layer: schema init, CRUD helpers, and 50-item seed matrix.
All base prices stored raw; the 10% markup is applied exclusively at query time in app.py.
"""

import sqlite3
import hashlib
import os
import json
import random

DB_PATH = os.path.join(os.path.dirname(__file__), "mashmart.db")


# ─────────────────────────────────────────────
#  Connection helper — returns a Row-factory conn
# ─────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ─────────────────────────────────────────────
#  Schema initialisation + seed on first boot
# ─────────────────────────────────────────────
def init_db():
    conn = get_db()
    c = conn.cursor()

    # --- users table ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT    NOT NULL UNIQUE,
            email    TEXT    NOT NULL UNIQUE,
            password TEXT    NOT NULL,
            role     TEXT    NOT NULL DEFAULT 'customer',
            phone    TEXT,
            address  TEXT,
            pincode  TEXT
        )
    """)

    # --- inventory table ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS inventory (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            title          TEXT    NOT NULL,
            category       TEXT    NOT NULL,
            image_url      TEXT,
            base_amazon    INTEGER NOT NULL,
            base_flipkart  INTEGER NOT NULL,
            base_myntra    INTEGER NOT NULL,
            rating         REAL    NOT NULL DEFAULT 4.0,
            active_status  INTEGER NOT NULL DEFAULT 1,
            brand          TEXT    DEFAULT '',
            description    TEXT    DEFAULT '',
            specifications TEXT    DEFAULT '{}',
            reviews_count  INTEGER DEFAULT 0
        )
    """)

    # --- orders table ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id              TEXT    NOT NULL UNIQUE,
            product_title         TEXT    NOT NULL,
            chosen_platform       TEXT    NOT NULL,
            total_price           INTEGER NOT NULL,
            system_revenue_margin INTEGER NOT NULL,
            customer_name         TEXT    NOT NULL,
            phone                 TEXT    NOT NULL,
            full_shipping_address TEXT    NOT NULL,
            status                TEXT    NOT NULL DEFAULT 'Paid',
            timestamp             DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # --- cart_items table ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS cart_items (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            product_title   TEXT    NOT NULL,
            chosen_platform TEXT    NOT NULL,
            price           INTEGER NOT NULL,
            quantity        INTEGER NOT NULL DEFAULT 1,
            image_url       TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    # --- banners table ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS banners (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            image_url TEXT NOT NULL
        )
    """)

    # --- category_videos table ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS category_videos (
            category  TEXT PRIMARY KEY,
            video_url TEXT NOT NULL,
            image_url TEXT
        )
    """)

    conn.commit()

    # Dynamic schema migration for older databases
    c.execute("PRAGMA table_info(inventory)")
    cols = [row[1] for row in c.fetchall()]
    migrated = False
    if "brand" not in cols:
        c.execute("ALTER TABLE inventory ADD COLUMN brand TEXT DEFAULT ''")
        migrated = True
    if "description" not in cols:
        c.execute("ALTER TABLE inventory ADD COLUMN description TEXT DEFAULT ''")
        migrated = True
    if "specifications" not in cols:
        c.execute("ALTER TABLE inventory ADD COLUMN specifications TEXT DEFAULT '{}'")
        migrated = True
    if "reviews_count" not in cols:
        c.execute("ALTER TABLE inventory ADD COLUMN reviews_count INTEGER DEFAULT 0")
        migrated = True

    if migrated:
        _migrate_existing_inventory(c)
        conn.commit()

    # Dynamic schema migration for category_videos table
    c.execute("PRAGMA table_info(category_videos)")
    cv_cols = [row[1] for row in c.fetchall()]
    if "image_url" not in cv_cols:
        c.execute("ALTER TABLE category_videos ADD COLUMN image_url TEXT")
        conn.commit()

    # Seed if empty
    c.execute("SELECT COUNT(*) FROM inventory")
    if c.fetchone()[0] == 0:
        _seed_inventory(c)
        conn.commit()
    else:
        # Migrate/clean existing unsplash images to support the new manual upload flow
        c.execute("UPDATE inventory SET image_url = '' WHERE image_url LIKE '%unsplash.com%'")
        conn.commit()

    # Seed default admin + demo customer
    c.execute("SELECT COUNT(*) FROM users")
    if c.fetchone()[0] == 0:
        _seed_users(c)
        conn.commit()

    # Seed default banners
    c.execute("SELECT COUNT(*) FROM banners")
    if c.fetchone()[0] == 0:
        c.executemany("INSERT INTO banners (image_url) VALUES (?)", [
            ("/static/banner1.png",),
            ("/static/banner2.png",),
            ("/static/banner3.png",),
        ])
        conn.commit()

    # Seed default category videos
    c.execute("SELECT COUNT(*) FROM category_videos")
    if c.fetchone()[0] == 0:
        c.executemany("INSERT INTO category_videos (category, video_url, image_url) VALUES (?, ?, ?)", [
            ("Fashion", "https://assets.mixkit.co/videos/preview/mixkit-girl-in-neon-sign-holding-camera-34281-large.mp4", "/static/fashion_cover.png"),
            ("Mobile", "https://assets.mixkit.co/videos/preview/mixkit-spinning-around-a-smart-watch-51925-large.mp4", "/static/mobile_cover.png"),
            ("Beauty", "https://assets.mixkit.co/videos/preview/mixkit-close-up-of-makeup-brushes-41725-large.mp4", "/static/beauty_cover.png"),
            ("Electronics", "https://assets.mixkit.co/videos/preview/mixkit-sound-waves-of-a-glowing-audio-speaker-51633-large.mp4", "/static/electronics_cover.png"),
            ("Food", "https://assets.mixkit.co/videos/preview/mixkit-fresh-vegetables-being-washed-in-a-sink-40545-large.mp4", "/static/food_cover.png"),
        ])
        conn.commit()
    else:
        # Update existing records with default image_url if null/empty
        c.execute("UPDATE category_videos SET image_url = '/static/fashion_cover.png' WHERE category = 'Fashion' AND (image_url IS NULL OR image_url = '')")
        c.execute("UPDATE category_videos SET image_url = '/static/mobile_cover.png' WHERE category = 'Mobile' AND (image_url IS NULL OR image_url = '')")
        c.execute("UPDATE category_videos SET image_url = '/static/beauty_cover.png' WHERE category = 'Beauty' AND (image_url IS NULL OR image_url = '')")
        c.execute("UPDATE category_videos SET image_url = '/static/electronics_cover.png' WHERE category = 'Electronics' AND (image_url IS NULL OR image_url = '')")
        c.execute("UPDATE category_videos SET image_url = '/static/food_cover.png' WHERE category = 'Food' AND (image_url IS NULL OR image_url = '')")
        conn.commit()

    conn.close()


def _hash(pw):
    return hashlib.sha256(pw.encode()).hexdigest()


def _seed_users(c):
    users = [
        ("admin",    "admin@mashmart.com",   _hash("admin123"),   "admin",    "9999999999", "HQ Tower, MG Road", "560001"),
        ("customer1","user@mashmart.com",    _hash("user123"),    "customer", "9876543210", "12 Brigade Road",   "560001"),
    ]
    c.executemany(
        "INSERT INTO users (username,email,password,role,phone,address,pincode) VALUES (?,?,?,?,?,?,?)",
        users
    )


# ─────────────────────────────────────────────
#  50-item seed matrix (10 per category)
# ─────────────────────────────────────────────
# ─────────────────────────────────────────────
#  Rich details generator helper
# ─────────────────────────────────────────────
def get_rich_details(title, category):
    title_lower = title.lower()
    rev_count = random.randint(150, 4800)
    
    # Platform-specific descriptions
    desc_amazon = ""
    desc_flipkart = ""
    desc_myntra = ""
    
    # Platform-specific specifications
    specs_amazon = {}
    specs_flipkart = {}
    specs_myntra = {}
    
    if "nike air force" in title_lower:
        brand = "Nike"
        desc_amazon = "Nike Air Force 1 Sneakers on Amazon. Enjoy premium cushioned soles, original packaging, and quick Prime shipping. Perfect for daily street wear."
        desc_flipkart = "F-Assured Nike Air Force 1 edition. Features robust double stitching, comfortable cupsole structure, and next-day replacement guarantee."
        desc_myntra = "Premium 100% authentic Nike Air Force 1 directly from brand store. Delivered in luxury gift box style with free 30-day fashion exchange."
        
        specs_amazon = {"Sole": "Air Cushion Rubber", "Material": "Synthetic Leather", "Warranty": "6 Months", "Seller": "Cloudtail India"}
        specs_flipkart = {"Sole": "Vulcanized Rubber", "Material": "Premium Leather", "Warranty": "6 Months Brand Warranty", "Seller": "RetailNet"}
        specs_myntra = {"Sole": "Premium Nike Air Rubber", "Material": "100% Genuine Full-grain Leather", "Warranty": "6 Months Official Card", "Seller": "Nike Retail"}
        
    elif "levi's 501" in title_lower:
        brand = "Levi's"
        desc_amazon = "Levi's 501 Straight Fit Jeans on Amazon. Classical 100% copper-riveted cotton denim straight-leg jeans with iconic button fly."
        desc_flipkart = "Flipkart Assured Levi's 501 Jeans. Highly durable construction, comfortable mid-rise straight fit, and quick replacement coverage."
        desc_myntra = "Premium fashion edition Levi's 501. Sourced straight from Levi Strauss warehouse. Delivered in luxury packaging with easy style swap."
        
        specs_amazon = {"Fabric": "100% Cotton Denim", "Fly": "Button Fly", "Rise": "Mid Rise", "Seller": "Appario Retail"}
        specs_flipkart = {"Fabric": "Premium Cotton Denim", "Fly": "Heavy-duty Button Fly", "Rise": "Regular Fit Mid Rise", "Seller": "RetailNet"}
        specs_myntra = {"Fabric": "Select Authentic Cotton Denim", "Fly": "Classic Button Fly", "Rise": "Classic Straight Mid Rise", "Seller": "Levi's Direct"}

    elif "iphone 15 pro" in title_lower:
        brand = "Apple"
        desc_amazon = "iPhone 15 Pro on Amazon. Built with titanium casing, powered by A17 Pro processor. Shipped via Amazon Secure Shipping."
        desc_flipkart = "Flipkart Assured Apple iPhone 15 Pro. Features advanced titanium design, Super Retina XDR screen, and superfast next-day delivery."
        desc_myntra = "Luxe edition Apple iPhone 15 Pro. Curated from authorized resellers, shipped in tamper-proof bubble container with 100% brand seal."
        
        specs_amazon = {"Processor": "A17 Pro", "Storage": "256 GB", "Chassis": "Titanium", "Seller": "Appario Retail"}
        specs_flipkart = {"Processor": "A17 Pro Hexa-Core", "Storage": "256 GB", "Chassis": "Aerospace Titanium", "Seller": "SuperComNet"}
        specs_myntra = {"Processor": "A17 Pro Apple Silicon", "Storage": "256 GB", "Chassis": "Luxe Grade Titanium", "Seller": "Apple Authorized"}

    elif "galaxy s24 ultra" in title_lower:
        brand = "Samsung"
        desc_amazon = "Galaxy S24 Ultra on Amazon. Unleash massive AI capabilities, quad telephoto system, and peak outdoor screen brightness."
        desc_flipkart = "F-Assured Samsung Galaxy S24 Ultra. Equipped with Snapdragon 8 Gen 3, titanium armor, and interactive Galaxy AI features."
        desc_myntra = "Premium Samsung Galaxy S24 Ultra. Shipped with dedicated active stylus pen in sealed official box with brand authenticity mark."
        
        specs_amazon = {"Processor": "Snapdragon 8 Gen 3", "Display": "6.8-inch Dynamic AMOLED", "RAM": "12 GB", "Seller": "Appario Retail"}
        specs_flipkart = {"Processor": "Snapdragon 8 Gen 3 for Galaxy", "Display": "6.8-inch QHD+", "RAM": "12 GB LPDDR5X", "Seller": "SuperComNet"}
        specs_myntra = {"Processor": "Octa-core Snapdragon 8 Gen 3", "Display": "6.8-inch Gorilla Glass Armor", "RAM": "12 GB High-Speed", "Seller": "Samsung Direct"}

    else:
        # Fallback category-based generation
        brand = title.split()[0] if len(title.split()) > 0 else "Generic"
        desc_amazon = f"Amazon Choice Deal: Premium {title}. Shipped with secure Prime logistics, original invoice, and brand warranty support."
        desc_flipkart = f"Flipkart Assured: Best seller {title}. Packed in high-durability transit casing with instant 7-day hassle-free replacement."
        desc_myntra = f"Myntra Luxe Curated: Elegant {title}. Sourced directly from official stores, shipped in premium style box packaging."
        
        specs_amazon = {"Origin": "Imported", "Seller": "Cloudtail India", "Shipping": "Prime Express"}
        specs_flipkart = {"Origin": "Domestic Sourced", "Seller": "RetailNet", "Shipping": "F-Assured Next-Day"}
        specs_myntra = {"Origin": "Brand Curated", "Seller": "Sherwood Active", "Shipping": "Luxe Gift Shipping"}
        
    desc_json = json.dumps({"amazon": desc_amazon, "flipkart": desc_flipkart, "myntra": desc_myntra})
    specs_json = json.dumps({"amazon": specs_amazon, "flipkart": specs_flipkart, "myntra": specs_myntra})
    
    return brand, desc_json, specs_json, rev_count

def _migrate_existing_inventory(c):
    c.execute("SELECT id, title, category FROM inventory")
    rows = c.fetchall()
    for row in rows:
        pid, title, category = row
        brand, desc, specs, revs = get_rich_details(title, category)
        c.execute("""
            UPDATE inventory
            SET brand = ?, description = ?, specifications = ?, reviews_count = ?
            WHERE id = ?
        """, (brand, desc, specs, revs, pid))

def _seed_inventory(c):
    items = [
        # ── FASHION (10) ──────────────────────────────────────────────────────
        ("Nike Air Force 1 Sneakers",          "Fashion", "",  7500,  7200,  7800, 4.5),
        ("Levi's 501 Straight Fit Jeans",      "Fashion", "",  3200,  3000,  3400, 4.3),
        ("Zara Floral Wrap Dress",             "Fashion", "",  2800,  2600,  2900, 4.1),
        ("Ray-Ban Aviator Sunglasses",         "Fashion", "",  6500,  6200,  6700, 4.6),
        ("Adidas Ultraboost Running Shoes",    "Fashion", "",  12000, 11500, 12500, 4.7),
        ("H&M Oversized Hoodie Grey",          "Fashion", "",  1800,  1600,  1900, 4.0),
        ("Fossil Minimalist Watch Brown",      "Fashion", "",  8900,  8500,  9200, 4.4),
        ("Wildcraft Trekking Backpack 45L",    "Fashion", "",  3500,  3300,  3700, 4.2),
        ("Biba Anarkali Ethnic Kurti",         "Fashion", "",  2200,  2000,  2300, 4.3),
        ("Puma Training Shorts Black",         "Fashion", "",  1200,  1100,  1300, 4.0),

        # ── MOBILE (10) ───────────────────────────────────────────────────────
        ("Apple iPhone 15 Pro 256GB",          "Mobile",  "", 134900,129900,139900, 4.8),
        ("Samsung Galaxy S24 Ultra 512GB",     "Mobile",  "", 124999,119999,129999, 4.7),
        ("OnePlus 12 5G 256GB Flowy Emerald",  "Mobile",  "",  64999, 62999, 66999, 4.6),
        ("Redmi Note 13 Pro+ 256GB",           "Mobile",  "",  29999, 28999, 30999, 4.4),
        ("Google Pixel 8 Pro 128GB",           "Mobile",  "",  84999, 82000, 87000, 4.6),
        ("Realme GT 5 Pro 256GB",              "Mobile",  "",  42999, 41000, 43999, 4.3),
        ("Nothing Phone 2a 256GB",             "Mobile",  "",  22999, 21999, 23999, 4.2),
        ("iQOO 12 5G 256GB Legend",            "Mobile",  "",  52999, 51000, 53999, 4.5),
        ("Motorola Edge 50 Fusion 256GB",      "Mobile",  "",  19999, 18999, 20999, 4.1),
        ("Vivo X100 Pro 512GB",                "Mobile",  "",  89999, 87000, 91999, 4.5),

        # ── BEAUTY (10) ───────────────────────────────────────────────────────
        ("Maybelline Fit Me Foundation",       "Beauty",  "",    799,   749,   849, 4.2),
        ("L'Oreal Paris Revitalift Serum",     "Beauty",  "",   1499,  1399,  1599, 4.4),
        ("Forest Essentials Facial Cleanser",  "Beauty",  "",   1850,  1750,  1950, 4.5),
        ("MAC Lipstick Ruby Woo",              "Beauty",  "",   1750,  1650,  1850, 4.6),
        ("Neutrogena Sunscreen SPF 50",        "Beauty",  "",    650,   599,   699, 4.3),
        ("Biotique Bio Papaya Scrub 75g",      "Beauty",  "",    249,   229,   269, 4.0),
        ("Nykaa Cosmetics Kohl Kajal Black",   "Beauty",  "",    349,   299,   369, 4.1),
        ("The Ordinary Niacinamide 10% 30ml",  "Beauty",  "",    799,   749,   849, 4.7),
        ("Lakme Absolute Mousse Foundation",   "Beauty",  "",    599,   549,   629, 4.1),
        ("Plum Green Tea Face Wash 100ml",     "Beauty",  "",    449,   399,   469, 4.4),

        # ── ELECTRONICS (10) ──────────────────────────────────────────────────
        ("Sony WH-1000XM5 Headphones",         "Electronics","", 34990, 32990, 35990, 4.8),
        ("Apple AirPods Pro 2nd Gen",          "Electronics","", 24900, 23500, 25900, 4.7),
        ("LG 65\" OLED 4K Smart TV",           "Electronics","",149990,144990,154990, 4.7),
        ("Dell XPS 15 Laptop i9 RTX 4070",     "Electronics","",199990,194990,204990, 4.6),
        ("Canon EOS R6 Mark II Mirrorless",    "Electronics","",209990,204990,214990, 4.8),
        ("Dyson V15 Detect Vacuum",            "Electronics","", 52900, 49900, 54900, 4.5),
        ("JBL Flip 6 Portable Speaker",        "Electronics","",  9999,  9499, 10499, 4.4),
        ("Kindle Paperwhite 11th Gen 32GB",    "Electronics","", 14999, 13999, 15499, 4.6),
        ("Mi 65W GaN Charger USB-C",           "Electronics","",  2499,  2299,  2599, 4.3),
        ("Logitech MX Master 3S Mouse",        "Electronics","",  9999,  9499, 10299, 4.6),

        # ── FOOD (10) ─────────────────────────────────────────────────────────
        ("Whole Farm Organic Turmeric 500g",   "Food",    "",    499,   449,   529, 4.3),
        ("Tata Tea Gold Premium Blend 500g",   "Food",    "",    349,   299,   369, 4.5),
        ("Nescafé Gold Blend 200g",            "Food",    "",    899,   849,   949, 4.4),
        ("Bournvita Health Drink 1kg",         "Food",    "",    649,   599,   679, 4.2),
        ("Millet Magic Multi-Grain Atta 5kg",  "Food",    "",    799,   749,   829, 4.3),
        ("Too Yumm Veggie Stix 200g",          "Food",    "",    199,   179,   219, 4.0),
        ("Figaro Extra Virgin Olive Oil 1L",   "Food",    "",    699,   649,   729, 4.4),
        ("Cadbury Dairy Milk Silk 300g",       "Food",    "",    399,   369,   419, 4.6),
        ("Organic India Tulsi Green Tea 25tb", "Food",    "",    299,   269,   319, 4.5),
        ("Yoga Bar Oats Choco Almond 400g",    "Food",    "",    449,   419,   469, 4.3),
    ]

    seeded_items = []
    for title, category, img, amz, flp, myn, rating in items:
        brand, desc, specs, revs = get_rich_details(title, category)
        seeded_items.append((title, category, img, amz, flp, myn, rating, brand, desc, specs, revs))

    c.executemany("""
        INSERT INTO inventory
            (title, category, image_url, base_amazon, base_flipkart, base_myntra, rating, brand, description, specifications, reviews_count)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, seeded_items)


# ─────────────────────────────────────────────
#  CRUD helpers
# ─────────────────────────────────────────────

def get_all_products(category=None, search=None):
    conn = get_db()
    c = conn.cursor()
    query = "SELECT * FROM inventory WHERE active_status=1"
    params = []
    if category and category != "All":
        query += " AND category=?"
        params.append(category)
    if search:
        query += " AND (title LIKE ? OR category LIKE ?)"
        params += [f"%{search}%", f"%{search}%"]
    c.execute(query, params)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_product_by_id(pid):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM inventory WHERE id=?", (pid,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def add_product(title, category, image_url, base_amazon, base_flipkart, base_myntra, rating=4.0, brand="", description="", specifications="{}", reviews_count=0):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO inventory (title,category,image_url,base_amazon,base_flipkart,base_myntra,rating,brand,description,specifications,reviews_count)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (title, category, image_url, base_amazon, base_flipkart, base_myntra, rating, brand, description, specifications, reviews_count))
    conn.commit()
    new_id = c.lastrowid
    conn.close()
    return new_id


def update_product(pid, title, category, image_url, base_amazon, base_flipkart, base_myntra, rating, brand="", description="", specifications="{}", reviews_count=0):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        UPDATE inventory
        SET title=?, category=?, image_url=?, base_amazon=?, base_flipkart=?, base_myntra=?, rating=?, brand=?, description=?, specifications=?, reviews_count=?
        WHERE id=?
    """, (title, category, image_url, base_amazon, base_flipkart, base_myntra, rating, brand, description, specifications, reviews_count, pid))
    conn.commit()
    conn.close()


def delete_product(pid):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE inventory SET active_status=0 WHERE id=?", (pid,))
    conn.commit()
    conn.close()


def save_order(order_id, product_title, chosen_platform, total_price, margin,
               customer_name, phone, address, status="Paid"):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO orders
            (order_id,product_title,chosen_platform,total_price,system_revenue_margin,
             customer_name,phone,full_shipping_address,status)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (order_id, product_title, chosen_platform, total_price, margin,
          customer_name, phone, address, status))
    conn.commit()
    conn.close()


def get_all_orders():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM orders ORDER BY timestamp DESC")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_orders_by_phone(phone):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM orders WHERE phone=? ORDER BY timestamp DESC", (phone,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_user_by_credentials(username, password):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE (username=? OR email=?) AND password=?",
              (username, username, _hash(password)))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def register_user(username, email, password, phone="", address="", pincode=""):
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO users (username,email,password,role,phone,address,pincode)
            VALUES (?,?,?,?,?,?,?)
        """, (username, email, _hash(password), "customer", phone, address, pincode))
        conn.commit()
        uid = c.lastrowid
        conn.close()
        return uid
    except sqlite3.IntegrityError:
        conn.close()
        return None


def get_dashboard_stats():
    """Aggregate analytics for admin dashboard."""
    conn = get_db()
    c = conn.cursor()

    c.execute("SELECT COALESCE(SUM(total_price),0) FROM orders")
    gross_sales = c.fetchone()[0]

    c.execute("SELECT COALESCE(SUM(system_revenue_margin),0) FROM orders")
    net_margin = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM orders")
    total_orders = c.fetchone()[0]

    c.execute("SELECT chosen_platform, COUNT(*) as cnt FROM orders GROUP BY chosen_platform")
    platform_dist = {r[0]: r[1] for r in c.fetchall()}

    c.execute("""
        SELECT product_title, chosen_platform, total_price, status, timestamp
        FROM orders ORDER BY timestamp DESC LIMIT 20
    """)
    recent = [dict(r) for r in c.fetchall()]

    c.execute("""
        SELECT i.category, COUNT(o.id) as cnt
        FROM orders o
        JOIN inventory i ON o.product_title = i.title
        GROUP BY i.category
    """)
    cat_dist = {r[0]: r[1] for r in c.fetchall()}

    # Grouped sales and profit by Category
    c.execute("""
        SELECT i.category, 
               COALESCE(SUM(o.total_price),0) as revenue, 
               COALESCE(SUM(o.system_revenue_margin),0) as profit,
               COUNT(o.id) as orders_count
        FROM orders o
        JOIN inventory i ON o.product_title = i.title
        GROUP BY i.category
    """)
    category_sales = [dict(r) for r in c.fetchall()]

    # Grouped inventory metrics by Category
    c.execute("""
        SELECT category, 
               COUNT(id) as total_products, 
               AVG(rating) as avg_rating
        FROM inventory 
        WHERE active_status = 1
        GROUP BY category
    """)
    category_inventory = {r[0]: {"total_products": r[1], "avg_rating": round(r[2], 2)} for r in c.fetchall()}

    # Merge Category Metrics
    c.execute("SELECT DISTINCT category FROM inventory WHERE active_status=1")
    all_cats = [r[0] for r in c.fetchall()]
    category_metrics = []
    for cat in all_cats:
        sales_info = next((item for item in category_sales if item["category"] == cat), None)
        inv_info = category_inventory.get(cat, {"total_products": 0, "avg_rating": 4.0})
        category_metrics.append({
            "category": cat,
            "revenue": sales_info["revenue"] if sales_info else 0,
            "profit": sales_info["profit"] if sales_info else 0,
            "orders_count": sales_info["orders_count"] if sales_info else 0,
            "total_products": inv_info["total_products"],
            "avg_rating": inv_info["avg_rating"]
        })

    # Platform revenue breakdown
    c.execute("SELECT chosen_platform, COALESCE(SUM(total_price), 0), COALESCE(SUM(system_revenue_margin), 0) FROM orders GROUP BY chosen_platform")
    platform_revenue = {r[0]: {"revenue": r[1], "profit": r[2]} for r in c.fetchall()}

    # Recent orders with categories
    c.execute("""
        SELECT o.product_title, o.chosen_platform, o.total_price, o.status, o.timestamp, i.category
        FROM orders o
        LEFT JOIN inventory i ON o.product_title = i.title
        ORDER BY o.timestamp DESC LIMIT 50
    """)
    recent_orders_with_cat = [dict(r) for r in c.fetchall()]

    conn.close()
    return {
        "gross_sales": gross_sales,
        "net_margin": net_margin,
        "total_orders": total_orders,
        "platform_dist": platform_dist,
        "cat_dist": cat_dist,
        "category_metrics": category_metrics,
        "platform_revenue": platform_revenue,
        "recent_orders": recent_orders_with_cat,
    }


# ─────────────────────────────────────────────
#  Cart persistence helpers
# ─────────────────────────────────────────────

def get_user_cart(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM cart_items WHERE user_id=?", (user_id,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def add_to_user_cart(user_id, product_title, chosen_platform, price, image_url, quantity=1):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT id, quantity FROM cart_items 
        WHERE user_id=? AND product_title=? AND chosen_platform=?
    """, (user_id, product_title, chosen_platform))
    row = c.fetchone()
    if row:
        new_qty = row["quantity"] + quantity
        c.execute("UPDATE cart_items SET quantity=? WHERE id=?", (new_qty, row["id"]))
    else:
        c.execute("""
            INSERT INTO cart_items (user_id, product_title, chosen_platform, price, image_url, quantity)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, product_title, chosen_platform, price, image_url, quantity))
    conn.commit()
    conn.close()

def update_cart_item_quantity(user_id, item_id, quantity):
    conn = get_db()
    c = conn.cursor()
    if quantity <= 0:
        c.execute("DELETE FROM cart_items WHERE id=? AND user_id=?", (item_id, user_id))
    else:
        c.execute("UPDATE cart_items SET quantity=? WHERE id=? AND user_id=?", (quantity, item_id, user_id))
    conn.commit()
    conn.close()

def remove_from_cart(user_id, item_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM cart_items WHERE id=? AND user_id=?", (item_id, user_id))
    conn.commit()
    conn.close()

def clear_user_cart(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM cart_items WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def sync_guest_cart(user_id, guest_items):
    for item in guest_items:
        add_to_user_cart(
            user_id,
            item.get("product_title", ""),
            item.get("chosen_platform", ""),
            item.get("price", 0),
            item.get("image_url", ""),
            item.get("quantity", 1)
        )


# ─────────────────────────────────────────────
#  Banners & Category Videos helpers
# ─────────────────────────────────────────────

def get_all_banners():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM banners ORDER BY id DESC")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def add_banner(image_url):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO banners (image_url) VALUES (?)", (image_url,))
    conn.commit()
    new_id = c.lastrowid
    conn.close()
    return new_id

def delete_banner(banner_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM banners WHERE id=?", (banner_id,))
    conn.commit()
    conn.close()

def get_all_category_videos():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM category_videos")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def update_category_video(category, video_url, image_url=None):
    conn = get_db()
    c = conn.cursor()
    if image_url is not None:
        c.execute("""
            INSERT INTO category_videos (category, video_url, image_url) 
            VALUES (?, ?, ?)
            ON CONFLICT(category) DO UPDATE SET video_url=excluded.video_url, image_url=excluded.image_url
        """, (category, video_url, image_url))
    else:
        c.execute("""
            INSERT INTO category_videos (category, video_url) 
            VALUES (?, ?)
            ON CONFLICT(category) DO UPDATE SET video_url=excluded.video_url
        """, (category, video_url))
    conn.commit()
    conn.close()