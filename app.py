# app.py - Versão Final com Correção de CORS para Produção
import os
from datetime import datetime
from flask import (Flask, render_template, request, jsonify,
                   send_from_directory, redirect, url_for)
from flask_cors import CORS
from flask_login import (LoginManager, UserMixin, login_user,
                         logout_user, login_required, current_user)
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
# Aplica a correção de proxy para o ambiente da Render
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

app.secret_key = os.environ.get("FLASK_SECRET_KEY", "1873bsabdjhbakaskda920392678")


app.config.update(
    FRONTEND_URL=os.environ.get("FRONTEND_URL", "https://patrimonio-ifs.netlify.app"),
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="None" 
)
# CORS configurado para permitir credenciais explicitamente da origem do frontend
CORS(app, supports_credentials=True, origins=[app.config['FRONTEND_URL']])


# --- Configuração do Login ---
login_manager = LoginManager(app)
login_manager.login_view = 'login_page' # Redireciona para a rota de login se o utilizador não estiver autenticado

# --- Conexão com Google Sheets ---
try:
    scope = ["https://spreadsheets.google.com/feeds", 'https://www.googleapis.com/auth/spreadsheets', "https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/drive"]
    creds_path = os.path.join(os.path.dirname(__file__), 'credentials.json')
    creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
    client = gspread.authorize(creds)
    spreadsheet = client.open("Controle_Patrimonio_IFS")
    patrimonio_sheet = spreadsheet.worksheet("patrimonios")
    users_sheet = spreadsheet.worksheet("users")
    print("Backend conectado com sucesso às planilhas!")
except Exception as e:
    print(f"ERRO CRÍTICO ao conectar com a planilha: {e}")
    patrimonio_sheet = None
    users_sheet = None

# --- Configuração do Cloudinary ---
cloudinary.config(
  cloud_name=os.environ.get('CLOUD_NAME'),
  api_key=os.environ.get('API_KEY'),
  api_secret=os.environ.get('API_SECRET')
)

# --- Modelo de Usuário e Autenticação ---
class User(UserMixin):
    def __init__(self, user_data):
        self.id = user_data.get('id')
        self.nome = user_data.get('nome')

@login_manager.user_loader
def load_user(user_id):
    if not users_sheet: return None
    try:
        user_cell = users_sheet.find(user_id, in_column=1)
        if not user_cell: return None
        user_data_list = users_sheet.row_values(user_cell.row)
        user_data = {'id': user_data_list[0], 'nome': user_data_list[1]}
        return User(user_data)
    except Exception:
        return None

# --- Funções Auxiliares ---
def allowed_file(filename):
    ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png'}
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- Rotas para Servir as Páginas HTML (Fallback) ---
# Estas rotas são úteis para testes, mas no deploy final, o Netlify serve as páginas.
@app.route('/')
@login_required
def index():
    return render_template('index.html')

@app.route('/patrimonios')
@login_required
def patrimonios_page():
    # Esta rota agora só é útil para testes locais. O frontend irá usar a API.
    if not patrimonio_sheet: return "Erro de conexão com o banco de dados.", 500
    lista_de_patrimonios = patrimonio_sheet.get_all_records()
    for i, item in enumerate(lista_de_patrimonios):
        item['row_num'] = i + 2
    return render_template('patrimonios.html', patrimonios=lista_de_patrimonios)

@app.route('/login')
def login_page():
    return render_template('login.html')

@app.route('/register')
def register_page():
    return render_template('register.html')

@app.route('/gerar_etiqueta')
@login_required
def gerar_etiqueta():
    patrimonio_id = request.args.get('id', 'ERRO')
    nome = request.args.get('nome', 'Item sem nome')
    
    qr_code_folder = os.path.join(app.static_folder, 'qrcodes')
    os.makedirs(qr_code_folder, exist_ok=True)
    qr_code_filename = f"{secure_filename(patrimonio_id)}.png"
    qr_code_filepath = os.path.join(qr_code_folder, qr_code_filename)
    
    qrcode.make(patrimonio_id).save(qr_code_filepath)
    
    return render_template('etiqueta.html', nome=nome, id=patrimonio_id, qr_code_url=url_for('static', filename=f'qrcodes/{qr_code_filename}'))

# --- Rotas da API e Autenticação ---
@app.route('/api/register', methods=['POST'])
def api_register():
    if not users_sheet: return jsonify({"success": False, "message": "Erro de conexão."}), 500
    data = request.json
    email = data.get('email')
    nome = data.get('nome')
    password = data.get('password')

    if not all([email, nome, password]):
        return jsonify({"success": False, "message": "Todos os campos são obrigatórios."}), 400
    
    cell = users_sheet.find(email, in_column=3)
    if cell is not None:
        return jsonify({"success": False, "message": "Este e-mail já está cadastrado."}), 409

    password_hash = generate_password_hash(password)
    new_id = f"user_{len(users_sheet.get_all_records()) + 1}"
    new_user_row = [new_id, nome, email, password_hash, "", "email"]
    users_sheet.append_row(new_user_row)
    return jsonify({"success": True, "message": "Usuário registrado com sucesso!"})

