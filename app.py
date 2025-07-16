import os
from datetime import datetime
from flask import (Flask, render_template, request, jsonify,
                   send_from_directory, redirect, session, url_for)
from flask_cors import CORS
from flask_login import (LoginManager, UserMixin, login_user,
                         logout_user, login_required, current_user)
from flask_dance.contrib.google import make_google_blueprint, google
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import cloudinary
import cloudinary.uploader
import qrcode

# --- Configurações Iniciais ---
app = Flask(__name__, static_folder='static')
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

app.secret_key = os.environ.get("FLASK_SECRET_KEY", "1873bsabdjhbakaskda920392678")
app.config.update(
    SERVER_NAME=os.environ.get("SERVER_NAME", "api-patrimonio-ifs.onrender.com"),
    PREFERRED_URL_SCHEME="https",
    FRONTEND_URL=os.environ.get("FRONTEND_URL", "https://patrimonio-ifs.netlify.app"),
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='None',
    SESSION_COOKIE_DOMAIN=os.environ.get("COOKIE_DOMAIN", ".netlify.app")
)

CORS(app, supports_credentials=True, origins=[app.config['FRONTEND_URL']])

# --- Login Manager ---
login_manager = LoginManager(app)
login_manager.login_view = 'login_page'

# --- Modelo de Usuário ---
class User(UserMixin):
    def __init__(self, data):
        self.id = data.get('id')
        self.nome = data.get('nome')
        self.email = data.get('email')
        self.profile_pic = data.get('profile_pic')

# --- Conexão com Google Sheets ---
try:
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
        "https://www.googleapis.com/auth/drive"
    ]
    creds_path = os.path.join(os.path.dirname(__file__), 'credentials.json')
    creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
    client = gspread.authorize(creds)
    spreadsheet = client.open("Controle_Patrimonio_IFS")
    patrimonio_sheet = spreadsheet.worksheet("patrimonios")
    users_sheet = spreadsheet.worksheet("users")
    print("Conectado ao Google Sheets com sucesso!")
except Exception as e:
    print(f"Erro ao conectar ao Sheets: {e}")
    patrimonio_sheet = None
    users_sheet = None

# --- User Loader ---
@login_manager.user_loader
def load_user(user_id):
    if not users_sheet:
        return None
    try:
        cell = users_sheet.find(user_id, in_column=1)
        row = users_sheet.row_values(cell.row)
        return User({'id': row[0], 'nome': row[1], 'email': row[2], 'profile_pic': row[4]})
    except Exception:
        return None

# --- OAuth Google Blueprint ---
google_bp = make_google_blueprint(
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    scope=["openid", "https://www.googleapis.com/auth/userinfo.email", "https://www.googleapis.com/auth/userinfo.profile"],
    redirect_url="/login/google/authorized"
)
# Callback customizado antes de registrar o blueprint
@google_bp.route('/google/authorized')
def google_authorized():
    frontend = app.config['FRONTEND_URL']
    if not google.authorized:
        return redirect(f"{frontend}/login.html?error=auth_failed")
    resp = google.get('/oauth2/v2/userinfo')
    if not resp.ok:
        return redirect(f"{frontend}/login.html?error=fetch_failed")
    info = resp.json()
    email = info['email']
    # Busca ou cria na planilha
    try:
        cell = users_sheet.find(email, in_column=3)
        row = users_sheet.row_values(cell.row)
        data = {'id': row[0], 'nome': row[1], 'email': row[2], 'profile_pic': row[4]}
    except Exception:
        new_id = f'user_{len(users_sheet.get_all_records())+1}'
        data = {'id': new_id, 'nome': info.get('name'), 'email': email,
                'profile_pic': info.get('picture')}
        users_sheet.append_row([data['id'], data['nome'], data['email'], '', data['profile_pic'], 'google'])
    user = User(data)
    login_user(user)
    session.pop('next', None)
    return redirect(frontend)

app.register_blueprint(google_bp, url_prefix="/login")

# --- Funções Auxiliares ---
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'jpg', 'jpeg', 'png'}

# --- Rotas HTML ---
@app.route('/')
@login_required
def index():
    return render_template('index.html')

@app.route('/patrimonios')
@login_required
def patrimonios_page():
    if not patrimonio_sheet:
        return "Erro de conexão.", 500
    itens = patrimonio_sheet.get_all_records()
    for i, x in enumerate(itens): x['row_num'] = i+2
    return render_template('patrimonios.html', patrimonios=itens, current_user=current_user)

@app.route('/login')
def login_page():
    return render_template('login.html')

@app.route('/register')
def register_page():
    return render_template('register.html')

