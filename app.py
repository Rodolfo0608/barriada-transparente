from flask import (
    Flask, render_template, request,
    redirect, url_for, send_from_directory, session
)
import psycopg2
import psycopg2.extras
import os
from werkzeug.utils import secure_filename
from datetime import datetime
from functools import wraps

# ==========================================================
# CONFIGURACIÓN GENERAL
# ==========================================================

app = Flask(__name__)
app.secret_key = 'barriada-segura'

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

UPLOAD_FOLDER = "/tmp/uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

DATABASE_URL = os.environ.get("DATABASE_URL")

# ==========================================================
# CONTEXTO GLOBAL PARA TEMPLATES (FIX LOGIN ERROR)
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
# ARCHIVOS
# ==========================================================

@app.route('/uploads/<filename>')
def uploads(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

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

    cur.execute("SELECT * FROM pagos ORDER BY fecha DESC")
    pagos = cur.fetchall()

    cur.execute("SELECT * FROM gastos ORDER BY fecha DESC")
    gastos = cur.fetchall()

    cur.execute("SELECT COALESCE(SUM(monto),0) total FROM pagos")
    ingresos = cur.fetchone()['total']

    cur.execute("SELECT COALESCE(SUM(monto),0) total FROM gastos")
    egresos = cur.fetchone()['total']

    conn.close()

    return render_template(
        'estado_cuenta.html',
        pagos=pagos,
        gastos=gastos,
        ingresos=ingresos,
        gastos_total=egresos,
        disponible=ingresos - egresos
    )


@app.route('/pago', methods=['GET', 'POST'])
def pago():
    if request.method == 'POST':
        casa = request.form['casa']
        monto = request.form['monto']
        archivo = request.files['comprobante']

        filename = None
        if archivo and archivo.filename:
            filename = secure_filename(archivo.filename)
            archivo.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        cur, conn = get_cursor()
        cur.execute("""
            INSERT INTO pagos (casa, monto, fecha, comprobante)
            VALUES (%s, %s, %s, %s)
        """, (
            casa,
            monto,
            datetime.now().date(),
            f"/uploads/{filename}" if filename else None
        ))
        conn.commit()
        conn.close()

        return redirect('/estado-cuenta')

    return render_template('admin_pago.html')


@app.route('/requerimientos')
def requerimientos():
    cur, conn = get_cursor()
    cur.execute("SELECT * FROM requerimientos ORDER BY prioridad")
    data = cur.fetchall()
    conn.close()
    return render_template('requerimientos.html', data=data)


@app.route('/comite')
def comite():
    cur, conn = get_cursor()
    cur.execute("SELECT * FROM comite")
    data = cur.fetchall()
    conn.close()
    return render_template('comite.html', data=data)


@app.route('/sugerencias', methods=['GET', 'POST'])
def sugerencias():
    cur, conn = get_cursor()

    if request.method == 'POST':
        texto = request.form['texto']
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

        filename = None
        if archivo and archivo.filename:
            filename = secure_filename(archivo.filename)
            archivo.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        cur, conn = get_cursor()
        cur.execute("""
            INSERT INTO minutas (titulo, resumen, archivo, fecha)
            VALUES (%s, %s, %s, %s)
        """, (
            titulo,
            resumen,
            f"/uploads/{filename}" if filename else None,
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

        filename = None
        if archivo and archivo.filename:
            filename = secure_filename(archivo.filename)
            archivo.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        cur, conn = get_cursor()
        cur.execute("""
            INSERT INTO gastos (descripcion, monto, fecha, factura)
            VALUES (%s, %s, %s, %s)
        """, (
            descripcion,
            monto,
            datetime.now().date(),
            f"/uploads/{filename}" if filename else None
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

        filename = None
        if archivo and archivo.filename:
            filename = secure_filename(archivo.filename)
            archivo.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        cur, conn = get_cursor()
        cur.execute("""
            INSERT INTO comite (nombre, cargo, casa, foto)
            VALUES (%s, %s, %s, %s)
        """, (
            nombre,
            cargo,
            casa,
            f"/uploads/{filename}" if filename else None
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
