import os
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory, redirect, url_for
from flask_cors import CORS
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_dance.contrib.google import make_google_blueprint, google
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import cloudinary
import cloudinary.uploader
import qrcode

# --- Configurações Iniciais ---
app = Flask(__name__, static_folder='static')
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "1873bsabdjhbakaskda920392678")
# Configuração de CORS para permitir cookies da sua URL do Netlify
CORS(app, origins=[os.environ.get("FRONTEND_URL", "http://localhost:3000")], supports_credentials=True)


# --- Configuração do Login ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login_page'

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
        self.email = user_data.get('email')
        self.profile_pic = user_data.get('profile_pic')

@login_manager.user_loader
def load_user(user_id):
    if not users_sheet: return None
    try:
        user_cell = users_sheet.find(user_id, in_column=1)
        if not user_cell: return None
        user_data_list = users_sheet.row_values(user_cell.row)
        user_data = {
            'id': user_data_list[0], 'nome': user_data_list[1], 'email': user_data_list[2],
            'password_hash': user_data_list[3], 'profile_pic': user_data_list[4]
        }
        return User(user_data)
    except gspread.exceptions.APIError as e:
        print(f"Erro de API do gspread ao carregar usuário: {e}")
        return None

# --- Configuração do Login Social com Google ---
google_bp = make_google_blueprint(
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    scope=["openid", "https://www.googleapis.com/auth/userinfo.email", "https://www.googleapis.com/auth/userinfo.profile"],
    redirect_url="/login/google/authorized"
)
app.register_blueprint(google_bp, url_prefix="/login")

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
    return render_template('patrimonios.html', patrimonios=lista_de_patrimonios, current_user=current_user)

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
    
    qr_code_filename = f"{secure_filename(patrimonio_id)}.png"
    qr_code_filepath = os.path.join(app.static_folder, 'qrcodes', qr_code_filename)
    
    if not os.path.exists(os.path.dirname(qr_code_filepath)):
        os.makedirs(os.path.dirname(qr_code_filepath))
        
    qrcode.make(patrimonio_id).save(qr_code_filepath)
    
    return render_template('etiqueta.html', 
                           nome=nome, 
                           id=patrimonio_id, 
                           qr_code_url=url_for('static', filename=f'qrcodes/{qr_code_filename}'))

# --- Rotas da API e Autenticação ---
@app.route("/login/google/authorized")
def google_authorized():
    frontend_url = os.environ.get("FRONTEND_URL")
    if not frontend_url:
        return "ERRO: A variável de ambiente FRONTEND_URL não está configurada no servidor.", 500

    if not google.authorized:
        return redirect(frontend_url + "/login.html?error=auth_failed")
    
    resp = google.get("/oauth2/v2/userinfo")
    if not resp.ok:
        return "Falha ao buscar informações do usuário no Google.", 500
        
    user_info = resp.json()
    user_email = user_info["email"]

    try:
        cell = users_sheet.find(user_email, in_column=3)
        user_data_list = users_sheet.row_values(cell.row)
        user_data = {'id': user_data_list[0], 'nome': user_data_list[1], 'email': user_data_list[2], 'profile_pic': user_data_list[4]}
    except (gspread.exceptions.CellNotFound, AttributeError):
        new_id = f"user_{len(users_sheet.get_all_records()) + 1}"
        user_data = {"id": new_id, "nome": user_info.get("name"), "email": user_email, "password_hash": "", "profile_pic": user_info.get("picture"), "provider": "google"}
        users_sheet.append_row(list(user_data.values()))
    
    user = User(user_data)
    login_user(user)
    
    return redirect(frontend_url)

@app.route('/api/register', methods=['POST'])
def api_register():
    if not users_sheet: return jsonify({"success": False, "message": "Erro de conexão com o banco de dados."}), 500
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
    if not users_sheet: return jsonify({"success": False, "message": "Erro de conexão com o banco de dados."}), 500
    data = request.json
    email = data.get('email')
    password = data.get('password')
    
    cell = users_sheet.find(email, in_column=3)
    if cell is None:
        return jsonify({"success": False, "message": "E-mail ou senha incorretos."}), 401

    user_row = users_sheet.row_values(cell.row)
    stored_hash = user_row[3]

    if stored_hash and check_password_hash(stored_hash, password):
        user_data = {'id': user_row[0], 'nome': user_row[1], 'email': user_row[2], 'profile_pic': user_row[4]}
        user = User(user_data)
        login_user(user)
        return jsonify({"success": True, "message": "Login bem-sucedido!"})
    else:
        return jsonify({"success": False, "message": "E-mail ou senha incorretos."}), 401