@app.route('/api/login', methods=['POST'])
def api_login():
    if not users_sheet: return jsonify({"success": False, "message": "Erro de conexão."}), 500
    data = request.json
    email = data.get('email')
    password = data.get('password')
    
    cell = users_sheet.find(email, in_column=3)
    if cell is None:
        return jsonify({"success": False, "message": "E-mail ou senha incorretos."}), 401

    user_row = users_sheet.row_values(cell.row)
    stored_hash = user_row[3]

    if stored_hash and check_password_hash(stored_hash, password):
        user_data = {'id': user_row[0], 'nome': user_row[1]}
        user = User(user_data)
        login_user(user)
        return jsonify({"success": True, "message": "Login bem-sucedido!"})
    else:
        return jsonify({"success": False, "message": "E-mail ou senha incorretos."}), 401

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login_page'))
    
@app.route('/api/user/status')
def user_status():
    if current_user.is_authenticated:
        return jsonify(isLoggedIn=True, user={'nome': current_user.nome})
    return jsonify(isLoggedIn=False)

# --- API de Patrimônios (Protegida) ---
@app.route('/api/patrimonios', methods=['GET'])
@login_required
def get_patrimonios():
    if not patrimonio_sheet: return jsonify(error="Erro de conexão."), 500
    recs = patrimonio_sheet.get_all_records()
    for i, x in enumerate(recs): x['row_num'] = i + 2
    return jsonify(recs)

@app.route('/api/patrimonio/registrar', methods=['POST'])
@login_required
def registrar_patrimonio():
    if not patrimonio_sheet:
        return jsonify(success=False, message="Erro de conexão."), 500
    form = request.form
    if not all(form.get(k) for k in ('id', 'nome', 'categoria', 'local')):
        return jsonify(success=False, message="Campos obrigatórios faltando."), 400
    
    # Validação de ID duplicado
    cell = patrimonio_sheet.find(form.get('id'), in_column=1)
    if cell is not None:
        return jsonify(success=False, message=f"O ID de patrimônio '{form.get('id')}' já existe."), 409

    foto_url = ''
    if 'foto' in request.files:
        file = request.files['foto']
        if file.filename != '':
            foto_url = cloudinary.uploader.upload(file)['secure_url']
            
    nova_linha = [form['id'], form['nome'], form['categoria'], form['local'], foto_url, datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
    patrimonio_sheet.append_row(nova_linha)
    return jsonify(success=True, message="Patrimônio registrado."), 201

@app.route('/api/patrimonio/editar', methods=['POST'])
@login_required
def editar_patrimonio():
    if not patrimonio_sheet:
        return jsonify(success=False, message="Erro de conexão."), 500

    form = request.form
    # Lê com get() para não abortar automaticamente se faltar algum campo
    row_num_str = form.get('row_num')
    nome       = form.get('nome')
    categoria  = form.get('categoria')
    local      = form.get('local')

    # Validação básica dos campos obrigatórios
    if not (row_num_str and nome and categoria and local):
        return jsonify(success=False, message="Campos obrigatórios faltando."), 400

    try:
        row_num = int(row_num_str)
        patrimonio_sheet.update_cell(row_num, 2, nome)
        patrimonio_sheet.update_cell(row_num, 3, categoria)
        patrimonio_sheet.update_cell(row_num, 4, local)

        # Se vier nova foto, faz o upload e atualiza também a coluna de foto
        if 'foto' in request.files:
            file = request.files['foto']
            if allowed_file(file.filename):
                url = cloudinary.uploader.upload(file)['secure_url']
                patrimonio_sheet.update_cell(row_num, 5, url)

        return jsonify(success=True, message="Patrimônio atualizado."), 200

    except ValueError:
        return jsonify(success=False, message="row_num inválido."), 400
    except Exception as e:
        return jsonify(success=False, message=f"Erro ao atualizar: {e}"), 500


@app.route('/api/patrimonio/deletar', methods=['POST'])
@login_required
def deletar_patrimonio():
    if not patrimonio_sheet: return jsonify(success=False, message="Erro de conexão."), 500
    try:
        patrimonio_sheet.delete_rows(int(request.json.get('row_num', 0)))
        return jsonify(success=True, message="Patrimônio deletado."), 200
    except Exception as e:
        return jsonify(success=False, message=f"Erro ao deletar: {e}"), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
