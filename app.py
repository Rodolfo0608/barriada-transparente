from flask import (
    Flask, render_template, request,
    redirect, session, Response
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
        comprobante TEXT,
        notas TEXT
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


# ==========================================================
# 🔧 VALIDACIÓN DE COLUMNA notas EN PAGOS
# ==========================================================

def ensure_pagos_notas_column():
    cur, conn = get_cursor()
    cur.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name='pagos'
          AND column_name='notas'
    """)
    existe = cur.fetchone()

    if not existe:
        cur.execute("ALTER TABLE pagos ADD COLUMN notas TEXT")
        conn.commit()

    conn.close()


# ==========================================================
# INICIALIZACIÓN
# ==========================================================

init_db()
crear_admin_si_no_existe()
ensure_pagos_notas_column()

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
            "SELECT COALESCE(SUM(monto),0) AS total FROM pagos WHERE casa=%s",
            (casa,)
        )
    else:
        cur.execute(
            "SELECT COALESCE(SUM(monto),0) AS total FROM pagos"
        )

    ingresos = cur.fetchone()['total']

    # GASTOS
    cur.execute("SELECT * FROM gastos ORDER BY fecha DESC")
    gastos = cur.fetchall()

    cur.execute("SELECT COALESCE(SUM(monto),0) AS total FROM gastos")
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


@app.route('/comite')
def comite():
    cur, conn = get_cursor()
    cur.execute("SELECT * FROM comite")
    data = cur.fetchall()
    conn.close()
    return render_template('comite.html', data=data)


@app.route('/requerimientos')
def requerimientos():
    cur, conn = get_cursor()
    cur.execute("SELECT * FROM requerimientos ORDER BY prioridad")
    data = cur.fetchall()
    conn.close()
    return render_template('requerimientos.html', data=data)


@app.route('/sugerencias', methods=['GET', 'POST'])
def sugerencias():
    cur, conn = get_cursor()

    if request.method == 'POST':
        texto = request.form.get('texto')

        if texto:
            cur.execute(
                "INSERT INTO sugerencias (texto, fecha) VALUES (%s, %s)",
                (texto, datetime.now())
            )
            conn.commit()

        conn.close()
        return redirect('/sugerencias')

    cur.execute("SELECT * FROM sugerencias ORDER BY fecha DESC")
    data = cur.fetchall()
    conn.close()

    return render_template('sugerencias.html', data=data)


@app.route('/estado-cuenta/excel')
def estado_cuenta_excel():
    casa = request.args.get('casa')
    cur, conn = get_cursor()

    # =========================
    # PAGOS
    # =========================
    if casa:
        cur.execute(
            "SELECT casa, monto, fecha, comprobante, notas FROM pagos WHERE casa=%s ORDER BY fecha DESC",
            (casa,)
        )
    else:
        cur.execute(
            "SELECT casa, monto, fecha, comprobante, notas FROM pagos ORDER BY fecha DESC"
        )

    pagos = cur.fetchall()

    # =========================
    # GASTOS
    # =========================
    cur.execute(
        "SELECT descripcion, monto, fecha, factura FROM gastos ORDER BY fecha DESC"
    )
    gastos = cur.fetchall()

    conn.close()

    # =========================
    # EXCEL
    # =========================
    wb = Workbook()

    # --- Hoja Pagos ---
    ws = wb.active
    ws.title = "Pagos"
    ws.append(["Casa", "Monto", "Fecha", "Notas", "Comprobante"])

    for p in pagos:
        ws.append([
            p['casa'],
            float(p['monto']),
            str(p['fecha']),
            p['notas'] or "",
            p['comprobante'] or ""
        ])

    # --- Hoja Gastos ---
    ws2 = wb.create_sheet("Gastos")
    ws2.append(["Descripción", "Monto", "Fecha", "Factura"])

    for g in gastos:
        ws2.append([
            g['descripcion'],
            float(g['monto']),
            str(g['fecha']),
            g['factura'] or ""
        ])

    # =========================
    # RESPUESTA
    # =========================
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
# 🔐 PAGO – SOLO ADMIN
# ==========================================================

@app.route('/admin/pago', methods=['GET', 'POST'])
@admin_required
def admin_pago():
    cur, conn = get_cursor()

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

        cur.execute("""
            INSERT INTO pagos (casa, monto, fecha, comprobante, notas)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            casa,
            monto,
            datetime.now().date(),
            url,
            request.form.get('notas')
        ))
        conn.commit()

        return redirect('/admin/pago')

    # 🔑 ESTO ERA LO QUE FALTABA
    cur.execute("SELECT * FROM pagos ORDER BY fecha DESC")
    data = cur.fetchall()
    conn.close()

    return render_template('admin_pago.html', data=data)


