from flask import Flask, render_template, request, jsonify, Response
import sqlite3, re, csv, io, os
from datetime import datetime, date
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB = BASE / 'sales.db'
app = Flask(__name__)


def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        cost_price REAL NOT NULL,
        selling_price REAL NOT NULL,
        stock INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS sales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sold_at TEXT NOT NULL,
        product_id INTEGER NOT NULL,
        quantity INTEGER NOT NULL,
        selling_price REAL NOT NULL,
        cost_price REAL NOT NULL,
        revenue REAL NOT NULL,
        profit REAL NOT NULL,
        FOREIGN KEY(product_id) REFERENCES products(id)
    );
    ''')
    count = conn.execute('SELECT COUNT(*) FROM products').fetchone()[0]
    if count == 0:
        products = [
            ('T-Shirt', 500, 800, 100),
            ('Shirt', 600, 1000, 75),
            ('Jeans', 900, 1400, 50),
            ('Shoes', 1200, 1800, 30),
            ('Cap', 150, 300, 120),
        ]
        conn.executemany('INSERT INTO products(name,cost_price,selling_price,stock) VALUES(?,?,?,?)', products)
    conn.commit(); conn.close()


def product_rows():
    conn = get_db(); rows = conn.execute('SELECT * FROM products ORDER BY name').fetchall(); conn.close(); return rows


def parse_sale(text):
    """Simple beginner-friendly natural language parser. Examples:
    'sold 5 tshirts', '5 t-shirts', 'sold 3 shirts for 3000', '2 jeans'
    """
    s = text.lower().strip()
    products = product_rows()
    found = None
    for p in products:
        name = p['name'].lower()
        variants = {name, name.replace('-', ' '), name.replace(' ', ''), name.replace('shirt','shirts')}
        if any(v and v in s for v in variants):
            found = p; break
    if not found:
        return None, 'I could not identify the product. Try: "sold 5 t-shirts".'
    qty_match = re.search(r'\b(\d+)\b', s)
    if not qty_match:
        return None, 'Please include quantity. Example: "sold 5 t-shirts".'
    qty = int(qty_match.group(1))
    if qty <= 0:
        return None, 'Quantity must be greater than 0.'
    # Optional total amount in message; otherwise use master selling price.
    amount_match = re.search(r'(?:for|total|amount)\s*(?:rs\.?|₹)?\s*(\d+(?:\.\d+)?)', s)
    if amount_match:
        total_amount = float(amount_match.group(1))
        sell = total_amount / qty
    else:
        sell = float(found['selling_price'])
    cost = float(found['cost_price'])
    revenue = sell * qty
    profit = (sell - cost) * qty
    return {'product': found, 'quantity': qty, 'selling_price': sell, 'cost_price': cost, 'revenue': revenue, 'profit': profit}, None


def add_sale(data):
    conn = get_db(); now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    p = data['product']
    conn.execute('INSERT INTO sales(sold_at,product_id,quantity,selling_price,cost_price,revenue,profit) VALUES(?,?,?,?,?,?,?)',
                 (now, p['id'], data['quantity'], data['selling_price'], data['cost_price'], data['revenue'], data['profit']))
    conn.execute('UPDATE products SET stock = MAX(stock - ?, 0) WHERE id=?', (data['quantity'], p['id']))
    conn.commit(); conn.close()
    return now


def summary(period='today'):
    conn = get_db()
    if period == 'today':
        where = "date(s.sold_at)=date('now','localtime')"
    elif period == 'week':
        where = "date(s.sold_at) >= date('now','localtime','-6 day')"
    elif period == 'month':
        where = "strftime('%Y-%m',s.sold_at)=strftime('%Y-%m','now','localtime')"
    else:
        where = '1=1'
    row = conn.execute(f'''SELECT COALESCE(SUM(quantity),0) items, COUNT(*) orders,
        COALESCE(SUM(revenue),0) revenue, COALESCE(SUM(quantity*cost_price),0) cost,
        COALESCE(SUM(profit),0) profit FROM sales s WHERE {where}''').fetchone()
    top = conn.execute(f'''SELECT p.name, SUM(s.quantity) qty, SUM(s.revenue) revenue
        FROM sales s JOIN products p ON p.id=s.product_id WHERE {where}
        GROUP BY p.id ORDER BY qty DESC LIMIT 1''').fetchone()
    conn.close()
    return {**dict(row), 'top_product': dict(top) if top else None}


@app.route('/')
def home():
    return render_template('index.html')

@app.get('/api/products')
def products():
    return jsonify([dict(r) for r in product_rows()])

@app.post('/api/sales')
def create_sale():
    payload = request.get_json(silent=True) or {}
    text = payload.get('message','')
    data, err = parse_sale(text)
    if err: return jsonify({'ok':False,'message':err}), 400
    now = add_sale(data)
    return jsonify({'ok':True,'message':f"Sale recorded: {data['quantity']} × {data['product']['name']}",
                    'sale': {'date':now,'product':data['product']['name'],'quantity':data['quantity'],
                             'selling_price':round(data['selling_price'],2),'revenue':round(data['revenue'],2),'profit':round(data['profit'],2)},
                    'summary': summary('today')})

@app.get('/api/summary')
def api_summary():
    return jsonify({p: summary(p) for p in ['today','week','month','all']})

@app.get('/api/sales')
def sales():
    conn = get_db(); rows = conn.execute('''SELECT s.*, p.name product FROM sales s JOIN products p ON p.id=s.product_id ORDER BY s.id DESC LIMIT 100''').fetchall(); conn.close()
    return jsonify([dict(r) for r in rows])

@app.get('/api/chart')
def chart():
    conn = get_db()
    daily = conn.execute("SELECT date(sold_at) day, SUM(revenue) revenue, SUM(profit) profit FROM sales GROUP BY day ORDER BY day DESC LIMIT 7").fetchall()
    by_product = conn.execute("SELECT p.name, SUM(s.quantity) qty, SUM(s.revenue) revenue, SUM(s.profit) profit FROM sales s JOIN products p ON p.id=s.product_id GROUP BY p.id ORDER BY revenue DESC").fetchall()
    conn.close()
    return jsonify({'daily':[dict(r) for r in reversed(daily)], 'products':[dict(r) for r in by_product]})

@app.get('/api/export')
def export_csv():
    conn = get_db(); rows = conn.execute('''SELECT s.sold_at, p.name product, s.quantity, s.selling_price, s.cost_price, s.revenue, s.profit FROM sales s JOIN products p ON p.id=s.product_id ORDER BY s.id DESC''').fetchall(); conn.close()
    out = io.StringIO(); w = csv.writer(out); w.writerow(['Date','Product','Quantity','Selling Price','Cost Price','Revenue','Profit'])
    for r in rows: w.writerow([r['sold_at'],r['product'],r['quantity'],r['selling_price'],r['cost_price'],r['revenue'],r['profit']])
    return Response(out.getvalue(), mimetype='text/csv', headers={'Content-Disposition':'attachment; filename=sales_report.csv'})

@app.post('/api/reset')
def reset():
    conn=get_db(); conn.execute('DELETE FROM sales'); conn.execute('UPDATE products SET stock=CASE name WHEN "T-Shirt" THEN 100 WHEN "Shirt" THEN 75 WHEN "Jeans" THEN 50 WHEN "Shoes" THEN 30 WHEN "Cap" THEN 120 ELSE stock END'); conn.commit(); conn.close(); return jsonify({'ok':True})

init_db()
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
