from flask import Flask, render_template, request, jsonify, Response
import sqlite3, re, csv, io, os
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB = BASE / "sales.db"
app = Flask(__name__)

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        cost_price REAL NOT NULL DEFAULT 0,
        selling_price REAL NOT NULL DEFAULT 0,
        stock INTEGER NOT NULL DEFAULT 0,
        cost_known INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS sales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sold_at TEXT NOT NULL,
        sale_type TEXT NOT NULL DEFAULT 'Sale',
        product_id INTEGER NOT NULL,
        quantity INTEGER NOT NULL,
        selling_price REAL NOT NULL,
        cost_price REAL NOT NULL,
        revenue REAL NOT NULL,
        discount REAL NOT NULL DEFAULT 0,
        profit REAL NOT NULL,
        cost_known INTEGER NOT NULL DEFAULT 1,
        FOREIGN KEY(product_id) REFERENCES products(id)
    );
    """)
    # Safe migration for an older database.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(sales)").fetchall()}
    if "sale_type" not in cols:
        conn.execute("ALTER TABLE sales ADD COLUMN sale_type TEXT NOT NULL DEFAULT 'Sale'")
    if "cost_known" not in cols:
        conn.execute("ALTER TABLE sales ADD COLUMN cost_known INTEGER NOT NULL DEFAULT 1")
    if "discount" not in cols:
        conn.execute("ALTER TABLE sales ADD COLUMN discount REAL NOT NULL DEFAULT 0")
    pcols = {r["name"] for r in conn.execute("PRAGMA table_info(products)").fetchall()}
    if "cost_known" not in pcols:
        conn.execute("ALTER TABLE products ADD COLUMN cost_known INTEGER NOT NULL DEFAULT 1")
    defaults = [
        ("T-Shirt",500,800,100),("Shirt",600,1000,75),("Jeans",900,1400,50),
        ("Shoes",1200,1800,30),("Cap",150,300,120),("Coffee",30,50,0),("Tea",10,20,0)
    ]
    for p in defaults:
        conn.execute("INSERT OR IGNORE INTO products(name,cost_price,selling_price,stock) VALUES(?,?,?,?)", p)
    conn.commit(); conn.close()

def product_rows():
    conn=get_db(); rows=conn.execute("SELECT * FROM products ORDER BY name").fetchall(); conn.close(); return rows

def find_or_create_product(name, unit_price=None):
    clean = re.sub(r"\s+"," ",name.strip()).strip()
    conn=get_db()
    row=conn.execute("SELECT * FROM products WHERE lower(name)=lower(?)",(clean,)).fetchone()
    if row:
        conn.close(); return row
    price=float(unit_price or 0)
    conn.execute("INSERT INTO products(name,cost_price,selling_price,stock,cost_known) VALUES(?,?,?,?,?)",(clean,0,price,0,0))
    conn.commit()
    row=conn.execute("SELECT * FROM products WHERE lower(name)=lower(?)",(clean,)).fetchone()
    conn.close(); return row

def parse_items(text):
    s=re.sub(r"\s+"," ",(text or "").lower().strip())
    # Accept natural sales phrases without confusing normal questions:
    # "I sold 5 coffee", "sold 5 coffee", "today sold 5 coffee", "5 coffee sold"
    s=re.sub(r"^(?:today|todays|today's|i|we)\s+","",s)
    s=re.sub(r"^(?:sold|sell|sale)\s+","",s)
    s=re.sub(r"\s+(?:sold|sale)$","",s)
    # Split common multi-item messages: "2 coffee, 3 tea" / "2 coffee and 3 tea"
    parts=re.split(r"\s*(?:,|\band\b|\+)\s*",s)
    items=[]
    for part in parts:
        m=re.search(r"\b(\d+)\s+([a-z][a-z0-9 -]*?)(?:\s+(?:for|total|amount|at|@)\s*(?:rs\.?|₹)?\s*([\d,]+(?:\.\d+)?))?\s*$",part)
        if not m: continue
        qty=int(m.group(1)); name=m.group(2).strip(" -")
        if qty<=0 or not name: continue
        amount=float(m.group(3).replace(",","")) if m.group(3) else None
        items.append((qty,name,amount))
    if not items:
        return None,"Use: 2 coffee, 3 tea  (or: 2 coffee at ₹50, 3 tea at ₹20)"
    result=[]
    for qty,name,amount in items:
        p=find_or_create_product(name, amount/qty if amount is not None else None)
        unit=float(amount/qty) if amount is not None else float(p["selling_price"])
        cost=float(p["cost_price"])
        known=int(p["cost_known"])==1 if "cost_known" in p.keys() else cost>0
        result.append({"product":p,"quantity":qty,"selling_price":unit,"cost_price":cost,
                       "cost_known":known,"revenue":unit*qty,
                       "profit":(unit-cost)*qty if known else 0})
    return result,None

def add_sales(items,sale_type="Sale",sale_date=None):
    conn=get_db()
    # Every submitted item becomes its own permanent sales-record row.
    stamp=(sale_date or datetime.now().strftime("%Y-%m-%d")) + " " + datetime.now().strftime("%H:%M:%S.%f")
    inserted_ids=[]
    for d in items:
        p=d["product"]
        cur=conn.execute("""INSERT INTO sales
        (sold_at,sale_type,product_id,quantity,selling_price,cost_price,revenue,discount,profit,cost_known)
        VALUES(?,?,?,?,?,?,?,?,?,?)""",(stamp,sale_type,p["id"],d["quantity"],d["selling_price"],d["cost_price"],d["revenue"],d.get("discount",0),d["profit"],d.get("cost_known",1)))
        inserted_ids.append(cur.lastrowid)
        conn.execute("UPDATE products SET stock=MAX(stock-?,0) WHERE id=?",(d["quantity"],p["id"]))
    conn.commit(); conn.close()
    return stamp, inserted_ids

def summary(period="today"):
    conn=get_db()
    if period=="today": where="date(s.sold_at)=date('now','localtime')"
    elif period=="week": where="date(s.sold_at)>=date('now','localtime','-6 day')"
    elif period=="month": where="strftime('%Y-%m',s.sold_at)=strftime('%Y-%m','now','localtime')"
    else: where="1=1"
    row=conn.execute(f"""SELECT COALESCE(SUM(quantity),0) items,COUNT(*) orders,
    COALESCE(SUM(revenue),0) revenue,COALESCE(SUM(quantity*cost_price),0) cost,
    COALESCE(SUM(profit),0) profit FROM sales s WHERE {where}""").fetchone()
    top=conn.execute(f"""SELECT p.name,SUM(s.quantity) qty,SUM(s.revenue) revenue
    FROM sales s JOIN products p ON p.id=s.product_id WHERE {where}
    GROUP BY p.id ORDER BY qty DESC LIMIT 1""").fetchone()
    conn.close()
    return {**dict(row),"top_product":dict(top) if top else None}

@app.route("/")
def home(): return render_template("index.html")

@app.after_request
def no_cache(response):
    if request.path == "/":
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response

@app.get("/api/health")
def health():
    conn=get_db()
    row=conn.execute("SELECT COUNT(*) count FROM sales").fetchone()
    conn.close()
    return jsonify(ok=True,database="sqlite",sales_records=int(row["count"] or 0))

@app.get("/api/products")
def products(): return jsonify([dict(r) for r in product_rows()])

def assistant_reply(message):
    """Friendly assistant. Business numbers come only from the sales database."""
    text = re.sub(r"\s+", " ", (message or "").strip())
    low = text.lower()

    def totals(where="1=1"):
        conn=get_db()
        r=conn.execute("SELECT COALESCE(SUM(quantity),0) items, COUNT(*) orders, COALESCE(SUM(revenue),0) revenue, COALESCE(SUM(profit),0) profit FROM sales WHERE "+where).fetchone()
        conn.close()
        return dict(r)

    def find_product():
        conn=get_db()
        rows=conn.execute("SELECT name FROM products ORDER BY length(name) DESC").fetchall()
        conn.close()
        for p in rows:
            if re.search(r"(?<![a-z])"+re.escape(p["name"].lower())+r"(?![a-z])",low):
                return p["name"]
        return None

    def product_stats(name, where="1=1"):
        conn=get_db()
        p=conn.execute("SELECT id,name FROM products WHERE lower(name)=lower(?)",(name,)).fetchone()
        if not p:
            conn.close()
            return None
        r=conn.execute("SELECT COALESCE(SUM(quantity),0) qty,COUNT(*) orders,COALESCE(SUM(revenue),0) revenue,COALESCE(SUM(profit),0) profit FROM sales WHERE product_id=? AND "+where,(p["id"],)).fetchone()
        conn.close()
        return {"name":p["name"],**dict(r)}

    if re.search(r"\b(hi|hii|hello|hey|hai|vanakkam|good morning|good afternoon|good evening)\b",low):
        return "Hi 😊 I’m Flowi, your SALES FLOW AI companion. Ask me about your real sales, revenue, profit, products, stock, or business performance."

    if re.search(r"\b(thank you|thanks|thank|super|great)\b",low):
        return "You’re welcome 😊 Flowi is here whenever you want to check your real sales data."

    is_today=bool(re.search(r"\b(today|todays|today's|innaiku|indru)\b",low))
    is_week=bool(re.search(r"\b(this week|weekly|week|intha week|indha week)\b",low))
    is_month=bool(re.search(r"\b(this month|monthly|month|intha month|indha month)\b",low))
    asks_profit=bool(re.search(r"\b(profit|profit evlo|laabam|labam)\b",low))
    asks_revenue=bool(re.search(r"\b(revenue|turnover|varavu)\b",low))
    asks_qty=bool(re.search(r"\b(how many|quantity|qty|units|sold|sales|evlo.*sale|evlo.*sold)\b",low))
    asks_orders=bool(re.search(r"\b(orders|order count|records)\b",low))
    asks_best=bool(re.search(r"\b(best|top|highest|most selling|best selling|top selling)\b",low))
    asks_margin=bool(re.search(r"\b(margin|profit margin)\b",low))
    product=find_product()

    today="date(sold_at)=date('now','localtime')"
    week="date(sold_at)>=date('now','localtime','-6 day')"
    month="strftime('%Y-%m',sold_at)=strftime('%Y-%m','now','localtime')"
    where=month if is_month else week if is_week else today if is_today else "1=1"
    period="this month" if is_month else "the last 7 days" if is_week else "today" if is_today else "all saved records"

    if product:
        r=product_stats(product,where)
        if r["qty"]==0:
            return f"I checked the real sales data for {r['name']}. There are no recorded sales for it in {period}."
        parts=[]
        if asks_qty or not (asks_profit or asks_revenue or asks_orders or asks_margin): parts.append(f"{r['qty']} units sold")
        if asks_orders: parts.append(f"{r['orders']} sales records")
        if asks_revenue: parts.append(f"₹{r['revenue']:,.2f} revenue")
        if asks_profit: parts.append(f"₹{r['profit']:,.2f} profit")
        if asks_margin: parts.append(f"{(r['profit']/r['revenue']*100 if r['revenue'] else 0):.1f}% profit margin")
        return f"Sure 😊 From {period}, {r['name']} has "+", ".join(parts)+"."

    if asks_best:
        conn=get_db()
        best=conn.execute("SELECT p.name,SUM(s.quantity) qty,SUM(s.revenue) revenue,SUM(s.profit) profit FROM sales s JOIN products p ON p.id=s.product_id WHERE "+where+" GROUP BY p.id ORDER BY revenue DESC LIMIT 1").fetchone()
        conn.close()
        if not best: return "I checked the database, but there are no sales records to rank yet."
        return f"Your top product by revenue is {best['name']} with {best['qty']} units sold, ₹{best['revenue']:,.2f} revenue and ₹{best['profit']:,.2f} profit."

    r=totals(where)
    if asks_margin:
        if not r["orders"]: return f"I checked the real sales data, but there are no records for {period} yet."
        return f"Your profit margin for {period} is {(r['profit']/r['revenue']*100 if r['revenue'] else 0):.1f}%, based on ₹{r['revenue']:,.2f} revenue and ₹{r['profit']:,.2f} profit."

    if asks_profit or asks_revenue or asks_qty or asks_orders or is_today or is_week or is_month:
        if not r["orders"]: return f"I checked the real database, but there are no sales records for {period} yet."
        parts=[]
        if asks_profit: parts.append(f"profit ₹{r['profit']:,.2f}")
        if asks_revenue: parts.append(f"revenue ₹{r['revenue']:,.2f}")
        if asks_qty: parts.append(f"{r['items']} items sold")
        if asks_orders: parts.append(f"{r['orders']} sales records")
        if not parts: parts=[f"{r['orders']} sales records",f"{r['items']} items sold",f"₹{r['revenue']:,.2f} revenue",f"₹{r['profit']:,.2f} profit"]
        return f"Here’s the real picture for {period}: "+", ".join(parts)+"."

    return "I’m Flowi 😊 Try: “What is today’s profit?”, “How many coffee were sold?”, “What is my revenue this month?”, “Which product sells the most?”, “Show low stock items”, or “What is my profit margin?”"


@app.post("/api/assistant")
def assistant():
    payload = request.get_json(silent=True) or {}
    message = payload.get("message", "")
    if not message.strip():
        return jsonify(ok=False, message="Please type a message."), 400
    return jsonify(ok=True, assistant="Flowi", source="SALES FLOW database", message=assistant_reply(message))

@app.post("/api/sales")
def create_sales():
    payload=request.get_json(silent=True) or {}
    items,err=parse_items(payload.get("message",""))
    if err: return jsonify(ok=False,message=err),400
    stamp, inserted_ids=add_sales(items,payload.get("type","Sale"),payload.get("date"))
    saved=[{"date":stamp,"type":payload.get("type","Sale"),"product":d["product"]["name"],
            "quantity":d["quantity"],"unit_price":round(d["selling_price"],2),"discount":round(d.get("discount",0),2),
            "total":round(d["revenue"],2),"profit":round(d["profit"],2),"cost_known":d.get("cost_known",1)} for d in items]
    return jsonify(ok=True,message="Saved "+", ".join(f'{d["quantity"]} × {d["product"]["name"]}' for d in items),sales=saved,record_ids=inserted_ids,stored_count=len(inserted_ids),summary=summary("today"))

@app.post("/api/sales/manual")
def manual_sale():
    p=request.get_json(silent=True) or {}
    try:
        qty=int(p.get("quantity",0)); unit=float(p.get("unit_price",0)); discount=float(p.get("discount",0) or 0)
    except: return jsonify(ok=False,message="Quantity, rate, and discount must be numbers."),400
    if qty<=0 or unit<0 or discount<0: return jsonify(ok=False,message="Enter valid quantity, rate, and discount."),400
    gross=qty*unit
    if discount>gross: return jsonify(ok=False,message="Discount cannot be greater than the gross sale amount."),400
    prod=find_or_create_product(p.get("product",""),unit)
    try:
        cost_value=float(p["cost_price"]) if p.get("cost_price","") not in ("",None) else float(prod["cost_price"])
    except:
        return jsonify(ok=False,message="Cost price must be a number."),400
    if cost_value<0:
        return jsonify(ok=False,message="Cost price cannot be negative."),400
    conn=get_db(); conn.execute("UPDATE products SET cost_price=?,cost_known=? WHERE id=?",(cost_value,1 if p.get("cost_price") not in ("",None) else int(prod["cost_known"]),prod["id"])); conn.commit()
    prod=conn.execute("SELECT * FROM products WHERE id=?",(prod["id"],)).fetchone(); conn.close()
    known=int(prod["cost_known"])==1
    net_revenue=gross-discount
    d={"product":prod,"quantity":qty,"selling_price":unit,"cost_price":float(prod["cost_price"]),"cost_known":known,
       "discount":discount,"revenue":net_revenue,"profit":(net_revenue-float(prod["cost_price"])*qty) if known else 0}
    stamp, inserted_ids=add_sales([d],p.get("type","Sale"),p.get("date"))
    conn=get_db()
    saved=conn.execute("""SELECT s.id,s.sold_at,s.sale_type,p.name product,s.quantity,
        s.selling_price,s.cost_price,s.revenue,s.discount,s.profit,s.cost_known
        FROM sales s JOIN products p ON p.id=s.product_id
        WHERE s.id=?""",(inserted_ids[0],)).fetchone()
    conn.close()
    return jsonify(ok=True,message=f"Saved {qty} × {prod['name']}",date=stamp,stored=True,record=dict(saved),record_ids=inserted_ids,stored_count=len(inserted_ids))

@app.put("/api/products/<int:product_id>")
def update_product(product_id):
    p=request.get_json(silent=True) or {}
    name=re.sub(r"\s+"," ",str(p.get("name","")).strip())
    try:
        selling=float(p.get("selling_price",0)); cost=float(p.get("cost_price",0)); stock=int(p.get("stock",0))
    except:
        return jsonify(ok=False,message="Price and stock values must be valid numbers."),400
    if not name or selling<0 or cost<0 or stock<0:
        return jsonify(ok=False,message="Enter valid product details."),400
    conn=get_db()
    try:
        conn.execute("UPDATE products SET name=?,selling_price=?,cost_price=?,stock=?,cost_known=1 WHERE id=?",(name,selling,cost,stock,product_id))
        if conn.total_changes==0:
            conn.close(); return jsonify(ok=False,message="Product not found."),404
        conn.commit()
        row=conn.execute("SELECT * FROM products WHERE id=?",(product_id,)).fetchone()
        conn.close()
        return jsonify(ok=True,product=dict(row))
    except sqlite3.IntegrityError:
        conn.close(); return jsonify(ok=False,message="A product with that name already exists."),409

@app.get("/api/insights")
def api_insights():
    conn=get_db()
    total=conn.execute("SELECT COALESCE(SUM(revenue),0) revenue,COALESCE(SUM(profit),0) profit,COALESCE(SUM(quantity),0) items,COUNT(*) orders FROM sales").fetchone()
    avg=conn.execute("SELECT COALESCE(AVG(revenue),0) value FROM (SELECT sold_at,SUM(revenue) revenue FROM sales GROUP BY sold_at)").fetchone()
    best_day=conn.execute("SELECT date(sold_at) day,SUM(revenue) revenue,SUM(profit) profit FROM sales GROUP BY day ORDER BY revenue DESC LIMIT 1").fetchone()
    low=conn.execute("SELECT name,stock FROM products WHERE stock<=10 ORDER BY stock ASC,name ASC LIMIT 10").fetchall()
    conn.close()
    revenue=float(total["revenue"] or 0); profit=float(total["profit"] or 0)
    return jsonify(revenue=revenue,profit=profit,items=int(total["items"] or 0),orders=int(total["orders"] or 0),
                   average_order_value=float(avg["value"] or 0),margin=(profit/revenue*100 if revenue else 0),
                   best_day=dict(best_day) if best_day else None,low_stock=[dict(x) for x in low])

@app.get("/api/summary")
def api_summary(): return jsonify({p:summary(p) for p in ["today","week","month","all"]})

@app.get("/api/sales")
def sales():
    conn=get_db()
    rows=conn.execute("""SELECT s.id,s.sold_at,s.sale_type,p.name product,s.quantity,
    s.selling_price,s.cost_price,s.revenue,s.discount,s.profit,s.cost_known
    FROM sales s JOIN products p ON p.id=s.product_id ORDER BY s.id DESC""").fetchall()
    conn.close(); return jsonify([dict(r) for r in rows])

@app.get("/api/chart")
def chart():
    conn=get_db()
    daily=conn.execute("SELECT date(sold_at) day,SUM(revenue) revenue,SUM(profit) profit FROM sales GROUP BY day ORDER BY day DESC LIMIT 7").fetchall()
    by_product=conn.execute("SELECT p.name,SUM(s.quantity) qty,SUM(s.revenue) revenue,SUM(s.profit) profit FROM sales s JOIN products p ON p.id=s.product_id GROUP BY p.id ORDER BY revenue DESC").fetchall()
    conn.close(); return jsonify(daily=[dict(r) for r in reversed(daily)],products=[dict(r) for r in by_product])

@app.get("/api/export")
def export_csv():
    conn=get_db()
    rows=conn.execute("""SELECT s.sold_at,s.sale_type,p.name product,s.quantity,s.selling_price,
    s.discount,s.cost_price,s.revenue,s.profit FROM sales s JOIN products p ON p.id=s.product_id ORDER BY s.id DESC""").fetchall()
    conn.close(); out=io.StringIO(); w=csv.writer(out)
    w.writerow(["Date","Type","Product","Quantity","Rate (₹)","Discount (₹)","Net Sales (₹)","Cost Price (₹)","Profit (₹)"])
    for r in rows: w.writerow([r["sold_at"],r["sale_type"],r["product"],r["quantity"],r["selling_price"],r["discount"],r["revenue"],r["cost_price"],r["profit"]])
    return Response(out.getvalue(),mimetype="text/csv",headers={"Content-Disposition":"attachment; filename=sales_data.csv"})

@app.post("/api/reset")
def reset():
    conn=get_db(); conn.execute("DELETE FROM sales")
    conn.execute("""UPDATE products SET stock=CASE name WHEN 'T-Shirt' THEN 100 WHEN 'Shirt' THEN 75
    WHEN 'Jeans' THEN 50 WHEN 'Shoes' THEN 30 WHEN 'Cap' THEN 120 ELSE stock END""")
    conn.commit(); conn.close(); return jsonify(ok=True)

init_db()
if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)),debug=False)