@app.route('/api/logout', methods=['POST'])
@login_required
def api_logout():
    logout_user()
    return jsonify({"success": True, "message": "Logout bem-sucedido!"})

@app.route('/api/user/status')
def user_status():
    if current_user.is_authenticated:
        return jsonify({"isLoggedIn": True, "user": {"nome": current_user.nome, "email": current_user.email, "profile_pic": current_user.profile_pic}})
    else:
        return jsonify({"isLoggedIn": False})

# --- API de Patrimônios (Protegida) ---
@app.route('/api/patrimonios', methods=['GET'])
@login_required
def get_patrimonios():
    if not patrimonio_sheet: return jsonify({"error": "Erro de conexão com o banco de dados."}), 500
    lista_de_patrimonios = patrimonio_sheet.get_all_records()
    for i, item in enumerate(lista_de_patrimonios):
        item['row_num'] = i + 2
    return jsonify(lista_de_patrimonios)

@app.route('/api/registrar', methods=['POST'])
@login_required
def registrar_patrimonio():
    if not patrimonio_sheet: return jsonify({"success": False, "message": "Erro de conexão."}), 500
    data = request.form
    if not all([data.get('id'), data.get('nome'), data.get('categoria'), data.get('local')]):
        return jsonify({"success": False, "message": "Todos os campos são obrigatórios."}), 400
    foto_url = ''
    if 'foto' in request.files:
        file = request.files['foto']
        if file and allowed_file(file.filename):
            upload_result = cloudinary.uploader.upload(file)
            foto_url = upload_result['secure_url']
    nova_linha = [data.get('id'), data.get('nome'), data.get('categoria'), data.get('local'), foto_url, datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
    patrimonio_sheet.append_row(nova_linha)
    return jsonify({"success": True, "message": f"Patrimônio '{data.get('nome')}' registrado!"})

@app.route('/api/patrimonio/editar', methods=['POST'])
@login_required
def editar_patrimonio():
    if not patrimonio_sheet: return jsonify({"success": False, "message": "Erro de conexão."}), 500
    
    data = request.form
    row_num = int(data.get('row_num'))
    
    try:
        # Atualiza os dados de texto
        patrimonio_sheet.update_cell(row_num, 2, data.get('nome'))
        patrimonio_sheet.update_cell(row_num, 3, data.get('categoria'))
        patrimonio_sheet.update_cell(row_num, 4, data.get('local'))
        
        # Verifica se uma nova foto foi enviada na edição
        if 'foto' in request.files:
            file = request.files['foto']
            if file and allowed_file(file.filename):
                upload_result = cloudinary.uploader.upload(file)
                nova_foto_url = upload_result['secure_url']
                # Atualiza a célula da URL da foto na planilha (coluna 5)
                patrimonio_sheet.update_cell(row_num, 5, nova_foto_url)

        return jsonify({"success": True, "message": "Patrimônio atualizado com sucesso!"})
    except Exception as e:
        return jsonify({"success": False, "message": f"Erro ao atualizar: {e}"}), 500

@app.route('/api/patrimonio/deletar', methods=['POST'])
@login_required
def deletar_patrimonio():
    if not patrimonio_sheet: return jsonify({"success": False, "message": "Erro de conexão."}), 500
    
    try:
        row_num = int(request.json.get('row_num'))
        patrimonio_sheet.delete_rows(row_num)
        return jsonify({"success": True, "message": "Patrimônio deletado com sucesso!"})
    except Exception as e:
        return jsonify({"success": False, "message": f"Erro ao deletar: {e}"}), 500

# Rota para PWA
@app.route('/service-worker.js')
def service_worker():
    return send_from_directory(app.static_folder, 'service-worker.js')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
