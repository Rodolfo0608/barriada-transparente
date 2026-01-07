from flask import Flask, render_template, request, redirect, url_for
import sqlite3
import os
from werkzeug.utils import secure_filename
from datetime import datetime

app = Flask(__name__)
DB = 'barriada.db'
UPLOAD_FOLDER = 'static/uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# ---------- DB ----------

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    db = get_db()
    db.executescript('''
    CREATE TABLE IF NOT EXISTS minutas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        titulo TEXT,
        resumen TEXT,
        archivo TEXT,
        fecha TEXT
    );

    CREATE TABLE IF NOT EXISTS requerimientos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        descripcion TEXT,
        prioridad INTEGER,
        estado TEXT
    );

    CREATE TABLE IF NOT EXISTS comite (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT,
        cargo TEXT,
        casa TEXT,
        foto TEXT
    );

    CREATE TABLE IF NOT EXISTS sugerencias (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        texto TEXT,
        fecha TEXT
    );

    CREATE TABLE IF NOT EXISTS pagos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        casa TEXT,
        monto REAL,
        fecha TEXT,
        comprobante TEXT
    );

    CREATE TABLE IF NOT EXISTS gastos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        descripcion TEXT,
        monto REAL,
        fecha TEXT,
        factura TEXT
    );
    ''')
    db.commit()

# ---------- RUTAS ----------

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/minutas')
def minutas():
    db = get_db()
    data = db.execute('SELECT * FROM minutas ORDER BY fecha DESC').fetchall()
    return render_template('minutas.html', data=data)

@app.route('/admin/minuta', methods=['GET', 'POST'])
def admin_minuta():
    if request.method == 'POST':
        titulo = request.form['titulo']
        resumen = request.form['resumen']
        archivo = request.files['archivo']

        filename = None
        if archivo and archivo.filename != '':
            filename = secure_filename(archivo.filename)
            archivo.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        db = get_db()
        db.execute("""
            INSERT INTO minutas (titulo, resumen, archivo, fecha)
            VALUES (?, ?, ?, ?)
        """, (
            titulo,
            resumen,
            f"/static/uploads/{filename}" if filename else None,
            datetime.now().strftime('%Y-%m-%d')
        ))
        db.commit()

        return redirect(url_for('minutas'))

    return render_template('admin_minuta.html')

@app.route('/admin/pago', methods=['GET', 'POST'])
def admin_pago():
    if request.method == 'POST':
        casa = request.form['casa']
        monto = request.form['monto']
        archivo = request.files['comprobante']

        filename = None
        if archivo and archivo.filename != '':
            filename = secure_filename(archivo.filename)
            archivo.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        db = get_db()
        db.execute("""
            INSERT INTO pagos (casa, monto, fecha, comprobante)
            VALUES (?, ?, ?, ?)
        """, (
            casa,
            monto,
            datetime.now().strftime('%Y-%m-%d'),
            f"/static/uploads/{filename}" if filename else None
        ))
        db.commit()

        return redirect(url_for('estado_cuenta'))

    return render_template('admin_pago.html')

@app.route('/admin/gasto', methods=['GET', 'POST'])
def admin_gasto():
    if request.method == 'POST':
        descripcion = request.form['descripcion']
        monto = request.form['monto']
        archivo = request.files['factura']

        filename = None
        if archivo and archivo.filename != '':
            filename = secure_filename(archivo.filename)
            archivo.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        db = get_db()
        db.execute("""
            INSERT INTO gastos (descripcion, monto, fecha, factura)
            VALUES (?, ?, ?, ?)
        """, (
            descripcion,
            monto,
            datetime.now().strftime('%Y-%m-%d'),
            f"/static/uploads/{filename}" if filename else None
        ))
        db.commit()

        return redirect(url_for('estado_cuenta'))

    return render_template('admin_gasto.html')

@app.route('/admin/comite', methods=['GET', 'POST'])
def admin_comite():
    if request.method == 'POST':
        nombre = request.form['nombre']
        cargo = request.form['cargo']
        casa = request.form['casa']
        archivo = request.files['foto']

        filename = None
        if archivo and archivo.filename != '':
            filename = secure_filename(archivo.filename)
            archivo.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        db = get_db()
        db.execute("""
            INSERT INTO comite (nombre, cargo, casa, foto)
            VALUES (?, ?, ?, ?)
        """, (
            nombre,
            cargo,
            casa,
            f"/static/uploads/{filename}" if filename else None
        ))
        db.commit()

        return redirect(url_for('comite'))

    return render_template('admin_comite.html')

@app.route('/estado-cuenta')
def estado_cuenta():
    db = get_db()

    pagos = db.execute('SELECT * FROM pagos ORDER BY fecha DESC').fetchall()
    gastos = db.execute('SELECT * FROM gastos ORDER BY fecha DESC').fetchall()

    total_ingresos = db.execute('SELECT SUM(monto) FROM pagos').fetchone()[0] or 0
    total_gastos = db.execute('SELECT SUM(monto) FROM gastos').fetchone()[0] or 0

    disponible = total_ingresos - total_gastos

    return render_template(
        'estado_cuenta.html',
        pagos=pagos,
        gastos=gastos,
        ingresos=total_ingresos,
        gastos_total=total_gastos,
        disponible=disponible
    )

@app.route('/requerimientos')
def requerimientos():
    db = get_db()
    data = db.execute('SELECT * FROM requerimientos ORDER BY prioridad').fetchall()
    return render_template('requerimientos.html', data=data)

@app.route('/comite')
def comite():
    db = get_db()
    data = db.execute('SELECT * FROM comite').fetchall()
    return render_template('comite.html', data=data)

@app.route('/sugerencias', methods=['GET', 'POST'])
def sugerencias():
    db = get_db()
    if request.method == 'POST':
        texto = request.form['texto']
        db.execute('INSERT INTO sugerencias(texto, fecha) VALUES (?, ?)',
                   (texto, datetime.now().strftime('%Y-%m-%d %H:%M')))
        db.commit()
        return redirect(url_for('sugerencias'))

    data = db.execute('SELECT * FROM sugerencias ORDER BY fecha DESC').fetchall()
    return render_template('sugerencias.html', data=data)

if __name__ == '__main__':
    init_db()
    app.run()

