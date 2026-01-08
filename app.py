from flask import (
    Flask, render_template, request,
    redirect, url_for, session, Response
)
from openpyxl import Workbook
import io
import psycopg2
import psycopg2.extras
import os
from datetime import datetime
from functools import wraps

# Cloudinary
import cloudinary
import cloudinary.uploader

# ==========================================================
# CONFIGURACIÓN GENERAL
# ==========================================================

app = Flask(__name__)
app.secret_key = 'barriada-segura'

DATABASE_URL = os.environ.get("DATABASE_URL")

# ==========================================================
# CLOUDINARY CONFIG
# ==========================================================

cloudinary.config(
    cloud_name=os.environ.get("CLOUDINARY_CLOUD_NAME"),
    api_key=os.environ.get("CLOUDINARY_API_KEY"),
    api_secret=os.environ.get("CLOUDINARY_API_SECRET"),
    secure=True
)

# ==========================================================
# CONTEXTO GLOBAL PARA TEMPLATES
# ==========================================================

@app.context_processor
def inject_session():
    return dict(session=session)

# ==========================================================
# BASE DE DATOS (POSTGRESQL)
# ==========================================================

def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode='require')


def get_cursor():
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    return cur, conn


def init_db():
    cur, conn = get_cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS minutas (
        id SERIAL PRIMARY KEY,
        titulo TEXT,
        resumen TEXT,
        archivo TEXT,
        fecha DATE
    );

    CREATE TABLE IF NOT EXISTS requerimientos (
        id SERIAL PRIMARY KEY,
        descripcion TEXT,
        prioridad INTEGER,
        estado TEXT
    );

    CREATE TABLE IF NOT EXISTS comite (
        id SERIAL PRIMARY KEY,
        nombre TEXT,
        cargo TEXT,
        casa TEXT,
        foto TEXT
    );

    CREATE TABLE IF NOT EXISTS sugerencias (
        id SERIAL PRIMARY KEY,
        texto TEXT,
        fecha TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS pagos (
        id SERIAL PRIMARY KEY,
        casa TEXT,
        monto NUMERIC,
        fecha DATE,
        comprobante TEXT
    );

    CREATE TABLE IF NOT EXISTS gastos (
        id SERIAL PRIMARY KEY,
        descripcion TEXT,
        monto NUMERIC,
        fecha DATE,
        factura TEXT
    );

    CREATE TABLE IF NOT EXISTS usuarios (
        id SERIAL PRIMARY KEY,
        usuario TEXT UNIQUE,
        password TEXT,
        rol TEXT
    );
    """)
    conn.commit()
    conn.close()


def crear_admin_si_no_existe():
    cur, conn = get_cursor()
    cur.execute("SELECT 1 FROM usuarios WHERE rol='admin'")
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO usuarios (usuario, password, rol) VALUES (%s, %s, %s)",
            ("admin", "admin123", "admin")
        )
        conn.commit()
    conn.close()


init_db()
crear_admin_si_no_existe()

# ==========================================================
# SEGURIDAD
# ==========================================================

def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if session.get('rol') != 'admin':
            return redirect('/login')
        return f(*args, **kwargs)
    return wrapper

# ==========================================================
# RUTAS PÚBLICAS
# ==========================================================

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/minutas')
def minutas():
    cur, conn = get_cursor()
    cur.execute("SELECT * FROM minutas ORDER BY fecha DESC")
    data = cur.fetchall()
    conn.close()
    return render_template('minutas.html', data=data)


@app.route('/estado-cuenta')
def estado_cuenta():
    casa = request.args.get('casa')

    cur, conn = get_cursor()

    # PAGOS
    if casa:
        cur.execute(
            "SELECT * FROM pagos WHERE casa=%s ORDER BY fecha DESC",
            (casa,)
        )
    else:
        cur.execute("SELECT * FROM pagos ORDER BY fecha DESC")

    pagos = cur.fetchall()

    # INGRESOS
    if casa:
        cur.execute(
            "SELECT COALESCE(SUM(monto),0) total FROM pagos WHERE casa=%s",
            (casa,)
        )
    else:
        cur.execute("SELECT COALESCE(SUM(monto),0) total FROM pagos")

    ingresos = cur.fetchone()['total']

    # GASTOS (siempre globales)
    cur.execute("SELECT * FROM gastos ORDER BY fecha DESC")
    gastos = cur.fetchall()

    cur.execute("SELECT COALESCE(SUM(monto),0) total FROM gastos")
    egresos = cur.fetchone()['total']

    conn.close()

    return render_template(
        'estado_cuenta.html',
        pagos=pagos,
        gastos=gastos,
        ingresos=ingresos,
        gastos_total=egresos,
        disponible=ingresos - egresos,
        casa=casa
    )

@app.route('/estado-cuenta/excel')
def estado_cuenta_excel():
    casa = request.args.get('casa')

    cur, conn = get_cursor()

    if casa:
        cur.execute(
            "SELECT * FROM pagos WHERE casa=%s ORDER BY fecha DESC",
            (casa,)
        )
    else:
        cur.execute("SELECT * FROM pagos ORDER BY fecha DESC")

    pagos = cur.fetchall()

    cur.execute("SELECT * FROM gastos ORDER BY fecha DESC")
    gastos = cur.fetchall()

    conn.close()

    # Crear Excel
    wb = Workbook()
    ws_pagos = wb.active
    ws_pagos.title = "Pagos"

    ws_pagos.append(["Casa", "Monto", "Fecha", "Comprobante"])
    for p in pagos:
        ws_pagos.append([
            p['casa'],
            float(p['monto']),
            str(p['fecha']),
            p['comprobante'] or ''
        ])

    ws_gastos = wb.create_sheet("Gastos")
    ws_gastos.append(["Descripción", "Monto", "Fecha", "Factura"])
    for g in gastos:
        ws_gastos.append([
            g['descripcion'],
            float(g['monto']),
            str(g['fecha']),
            g['factura'] or ''
        ])

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    nombre = f"estado_cuenta_{casa}.xlsx" if casa else "estado_cuenta_general.xlsx"

    return Response(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename={nombre}"
        }
    )

# ==========================================================
# PAGO (CARGA LIBRE)
# ==========================================================

@app.route('/pago', methods=['GET', 'POST'])
def pago():
    if request.method == 'POST':
        casa = request.form['casa']
        monto = request.form['monto']
        archivo = request.files['comprobante']

        url = None
        if archivo and archivo.filename:
            result = cloudinary.uploader.upload(
                archivo,
                resource_type="auto",
                folder="barriada/pagos"
            )
            url = result["secure_url"]

        cur, conn = get_cursor()
        cur.execute("""
            INSERT INTO pagos (casa, monto, fecha, comprobante)
            VALUES (%s, %s, %s, %s)
        """, (
            casa,
            monto,
            datetime.now().date(),
            url
        ))
        conn.commit()
        conn.close()

        return redirect('/estado-cuenta')

    return render_template('admin_pago.html')

# ==========================================================
# ADMINISTRACIÓN
# ==========================================================

@app.route('/admin/minuta', methods=['GET', 'POST'])
@admin_required
def admin_minuta():
    if request.method == 'POST':
        titulo = request.form['titulo']
        resumen = request.form['resumen']
        archivo = request.files['archivo']

        url = None
        if archivo and archivo.filename:
            result = cloudinary.uploader.upload(
                archivo,
                resource_type="auto",
                folder="barriada/minutas"
            )
            url = result["secure_url"]

        cur, conn = get_cursor()
        cur.execute("""
            INSERT INTO minutas (titulo, resumen, archivo, fecha)
            VALUES (%s, %s, %s, %s)
        """, (
            titulo,
            resumen,
            url,
            datetime.now().date()
        ))
        conn.commit()
        conn.close()

        return redirect('/minutas')

    return render_template('admin_minuta.html')


@app.route('/admin/gasto', methods=['GET', 'POST'])
@admin_required
def admin_gasto():
    if request.method == 'POST':
        descripcion = request.form['descripcion']
        monto = request.form['monto']
        archivo = request.files['factura']

        url = None
        if archivo and archivo.filename:
            result = cloudinary.uploader.upload(
                archivo,
                resource_type="auto",
                folder="barriada/gastos"
            )
            url = result["secure_url"]

        cur, conn = get_cursor()
        cur.execute("""
            INSERT INTO gastos (descripcion, monto, fecha, factura)
            VALUES (%s, %s, %s, %s)
        """, (
            descripcion,
            monto,
            datetime.now().date(),
            url
        ))
        conn.commit()
        conn.close()

        return redirect('/estado-cuenta')

    return render_template('admin_gasto.html')


@app.route('/admin/comite', methods=['GET', 'POST'])
@admin_required
def admin_comite():
    if request.method == 'POST':
        nombre = request.form['nombre']
        cargo = request.form['cargo']
        casa = request.form['casa']
        archivo = request.files['foto']

        url = None
        if archivo and archivo.filename:
            result = cloudinary.uploader.upload(
                archivo,
                resource_type="image",
                folder="barriada/comite"
            )
            url = result["secure_url"]

        cur, conn = get_cursor()
        cur.execute("""
            INSERT INTO comite (nombre, cargo, casa, foto)
            VALUES (%s, %s, %s, %s)
        """, (
            nombre,
            cargo,
            casa,
            url
        ))
        conn.commit()
        conn.close()

        return redirect('/comite')

    return render_template('admin_comite.html')

# ==========================================================
# LOGIN / LOGOUT
# ==========================================================

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None

    if request.method == 'POST':
        user = request.form['usuario']
        pwd = request.form['password']

        cur, conn = get_cursor()
        cur.execute(
            "SELECT * FROM usuarios WHERE usuario=%s AND password=%s",
            (user, pwd)
        )
        u = cur.fetchone()
        conn.close()

        if u:
            session['usuario'] = u['usuario']
            session['rol'] = u['rol']
            return redirect('/')
        else:
            error = "Usuario o contraseña incorrectos"

    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

# ==========================================================

if __name__ == '__main__':
    app.run(debug=True)