# ==========================================================
# ADMINISTRACIÓN (MINUTA / GASTO / COMITÉ)
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
            url = cloudinary.uploader.upload(
                archivo,
                resource_type="auto",
                folder="barriada/minutas"
            )["secure_url"]

        cur, conn = get_cursor()
        cur.execute("""
            INSERT INTO minutas (titulo, resumen, archivo, fecha)
            VALUES (%s, %s, %s, %s)
        """, (titulo, resumen, url, datetime.now().date()))
        conn.commit()
        conn.close()

        return redirect('/admin/minuta')

    cur, conn = get_cursor()
    cur.execute("SELECT * FROM minutas ORDER BY fecha DESC")
    data = cur.fetchall()
    conn.close()

    return render_template('admin_minuta.html', data=data)


@app.route('/admin/gasto', methods=['GET', 'POST'])
@admin_required
def admin_gasto():
    if request.method == 'POST':
        descripcion = request.form['descripcion']
        monto = request.form['monto']
        archivo = request.files['factura']

        url = None
        if archivo and archivo.filename:
            url = cloudinary.uploader.upload(
                archivo,
                resource_type="auto",
                folder="barriada/gastos"
            )["secure_url"]

        cur, conn = get_cursor()
        cur.execute("""
            INSERT INTO gastos (descripcion, monto, fecha, factura)
            VALUES (%s, %s, %s, %s)
        """, (descripcion, monto, datetime.now().date(), url))
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
            url = cloudinary.uploader.upload(
                archivo,
                resource_type="image",
                folder="barriada/comite"
            )["secure_url"]

        cur, conn = get_cursor()
        cur.execute("""
            INSERT INTO comite (nombre, cargo, casa, foto)
            VALUES (%s, %s, %s, %s)
        """, (nombre, cargo, casa, url))
        conn.commit()
        conn.close()

        return redirect('/admin/comite')

    cur, conn = get_cursor()
    cur.execute("SELECT * FROM comite ORDER BY nombre")
    data = cur.fetchall()
    conn.close()

    return render_template('admin_comite.html', data=data)

# ==========================================================
# 🗑️ DELETE – SOLO ADMIN
# ==========================================================

@app.route('/admin/delete/pago/<int:id>', methods=['POST'])
@admin_required
def delete_pago(id):
    cur, conn = get_cursor()
    cur.execute("DELETE FROM pagos WHERE id=%s", (id,))
    conn.commit()
    conn.close()
    return redirect('/admin/pago')


@app.route('/admin/delete/minuta/<int:id>', methods=['POST'])
@admin_required
def delete_minuta(id):
    cur, conn = get_cursor()
    cur.execute("DELETE FROM minutas WHERE id=%s", (id,))
    conn.commit()
    conn.close()
    return redirect('/minutas')


@app.route('/admin/delete/gasto/<int:id>', methods=['POST'])
@admin_required
def delete_gasto(id):
    cur, conn = get_cursor()
    cur.execute("DELETE FROM gastos WHERE id=%s", (id,))
    conn.commit()
    conn.close()
    return redirect('/estado-cuenta')


@app.route('/admin/delete/comite/<int:id>', methods=['POST'])
@admin_required
def delete_comite(id):
    cur, conn = get_cursor()
    cur.execute("DELETE FROM comite WHERE id=%s", (id,))
    conn.commit()
    conn.close()
    return redirect('/comite')


@app.route('/admin/delete/requerimiento/<int:id>', methods=['POST'])
@admin_required
def delete_requerimiento(id):
    cur, conn = get_cursor()
    cur.execute("DELETE FROM requerimientos WHERE id=%s", (id,))
    conn.commit()
    conn.close()
    return redirect('/requerimientos')

# ==========================================================
# LOGIN / LOGOUT
# ==========================================================

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        cur, conn = get_cursor()
        cur.execute(
            "SELECT * FROM usuarios WHERE usuario=%s AND password=%s",
            (request.form['usuario'], request.form['password'])
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


if __name__ == '__main__':
    app.run(debug=True)