@app.route('/gerar_etiqueta')
@login_required
def gerar_etiqueta():
    pid = request.args.get('id', 'ERRO')
    nome = request.args.get('nome', '')
    fn = secure_filename(pid) + '.png'
    path = os.path.join(app.static_folder, 'qrcodes')
    os.makedirs(path, exist_ok=True)
    qrcode.make(pid).save(os.path.join(path, fn))
    return render_template('etiqueta.html', nome=nome, id=pid, qr_code_url=url_for('static', filename=f'qrcodes/{fn}'))

# --- Endpoints de API ---
@app.route('/api/register', methods=['POST'])
def api_register():
    if not users_sheet:
        return jsonify(success=False, message="Erro de conexão."), 500
    d = request.json or {}
    if not all(k in d for k in ('email', 'nome', 'password')):
        return jsonify(success=False, message="Campos faltando."), 400
    try:
        if users_sheet.find(d['email'], in_column=3):
            return jsonify(success=False, message="E-mail já existe."), 409
    except:
        pass
    pwd = generate_password_hash(d['password'])
    new_id = f'user_{len(users_sheet.get_all_records())+1}'
    users_sheet.append_row([new_id, d['nome'], d['email'], pwd, '', 'email'])
    return jsonify(success=True, message="Registrado com sucesso!"), 201

@app.route('/api/login', methods=['POST'])
def api_login():
    if not users_sheet:
        return jsonify(success=False, message="Erro de conexão."), 500
    d = request.json or {}
    try:
        cell = users_sheet.find(d.get('email'), in_column=3)
        row = users_sheet.row_values(cell.row)
    except Exception:
        return jsonify(success=False, message="Credenciais inválidas."), 401
    if check_password_hash(row[3], d.get('password', '')):
        user = User({'id': row[0], 'nome': row[1], 'email': row[2], 'profile_pic': row[4]})
        login_user(user)
        return jsonify(success=True, message="Login OK"), 200
    return jsonify(success=False, message="Credenciais inválidas."), 401

@app.route('/api/logout', methods=['POST'])
@login_required
def api_logout():
    logout_user()
    return jsonify(success=True), 200

@app.route('/api/user/status')
def user_status():
    if current_user.is_authenticated:
        return jsonify(isLoggedIn=True,
                       user={'nome': current_user.nome, 'email': current_user.email, 'profile_pic': current_user.profile_pic})
    return jsonify(isLoggedIn=False)

@app.route('/api/patrimonios', methods=['GET'])
@login_required
def get_patrimonios():
    if not patrimonio_sheet:
        return jsonify(error="Erro de conexão."), 500
    recs = patrimonio_sheet.get_all_records()
    for i, x in enumerate(recs): x['row_num'] = i+2
    return jsonify(recs)

@app.route('/api/registrar', methods=['POST'])
@login_required
def registrar_patrimonio():
    if not patrimonio_sheet:
        return jsonify(success=False, message="Erro de conexão."), 500
    form = request.form
    if not all(form.get(k) for k in ('id', 'nome', 'categoria', 'local')):
        return jsonify(success=False, message="Campos obrigatórios faltando."), 400
    foto_url = ''
    if 'foto' in request.files:
        f = request.files['foto']
        if allowed_file(f.filename):
            foto_url = cloudinary.uploader.upload(f)['secure_url']
    row = [form['id'], form['nome'], form['categoria'], form['local'], foto_url,
           datetime.now().strftime('%Y-%m-%d %H:%M:%S')]
    patrimonio_sheet.append_row(row)
    return jsonify(success=True, message="Patrimônio registrado."), 201

@app.route('/api/patrimonio/editar', methods=['POST'])
@login_required
def editar_patrimonio():
    if not patrimonio_sheet:
        return jsonify(success=False, message="Erro de conexão."), 500
    form = request.form
    row_num = int(form.get('row_num', 0))
    try:
        patrimonio_sheet.update_cell(row_num, 2, form['nome'])
        patrimonio_sheet.update_cell(row_num, 3, form['categoria'])
        patrimonio_sheet.update_cell(row_num, 4, form['local'])
        if 'foto' in request.files:
            f = request.files['foto']
            if allowed_file(f.filename):
                url = cloudinary.uploader.upload(f)['secure_url']
                patrimonio_sheet.update_cell(row_num, 5, url)
        return jsonify(success=True, message="Atualizado."), 200
    except Exception as e:
        return jsonify(success=False, message=f"Erro: {e}"), 500

@app.route('/api/patrimonio/deletar', methods=['POST'])
@login_required
def deletar_patrimonio():
    if not patrimonio_sheet:
        return jsonify(success=False, message="Erro de conexão."), 500
    try:
        num = int(request.json.get('row_num', 0))
        patrimonio_sheet.delete_rows(num)
        return jsonify(success=True, message="Deletado."), 200
    except Exception as e:
        return jsonify(success=False, message=f"Erro: {e}"), 500

@app.route('/service-worker.js')
def service_worker():
    return send_from_directory(app.static_folder, 'service-worker.js')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=True)
