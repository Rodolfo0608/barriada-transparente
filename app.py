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
from supabase import create_client
import uuid


# ==========================================================
# CONFIGURACIÓN GENERAL
# ==========================================================

app = Flask(__name__)
app.secret_key = 'barriada-segura'

DATABASE_URL = os.environ.get("DATABASE_URL")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_BUCKET = os.environ.get("SUPABASE_BUCKET")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
TOTAL_CASAS = 250


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
    
    CREATE TABLE IF NOT EXISTS cuotas (
        id SERIAL PRIMARY KEY,
        nombre TEXT NOT NULL,
        monto NUMERIC NOT NULL,
        fecha DATE
    );

    CREATE TABLE IF NOT EXISTS cuota_pagos (
        id SERIAL PRIMARY KEY,
        cuota_id INTEGER REFERENCES cuotas(id) ON DELETE CASCADE,
        casa INTEGER,
        monto NUMERIC,
        comprobante TEXT,
        fecha DATE
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
    
def ensure_pagos_notas_column():
    cur, conn = get_cursor()
    cur.execute("""
        ALTER TABLE pagos
        ADD COLUMN IF NOT EXISTS notas TEXT
    """)
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


def subir_a_supabase(file, carpeta):
    # Extensión segura
    ext = file.filename.rsplit('.', 1)[-1].lower()

    # Nombre único por archivo
    nombre = f"{carpeta}/{uuid.uuid4()}.{ext}"

    # Leer contenido UNA sola vez
    contenido = file.read()

    # Subir a Supabase Storage
    supabase.storage.from_(SUPABASE_BUCKET).upload(
        nombre,
        contenido,
        file_options={
            "content-type": file.mimetype,
            "upsert": False
        }
    )

    # URL pública
    return supabase.storage.from_(SUPABASE_BUCKET).get_public_url(nombre)


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
            # 📦 Subida a Supabase Storage
            url = subir_a_supabase(archivo, "pagos")

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

    # 🔑 listado de pagos
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
            url = subir_a_supabase(archivo, "minutas")

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
            # 📦 Subida a Supabase Storage
            url = subir_a_supabase(archivo, "gastos")

        cur, conn = get_cursor()
        cur.execute("""
            INSERT INTO gastos (descripcion, monto, fecha, factura)
            VALUES (%s, %s, %s, %s)
        """, (descripcion, monto, datetime.now().date(), url))
        conn.commit()
        conn.close()

        return redirect('/admin/gasto')

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
            # 📦 Subida a Supabase Storage
            url = subir_a_supabase(archivo, "comite")

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

    
@app.route('/admin/cuota', methods=['GET', 'POST'])
@admin_required
def admin_cuota():
    if request.method == 'POST':
        nombre = request.form['nombre']
        monto = request.form['monto']

        cur, conn = get_cursor()
        cur.execute("""
            INSERT INTO cuotas (nombre, monto, fecha)
            VALUES (%s, %s, %s)
        """, (nombre, monto, datetime.now().date()))
        conn.commit()
        conn.close()

        return redirect('/admin/cuotas')

    return render_template('admin_cuota.html')


@app.route('/admin/cuotas')
@admin_required
def admin_cuotas():
    cur, conn = get_cursor()
    cur.execute("SELECT * FROM cuotas ORDER BY fecha DESC")
    data = cur.fetchall()
    conn.close()

    return render_template('admin_cuotas.html', data=data)


@app.route('/admin/cuota/<int:cuota_id>')
@admin_required
def admin_cuota_detalle(cuota_id):
    cur, conn = get_cursor()

    cur.execute("SELECT * FROM cuotas WHERE id=%s", (cuota_id,))
    cuota = cur.fetchone()

    cur.execute("""
        SELECT casa, monto, comprobante
        FROM cuota_pagos
        WHERE cuota_id=%s
    """, (cuota_id,))
    pagos = {p['casa']: p for p in cur.fetchall()}
    conn.close()

    casas = []
    for i in range(1, TOTAL_CASAS + 1):
        casas.append({
            "numero": i,
            "pago": pagos.get(i)
        })

    return render_template(
        'admin_cuota_detalle.html',
        cuota=cuota,
        casas=casas
    )


def get_cuota(cuota_id):
    cur, conn = get_cursor()
    cur.execute("SELECT * FROM cuotas WHERE id=%s", (cuota_id,))
    cuota = cur.fetchone()
    conn.close()
    return cuota


def get_casas_con_pago(cuota_id):
    cur, conn = get_cursor()
    cur.execute("""
        SELECT casa, monto, comprobante
        FROM cuota_pagos
        WHERE cuota_id=%s
    """, (cuota_id,))
    pagos = {p['casa']: p for p in cur.fetchall()}
    conn.close()

    casas = []
    for i in range(1, TOTAL_CASAS + 1):
        casas.append({
            "numero": i,
            "pago": pagos.get(i)
        })
    return casas

@app.route('/admin/cuota/<int:cuota_id>/pagar', methods=['POST'])
@admin_required
def pagar_cuota(cuota_id):
    casa = int(request.form['casa'])
    monto = request.form['monto']
    archivo = request.files['comprobante']

    url = None
    if archivo and archivo.filename:
        url = subir_a_supabase(archivo, "cuotas")

    cur, conn = get_cursor()
    cur.execute("""
        INSERT INTO cuota_pagos (cuota_id, casa, monto, comprobante, fecha)
        VALUES (%s, %s, %s, %s, %s)
    """, (
        cuota_id,
        casa,
        monto,
        url,
        datetime.now().date()
    ))
    conn.commit()
    conn.close()

    return redirect(f'/admin/cuota/{cuota_id}')

@app.route('/estado-cuenta/casa')
def estado_cuenta_por_casa():
    cur, conn = get_cursor()

    # Total pagado por casa (todas las cuotas)
    cur.execute("""
        SELECT casa, COALESCE(SUM(monto),0) AS total_pagado
        FROM cuota_pagos
        GROUP BY casa
        ORDER BY casa
    """)
    pagos = {p['casa']: float(p['total_pagado']) for p in cur.fetchall()}

    # Total esperado (suma de todas las cuotas)
    cur.execute("SELECT COALESCE(SUM(monto),0) AS total FROM cuotas")
    total_cuotas = float(cur.fetchone()['total'])

    conn.close()

    casas = []
    for i in range(1, TOTAL_CASAS + 1):
        pagado = pagos.get(i, 0)
        casas.append({
            'casa': i,
            'pagado': round(pagado, 2),
            'pendiente': round(total_cuotas - pagado, 2)
        })

    return render_template(
        'estado_cuenta_casa.html',
        casas=casas,
        total_cuotas=round(total_cuotas, 2)
    )

@app.route('/admin/cuota/<int:cuota_id>/estado')
@admin_required
def estado_cuenta_cuota(cuota_id):
    cuota = get_cuota(cuota_id)
    casas = get_casas_con_pago(cuota_id)

    total_esperado = TOTAL_CASAS * float(cuota['monto'])
    total_pagado = sum(
        float(c['pago']['monto'])
        for c in casas if c['pago']
    )

    resumen = {
        'total_casas': TOTAL_CASAS,
        'total_esperado': round(total_esperado, 2),
        'total_pagado': round(total_pagado, 2),
        'total_pendiente': round(total_esperado - total_pagado, 2)
    }

    return render_template(
        'admin_cuota_estado.html',
        cuota=cuota,
        casas=casas,
        resumen=resumen
    )

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
