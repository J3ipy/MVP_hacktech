# app.py - Versão Final com Cloudinary, PWA e Geração de Etiqueta
from flask import Flask, render_template, request, jsonify, send_from_directory
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import os
from werkzeug.utils import secure_filename
import qrcode
import cloudinary
import cloudinary.uploader

# --- Configurações Iniciais ---
# A pasta 'uploads' local não é mais necessária para o deploy, mas pode ser mantida para testes.
STATIC_FOLDER = 'static'
QR_CODE_FOLDER = os.path.join(STATIC_FOLDER, 'qrcodes')

app = Flask(__name__, static_folder=STATIC_FOLDER)

# Garante que a pasta de QR Codes exista
os.makedirs(QR_CODE_FOLDER, exist_ok=True)

# --- Configuração do Cloudinary ---
# IMPORTANTE: No deploy, substitua estes valores por Variáveis de Ambiente!
cloudinary.config(
  cloud_name = os.environ.get('CLOUD_NAME'),
  api_key = os.environ.get('API_KEY'),
  api_secret = os.environ.get('API_SECRET')
)

# --- Conexão com Google Sheets ---
try:
    scope = ["https://spreadsheets.google.com/feeds", 'https://www.googleapis.com/auth/spreadsheets',
             "https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/drive"]
    creds_path = os.path.join(os.path.dirname(__file__), 'credentials.json')
    creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
    client = gspread.authorize(creds)
    sheet = client.open("Controle_Patrimonio_IFS").sheet1
    print("Backend final conectado com sucesso à planilha Google!")
except Exception as e:
    print(f"ERRO: Falha ao conectar com a planilha: {e}")
    sheet = None

def allowed_file(filename):
    """Verifica se a extensão do arquivo de imagem é permitida."""
    ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png'}
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- Rotas Principais e de PWA ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/patrimonios')
def listar_patrimonios():
    if not sheet: return "Erro de conexão com o banco de dados.", 500
    lista_de_patrimonios = sheet.get_all_records()
    for i, item in enumerate(lista_de_patrimonios):
        item['row_num'] = i + 2
    return render_template('patrimonios.html', patrimonios=lista_de_patrimonios)

@app.route('/gerar_etiqueta')
def gerar_etiqueta():
    patrimonio_id = request.args.get('id', 'ERRO')
    nome = request.args.get('nome', 'Item sem nome')
    
    qr_code_filename = f"{secure_filename(patrimonio_id)}.png"
    qr_code_filepath = os.path.join(QR_CODE_FOLDER, qr_code_filename)
    
    if not os.path.exists(qr_code_filepath):
        qr_img = qrcode.make(patrimonio_id)
        qr_img.save(qr_code_filepath)
    
    return render_template('etiqueta.html', 
                           nome=nome, 
                           id=patrimonio_id, 
                           qr_code_url=f'/{QR_CODE_FOLDER}/{qr_code_filename}')

@app.route('/service-worker.js')
def service_worker():
    return send_from_directory(app.static_folder, 'service-worker.js')

# --- API com funcionalidades CRUD ---
@app.route('/api/registrar', methods=['POST'])
def registrar_patrimonio():
    if not sheet: return jsonify({"success": False, "message": "Erro de conexão."}), 500
    
    data = request.form
    if not all([data.get('id'), data.get('nome'), data.get('categoria'), data.get('local')]):
        return jsonify({"success": False, "message": "Todos os campos são obrigatórios."}), 400

    foto_url = ''
    if 'foto' in request.files:
        file = request.files['foto']
        if file and allowed_file(file.filename):
            upload_result = cloudinary.uploader.upload(file)
            foto_url = upload_result['secure_url']

    nova_linha = [
        data.get('id'), data.get('nome'), data.get('categoria'), 
        data.get('local'), foto_url, datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ]
    sheet.append_row(nova_linha)
    return jsonify({"success": True, "message": f"Patrimônio '{data.get('nome')}' registrado!"})

@app.route('/api/patrimonio/editar', methods=['POST'])
def editar_patrimonio():
    if not sheet: return jsonify({"success": False, "message": "Erro de conexão."}), 500
    
    data = request.form
    row_num = int(data.get('row_num'))
    
    try:
        sheet.update_cell(row_num, 2, data.get('nome'))
        sheet.update_cell(row_num, 3, data.get('categoria'))
        sheet.update_cell(row_num, 4, data.get('local'))
        
        if 'foto' in request.files:
            file = request.files['foto']
            if file and allowed_file(file.filename):
                upload_result = cloudinary.uploader.upload(file)
                sheet.update_cell(row_num, 5, upload_result['secure_url'])

        return jsonify({"success": True, "message": "Patrimônio atualizado!"})
    except Exception as e:
        return jsonify({"success": False, "message": f"Erro ao atualizar: {e}"}), 500

@app.route('/api/patrimonio/deletar', methods=['POST'])
def deletar_patrimonio():
    if not sheet: return jsonify({"success": False, "message": "Erro de conexão."}), 500
    
    try:
        row_num = int(request.json.get('row_num'))
        sheet.delete_rows(row_num)
        return jsonify({"success": True, "message": "Patrimônio deletado!"})
    except Exception as e:
        return jsonify({"success": False, "message": f"Erro ao deletar: {e}"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
