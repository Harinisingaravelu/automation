from flask import Flask, render_template, request, jsonify, Response
import sqlite3, re, csv, io, os, json
from datetime import datetime, date, timedelta
from pathlib import Path
from openpyxl import Workbook

BASE=Path(__file__).resolve().parent
DB=Path(os.environ.get("SALES_DB_PATH",str(BASE/"sales.db")))
app=Flask(__name__)

def get_db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; c.execute("PRAGMA foreign_keys=ON"); return c
def rows(sql,args=()):
    c=get_db(); r=c.execute(sql,args).fetchall(); c.close(); return [dict(x) for x in r]
def row(sql,args=()):
    c=get_db(); r=c.execute(sql,args).fetchone(); c.close(); return dict(r) if r else None
def money(v): return round(float(v or 0),2)

def init_db():
    c=get_db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT,email TEXT,phone TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT UNIQUE NOT NULL,category TEXT NOT NULL DEFAULT 'General',cost_price REAL NOT NULL DEFAULT 0,selling_price REAL NOT NULL DEFAULT 0,stock INTEGER NOT NULL DEFAULT 0,low_stock_threshold INTEGER NOT NULL DEFAULT 10,cost_known INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS sales(id INTEGER PRIMARY KEY AUTOINCREMENT,sold_at TEXT NOT NULL,sale_type TEXT NOT NULL DEFAULT 'Sale',product_id INTEGER NOT NULL,quantity INTEGER NOT NULL,selling_price REAL NOT NULL,cost_price REAL NOT NULL,revenue REAL NOT NULL,discount REAL NOT NULL DEFAULT 0,profit REAL NOT NULL,cost_known INTEGER NOT NULL DEFAULT 1,payment_method TEXT NOT NULL DEFAULT 'Cash',status TEXT NOT NULL DEFAULT 'Completed',FOREIGN KEY(product_id) REFERENCES products(id));
    CREATE TABLE IF NOT EXISTS reports(id INTEGER PRIMARY KEY AUTOINCREMENT,report_type TEXT NOT NULL,period_start TEXT,period_end TEXT,payload TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL DEFAULT '');
    """)
    pc={x["name"] for x in c.execute("PRAGMA table_info(products)")}
    sc={x["name"] for x in c.execute("PRAGMA table_info(sales)")}
    for n,s in {"category":"ALTER TABLE products ADD COLUMN category TEXT NOT NULL DEFAULT 'General'","low_stock_threshold":"ALTER TABLE products ADD COLUMN low_stock_threshold INTEGER NOT NULL DEFAULT 10","cost_known":"ALTER TABLE products ADD COLUMN cost_known INTEGER NOT NULL DEFAULT 1"}.items():
        if n not in pc:c.execute(s)
    for n,s in {"discount":"ALTER TABLE sales ADD COLUMN discount REAL NOT NULL DEFAULT 0","cost_known":"ALTER TABLE sales ADD COLUMN cost_known INTEGER NOT NULL DEFAULT 1","payment_method":"ALTER TABLE sales ADD COLUMN payment_method TEXT NOT NULL DEFAULT 'Cash'","status":"ALTER TABLE sales ADD COLUMN status TEXT NOT NULL DEFAULT 'Completed'"}.items():
        if n not in sc:c.execute(s)
    # Repair the status migration safely if an old database did not have it.
    if "status" not in {x["name"] for x in c.execute("PRAGMA table_info(sales)")}: c.execute("ALTER TABLE sales ADD COLUMN status TEXT NOT NULL DEFAULT 'Completed'")
    defaults=[("T-Shirt","Clothing",500,800,100,10),("Shirt","Clothing",600,1000,75,10),("Jeans","Clothing",900,1400,50,10),("Shoes","Footwear",1200,1800,30,5),("Laptop Bag","Accessories",1000,1600,25,5),("Cap","Accessories",150,300,120,10)]
    for p in defaults:c.execute("INSERT OR IGNORE INTO products(name,category,cost_price,selling_price,stock,low_stock_threshold,cost_known) VALUES(?,?,?,?,?,?,1)",p)
    for k,v in {"business_name":"SALES FLOW","owner_name":"","email":"","phone":"","currency":"INR ₹","default_payment":"Cash","low_stock_threshold":"10","date_format":"DD/MM/YYYY","notifications":"true","theme":"light"}.items():
        c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",(k,v))
    if c.execute("SELECT COUNT(*) FROM users").fetchone()[0]==0:c.execute("INSERT INTO users(name) VALUES(?)",("SALES FLOW Owner",))
    c.commit();c.close()

def product_list():
    return rows("""SELECT p.*,COALESCE(SUM(s.quantity),0) units_sold,COALESCE(SUM(s.revenue),0) revenue,
      COALESCE(SUM(CASE WHEN s.cost_known=1 THEN s.profit ELSE 0 END),0) profit
      FROM products p LEFT JOIN sales s ON p.id=s.product_id GROUP BY p.id ORDER BY p.name""")
def sales_list():
    return rows("""SELECT s.id,s.sold_at,s.sale_type,p.id product_id,p.name product,p.category,s.quantity,
      s.selling_price,s.cost_price,s.revenue,s.discount,s.profit,s.cost_known,s.payment_method,s.status
      FROM sales s JOIN products p ON p.id=s.product_id ORDER BY s.sold_at DESC,s.id DESC""")
def bounds(period="30d",start=None,end=None):
    t=date.today()
    if period=="today":return t.isoformat(),t.isoformat()
    if period=="7d":return (t-timedelta(days=6)).isoformat(),t.isoformat()
    if period=="30d":return (t-timedelta(days=29)).isoformat(),t.isoformat()
    if period=="this_month":return t.replace(day=1).isoformat(),t.isoformat()
    if period=="last_month":
        f=t.replace(day=1);last=f-timedelta(days=1);return last.replace(day=1).isoformat(),last.isoformat()
    if period=="3m":return (t-timedelta(days=89)).isoformat(),t.isoformat()
    return start or "1900-01-01",end or t.isoformat()
def sales_between(a,b):
    return rows("""SELECT s.*,p.name product,p.category FROM sales s JOIN products p ON p.id=s.product_id
      WHERE date(s.sold_at) BETWEEN date(?) AND date(?) ORDER BY s.sold_at,s.id""",(a,b))
def aggregate(xs):
    rev=sum(float(x["revenue"]) for x in xs); profit=sum(float(x["profit"]) for x in xs if int(x.get("cost_known",1)))
    return {"revenue":money(rev),"cost":money(rev-profit),"profit":money(profit),"orders":len(xs),"items":sum(int(x["quantity"]) for x in xs),"margin":money(profit/rev*100 if rev else 0),"pending_cost":sum(1 for x in xs if not int(x.get("cost_known",1)))}

def dashboard(period="30d",start=None,end=None):
    a,b=bounds(period,start,end); xs=sales_between(a,b); k=aggregate(xs); pm={}
    for x in xs:
        z=pm.setdefault(x["product"],{"product":x["product"],"units_sold":0,"revenue":0,"profit":0});z["units_sold"]+=x["quantity"];z["revenue"]+=x["revenue"];z["profit"]+=x["profit"] if x["cost_known"] else 0
    top=sorted(pm.values(),key=lambda z:z["revenue"],reverse=True)
    for z in top:z["revenue"]=money(z["revenue"]);z["profit"]=money(z["profit"]);z["margin"]=money(z["profit"]/z["revenue"]*100 if z["revenue"] else 0)
    ps=product_list();low=[p for p in ps if int(p["stock"])<=int(p["low_stock_threshold"])]
    prev=[]
    if a!="1900-01-01":
        days=(date.fromisoformat(b)-date.fromisoformat(a)).days+1; pe=date.fromisoformat(a)-timedelta(days=1);pa=pe-timedelta(days=days-1);prev=sales_between(pa.isoformat(),pe.isoformat())
    tr=[];d=date.fromisoformat(a);e=date.fromisoformat(b)
    while d<=e:
        z=aggregate([x for x in xs if str(x["sold_at"])[:10]==d.isoformat()]);tr.append({"date":d.isoformat(),"revenue":z["revenue"],"profit":z["profit"],"items":z["items"],"orders":z["orders"]});d+=timedelta(days=1)
    return {"range":{"start":a,"end":b},"kpis":k,"previous":aggregate(prev),"products":top[:10],"best":{"revenue":top[0]["product"] if top else None,"profit":max(top,key=lambda z:z["profit"])["product"] if top else None,"units":max(top,key=lambda z:z["units_sold"])["product"] if top else None},"low_stock":low[:10],"trend":tr}
def analytics(period="30d",start=None,end=None):
    d=dashboard(period,start,end);xs=sales_between(d["range"]["start"],d["range"]["end"]);cats={}
    for x in xs:
        z=cats.setdefault(x["category"],{"category":x["category"],"revenue":0,"profit":0,"units":0});z["revenue"]+=x["revenue"];z["profit"]+=x["profit"] if x["cost_known"] else 0;z["units"]+=x["quantity"]
    for z in cats.values():z["revenue"]=money(z["revenue"]);z["profit"]=money(z["profit"]);z["margin"]=money(z["profit"]/z["revenue"]*100 if z["revenue"] else 0)
    dow={}
    for x in xs:
        day=datetime.fromisoformat(str(x["sold_at"])[:19]).strftime("%A");dow[day]=dow.get(day,0)+x["revenue"]
    return {"dashboard":d,"category":list(cats.values()),"day_of_week":dow,"strongest_day":max(dow,key=dow.get) if dow else None}

def find_product(name):
    clean=re.sub(r"\s+"," ",str(name or "").strip())
    p=row("SELECT * FROM products WHERE lower(name)=lower(?)",(clean,))
    if p:return p
    for x in product_list():
        if clean.lower() in x["name"].lower() or x["name"].lower() in clean.lower():return x
    return None

def detect_sale(msg):
    s=re.sub(r"\s+"," ",str(msg).strip().lower())
    if not re.search(r"\b(sold|sell|sale|selling)\b",s):return None
    m=re.search(r"\b(\d+)\s+([a-z][a-z0-9 -]*?)(?:\s+(?:for|at|@)\s*(?:rs\.?|₹)?\s*([\d,]+(?:\.\d+)?))?\s*(?:each|per\s*(?:item|unit))?\b",s)
    if not m:return None
    qty=int(m.group(1));name=m.group(2).strip(" -");price=float(m.group(3).replace(",","")) if m.group(3) else None;p=find_product(name)
    if not p:return None
    unit=price if price is not None else float(p["selling_price"]);rev=qty*unit;cost=qty*float(p["cost_price"])
    return {"product_id":p["id"],"product":p["name"],"category":p["category"],"quantity":qty,"selling_price":unit,"cost_price":p["cost_price"],"revenue":money(rev),"cost":money(cost),"profit":money(rev-cost),"stock_before":p["stock"],"stock_after":max(0,p["stock"]-qty)}
def save_sale(data):
    pid=int(data["product_id"]);qty=int(data["quantity"]);sell=float(data["selling_price"]);cost=float(data["cost_price"]);discount=float(data.get("discount",0) or 0);payment=data.get("payment_method","Cash")
    p=row("SELECT * FROM products WHERE id=?",(pid,))
    if not p:raise ValueError("Product not found.")
    if qty<=0 or sell<0 or cost<0 or discount<0 or discount>qty*sell:raise ValueError("Invalid sale values.")
    if p["stock"]<qty:raise ValueError(f"Only {p['stock']} units of {p['name']} are available.")
    rev=qty*sell-discount;profit=rev-qty*cost;dt=str(data.get("date") or date.today().isoformat())[:10]
    c=get_db();cur=c.execute("""INSERT INTO sales(sold_at,product_id,quantity,selling_price,cost_price,revenue,discount,profit,cost_known,payment_method,status)
      VALUES(?,?,?,?,?,?,?,?,?,?,?)""",(dt+" "+datetime.now().strftime("%H:%M:%S.%f"),pid,qty,sell,cost,rev,discount,profit,1,payment,"Completed"))
    c.execute("UPDATE products SET stock=stock-? WHERE id=?",(qty,pid));c.commit();sid=cur.lastrowid;c.close();out=row("""SELECT s.id,s.sold_at,s.sale_type,p.id product_id,p.name product,p.category,s.quantity,s.selling_price,s.cost_price,s.revenue,s.discount,s.profit,s.cost_known,s.payment_method,s.status,p.stock stock_after FROM sales s JOIN products p ON p.id=s.product_id WHERE s.id=?""",(sid,));return out
def update_sale(sid,data):
    old=row("SELECT * FROM sales WHERE id=?",(sid,))
    if not old:raise ValueError("Sale not found.")
    pid=int(data["product_id"]);qty=int(data["quantity"]);sell=float(data["selling_price"]);cost=float(data["cost_price"]);discount=float(data.get("discount",0) or 0)
    p=row("SELECT * FROM products WHERE id=?",(pid,))
    if not p:raise ValueError("Product not found.")
    available=p["stock"]+(old["quantity"] if old["product_id"]==pid else 0)
    if qty<=0 or available<qty:raise ValueError(f"Only {available} units are available.")
    c=get_db();c.execute("UPDATE products SET stock=stock+? WHERE id=?",(old["quantity"],old["product_id"]));c.execute("UPDATE products SET stock=stock-? WHERE id=?",(qty,pid))
    rev=qty*sell-discount;profit=rev-qty*cost
    c.execute("""UPDATE sales SET sold_at=?,product_id=?,quantity=?,selling_price=?,cost_price=?,revenue=?,discount=?,profit=?,cost_known=1,payment_method=?,status=? WHERE id=?""",(str(data.get("date") or old["sold_at"])[:10]+" "+datetime.now().strftime("%H:%M:%S.%f"),pid,qty,sell,cost,rev,discount,profit,data.get("payment_method",old["payment_method"]),data.get("status",old["status"]),sid));c.commit();c.close();return row("""SELECT s.id,s.sold_at,s.sale_type,p.id product_id,p.name product,p.category,s.quantity,s.selling_price,s.cost_price,s.revenue,s.discount,s.profit,s.cost_known,s.payment_method,s.status FROM sales s JOIN products p ON p.id=s.product_id WHERE s.id=?""",(sid,))

def assistant(msg):
    d=detect_sale(msg)
    if d:return {"message":f"I found {d['quantity']} × {d['product']}. Revenue ₹{d['revenue']:,.2f}, cost ₹{d['cost']:,.2f}, profit ₹{d['profit']:,.2f}. Confirm before I save it.","sale_detected":d}
    low=msg.lower();period="this_month" if "month" in low else "7d" if "week" in low else "today" if "today" in low else "30d";a=analytics(period);k=a["dashboard"]["kpis"]
    if any(x in low for x in ["hi","hello","hey","vanakkam"]):return {"message":"Hi 👋 I’m FLOWI. I’m connected to your SALES FLOW database. Ask me about sales, profit, products, stock or reports."}
    if "low stock" in low:return {"message":"Low stock: "+(", ".join(f"{p['name']} ({p['stock']} left)" for p in a["dashboard"]["low_stock"]) if a["dashboard"]["low_stock"] else "Everything is above its configured threshold.")}
    if "best" in low or "most" in low or "top" in low:return {"message":f"Your best-selling product is {a['dashboard']['best']['units']}." if a["dashboard"]["best"]["units"] else "There are no sales in this period yet."}
    if "profit" in low and "margin" not in low:return {"message":f"Your profit is ₹{k['profit']:,.2f} for the selected period."}
    if "revenue" in low or "sales amount" in low:return {"message":f"Your revenue is ₹{k['revenue']:,.2f} for the selected period."}
    if "report" in low:return {"message":f"Report: {k['orders']} orders, {k['items']} items, ₹{k['revenue']:,.2f} revenue and ₹{k['profit']:,.2f} profit."}
    return {"message":"I’m FLOWI 😊 Try “today profit”, “monthly report”, “best selling product”, “show low stock”, or “Sold 5 T-Shirts for ₹800 each”."}

@app.route("/")
def home():
    html=render_template("index.html")
    return html.replace("</head>",'<link rel="stylesheet" href="/static/platform.css?v=2"><script src="/static/platform.js?v=2"></script></head>')
@app.after_request
def no_cache(r):
    if request.path=="/":r.headers["Cache-Control"]="no-store, no-cache, must-revalidate, max-age=0"
    return r

@app.get("/api/health")
def health():return jsonify(ok=True,database="sqlite",sales_records=row("SELECT COUNT(*) count FROM sales")["count"])
@app.get("/api/dashboard")
def api_dashboard():return jsonify(dashboard(request.args.get("period","30d"),request.args.get("start"),request.args.get("end")))
@app.get("/api/analytics")
def api_analytics():return jsonify(analytics(request.args.get("period","30d"),request.args.get("start"),request.args.get("end")))
@app.get("/api/products")
def api_products():return jsonify(product_list())
@app.post("/api/products")
def api_products_create():
    p=request.get_json(silent=True) or {}
    try:name=str(p["name"]).strip();category=str(p.get("category","General"));cost=float(p.get("cost_price",0));sell=float(p.get("selling_price",0));stock=int(p.get("stock",0));th=int(p.get("low_stock_threshold",10))
    except:return jsonify(ok=False,message="Enter valid product values."),400
    c=get_db()
    try:cur=c.execute("INSERT INTO products(name,category,cost_price,selling_price,stock,low_stock_threshold,cost_known) VALUES(?,?,?,?,?,?,1)",(name,category,cost,sell,stock,th));c.commit();out=c.execute("SELECT * FROM products WHERE id=?",(cur.lastrowid,)).fetchone();c.close();return jsonify(ok=True,product=dict(out))
    except sqlite3.IntegrityError:c.close();return jsonify(ok=False,message="Product already exists."),409
@app.put("/api/products/<int:pid>")
def api_products_update(pid):
    p=request.get_json(silent=True) or {};c=get_db()
    try:c.execute("UPDATE products SET name=?,category=?,cost_price=?,selling_price=?,stock=?,low_stock_threshold=?,cost_known=1 WHERE id=?",(p["name"],p.get("category","General"),float(p["cost_price"]),float(p["selling_price"]),int(p["stock"]),int(p.get("low_stock_threshold",10)),pid));c.commit()
    except Exception as e:c.close();return jsonify(ok=False,message=str(e)),400
    out=c.execute("SELECT * FROM products WHERE id=?",(pid,)).fetchone();c.close();return jsonify(ok=True,product=dict(out) if out else None)
@app.delete("/api/products/<int:pid>")
def api_products_delete(pid):
    c=get_db()
    if c.execute("SELECT 1 FROM sales WHERE product_id=?",(pid,)).fetchone():c.close();return jsonify(ok=False,message="Product has sales history; edit it instead of deleting."),409
    c.execute("DELETE FROM products WHERE id=?",(pid,));c.commit();c.close();return jsonify(ok=True)

@app.get("/api/sales")
def api_sales():return jsonify(sales_list())
@app.post("/api/sales")
def api_sales_create():
    p=request.get_json(silent=True) or {}
    if p.get("message"):
        d=detect_sale(p["message"])
        if not d:return jsonify(ok=False,message="Could not detect the sale."),400
        if not p.get("confirm"):return jsonify(ok=True,confirmation_required=True,sale=d)
        p.update(d)
    try:return jsonify(ok=True,sale=save_sale(p))
    except ValueError as e:return jsonify(ok=False,message=str(e)),400
@app.post("/api/sales/manual")
def api_manual():return api_sales_create()
@app.put("/api/sales/<int:sid>")
def api_sales_update(sid):
    try:return jsonify(ok=True,sale=update_sale(sid,request.get_json(silent=True) or {}))
    except ValueError as e:return jsonify(ok=False,message=str(e)),400
@app.delete("/api/sales/<int:sid>")
def api_sales_delete(sid):
    c=get_db();x=c.execute("SELECT product_id,quantity FROM sales WHERE id=?",(sid,)).fetchone()
    if not x:c.close();return jsonify(ok=False,message="Sale not found."),404
    c.execute("UPDATE products SET stock=stock+? WHERE id=?",(x["quantity"],x["product_id"]));c.execute("DELETE FROM sales WHERE id=?",(sid,));c.commit();c.close();return jsonify(ok=True)
@app.get("/api/stock")
def api_stock():return jsonify(product_list())

@app.get("/api/reports")
def api_reports():return jsonify(rows("SELECT id,report_type,period_start,period_end,created_at FROM reports ORDER BY id DESC LIMIT 50"))
@app.post("/api/reports")
def api_report():
    p=request.get_json(silent=True) or {};d=dashboard(p.get("period","today"),p.get("start"),p.get("end"));c=get_db();cur=c.execute("INSERT INTO reports(report_type,period_start,period_end,payload) VALUES(?,?,?,?)",(p.get("report_type","Business Report"),d["range"]["start"],d["range"]["end"],json.dumps(d)));c.commit();c.close();return jsonify(ok=True,id=cur.lastrowid,report=d)

@app.get("/api/export")
def api_export():
    kind=request.args.get("type","sales_csv");d=dashboard(request.args.get("period","all"),request.args.get("start"),request.args.get("end"))
    sales=sales_between(d["range"]["start"],d["range"]["end"])
    headers=["ID","Date","Product","Category","Quantity","Selling Price","Cost Price","Revenue","Profit","Margin","Payment","Status"]
    data=[[s["id"],s["sold_at"],s["product"],s["category"],s["quantity"],s["selling_price"],s["cost_price"],s["revenue"],s["profit"],money(s["profit"]/s["revenue"]*100 if s["revenue"] else 0),s["payment_method"],s["status"]] for s in sales]
    if kind=="products_csv":
        out=io.StringIO();w=csv.writer(out);w.writerow(["Product","Category","Cost","Selling","Stock","Units Sold","Revenue","Profit","Margin"])
        for p in product_list():w.writerow([p["name"],p["category"],p["cost_price"],p["selling_price"],p["stock"],p["units_sold"],p["revenue"],p["profit"],money(p["profit"]/p["revenue"]*100 if p["revenue"] else 0)])
        return Response(out.getvalue(),mimetype="text/csv",headers={"Content-Disposition":"attachment; filename=products.csv"})
    if kind=="sales_excel":
        wb=Workbook();ws=wb.active;ws.title="Sales";ws.append(headers)
        for x in data:ws.append(x)
        for col in ws.columns:
            width=min(max(len(str(cell.value or "")) for cell in col)+2,28);ws.column_dimensions[col[0].column_letter].width=width
        buf=io.BytesIO();wb.save(buf);buf.seek(0)
        return Response(buf.getvalue(),mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",headers={"Content-Disposition":"attachment; filename=sales_flow.xlsx"})
    out=io.StringIO();w=csv.writer(out);w.writerow(headers)
    for x in data:w.writerow(x)
    return Response(out.getvalue(),mimetype="text/csv",headers={"Content-Disposition":"attachment; filename=sales.csv"})

@app.get("/api/settings")
def api_settings():return jsonify({x["key"]:x["value"] for x in rows("SELECT key,value FROM settings")})
@app.put("/api/settings")
def api_settings_update():
    p=request.get_json(silent=True) or {};c=get_db()
    for k,v in p.items():c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(k,str(v)))
    c.commit();c.close();return jsonify(ok=True)
@app.get("/api/search")
def api_search():
    q=request.args.get("q","").lower().strip()
    return jsonify(products=rows("SELECT * FROM products WHERE lower(name) LIKE ? OR lower(category) LIKE ? LIMIT 10",(f"%{q}%",f"%{q}%")),sales=rows("SELECT s.id,s.sold_at,p.name product,s.quantity,s.revenue,s.profit FROM sales s JOIN products p ON p.id=s.product_id WHERE lower(p.name) LIKE ? LIMIT 10",(f"%{q}%",)),reports=rows("SELECT id,report_type,created_at FROM reports WHERE lower(report_type) LIKE ? LIMIT 10",(f"%{q}%",)))
@app.post("/api/assistant")
def api_assistant():
    p=request.get_json(silent=True) or {};m=str(p.get("message","")).strip()
    if not m:return jsonify(ok=False,message="Please type a message."),400
    x=assistant(m);x.update(ok=True,assistant="FLOWI",source="SALES FLOW database");return jsonify(x)
@app.post("/api/reset")
def api_reset():
    c=get_db();c.execute("DELETE FROM sales");c.execute("DELETE FROM reports")
    for n,s in {"T-Shirt":100,"Shirt":75,"Jeans":50,"Shoes":30,"Laptop Bag":25,"Cap":120}.items():c.execute("UPDATE products SET stock=? WHERE name=?",(s,n))
    c.commit();c.close();return jsonify(ok=True)

init_db()
if __name__=="__main__":app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)),debug=False)
