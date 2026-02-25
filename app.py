from flask import (
    Flask, render_template, request,
    redirect, session, Response, jsonify
)
from openpyxl import Workbook
import io
import psycopg2
import psycopg2.extras
import os
from datetime import datetime, date
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
        notas TEXT,
        cuota_id INTEGER
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

    CREATE TABLE IF NOT EXISTS cuotas (
        id SERIAL PRIMARY KEY,
        descripcion TEXT NOT NULL,
        monto NUMERIC NOT NULL,
        fecha_vencimiento DATE NOT NULL,
        tipo TEXT DEFAULT 'mensual',
        activa BOOLEAN DEFAULT TRUE
    );
    """)
    conn.commit()

    # Add cuota_id column to pagos if it does not exist
    try:
        cur.execute("ALTER TABLE pagos ADD COLUMN IF NOT EXISTS cuota_id INTEGER;")
        conn.commit()
    except Exception:
        conn.rollback()

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
    cur, conn = get_cursor()

    # INGRESOS TOTALES
    cur.execute("SELECT COALESCE(SUM(monto),0) AS total FROM pagos")
    ingresos = cur.fetchone()['total']

    # GASTOS
    cur.execute("SELECT * FROM gastos ORDER BY fecha DESC")
    gastos = cur.fetchall()

    cur.execute("SELECT COALESCE(SUM(monto),0) AS total FROM gastos")
    egresos = cur.fetchone()['total']

    # CUOTAS ACTIVAS
    cur.execute("SELECT * FROM cuotas WHERE activa=TRUE ORDER BY fecha_vencimiento DESC")
    cuotas = cur.fetchall()

    # PAGOS TOTALES POR CASA
    cur.execute("""
        SELECT casa, COALESCE(SUM(monto),0) as total_pagado
        FROM pagos GROUP BY casa
    """)
    pagos_por_casa_raw = cur.fetchall()
    pagos_por_casa = {}
    for r in pagos_por_casa_raw:
        try:
            key = int(r['casa'])
        except (ValueError, TypeError):
            key = r['casa']
        pagos_por_casa[key] = float(r['total_pagado'])

    # Total cuotas activas
    cur.execute("SELECT COALESCE(SUM(monto),0) as total FROM cuotas WHERE activa=TRUE")
    total_cuotas = float(cur.fetchone()['total'] or 0)

    conn.close()

    return render_template(
        'estado_cuenta.html',
        gastos=gastos,
        ingresos=ingresos,
        gastos_total=egresos,
        disponible=ingresos - egresos,
        cuotas=cuotas,
        pagos_por_casa=pagos_por_casa,
        total_cuotas=total_cuotas
    )


@app.route('/api/estado-casa/<int:numero_casa>')
def api_estado_casa(numero_casa):
    """API JSON: estado de cuenta de una casa específica"""
    cur, conn = get_cursor()

    casa_str = str(numero_casa)

    # Pagos de esta casa
    cur.execute("""
        SELECT p.id, p.casa, p.monto, p.fecha, p.notas, p.comprobante,
               c.descripcion as cuota_desc
        FROM pagos p
        LEFT JOIN cuotas c ON p.cuota_id = c.id
        WHERE p.casa = %s
        ORDER BY p.fecha DESC
    """, (casa_str,))
    pagos = []
    for r in cur.fetchall():
        row = dict(r)
        if row.get('fecha'):
            row['fecha'] = str(row['fecha'])
        row['monto'] = float(row['monto'])
        pagos.append(row)

    # Total pagado
    cur.execute("SELECT COALESCE(SUM(monto),0) as total FROM pagos WHERE casa=%s", (casa_str,))
    total_pagado = float(cur.fetchone()['total'])

    # Cuotas activas
    cur.execute("SELECT * FROM cuotas WHERE activa=TRUE ORDER BY fecha_vencimiento")
    cuotas_activas = []
    for r in cur.fetchall():
        row = dict(r)
        if row.get('fecha_vencimiento'):
            row['fecha_vencimiento'] = str(row['fecha_vencimiento'])
        row['monto'] = float(row['monto'])
        cuotas_activas.append(row)

    total_cuotas = sum(c['monto'] for c in cuotas_activas)

    # Cuotas pagadas (por cuota_id)
    cur.execute(
        "SELECT DISTINCT cuota_id FROM pagos WHERE casa=%s AND cuota_id IS NOT NULL",
        (casa_str,)
    )
    cuotas_pagadas_ids = {r['cuota_id'] for r in cur.fetchall()}

    cuotas_pendientes = [c for c in cuotas_activas if c['id'] not in cuotas_pagadas_ids]

    conn.close()

    return jsonify({
        'casa': numero_casa,
        'total_pagado': total_pagado,
        'total_debe': max(0, total_cuotas - total_pagado),
        'total_cuotas': total_cuotas,
        'pagos': pagos,
        'cuotas_pendientes': cuotas_pendientes
    })


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
    cur, conn = get_cursor()

    cur.execute("SELECT * FROM pagos ORDER BY fecha DESC")
    pagos = cur.fetchall()
    cur.execute("SELECT * FROM gastos ORDER BY fecha DESC")
    gastos = cur.fetchall()
    conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Pagos"
    ws.append(["Casa", "Monto", "Fecha", "Notas", "Comprobante"])

    for p in pagos:
        ws.append([p['casa'], float(p['monto']), str(p['fecha']),
                   p['notas'] or "", p['comprobante'] or ""])

    ws2 = wb.create_sheet("Gastos")
    ws2.append(["Descripción", "Monto", "Fecha", "Factura"])

    for g in gastos:
        ws2.append([g['descripcion'], float(g['monto']), str(g['fecha']),
                    g['factura'] or ""])

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    return Response(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=estado_cuenta_general.xlsx"}
    )

# ==========================================================
# ADMIN – PAGO
# ==========================================================

@app.route('/admin/pago', methods=['GET', 'POST'])
@admin_required
def admin_pago():
    if request.method == 'POST':
        casa = request.form['casa']
        monto = request.form['monto']
        cuota_id = request.form.get('cuota_id') or None
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
            INSERT INTO pagos (casa, monto, fecha, comprobante, notas, cuota_id)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (casa, monto, datetime.now().date(), url,
              request.form.get('notas'), cuota_id))
        conn.commit()
        conn.close()

        return redirect('/estado-cuenta')

    cur, conn = get_cursor()
    cur.execute("SELECT * FROM pagos ORDER BY fecha DESC")
    pagos = cur.fetchall()
    cur.execute("SELECT * FROM cuotas WHERE activa=TRUE ORDER BY fecha_vencimiento")
    cuotas = cur.fetchall()
    conn.close()
    return render_template('admin_pago.html', pagos=pagos, cuotas=cuotas)


# ==========================================================
# ADMIN – MINUTA
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

        return redirect('/minutas')

    cur, conn = get_cursor()
    cur.execute("SELECT * FROM minutas ORDER BY fecha DESC")
    minutas_list = cur.fetchall()
    conn.close()
    return render_template('admin_minuta.html', minutas=minutas_list)


# ==========================================================
# ADMIN – GASTO
# ==========================================================

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

    cur, conn = get_cursor()
    cur.execute("SELECT * FROM gastos ORDER BY fecha DESC")
    gastos = cur.fetchall()
    conn.close()
    return render_template('admin_gasto.html', gastos=gastos)


# ==========================================================
# ADMIN – COMITÉ
# ==========================================================

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

        return redirect('/comite')

    cur, conn = get_cursor()
    cur.execute("SELECT * FROM comite ORDER BY nombre")
    miembros = cur.fetchall()
    conn.close()
    return render_template('admin_comite.html', miembros=miembros)


# ==========================================================
# ADMIN – CUOTAS
# ==========================================================

@app.route('/admin/cuotas', methods=['GET', 'POST'])
@admin_required
def admin_cuotas():
    cur, conn = get_cursor()

    if request.method == 'POST':
        descripcion = request.form['descripcion']
        monto = request.form['monto']
        fecha_vencimiento = request.form['fecha_vencimiento']
        tipo = request.form.get('tipo', 'mensual')

        cur.execute("""
            INSERT INTO cuotas (descripcion, monto, fecha_vencimiento, tipo, activa)
            VALUES (%s, %s, %s, %s, TRUE)
        """, (descripcion, monto, fecha_vencimiento, tipo))
        conn.commit()
        conn.close()
        return redirect('/admin/cuotas')

    cur.execute("SELECT * FROM cuotas ORDER BY fecha_vencimiento DESC")
    cuotas = cur.fetchall()
    conn.close()
    return render_template('admin_cuotas.html', cuotas=cuotas)


# ==========================================================
# DELETE – SOLO ADMIN
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
    return redirect('/admin/minuta')


@app.route('/admin/delete/gasto/<int:id>', methods=['POST'])
@admin_required
def delete_gasto(id):
    cur, conn = get_cursor()
    cur.execute("DELETE FROM gastos WHERE id=%s", (id,))
    conn.commit()
    conn.close()
    return redirect('/admin/gasto')


@app.route('/admin/delete/comite/<int:id>', methods=['POST'])
@admin_required
def delete_comite(id):
    cur, conn = get_cursor()
    cur.execute("DELETE FROM comite WHERE id=%s", (id,))
    conn.commit()
    conn.close()
    return redirect('/admin/comite')


@app.route('/admin/delete/requerimiento/<int:id>', methods=['POST'])
@admin_required
def delete_requerimiento(id):
    cur, conn = get_cursor()
    cur.execute("DELETE FROM requerimientos WHERE id=%s", (id,))
    conn.commit()
    conn.close()
    return redirect('/requerimientos')


@app.route('/admin/delete/cuota/<int:id>', methods=['POST'])
@admin_required
def delete_cuota(id):
    cur, conn = get_cursor()
    cur.execute("DELETE FROM cuotas WHERE id=%s", (id,))
    conn.commit()
    conn.close()
    return redirect('/admin/cuotas')


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
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

#if __name__ == '__main__':
#    app.run(debug=True)
