# app.py - Versão Final Simplificada para o Hackathon
import os
from datetime import datetime
from flask import (Flask, render_template, request, jsonify,
                   send_from_directory, redirect, url_for)
from flask_cors import CORS
from flask_login import (LoginManager, UserMixin, login_user,
                         logout_user, login_required, current_user)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import cloudinary
import cloudinary.uploader
import qrcode

# --- Configurações Iniciais ---
app = Flask(__name__, static_folder='static')
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "uma-chave-secreta-longa-e-dificil-de-adivinhar")
CORS(app) # CORS simples é suficiente para esta arquitetura

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

# --- Rotas para Servir as Páginas HTML ---
@app.route('/')
@login_required
def index():
    return render_template('index.html')

@app.route('/patrimonios')
@login_required
def patrimonios_page():
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

# --- API de Patrimônios (Protegida) ---
@app.route('/api/registrar-patrimonio', methods=['POST'])
@login_required
def registrar_patrimonio():
    if not patrimonio_sheet: return jsonify(success=False, message="Erro de conexão."), 500
    form = request.form
    if not all(form.get(k) for k in ('id', 'nome', 'categoria', 'local')):
        return jsonify(success=False, message="Campos obrigatórios faltando."), 400
    foto_url = ''
    if 'foto' in request.files:
        file = request.files['foto']
        if allowed_file(file.filename):
            foto_url = cloudinary.uploader.upload(file)['secure_url']
    patrimonio_sheet.append_row([form['id'], form['nome'], form['categoria'], form['local'], foto_url, datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
    return jsonify(success=True, message="Patrimônio registrado."), 201

@app.route('/api/patrimonio/editar', methods=['POST'])
@login_required
def editar_patrimonio():
    if not patrimonio_sheet: return jsonify(success=False, message="Erro de conexão."), 500
    form = request.form
    row_num = int(form.get('row_num', 0))
    try:
        patrimonio_sheet.update_cell(row_num, 2, form['nome'])
        patrimonio_sheet.update_cell(row_num, 3, form['categoria'])
        patrimonio_sheet.update_cell(row_num, 4, form['local'])
        if 'foto' in request.files:
            file = request.files['foto']
            if allowed_file(file.filename):
                url = cloudinary.uploader.upload(file)['secure_url']
                patrimonio_sheet.update_cell(row_num, 5, url)
        return jsonify(success=True, message="Patrimônio atualizado."), 200
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
