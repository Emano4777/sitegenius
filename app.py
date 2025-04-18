from flask import Flask, render_template, request, redirect, url_for, session, jsonify,make_response
import os
print("🔐 Caminho do certificado:", os.getenv("EFI_CERTIFICATE_PATH"))
import psycopg2
import bcrypt
import requests
import uuid
from flask_cors import CORS
import logging
import re
from bs4 import BeautifulSoup
from werkzeug.security import check_password_hash
from functools import wraps
from flask import session, redirect, url_for, jsonify, request,flash,send_from_directory
from datetime import datetime
import mercadopago
import cloudinary
import cloudinary.uploader
import smtplib
from email.mime.text import MIMEText
import hmac
import hashlib
from dotenv import load_dotenv
from authlib.integrations.flask_client import OAuth
from efipay import EfiPay
import json
from openai import OpenAI
from flask import Response
from collections import defaultdict
from psycopg2.extras import RealDictCursor
from cloudinary.uploader import upload
import time
import hashlib
import hmac
from dateutil import parser
# Configuração
cloudinary.config(
    cloud_name='dyyrgll7h',
    api_key='121426271799432',
    api_secret='pJuEZvImfvoRQQE3p5cWFxK9erA'
)
cloud_name = 'dyyrgll7h'
api_key = '121426271799432'
api_secret = 'pJuEZvImfvoRQQE3p5cWFxK9erA'
logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)

app.secret_key = 'minha-chave-segura'
CORS(app, supports_credentials=True)

load_dotenv()


oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)
# CONFIG ESSENCIAL
app.config['SESSION_COOKIE_SAMESITE'] = 'None'
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_PATH'] = '/'


MERCADO_PAGO_ACCESS_TOKEN = "APP_USR-159038661951932-040216-41e126ddffaed400ae1a1f25863dd676-2342791238"
# Sua chave da OpenAI aqui (ou use dotenv se preferir)
BASE_URL = "https://sitegenius.com.br"
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def get_db_connection():
    return psycopg2.connect("postgresql://postgres:Poupaqui123@406279.hstgr.cloud:5432/postgres")
def login_required_admin(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            if request.path.startswith('/api'):
                return jsonify({'success': False, 'message': 'Admin não autenticado'}), 401
            
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def login_required_cliente(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        cliente_id = session.get('cliente_id')
        if not cliente_id:
            if request.path.startswith('/api'):
                return jsonify({'success': False, 'message': 'Cliente não autenticado'}), 401
            return redirect(url_for('login_cliente', subdomain=kwargs.get('subdomain', '')))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/ads.txt')
def ads_txt():
    return send_from_directory('.', 'ads.txt')


@app.route('/login/google')
def login_google():
    redirect_uri = url_for('auth_google_callback', _external=True)
    print("🔁 Redirect URI enviado para o Google:", redirect_uri)
    return google.authorize_redirect(redirect_uri)

@app.route("/template-ia")
def template_ia():
    return render_template("template11_index.html")


@app.route('/api/chat/enviar', methods=['POST'])
def enviar_mensagem():
    data = request.get_json()
    texto = data.get('texto')

    if not texto:
        return jsonify({'erro': 'Mensagem vazia'}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO mensagens (texto) VALUES (%s)", (texto,))
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'status': 'ok'}), 200


@app.route('/api/chat/mensagens')
def listar_mensagens():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT texto, to_char(timestamp, 'HH24:MI:SS') FROM mensagens ORDER BY timestamp DESC LIMIT 10")
    mensagens = cur.fetchall()
    cur.close()
    conn.close()

    return jsonify([{'texto': m[0], 'hora': m[1]} for m in mensagens[::-1]])


@app.route('/auth/google/callback')
def auth_google_callback():
    token = google.authorize_access_token()
    userinfo = token['userinfo']  # ou use google.parse_id_token(token)

    email = userinfo['email']
    nome = userinfo.get('name')
    avatar_url = userinfo.get('picture')
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, nome, is_premium, avatar_url FROM users2 WHERE email = %s", (email,))
    user = cur.fetchone()

    if user:
        user_id = user[0]
        nome = user[1]
        is_premium = user[2]
        avatar_url = user[3]
    else:
        senha_fake = bcrypt.hashpw(uuid.uuid4().hex[:10].encode('utf-8'), bcrypt.gensalt()).decode()
        cur.execute(
            "INSERT INTO users2 (nome, email, senha, is_premium, avatar_url) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (nome, email, senha_fake, False, avatar_url)
        )
        user_id = cur.fetchone()[0]
        conn.commit()
        is_premium = False

    session['user_id'] = user_id
    session['user_name'] = nome
    session['is_premium'] = is_premium
    session['avatar_url'] = avatar_url

    session_token = str(uuid.uuid4())
    cur.execute("UPDATE users2 SET session_token = %s WHERE id = %s", (session_token, user_id))
    conn.commit()
    cur.close()
    conn.close()

    resp = make_response(redirect(url_for('home')))
    resp.set_cookie('session_token', session_token, samesite='None', secure=True)
    return resp

@app.route('/sitemap.xml')
def sitemap():
    return send_from_directory('.', 'sitemap.xml')
# Rota para buscar todos os produtos
@app.route('/api/produtos')
def listar_produtos():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT id, nome, descricao, preco, imagem FROM produtos')
    rows = cur.fetchall()
    cur.close()
    conn.close()

    produtos = []
    for row in rows:
        produtos.append({
            'id': row[0],
            'nome': row[1],
            'descricao': row[2],
            'preco': float(row[3]),
            'imagem': row[4]
        })
    return jsonify(produtos)




@app.route("/gerar-site", methods=["POST"])
def gerar_site():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Não autorizado"}), 401

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT premium_level, ia_usos FROM users2 WHERE id = %s", (user_id,))
    resultado = cur.fetchone()

    if not resultado:
        cur.close()
        conn.close()
        return jsonify({"error": "Usuário não encontrado."}), 404

    nivel, usos = resultado
    nivel = nivel.strip().lower()

    limite_por_nivel = {
        "essential": 1,
        "moderado": 8,
        "master": float("inf")
    }

    limite = limite_por_nivel.get(nivel, 0)

    if usos >= limite:
        cur.close()
        conn.close()
        if nivel == "essential":
            return jsonify({"error": "Você atingiu o limite de uso da IA (1 vez)."}), 403
        elif nivel == "moderado":
            return jsonify({"error": "Você atingiu o limite de uso da IA (8 vezes)."}), 403
        else:
            return jsonify({"error": "Você atingiu o limite de uso da IA."}), 403

    # Incrementa contador se não for master
    if nivel != "master":
        cur.execute("UPDATE users2 SET ia_usos = ia_usos + 1 WHERE id = %s", (user_id,))
        conn.commit()


    data = request.get_json()
    prompt = data.get("prompt")

    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "Você é um gerador de sites profissionais. Gere HTML com estrutura completa: cabeçalho com menus, seção de serviços, seção de produtos (com nome, imagem e preço), seção de contato e um botão flutuante do WhatsApp. Inclua o estilo dentro da tag <style> com layout bonito e moderno. Não inclua explicações."},
            {"role": "user", "content": prompt}
        ],
        max_tokens=1500,
        temperature=0.7
    )

    html_gerado = response.choices[0].message.content
    soup = BeautifulSoup(html_gerado, 'html.parser')

    style_tag = soup.find('style')
    style = style_tag.string if style_tag else ""
    if style_tag:
        style_tag.extract()

    html_sem_style = str(soup)

    template_id = session.get("template_id") or request.cookies.get("template_id")
    page_name = session.get("page_name") or request.cookies.get("page_name")

    if not template_id or not page_name:
        cur.close()
        conn.close()
        return jsonify({"error": "Template ou página não definidos na sessão."}), 400

    cur.execute("""
        SELECT template_name, subdomain
        FROM user_templates
        WHERE user_id = %s AND id = %s
        LIMIT 1
    """, (user_id, template_id))

    info = cur.fetchone()

    if not info:
        cur.close()
        conn.close()
        return jsonify({"error": "Template não encontrado."}), 404

    template_name, subdomain = info

    # Atualiza o HTML e CSS no banco
    html_completo = f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>{style}</style>
    </head>
    <body>{html_sem_style}</body>
    </html>
    """

    cur.execute("""
        UPDATE user_templates 
        SET custom_html = %s
        WHERE user_id = %s AND template_name = %s AND subdomain = %s AND page_name = %s
    """, (html_completo, user_id, template_name, subdomain, page_name))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"html": html_sem_style, "css": style})



@app.route("/admin/classificados")
def admin_classificados():
    if not session.get("user_id"):
        return redirect("/login")

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT c.id, c.titulo, c.descricao, c.imagem, c.data, c.autor, c.aprovado
        FROM classificados c
        JOIN user_templates ut ON ut.subdomain = c.comunidade
        WHERE ut.user_id = %s
        ORDER BY c.data DESC
    """, (session["user_id"],))
    classificados = cur.fetchall()
    cur.close()

    return render_template("admin_classificados.html", classificados=classificados, tipo="classificado")  # <<< adicionado tipo


@app.route("/admin/eventos")
def admin_eventos():
    if not session.get("user_id"):
        return redirect("/login")

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
    SELECT e.id, e.titulo, e.descricao, e.imagem, e.data, e.autor, e.aprovado
    FROM eventos e
    JOIN user_templates ut ON ut.subdomain = e.comunidade
    WHERE ut.user_id = %s
    ORDER BY e.data DESC
""", (session["user_id"],))
    eventos = cur.fetchall()
    cur.close()

    return render_template("admin_classificados.html", classificados=eventos, tipo="evento")



@app.route("/admin/eventos/moderar", methods=["POST"])
def moderar_evento():
    if "user_id" not in session:
        return "Acesso negado", 403

    data = request.get_json()
    id = data.get("id")
    aprovar = data.get("aprovar")

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE eventos SET aprovado = %s WHERE id = %s", (aprovar, id))
    conn.commit()
    cur.close()

    return "", 204


@app.route("/admin/eventos/excluir", methods=["POST"])
def excluir_evento():
    if "user_id" not in session:
        return "Acesso negado", 403

    data = request.get_json()
    id = data.get("id")

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM eventos WHERE id = %s", (id,))
    conn.commit()
    cur.close()

    return "", 204


@app.route("/admin/classificados/moderar", methods=["POST"])
def moderar_classificado():
    if "user_id" not in session:
        return "Acesso negado", 403

    data = request.get_json()
    id = data.get("id")
    aprovar = data.get("aprovar")

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE classificados SET aprovado = %s WHERE id = %s", (aprovar, id))
    conn.commit()
    cur.close()

    return "", 204


@app.route("/admin/classificados/excluir", methods=["POST"])
def excluir_classificado():
    if "user_id" not in session:
        return "Acesso negado", 403

    data = request.get_json()
    id = data.get("id")

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM classificados WHERE id = %s", (id,))
    conn.commit()
    cur.close()

    return "", 204




@app.route('/<subdomain>/login-cliente', methods=['GET', 'POST'])
def login_cliente(subdomain):
    if request.method == 'POST':
        email = request.form['email']
        senha = request.form['senha']

        conn = get_db_connection()
        cur = conn.cursor()

        # Busca o ID da loja baseado no subdomínio
        cur.execute("SELECT id FROM user_templates WHERE subdomain = %s LIMIT 1", (subdomain,))
        loja = cur.fetchone()

        if not loja:
            cur.close()
            conn.close()
            return "Loja não encontrada", 404

        loja_id = loja[0]

        # Busca o cliente
        cur.execute("SELECT id, senha FROM clientes_loja WHERE email = %s AND loja_id = %s", (email, loja_id))
        cliente = cur.fetchone()

        cur.close()
        conn.close()

        if cliente and bcrypt.checkpw(senha.encode(), cliente[1].encode()):
            session['cliente_id'] = cliente[0]  # Armazena na sessão
            return redirect(f'/{subdomain}')     # Redireciona normalmente

        return "Credenciais inválidas", 401

    return render_template('login_cliente.html', subdomain=subdomain)


@app.route('/editar-dados', methods=['GET', 'POST'])
def editar_dados_painel():
    if 'user_id' not in session:
        return redirect('/login')

    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        nome = request.form.get('nome')
        email = request.form.get('email')
        senha = request.form.get('senha')
        avatar_url = None

        # Verifica e faz upload do avatar se houver
        if 'avatar' in request.files:
            avatar_file = request.files['avatar']
            if avatar_file and avatar_file.filename != '':
                resultado = cloudinary.uploader.upload(avatar_file, folder="usuarios_sitegenius")
                avatar_url = resultado['secure_url']

        # Atualiza dados com base no que foi enviado
        if senha:
            hashed = bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()
            if avatar_url:
                cur.execute("""
                    UPDATE users2 SET nome = %s, email = %s, senha = %s, avatar_url = %s
                    WHERE id = %s
                """, (nome, email, hashed, avatar_url, session['user_id']))
            else:
                cur.execute("""
                    UPDATE users2 SET nome = %s, email = %s, senha = %s
                    WHERE id = %s
                """, (nome, email, hashed, session['user_id']))
        else:
            if avatar_url:
                cur.execute("""
                    UPDATE users2 SET nome = %s, email = %s, avatar_url = %s
                    WHERE id = %s
                """, (nome, email, avatar_url, session['user_id']))
            else:
                cur.execute("""
                    UPDATE users2 SET nome = %s, email = %s
                    WHERE id = %s
                """, (nome, email, session['user_id']))

        conn.commit()
        cur.close(); conn.close()
        return redirect('/')

    # GET - carrega dados atuais
    cur.execute("SELECT nome, email, avatar_url FROM users2 WHERE id = %s", (session['user_id'],))
    dados = cur.fetchone()
    cur.close(); conn.close()

    if not dados:
        return "Usuário não encontrado", 404

    return render_template('editardados.html', nome=dados[0], email=dados[1], avatar=dados[2])


@app.route('/<subdomain>/editar-dados', methods=['GET', 'POST'])
def editar_dados(subdomain):
    cliente_id = session.get('cliente_id')
    if not cliente_id:
        return redirect(f'/{subdomain}/login-cliente')

    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        nome = request.form['nome']
        email = request.form['email']
        senha = request.form['senha']

        hashed = bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()

        cur.execute("""
            UPDATE clientes_loja
            SET nome = %s, email = %s, senha = %s
            WHERE id = %s
        """, (nome, email, hashed, cliente_id))
        conn.commit()
        cur.close(); conn.close()

        return redirect(f'/{subdomain}')  # Volta pra loja

    # GET
    cur.execute("SELECT nome, email FROM clientes_loja WHERE id = %s", (cliente_id,))
    dados = cur.fetchone()
    cur.close(); conn.close()

    if not dados:
        return "Cliente não encontrado", 404
    return render_template('template10_editar_dados.html', nome=dados[0], email=dados[1], subdomain=subdomain)


def registrar_acesso(user_id, ip, subdomain):
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT MIN(id) FROM user_templates
        WHERE user_id = %s AND subdomain = %s
    """, (user_id, subdomain))
    template = cur.fetchone()
    if not template or not template[0]:
        cur.close(); conn.close()
        return

    template_id = template[0]

    cidade = estado = pais = None
    try:
        if ip.startswith('127.') or ip.startswith('192.168'):
            ip = '8.8.8.8'
        r = requests.get(f'https://ipinfo.io/{ip}/json')
        if r.status_code == 200:
            data = r.json()
            cidade = data.get('city')
            estado = data.get('region')
            pais = data.get('country')
    except:
        pass

    cur.execute("""
        INSERT INTO acessos2 (user_id, template_id, ip, cidade, estado, pais)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (user_id, template_id, ip, cidade, estado, pais))
    conn.commit()
    cur.close(); conn.close()



@app.route('/site/<int:template_id>/<page>')
def exibir_site_por_id(template_id, page):
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT custom_html, subdomain, user_id FROM user_templates WHERE id = %s AND page_name = %s", (template_id, page))
    resultado = cur.fetchone()
    cur.close(); conn.close()

    if not resultado:
        return "Página não encontrada", 404

    html, subdomain, user_id = resultado

    registrar_acesso(user_id, request.remote_addr, subdomain)

    html = adaptar_links(html, subdomain)

    # 🔍 Log no terminal
    print(f"\n🔍 Renderizando /site/{template_id}/{page} para subdomínio {subdomain}")
    print(html[:1000])  # imprime os primeiros 1000 caracteres do HTML

    return Response(html, mimetype='text/html')
@app.route('/novidades')
def novidades():
    return render_template('novidades.html')


@app.route('/<subdomain>/<page>', methods=["GET", "POST"])
def exibir_site_por_subdominio(subdomain, page):

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT custom_html, user_id, template_name, template_id
        FROM user_templates
        WHERE subdomain = %s AND page_name = %s
        ORDER BY id ASC
        LIMIT 1
    """, (subdomain, page))
    resultado = cur.fetchone()

    if not resultado:
        cur.execute("SELECT user_id, template_name FROM user_templates WHERE subdomain = %s LIMIT 1", (subdomain,))
        dono = cur.fetchone()
        cur.close(); conn.close()

        # Redireciona pro index que já trata o template17 com dono_id corretamente
    
        if dono and dono[1] in ["template17", "template19"]:
            return redirect(url_for('index_loja', subdomain=subdomain))

        return "Página não encontrada", 404

    html, user_id, template_name, template_id = resultado

    # Se for template17, redireciona para o index que já faz tudo corretamente com dono_id
    if template_name in ["template17", "template19"]:
        cur.close(); conn.close()
        return redirect(url_for('index_loja', subdomain=subdomain))
    
    if template_name == "template18":
        if page == "eventos":
            if request.method == 'POST':
                titulo = request.form.get("titulo")
                descricao = request.form.get("descricao")
                imagem = request.form.get("imagem")

                conn = get_db_connection()
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO eventos (titulo, descricao, imagem, comunidade, data, autor, aprovado)
                    VALUES (%s, %s, %s, %s, NOW(), %s, FALSE)
                """, (titulo, descricao, imagem, subdomain, 'anônimo'))
                conn.commit()
                cur.close(); conn.close()
                return '', 200

            cur.execute("""
                SELECT id, titulo, descricao, imagem, data, autor, aprovado
                FROM eventos
                WHERE comunidade = %s AND aprovado = TRUE
                ORDER BY data DESC
            """, (subdomain,))
            eventos = cur.fetchall()
            eventos = [
                (
                    *linha[:4],
                    parser.parse(linha[4]) if isinstance(linha[4], str) else linha[4],
                    *linha[5:]
                )
                for linha in eventos
            ]
            cur.close(); conn.close()
            return render_template("template18_eventos.html", sub=subdomain, eventos=eventos)

        elif page == "index":
            cur.execute("""
                SELECT id, titulo, descricao, imagem, data, autor, aprovado
                FROM classificados
                WHERE comunidade = %s AND aprovado = TRUE
                ORDER BY data DESC
            """, (subdomain,))
            classificados = cur.fetchall()
            cur.close(); conn.close()
            return render_template("template18_index.html", sub=subdomain, classificados=classificados)

        else:
            cur.close(); conn.close()
            return f"Página '{page}' não encontrada para o template18.", 404

    if template_name == "template10":
        try:
            return render_template(f"{page}.html", subdomain=subdomain)
        except:
            if html:
                soup = BeautifulSoup(html, 'html.parser')
                registrar_acesso(user_id, request.remote_addr, subdomain)
                html_renderizado = str(soup).replace("{{sub}}", subdomain)
                return Response(html_renderizado, mimetype='text/html')
            else:
                return f"Página '{page}' não encontrada para subdomínio '{subdomain}'.", 404  

    # Templates padrão com custom_html
    soup = BeautifulSoup(html, 'html.parser')
    registrar_acesso(user_id, request.remote_addr, subdomain)
    html_renderizado = str(soup).replace("{{sub}}", subdomain)
    cur.close(); conn.close()
    return Response(html_renderizado, mimetype='text/html')




@app.route('/<subdomain>/acompanhar-pedido/<int:pedido_id>')
def acompanhar_pedido(subdomain, pedido_id):
    cliente_id = session.get('cliente_id')
    if not cliente_id:
        return redirect(f'/{subdomain}/login-cliente')

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, data, total, status_entrega, tipo_entrega, endereco_entrega, whatsapp_entregador
        FROM pedidos
        WHERE id = %s AND cliente_id = %s
    """, (pedido_id, cliente_id))
    
    row = cur.fetchone()
    cur.close(); conn.close()

    if not row:
        return "Pedido não encontrado", 404

    pedido = {
        'id': row[0],
        'data': row[1],
        'total': row[2],
        'status_entrega': row[3],
        'tipo_entrega': row[4],
        'endereco_entrega': row[5],
        'whatsapp_entregador': row[6] or ''  # <- evita erro com None
    }

    return render_template("template10_acompanhar_pedido.html", pedido=pedido, subdomain=subdomain)


@app.route("/admin/editar-imagem/<int:imagem_id>", methods=["POST"])
def editar_imagem(imagem_id):
    descricao = request.form["descricao"]
    ordem = request.form["ordem"]
    imagem_antiga = request.form["imagem_antiga"]
    page_name = request.form["pagina"]

    nova_imagem = None
    imagem_file = request.files.get("nova_imagem_file")

    # Se o usuário enviou uma nova imagem, fazemos upload no Cloudinary
    if imagem_file and imagem_file.filename != "":
        upload_result = cloudinary.uploader.upload(imagem_file)
        nova_imagem = upload_result['secure_url']

    conn = get_db_connection()
    cur = conn.cursor()

    # 1. Atualiza a tabela de imagens
    if nova_imagem:
        cur.execute("""
            UPDATE template_images 
            SET descricao = %s, ordem = %s, image_url = %s
            WHERE id = %s
        """, (descricao, ordem, nova_imagem, imagem_id))
    else:
        cur.execute("""
            UPDATE template_images 
            SET descricao = %s, ordem = %s
            WHERE id = %s
        """, (descricao, ordem, imagem_id))

 # 2. Atualiza o custom_html nos templates do usuário logado
    if nova_imagem:
        cur.execute("""
            SELECT id, custom_html 
            FROM user_templates 
            WHERE custom_html LIKE %s AND user_id = %s
        """, (f"%{imagem_antiga}%", session['user_id']))
        resultados = cur.fetchall()

        for template_id, html in resultados:
            html_atualizado = html.replace(imagem_antiga, nova_imagem)
            cur.execute("""
                UPDATE user_templates
                SET custom_html = %s
                WHERE id = %s
            """, (html_atualizado, template_id))

    conn.commit()
    cur.close(); conn.close()

    return redirect("/admin/editar-imagens")



@app.route("/admin/editar-imagens")
def listar_imagens():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, template_name, page_name, image_url, descricao, ordem FROM template_images ORDER BY template_name, page_name, ordem")
    imagens = [dict(id=row[0], template_name=row[1], page_name=row[2], image_url=row[3], descricao=row[4], ordem=row[5]) for row in cur.fetchall()]
    cur.close(); conn.close()
    return render_template("editarimagem.html", imagens=imagens)




@app.route("/admin/clientescadastrados")
def clientes_cadastrados():
    if 'user_id' not in session:
            return redirect('/login')

    if not session.get('is_premium'):
            return redirect('/preco')  # Redireciona para a página de planos

    user_id = session['user_id']
    conn = get_db_connection()
    cur = conn.cursor()

    # Recuperar as lojas do usuário
    cur.execute("""
        SELECT MIN(id), subdomain
        FROM user_templates
        WHERE user_id = %s
        GROUP BY subdomain
    """, (user_id,))
    lojas = cur.fetchall()

    clientes_info = []

    for loja_id, sub in lojas:
        # Buscar clientes da loja com total de pedidos
        cur.execute("""
            SELECT 
                cl.id, 
                cl.nome, 
                cl.email,
                COUNT(p.id) AS total_pedidos
            FROM clientes_loja cl
            LEFT JOIN pedidos p ON cl.id = p.cliente_id AND p.loja_id = %s
            WHERE cl.loja_id = %s
            GROUP BY cl.id, cl.nome, cl.email
            ORDER BY total_pedidos DESC
        """, (loja_id, loja_id))

        clientes = cur.fetchall()

        for c in clientes:
            clientes_info.append({
                'nome': c[1],
                'telefone': c[2],
                'total_pedidos': c[3],
                'subdomain': sub
            })

    cur.close()
    conn.close()

    # Agora sim, extraímos de clientes_info (que contém todos os clientes)
    nomes = [c['nome'] for c in clientes_info]
    totais = [c['total_pedidos'] for c in clientes_info]

    return render_template("clientescadastrados.html", clientes=clientes_info, nomes=nomes, totais=totais)



@app.route('/<subdomain>/meus-pedidos')
@login_required_cliente
def meus_pedidos(subdomain):
    cliente_id = session.get('cliente_id')

    conn = get_db_connection()
    cur = conn.cursor()

    # Identifica loja atual
    cur.execute("SELECT id FROM user_templates WHERE subdomain = %s LIMIT 1", (subdomain,))
    loja = cur.fetchone()
    if not loja:
        return "Loja não encontrada", 404

    loja_id = loja[0]

    # Agora filtra por cliente_id e loja_id juntos
    cur.execute("""
        SELECT id, data, total, status_entrega, tipo_entrega, endereco_entrega
        FROM pedidos
        WHERE cliente_id = %s AND loja_id = %s
        ORDER BY data DESC
    """, (cliente_id, loja_id))
    
    pedidos = cur.fetchall()
    cur.close(); conn.close()

    return render_template("template10_meuspedidos.html", pedidos=pedidos, subdomain=subdomain)


@app.route('/gerar-assinatura-cloudinary', methods=['GET'])
def gerar_assinatura_cloudinary():
    timestamp = str(int(time.time()))
    folder = "experiencias_sitegenius"

    string_to_sign = f"folder={folder}&timestamp={timestamp}{api_secret}"
    signature = hashlib.sha1(string_to_sign.encode('utf-8')).hexdigest()

    return jsonify({
        "timestamp": timestamp,
        "signature": signature,
        "api_key": api_key,
        "cloud_name": cloud_name,
        "folder": folder  # 👈 se quiser usar no frontend
    })


# Flask route adaptada para Cloudinary via frontend
@app.route('/admin/experiencias', methods=['GET', 'POST'])
def admin_experiencias():
    user_id = session.get("user_id")
    if not user_id:
        return redirect("/login")

    if request.method == 'POST':
        id_editar = request.form.get('id_editar')
        titulo = request.form['titulo']
        descricao = request.form['descricao']
        playlist = request.form['playlist']
        prato = request.form.get("prato")
        preco = request.form.get("preco")
        habilitar_escolha = 'habilitar_escolha' in request.form

        imagem = request.form.get('imagem')
        video_fundo = request.form.get('video_fundo')
        som_clima = request.form.get('som_clima')
        imagem_prato = request.form.get('imagem_prato')

        with get_db_connection() as conn:
            with conn.cursor() as cur:
                if id_editar:
                    cur.execute("SELECT * FROM experiencias WHERE id = %s AND user_id = %s", (id_editar, user_id))
                    experiencia = cur.fetchone()

                    cur.execute("""
                        UPDATE experiencias SET
                            titulo=%s,
                            descricao=%s,
                            playlist=%s,
                            imagem=%s,
                            video_fundo=%s,
                            som_clima=%s,
                            prato=%s,
                            preco=%s,
                            imagem_prato=%s,
                            habilitar_escolha=%s
                        WHERE id=%s
                    """, (
                        titulo,
                        descricao,
                        playlist,
                        imagem or experiencia[4],
                        video_fundo or experiencia[5],
                        som_clima or experiencia[6],
                        prato,
                        preco,
                        imagem_prato or experiencia[9],
                        habilitar_escolha,
                        id_editar
                    ))
                else:
                    cur.execute("""
                        INSERT INTO experiencias (
                            user_id, titulo, descricao, playlist, imagem,
                            video_fundo, som_clima, prato, preco,
                            imagem_prato, votos, habilitar_escolha
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0, %s)
                    """, (
                        user_id, titulo, descricao, playlist, imagem,
                        video_fundo, som_clima, prato, preco,
                        imagem_prato, habilitar_escolha
                    ))

            conn.commit()
        return redirect(url_for('admin_experiencias'))

    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM experiencias WHERE user_id = %s ORDER BY id DESC", (user_id,))
            experiencias = cur.fetchall()

    return render_template("admin_experiencias.html", experiencias=experiencias)


@app.route('/votar_experiencia/<int:id>', methods=['POST'])
def votar_experiencia(id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE experiencias SET votos = votos + 1 WHERE id = %s AND habilitar_escolha = TRUE", (id,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})
@app.route('/admin/experiencias/delete/<int:id>')
def deletar_experiencia(id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM experiencias WHERE id = %s", (id,))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('admin_experiencias'))



@app.route('/<subdomain>/cadastrar', methods=['GET', 'POST'])
def cadastrar_cliente(subdomain):
    if request.method == 'POST':
        nome = request.form['nome']
        email = request.form['email']
        senha = request.form['senha']

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("SELECT id FROM user_templates WHERE subdomain = %s LIMIT 1", (subdomain,))
        loja = cur.fetchone()
        if not loja:
            cur.close(); conn.close()
            return "Loja não encontrada", 404

        loja_id = loja[0]
        senha_hash = bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()

        cur.execute("INSERT INTO clientes_loja (nome, email, senha, loja_id) VALUES (%s, %s, %s, %s)",
                    (nome, email, senha_hash, loja_id))
        conn.commit()
        cur.close(); conn.close()

        return redirect(f'/{subdomain}/login-cliente')

    return render_template('cadastrar_cliente.html', subdomain=subdomain)

@app.route('/<subdomain>/api/produtos')
def listar_produtos_por_loja(subdomain):
    conn = get_db_connection()
    cur = conn.cursor()

    # Busca a loja pelo subdomínio
    cur.execute("SELECT id FROM user_templates WHERE subdomain = %s LIMIT 1", (subdomain,))
    loja = cur.fetchone()

    if not loja:
        cur.close(); conn.close()
        return jsonify([])

    loja_id = loja[0]

    # Busca produtos da loja
    cur.execute("""
        SELECT id, nome, descricao, preco, imagem
        FROM produtos
        WHERE loja_id = %s
    """, (loja_id,))
    
    rows = cur.fetchall()
    cur.close(); conn.close()

    produtos = [{
        'id': r[0],
        'nome': r[1],
        'descricao': r[2],
        'preco': float(r[3]),
        'imagem': r[4]
    } for r in rows]

    return jsonify(produtos)


@app.route('/<subdomain>/api/check-login-cliente')
def check_login_cliente(subdomain):
    if 'cliente_id' in session:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT nome FROM clientes_loja WHERE id = %s", (session['cliente_id'],))
        cliente = cur.fetchone()
        cur.close(); conn.close()

        return jsonify({
            "logado": True,
            "nome": cliente[0] if cliente else "Cliente"
        })
    return jsonify({"logado": False})



@app.route('/<subdomain>/logout-cliente')
def logout_cliente(subdomain):
    session.pop('cliente_id', None)
    return redirect(f'/{subdomain}/login-cliente')


@app.route('/<subdomain>', methods=["GET", "POST"])
@app.route('/<subdomain>/index', methods=["GET", "POST"])
def index_loja(subdomain):
    print(f"[DEBUG] Subdomínio acessado: {subdomain}")

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT user_id, template_name FROM user_templates WHERE subdomain = %s LIMIT 1", (subdomain,))
    dono = cur.fetchone()
    print(f"[DEBUG] Resultado dono do template: {dono}")

    if not dono:
        print("[ERRO] Loja não encontrada para esse subdomínio.")
        return "Loja não encontrada", 404

    dono_id, template_name = dono
    print(f"[DEBUG] Template: {template_name} | User ID: {dono_id}")

    if template_name == "template10":
        cur.execute("SELECT id, nome, descricao, preco, imagem FROM produtos WHERE user_id = %s", (dono_id,))
        produtos = cur.fetchall()
        print(f"[DEBUG] Produtos encontrados: {len(produtos)}")
        cur.close(); conn.close()
        return render_template("template10_index.html", produtos=produtos, subdomain=subdomain)

    elif template_name == "template17":
        cur.close(); conn.close()

        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT id, titulo, descricao, playlist, imagem, video_fundo, som_clima, prato, preco, imagem_prato, votos, habilitar_escolha 
            FROM experiencias WHERE user_id = %s ORDER BY id DESC
        """, (dono_id,))
        experiencias = cur.fetchall()
        print(f"[DEBUG] Experiências encontradas: {len(experiencias)}")
        cur.close(); conn.close()
        return render_template("template17_index.html", experiencias=experiencias, subdomain=subdomain)
       

    elif template_name == "template18":
        if request.method == 'POST':
            titulo = request.form.get("titulo")
            descricao = request.form.get("descricao")
            imagem = request.form.get("imagem")

            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO classificados (titulo, descricao, imagem, comunidade, data, autor, aprovado)
                VALUES (%s, %s, %s, %s, NOW(), %s, FALSE)
            """, (titulo, descricao, imagem, subdomain, 'anônimo'))
            conn.commit()
            cur.close(); conn.close()
            return '', 200

        cur.close(); conn.close()

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT id, titulo, descricao, imagem, data, autor, aprovado
            FROM classificados
            WHERE comunidade = %s AND aprovado = TRUE
            ORDER BY data DESC
        """, (subdomain,))
        classificados = cur.fetchall()
        cur.close(); conn.close()

        return render_template("template18_index.html", sub=subdomain, classificados=classificados)


    elif template_name == "template19":
        cur.execute("""
            SELECT id, nome, email, voo, problema, whatsapp, data_envio
            FROM reclamacoes_voo
            WHERE user_id = %s
            ORDER BY data_envio DESC
        """, (dono_id,))
        reclamacoes = cur.fetchall()

        # Comentários
        cur.execute("SELECT reclamacao_id, nome, comentario FROM comentarios ORDER BY data_envio ASC")
        comentarios_raw = cur.fetchall()
        comentarios = {}
        for r_id, nome, texto in comentarios_raw:
            comentarios.setdefault(r_id, []).append({'nome': nome, 'comentario': texto})

        # Avaliações
        cur.execute("SELECT reclamacao_id, ROUND(AVG(nota), 1) FROM avaliacoes GROUP BY reclamacao_id")
        avaliacoes_raw = dict(cur.fetchall())

        cur.close(); conn.close()

        return render_template("template19_index.html", subdomain=subdomain, reclamacoes=[
            {
                "id": r[0],
                "nome": r[1],
                "email": r[2],
                "voo": r[3],
                "problema": r[4],
                "whatsapp": r[5],
                "data_envio": r[6],
                "comentarios": comentarios.get(r[0], []),
                "media_avaliacao": avaliacoes_raw.get(r[0])
            }
            for r in reclamacoes
        ])






@app.route('/<subdomain>/api/carrinho/adicionar/<int:produto_id>', methods=['POST'])
@login_required_cliente
def adicionar_carrinho_cliente(subdomain, produto_id):
    cliente_id = session.get('cliente_id')
    if not cliente_id:
        return jsonify({'success': False, 'message': 'Você precisa estar logado.'}), 401

    data = request.get_json()
    quantidade = data.get('quantidade', 1)

    conn = get_db_connection()
    cur = conn.cursor()

    # 🔎 Busca a loja correta pelo subdomínio e cliente vinculado a ela
    cur.execute("""
        SELECT ut.id
        FROM user_templates ut
        JOIN clientes_loja cl ON cl.loja_id = ut.id
        WHERE ut.subdomain = %s AND cl.id = %s
        LIMIT 1
    """, (subdomain, cliente_id))
    
    loja = cur.fetchone()

    if not loja:
        cur.close(); conn.close()
        return jsonify({'success': False, 'message': 'Loja não encontrada ou cliente não pertence a essa loja'}), 404

    loja_id = loja[0]

    # 🛒 Insere no carrinho com cliente_id e loja_id corretos
    cur.execute('''
        INSERT INTO carrinho (produto_id, quantidade, cliente_id, loja_id)
        VALUES (%s, %s, %s, %s)
    ''', (produto_id, quantidade, cliente_id, loja_id))

    conn.commit()
    cur.close(); conn.close()

    return jsonify({'success': True})


@app.route('/<subdomain>/api/carrinho/adicionar/<int:produto_id>', methods=['POST'])
def adicionar_item_carrinho(subdomain, produto_id):
    cliente_id = session.get('cliente_id')
    if not cliente_id:
        return jsonify({'success': False, 'message': 'Usuário não autenticado'}), 401

    data = request.get_json()
    quantidade = data.get('quantidade', 1)

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM user_templates WHERE subdomain = %s LIMIT 1", (subdomain,))
    loja = cur.fetchone()
    if not loja:
        return jsonify({'success': False, 'message': 'Loja não encontrada'}), 404

    loja_id = loja[0]

    cur.execute('''
        INSERT INTO carrinho (produto_id, quantidade, cliente_id, loja_id)
        VALUES (%s, %s, %s, %s)
    ''', (produto_id, quantidade, cliente_id, loja_id))

    conn.commit()
    cur.close(); conn.close()

    return jsonify({'success': True})

@app.route('/<subdomain>/api/mercado-pago/pagamento', methods=['POST'])
def gerar_pagamento_cliente(subdomain):
    data = request.get_json()
    tipo_entrega = data.get('tipo_entrega')
    endereco_entrega = data.get('endereco_entrega') if tipo_entrega == 'delivery' else None

    # Salvar temporariamente na sessão
    session['tipo_entrega'] = tipo_entrega
    session['endereco_entrega'] = endereco_entrega
    cliente_id = session.get('cliente_id')
    if not cliente_id:
        return jsonify({'success': False, 'message': 'Cliente não autenticado'}), 401

    conn = get_db_connection()
    cur = conn.cursor()

    # Descobre a loja baseada no subdomínio
    cur.execute("SELECT id, user_id FROM user_templates WHERE subdomain = %s LIMIT 1", (subdomain,))
    loja = cur.fetchone()
    if not loja:
        return jsonify({'success': False, 'message': 'Loja não encontrada'}), 404

    loja_id = loja[0]
    dono_id = loja[1]

    # Busca o token do dono da loja (na tabela users2)
    cur.execute("SELECT mercado_pago_token FROM users2 WHERE id = %s", (dono_id,))
    user = cur.fetchone()
    if not user or not user[0]:
        return jsonify({'success': False, 'message': 'Token do Mercado Pago não configurado'}), 400

    access_token = user[0]

    # Busca os itens do carrinho do cliente
    cur.execute("""
        SELECT p.nome, c.quantidade, p.preco
        FROM carrinho c
        JOIN produtos p ON c.produto_id = p.id
        WHERE c.cliente_id = %s AND c.loja_id = %s
    """, (cliente_id, loja_id))

    itens = cur.fetchall()
    cur.close(); conn.close()

    if not itens:
        return jsonify({'success': False, 'message': 'Carrinho está vazio'}), 400

    # Monta os dados da preferência
    preference_items = [{
        "title": nome,
        "quantity": int(quantidade),
        "unit_price": float(preco),
        "currency_id": "BRL"
    } for nome, quantidade, preco in itens]

    sdk = mercadopago.SDK(access_token)

    preference_data = {
        "items": preference_items,
        "back_urls": {
        "success": f"https://{request.host}/{subdomain}/sucesso",
        "failure": f"https://{request.host}/{subdomain}/erro",
        "pending": f"https://{request.host}/{subdomain}/pendente"

        },
        "auto_return": "approved"
    }

    preference_response = sdk.preference().create(preference_data)
    init_point = preference_response["response"]["init_point"]

    return jsonify({'success': True, 'link': init_point})


@app.route('/<subdomain>/sucesso')
def pagamento_sucesso(subdomain):
    cliente_id = session.get('cliente_id')
    if not cliente_id:
        return redirect(f'/{subdomain}/login-cliente')

    conn = get_db_connection()
    cur = conn.cursor()

    # Identifica loja
    cur.execute("SELECT id FROM user_templates WHERE subdomain = %s LIMIT 1", (subdomain,))
    loja = cur.fetchone()
    if not loja:
        return "Loja não encontrada", 404
    loja_id = loja[0]

    # Busca itens do carrinho (para somar o total)
    cur.execute("""
        SELECT p.preco, c.quantidade
        FROM carrinho c
        JOIN produtos p ON c.produto_id = p.id
        WHERE c.cliente_id = %s AND c.loja_id = %s
    """, (cliente_id, loja_id))
    itens = cur.fetchall()

    total = sum(preco * quantidade for preco, quantidade in itens)
    tipo_entrega = session.pop('tipo_entrega', None)
    endereco_entrega = session.pop('endereco_entrega', None)
    # Insere o pedido
    cur.execute("""
    INSERT INTO pedidos (cliente_id, loja_id, data, total, status_entrega, tipo_entrega, endereco_entrega)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
""", (cliente_id, loja_id, datetime.now(), total, 'Recebido', tipo_entrega, endereco_entrega))

    # Limpa carrinho
    cur.execute("DELETE FROM carrinho WHERE cliente_id = %s AND loja_id = %s", (cliente_id, loja_id))
    conn.commit()
    cur.close(); conn.close()

    return render_template('template10_meuspedidos.html', subdomain=subdomain)



@app.route('/<subdomain>/api/carrinho')
def listar_carrinho_cliente(subdomain):
    cliente_id = session.get('cliente_id')
    if not cliente_id:
        return jsonify([])

    conn = get_db_connection()
    cur = conn.cursor()

    # Busca o id da loja pelo subdomínio
    cur.execute("SELECT id FROM user_templates WHERE subdomain = %s LIMIT 1", (subdomain,))
    loja = cur.fetchone()
    if not loja:
        cur.close(); conn.close()
        return jsonify([])

    loja_id = loja[0]

    cur.execute('''
        SELECT p.nome, c.quantidade, p.preco, c.id, p.imagem
        FROM carrinho c
        JOIN produtos p ON c.produto_id = p.id
        WHERE c.cliente_id = %s AND c.loja_id = %s
    ''', (cliente_id, loja_id))
    rows = cur.fetchall()
    cur.close(); conn.close()

    itens = [{
        'nome': r[0],
        'quantidade': r[1],
        'preco': float(r[2]),
        'id': r[3],
        'imagem': r[4]  # 👈 aqui adiciona a imagem
    } for r in rows]

    return jsonify(itens)


@app.route('/admin')
def admin_dashboard():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT premium_level FROM users2 WHERE id = %s", (user_id,))
    result = cur.fetchone()
    cur.close()
    conn.close()

    if not result or result[0] == 'free':
        return redirect('/preco')  # redireciona para página de planos

    return render_template('admin_dashboard.html')




@app.route('/api/carrinho')
def listar_carrinho():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify([])

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        SELECT c.id, p.nome, p.preco, c.quantidade
        FROM carrinho c
        JOIN produtos p ON c.produto_id = p.id
        WHERE c.user_id = %s
    ''', (user_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    itens = [{
        'id': row[0],
        'nome': row[1],
        'preco': float(row[2]),
        'quantidade': row[3]
    } for row in rows]

    return jsonify(itens)


@app.route('/api/carrinho/limpar', methods=['POST'])
def limpar_carrinho():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('DELETE FROM carrinho')
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'status': 'ok', 'mensagem': 'Carrinho limpo com sucesso'})

@app.route('/configurar-mercado-pago', methods=['GET'])
def pagina_configuracao_mercado_pago():
    user_id = session.get('user_id')
    if not user_id:
        return redirect('/login')

    return render_template('configurar_mercado_pago.html')



@app.route('/configurar-mercado-pago', methods=['POST'])
def configurar_mercado_pago():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'message': 'Você precisa estar logado.'}), 401

    data = request.get_json()
    token = data.get('token')

    if not token:
        return jsonify({'success': False, 'message': 'Token não fornecido.'}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users2 SET mercado_pago_token = %s WHERE id = %s", (token, user_id))
    conn.commit()
    cur.close(); conn.close()

    return jsonify({'success': True, 'message': 'Token salvo com sucesso!'})


@app.route('/api/mercado-pago/pagamento', methods=['POST'])
def gerar_preferencia_pagamento():
    user_id = session.get('user_id')
    cliente_id = session.get('cliente_id')
    data = request.get_json()
    tipo_entrega = data.get('tipo_entrega')
    endereco_entrega = data.get('endereco_entrega') if tipo_entrega == 'delivery' else None

    # Salvar temporariamente na sessão
    session['tipo_entrega'] = tipo_entrega
    session['endereco_entrega'] = endereco_entrega


    if not (user_id or cliente_id):
        return jsonify({'success': False, 'message': 'Usuário não autenticado.'}), 401

    conn = get_db_connection()
    cur = conn.cursor()

    if cliente_id:
        # 🔎 Buscar loja_id pelo cliente logado
        cur.execute("SELECT loja_id FROM clientes_loja WHERE id = %s", (cliente_id,))
        loja = cur.fetchone()
        if not loja:
            return jsonify({'success': False, 'message': 'Loja do cliente não encontrada'}), 400
        loja_id = loja[0]

        # 🔎 Buscar token da loja
        cur.execute("""
            SELECT u.mercado_pago_token FROM user_templates t
            JOIN users2 u ON u.id = t.user_id
            WHERE t.id = %s
        """, (loja_id,))
        row = cur.fetchone()
        if not row or not row[0]:
            return jsonify({'success': False, 'message': 'Token do Mercado Pago não encontrado'}), 400
        access_token = row[0]

        # 🔎 Itens do carrinho do cliente
        cur.execute("""
            SELECT p.nome, c.quantidade, p.preco
            FROM carrinho c
            JOIN produtos p ON c.produto_id = p.id
            WHERE c.cliente_id = %s AND c.loja_id = %s
        """, (cliente_id, loja_id))
        itens = cur.fetchall()

    else:
        # 🧑‍💻 Dono do site
        cur.execute("SELECT mercado_pago_token FROM users2 WHERE id = %s", (user_id,))
        row = cur.fetchone()
        if not row or not row[0]:
            return jsonify({'success': False, 'message': 'Token do Mercado Pago não configurado.'}), 400
        access_token = row[0]

        cur.execute("""
            SELECT p.nome, c.quantidade, p.preco
            FROM carrinho c
            JOIN produtos p ON c.produto_id = p.id
            WHERE c.user_id = %s
        """, (user_id,))
        itens = cur.fetchall()

    if not itens:
        return jsonify({'success': False, 'message': 'Carrinho vazio.'}), 400

    # ✅ Salvar pedido no banco
    if cliente_id:
        total = sum([qtd * preco for _, qtd, preco in itens])
        cur.execute("""
            INSERT INTO pedidos (cliente_id, loja_id, total)
            VALUES (%s, %s, %s) RETURNING id
        """, (cliente_id, loja_id, total))
        pedido_id = cur.fetchone()[0]

        for nome, quantidade, preco in itens:
            cur.execute("""
                INSERT INTO itens_pedido (pedido_id, produto_nome, quantidade, preco)
                VALUES (%s, %s, %s, %s)
            """, (pedido_id, nome, quantidade, preco))

    conn.commit()
    cur.close(); conn.close()
    subdomain = session.get("subdomain") or "www"  # fallback para www se não tiver subdomínio
    plano = data.get("plano") or "default"  # caso o plano seja usado na URL
    # 🔁 Criar preferência Mercado Pago
    sdk = mercadopago.SDK(access_token)
    preference_data = {
        "items": [
            {
                "title": nome,
                "quantity": int(quantidade),
                "unit_price": float(preco),
                "currency_id": "BRL"
            } for nome, quantidade, preco in itens
        ],
      "back_urls": {
    "success": f"https://{subdomain}.sitegenius.com.br/payment-success?plano={plano}",
    "failure": f"https://{subdomain}.sitegenius.com.br/payment-failure",
    "pending": f"https://{subdomain}.sitegenius.com.br/payment-pending"
},
        "auto_return": "approved"
    }

    preference_response = sdk.preference().create(preference_data)
    init_point = preference_response["response"]["init_point"]
    return jsonify({'success': True, 'link': init_point})

@app.route('/payment-failure')
def payment_failure():
    return """
    <div style="text-align: center; margin-top: 80px; font-family: Arial, sans-serif;">
        <h2 style="color: #d9534f;">Pagamento não foi concluído 😞</h2>
        <p style="font-size: 18px;">Algo deu errado com o seu pagamento.<br>
        Você pode tentar novamente ou escolher outro método.</p>
        <a href="/preco" style="display: inline-block; margin-top: 20px; padding: 10px 20px; 
           background-color: #d9534f; color: white; text-decoration: none; border-radius: 6px;">
           Voltar para a Loja
        </a>
    </div>
    """




@app.route('/payment-pending')
def payment_pending():
    return """
    <div style="text-align: center; margin-top: 80px; font-family: Arial, sans-serif;">
        <h2 style="color: #f0ad4e;">Pagamento em análise ⏳</h2>
        <p style="font-size: 18px;">Seu pagamento está sendo processado.<br>
        Assim que for confirmado, você será notificado.</p>
        <a href="/preco" style="display: inline-block; margin-top: 20px; padding: 10px 20px; 
           background-color: #f0ad4e; color: white; text-decoration: none; border-radius: 6px;">
           Voltar para a Loja
        </a>
    </div>
    """



@app.route("/admin/controlepedidos")
def controle_pedidos():
    if 'user_id' not in session:
        return redirect('/login')

    if not session.get('is_premium'):
        return redirect('/preco')  # Redireciona para a página de planos

    user_id = session['user_id']
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT MIN(ut.id), ut.subdomain
        FROM user_templates ut
        WHERE ut.user_id = %s
        GROUP BY ut.subdomain
    """, (user_id,))
    lojas = cur.fetchall()

    pedidos = []

    for loja_id, sub in lojas:
        cur.execute("""
            SELECT 
                p.id, 
                p.data, 
                p.total, 
                p.status_entrega, 
                p.tipo_entrega, 
                p.nome_entregador, 
                p.whatsapp_entregador, 
                p.endereco_entrega,
                c.nome AS nome_cliente,
                ut.subdomain
            FROM pedidos p
            JOIN clientes_loja c ON p.cliente_id = c.id
            JOIN user_templates ut ON p.loja_id = ut.id
            WHERE p.loja_id = %s
            ORDER BY p.data DESC
        """, (loja_id,))
        pedidos_loja = cur.fetchall()

        for p in pedidos_loja:
            pedidos.append({
                'id': p[0],
                'data': p[1],
                'total': p[2],
                'status_entrega': p[3],
                'tipo_entrega': p[4],
                'nome_entregador': p[5],
                'whatsapp_entregador': p[6],
                'endereco_entrega': p[7],
                'nome_cliente': p[8],
                'subdomain': p[9]
            })

    cur.close(); conn.close()
    return render_template("controlepedidos.html", pedidos=pedidos)



@app.route("/admin/atualizar-pedido", methods=["POST"])
def atualizar_pedido():
    if 'user_id' not in session:
        return redirect("/login")

    pedido_id = request.form.get("pedido_id")
    status_entrega = request.form.get("status_entrega")
    nome_entregador = request.form.get("nome_entregador")
    whatsapp_entregador = request.form.get("whatsapp_entregador")

    user_id = session['user_id']
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT MIN(id)
        FROM user_templates
        WHERE user_id = %s
        GROUP BY subdomain
    """, (user_id,))
    loja_ids = [row[0] for row in cur.fetchall()]

    # Atualiza o pedido se ele pertencer a uma loja do usuário
    cur.execute("""
        UPDATE pedidos
        SET status_entrega = %s,
            nome_entregador = %s,
            whatsapp_entregador = %s
        WHERE id = %s AND loja_id = ANY(%s)
    """, (status_entrega, nome_entregador, whatsapp_entregador, pedido_id, loja_ids))
    
    conn.commit()
    cur.close(); conn.close()

    return redirect("/admin/controlepedidos")




@app.route('/api/mercado-pago/token')
def pegar_token_mercado_pago():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'message': 'Usuário não autenticado.'}), 401

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT mercado_pago_token FROM users2 WHERE id = %s', (user_id,))
    row = cur.fetchone()
    cur.close(); conn.close()

    if not row or not row[0]:
        return jsonify({'success': False, 'message': 'Token do Mercado Pago não configurado.'}), 404

    return jsonify({'success': True, 'token': row[0]})

@app.route('/<subdomain>/api/carrinho/remover/<int:item_id>', methods=['DELETE'])
def remover_item_carrinho(subdomain, item_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('DELETE FROM carrinho WHERE id = %s', (item_id,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'status': 'ok', 'mensagem': 'Item removido do carrinho'})

@app.route('/<subdomain>/produtos')
def produtos(subdomain):
    cliente_id = session.get('cliente_id')
    if not cliente_id:
        return jsonify({'success': False, 'message': 'Você precisa estar logado.'}), 401

    conn = get_db_connection()
    cur = conn.cursor()

    # Pega o id da loja diretamente do user_templates pelo subdominio
    cur.execute("SELECT id FROM user_templates WHERE subdomain = %s LIMIT 1", (subdomain,))
    loja = cur.fetchone()

    if not loja:
        cur.close(); conn.close()
        return "Loja não encontrada", 404

    loja_id = loja[0]

    # Agora confronta a loja_id encontrada diretamente com a coluna loja_id em produtos
    cur.execute("""
    SELECT id, nome, preco, quantidade, imagem, categoria
    FROM produtos
    WHERE loja_id = %s
    ORDER BY categoria, nome
""", (loja_id,))
    produtos = cur.fetchall()
    produtos_por_categoria = {}

    for p in produtos:
        categoria = p[5] or "Outros"
        if categoria not in produtos_por_categoria:
            produtos_por_categoria[categoria] = []
        produtos_por_categoria[categoria].append({
            'id': p[0],
            'nome': p[1],
            'preco': p[2],
            'quantidade': p[3],
            'imagem': p[4]
        })

 
    cur.close(); conn.close()

    return render_template('produtos.html', produtos_por_categoria=produtos_por_categoria, subdomain=subdomain)

@app.route('/relatorio-free')
def relatorio_free():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    conn = get_db_connection()
    cur = conn.cursor()

    # Busca o nível do plano
    cur.execute("SELECT premium_level FROM users2 WHERE id = %s", (user_id,))
    result = cur.fetchone()
    nivel = result[0] if result else 'free'

    # Pega os templates do usuário
    cur.execute("""
        SELECT MIN(ut.id) as id, ut.subdomain
        FROM user_templates ut
        WHERE ut.user_id = %s
        GROUP BY ut.subdomain
    """, (user_id,))
    lojas = cur.fetchall()

    if not lojas:
        return "Você ainda não escolheu um template.", 400

    template_ids = [t[0] for t in lojas]

    # Acessos dos últimos 7 dias
    cur.execute("""
        SELECT template_id, DATE(timestamp), COUNT(*)
        FROM acessos2
        WHERE template_id = ANY(%s)
        GROUP BY template_id, DATE(timestamp)
    """, (template_ids,))
    todos_acessos = cur.fetchall()

    # Visitantes que retornaram
    cur.execute("""
        SELECT template_id, ip, COUNT(*)
        FROM acessos2
        WHERE template_id = ANY(%s)
        GROUP BY template_id, ip
        HAVING COUNT(*) > 1
    """, (template_ids,))
    retornos = cur.fetchall()

    # Dias com mais acessos
    cur.execute("""
        SELECT template_id, DATE(timestamp), COUNT(*) as total
        FROM acessos2
        WHERE template_id = ANY(%s)
        GROUP BY template_id, DATE(timestamp)
    """, (template_ids,))
    mais_ativos_todos = cur.fetchall()

    # Localizações (se for premium)
    localizacoes_todos = []
    if nivel in ('essential', 'moderado', 'master'):
        cur.execute("""
            SELECT template_id, cidade, estado, pais, COUNT(*)
            FROM acessos2
            WHERE template_id = ANY(%s)
            GROUP BY template_id, cidade, estado, pais
        """, (template_ids,))
        localizacoes_todos = cur.fetchall()

    # Organiza os dados
    acessos_gerais = {}
    tem_acessos = False
    for template_id, sub in lojas:
        sub = sub.strip().lower()
        acessos = [(d, c) for tid, d, c in todos_acessos if tid == template_id]
        retornos_sub = [(ip, c) for tid, ip, c in retornos if tid == template_id]
        mais_ativo_sub = sorted([(d, c) for tid, d, c in mais_ativos_todos if tid == template_id], key=lambda x: x[1], reverse=True)
        locais_sub = [(cid, est, pais, c) for tid, cid, est, pais, c in localizacoes_todos if tid == template_id]

        if acessos:
            tem_acessos = True

        acessos_gerais[sub] = {
            'labels': [str(a[0]) for a in acessos],
            'data': [a[1] for a in acessos],
            'retornos': retornos_sub,
            'mais_ativo': mais_ativo_sub[0] if mais_ativo_sub else None,
            'localizacoes': locais_sub
        }
    cliques_totais = defaultdict(lambda: defaultdict(int))

    if os.path.exists('cliques.json'):
        with open('cliques.json', 'r') as f:
            for linha in f:
                try:
                    clique = json.loads(linha.strip())
                    template = clique.get('template')
                    acao = clique.get('acao')
                    if template and acao:
                        cliques_totais[template][acao] += 1
                except Exception:
                    continue

    cur.close()
    conn.close()

    mensagem = None
    if not tem_acessos:
        mensagem = """<strong>Que pena!</strong> 😢 Seu site ainda não teve acessos recentes.
        Mas não se preocupe: temos um <a href='/preco'><strong>plano de tráfego pago</strong></a>
        que pode impulsionar seus acessos!"""

    acessos_gerais_json = [
        {
            'labels': dados['labels'],
            'data': dados['data'],
            'retornos': dados['retornos'],
            'mais_ativo': dados['mais_ativo'],
            'localizacoes': dados['localizacoes']
        }
        for sub, dados in acessos_gerais.items()
    ]

    is_premium = nivel in ('essential', 'moderado', 'master')

    return render_template(
        'relatorios_free.html',
        acessos_gerais=acessos_gerais,
        acessos_gerais_json=acessos_gerais_json,
        mensagem=mensagem,
        nivel=nivel,
        is_premium=is_premium,
        cliques_totais=cliques_totais
    )




@app.route('/admin/relatorios')
def relatorio_geral_estoque():
    if 'user_id' not in session:
        return redirect('/login')

    if not session.get('is_premium'):
        return redirect('/preco')  # Redireciona para a página de planos

    user_id = session['user_id']
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
       SELECT MIN(ut.id), ut.subdomain
        FROM user_templates ut
        WHERE ut.user_id = %s
        GROUP BY ut.subdomain
    """, (user_id,))
    lojas = cur.fetchall()

    relatorios = []

    for loja_id, sub in lojas:
        # Estoque atual
        cur.execute("SELECT nome, quantidade FROM produtos WHERE loja_id = %s", (loja_id,))
        produtos = cur.fetchall()
        estoque_labels = [p[0] for p in produtos]
        estoque_data = [p[1] for p in produtos]

        # Vendas (quantidade de pedidos feitos para essa loja)
        cur.execute("SELECT COUNT(*) FROM pedidos WHERE loja_id = %s", (loja_id,))
        total_pedidos = cur.fetchone()[0]

        vendas_labels = ['Pedidos Realizados']
        vendas_data = [total_pedidos]

        relatorios.append({
            'subdomain': sub,
            'estoque_labels': estoque_labels,
            'estoque_data': estoque_data,
            'vendas_labels': vendas_labels,
            'vendas_data': vendas_data
        })

    cur.close(); conn.close()
    return render_template('relatorios.html', relatorios=relatorios)



@app.route('/api/carrinho/atualizar/<int:item_id>', methods=['PUT'])
def atualizar_quantidade(item_id):
    data = request.get_json()
    nova_qtd = data.get('quantidade', 1)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('UPDATE carrinho SET quantidade = %s WHERE id = %s', (nova_qtd, item_id))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'status': 'ok', 'mensagem': 'Quantidade atualizada'})

@app.route('/<subdomain>/admin/cadastrar-produto', methods=['GET', 'POST'])
def cadastrar_produto(subdomain):
    if 'user_id' not in session:
        return redirect('/login')

    if not session.get('is_premium'):
        return redirect('/preco')  # Redireciona para a página de planos
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 🔎 Busca a loja correta pelo subdomínio e user_id
    cur.execute("SELECT id FROM user_templates WHERE subdomain = %s AND user_id = %s", (subdomain, session['user_id']))
    loja = cur.fetchone()

    if not loja:
        cur.close(); conn.close()
        return "Loja não encontrada ou não pertence a este usuário", 404

    loja_id = loja[0]

    if request.method == 'POST':
        nome = request.form['nome']
        descricao = request.form['descricao']
        preco = request.form['preco']
        imagem = request.form['imagem']
        quantidade = request.form['quantidade']
        categoria = request.form.get('categoria', 'Outros')

        cur.execute(
            'INSERT INTO produtos (nome, descricao, preco, imagem, quantidade, categoria, loja_id) VALUES (%s, %s, %s, %s, %s, %s, %s)',
            (nome, descricao, preco, imagem, quantidade, categoria, loja_id)
        )
        conn.commit()
        cur.close(); conn.close()
        return redirect(url_for('cadastrar_produto', subdomain=subdomain))

    cur.close(); conn.close()

    return render_template('form_cadastrar_produto.html')


@app.route('/admin/produtos')
def admin_produtos():
    if 'user_id' not in session:
        return redirect('/login')

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT premium_level FROM users2 WHERE id = %s", (session['user_id'],))
    result = cur.fetchone()

    if not result or result[0] == 'free':
        cur.close(); conn.close()
        return redirect('/preco')  # Redireciona para a página de planos

    # Busca produtos com subdomínio da loja do usuário (único por loja)
    cur.execute("""
        SELECT p.id, p.nome, p.preco, u.subdomain
        FROM produtos p
        JOIN (
            SELECT MIN(id) AS id, subdomain
            FROM user_templates
            WHERE user_id = %s
            GROUP BY subdomain
        ) u ON p.loja_id = u.id
    """, (session['user_id'],))
    
    produtos = cur.fetchall()

    # Busca lojas únicas do usuário
    cur.execute("""
        SELECT DISTINCT subdomain 
        FROM user_templates
        WHERE user_id = %s
    """, (session['user_id'],))
    lojas = cur.fetchall()

    cur.close(); conn.close()

    html = """
    <style>
      body { font-family: Arial; background: #f4f4f4; padding: 20px; }
      table { width: 100%; background: white; border-collapse: collapse; }
      th, td { padding: 10px; text-align: left; border-bottom: 1px solid #ddd; }
      th { background: #007bff; color: white; }
      a.btn { padding: 6px 12px; background: #28a745; color: white; text-decoration: none; border-radius: 4px; margin-right: 5px; }
      a.btn-danger { background: #dc3545; }
      .top-bar { margin-bottom: 20px; }
    </style>

    <div class="top-bar">
      <a href="/admin" class="btn">🏠 Voltar ao Painel</a>
    </div>

    <h2>Produtos Cadastrados</h2>
    """

    if produtos:
        html += """
        <table>
          <thead>
            <tr>
              <th>Nome</th>
              <th>Preço</th>
              <th>Loja (Subdomínio)</th>
              <th>Ações</th>
            </tr>
          </thead>
          <tbody>
        """
        for p in produtos:
            html += f"""
            <tr>
              <td>{p[1]}</td>
              <td>R$ {p[2]:.2f}</td>
              <td>{p[3]}</td>
              <td>
                <a href='/admin/editar/{p[0]}' class='btn'>Editar</a>
                <a href='/admin/excluir/{p[0]}' class='btn btn-danger'>Excluir</a>
                <a href='/{p[3]}/admin/cadastrar-produto' class='btn'>+ Produto</a>
              </td>
            </tr>
            """
        html += "</tbody></table>"
    else:
        html += "<p>⚠️ Nenhum produto cadastrado ainda.</p>"

    # Mostra o botão de +Produto por loja (sempre aparece)
    if lojas:
        html += "<h3>➕ Cadastrar Produto por Loja:</h3>"
        for loja in lojas:
            html += f"<a href='/{loja[0]}/admin/cadastrar-produto' class='btn'>Cadastrar para {loja[0]}</a>"

    return html



@app.route('/admin/excluir/<int:id>')
def excluir_produto(id):
    conn = get_db_connection()
    cur = conn.cursor()

    # Verifica se está no carrinho
    cur.execute('SELECT 1 FROM carrinho WHERE produto_id = %s LIMIT 1', (id,))
    if cur.fetchone():
        cur.close(); conn.close()
        return "⚠️ Não é possível excluir: produto está em um carrinho.", 400

    # Busca a categoria do produto
    cur.execute('SELECT categoria FROM produtos WHERE id = %s', (id,))
    categoria_result = cur.fetchone()
    categoria = categoria_result[0] if categoria_result else None

    # Exclui o produto
    cur.execute('DELETE FROM produtos WHERE id = %s', (id,))

    # Verifica se a categoria ainda está em uso
    if categoria:
        cur.execute('SELECT 1 FROM produtos WHERE categoria = %s LIMIT 1', (categoria,))
        ainda_em_uso = cur.fetchone()
        if not ainda_em_uso:
            print(f"Categoria '{categoria}' não está mais em uso e pode ser removida da UI.")

    conn.commit()
    cur.close(); conn.close()

    return redirect('/admin/produtos')




@app.route('/admin/editar/<int:id>', methods=['GET', 'POST'])
def editar_produto(id):
    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        nome = request.form['nome']
        descricao = request.form['descricao']
        preco = request.form['preco']
        imagem = request.form['imagem']
        categoria = request.form['categoria']

        cur.execute('''
            UPDATE produtos
            SET nome=%s, descricao=%s, preco=%s, imagem=%s, categoria=%s
            WHERE id=%s
        ''', (nome, descricao, preco, imagem, categoria, id))

        conn.commit()
        cur.close()
        conn.close()
        return '<p style="color:green;">Produto atualizado com sucesso! <a href="/admin/produtos">Voltar</a></p>'

    cur.execute('SELECT nome, descricao, preco, imagem, categoria FROM produtos WHERE id = %s', (id,))
    produto = cur.fetchone()
    cur.close()
    conn.close()


    return f'''
    <h2>Editar Produto</h2>
    <form method="POST" style="max-width: 500px; margin: auto;">
        <input name="nome" value="{produto[0]}" placeholder="Nome do produto" required><br><br>
        <textarea name="descricao" placeholder="Descrição do produto">{produto[1]}</textarea><br><br>
        <input name="preco" type="number" step="0.01" value="{produto[2]}" placeholder="Preço (R$)" required><br><br>
        <input name="imagem" value="{produto[3]}" placeholder="URL da imagem"><br><br>
        <input name="categoria" value="{produto[4]}" placeholder="Categoria (ex: Medicamentos, Cosméticos)" required><br><br>
        <button type="submit" style="padding: 0.6rem 1.2rem; background-color: #28a745; color: white; border: none; border-radius: 6px;">Salvar</button>
    </form>

    <br>
    <div style="text-align: center;">
      <a href="/admin/produtos" style="text-decoration: none; color: #007bff;">Cancelar</a>
    </div>
'''


@app.route('/')
def home():
    return render_template('index.html')

@app.route('/generate-payment')
def generate_payment():
    plano = request.args.get("plano")
    if not plano:
        return "Plano não informado", 400

    planos_info = {
        "essential": {"price": 20.00, "title": "Premium Essential"},
        "moderado": {"price": 45.00, "title": "Premium Moderado"},
        "master": {"price": 100.00, "title": "Premium Master"}
    }

    if plano not in planos_info:
        return "Plano inválido", 400

    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("login"))

    # Buscar nome e email do usuário
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT nome, email FROM users2 WHERE id = %s", (user_id,))
    user_info = cur.fetchone()
    cur.close()
    conn.close()

    if not user_info:
        return "Usuário não encontrado", 404

    payment_data = {
    "items": [{
        "title": f"Plano {planos_info[plano]['title']} - Site Genius",
        "quantity": 1,
        "currency_id": "BRL",
        "unit_price": planos_info[plano]["price"]
    }],
    "payer": {
        "email": user_info[1],
        "name": user_info[0]
    },
    "metadata": {
        "user_id": user_id,
        "plano": plano
    },
    "back_urls": {
        "success": f"{BASE_URL}/payment-success?plano={plano}",
        "failure": f"{BASE_URL}/payment-failure"
    },
    "auto_return": "approved"
}

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}"
    }
    print("🔍 Dados do pagador:", user_info)
    print("📦 Payload enviado ao Mercado Pago:", payment_data)

    response = requests.post("https://api.mercadopago.com/checkout/preferences", json=payment_data, headers=headers)

    if response.status_code == 201:
        return redirect(response.json()["init_point"])
    else:
        print("Erro:", response.text)  # Pode ajudar no debug
        print("Detalhes:", response.json())
        return "Erro ao gerar pagamento", 500


@app.route("/verificar-pagamento")
def verificar_pagamento():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"status": "erro", "mensagem": "Usuário não autenticado"}), 401

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT txid FROM pagamentos_pix WHERE user_id = %s ORDER BY id DESC LIMIT 1", (user_id,))
    result = cur.fetchone()

    if not result:
        cur.close()
        conn.close()
        return jsonify({"status": "erro", "mensagem": "Nenhuma cobrança encontrada"}), 404

    txid = result[0]

    if not txid:
        cur.close()
        conn.close()
        return jsonify({"status": "erro", "mensagem": "Cobrança sem TXID registrado"}), 400

    config = {
        "client_id": os.getenv("EFI_CLIENT_ID"),
        "client_secret": os.getenv("EFI_CLIENT_SECRET"),
        "certificate": os.path.join(os.path.dirname(__file__), os.getenv("EFI_CERTIFICATE_PATH")),
        "sandbox": os.getenv("EFI_SANDBOX", "false").lower() == "true"
    }

    gn = EfiPay(config)

    try:
        pagamento = gn.pix_detail_charge(params={"txid": txid})

        if pagamento.get("status") == "CONCLUIDA":
            cur.execute("UPDATE users2 SET is_premium = TRUE WHERE id = %s", (user_id,))
            cur.execute("UPDATE pagamentos_pix SET confirmado = TRUE WHERE txid = %s", (txid,))
            conn.commit()
            return jsonify({
                "status": "ok",
                "mensagem": "Parabéns! Seu pagamento foi aprovado, agora você está apto a editar imagens."
            })
        else:
            return jsonify({"status": "pendente", "mensagem": "Pagamento ainda não foi confirmado."})
    except Exception as e:
        return jsonify({"status": "erro", "mensagem": str(e)})
    finally:
        cur.close()
        conn.close()




@app.route("/generate-payment-pix")
def generate_payment_pix():
    servico = request.args.get("servico")
    user_id = session.get("user_id")

    if not user_id:
        return jsonify({"error": "Usuário não autenticado"}), 401

    planos_info = {
        "editar-imagens": {"price": 10.00, "title": "Desbloqueio de Edição de Imagens"}
    }

    if servico not in planos_info:
        return jsonify({"error": "Serviço inválido"}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT nome, email FROM users2 WHERE id = %s", (user_id,))
    user_info = cur.fetchone()
    cur.close()
    conn.close()

    if not user_info:
        return jsonify({"error": "Usuário nao encontrado"}), 404

    config = {
        "client_id": os.getenv("EFI_CLIENT_ID"),
        "client_secret": os.getenv("EFI_CLIENT_SECRET"),
        "certificate": os.path.join(os.path.dirname(__file__), os.getenv("EFI_CERTIFICATE_PATH")),
        "sandbox": os.getenv("EFI_SANDBOX", "false").lower() == "true"
    }
    gn = EfiPay(config)

    body = {
        "calendario": {"expiracao": 3600},
        "valor": {"original": f"{planos_info[servico]['price']:.2f}"},
        "chave": "49995ebf-1875-462b-89c0-edc4af1ac08a",
        "solicitacaoPagador": f"Pagamento referente ao serviço: {planos_info[servico]['title']}"
    }

    try:
        charge = gn.pix_create_immediate_charge(body=body)
        txid = charge["txid"]
        print("🔍 RESPOSTA DA CRIAÇÃO DE COBRANÇA:", charge)
        print("✅ TXID:", txid)

        # Salvar cobrança no banco
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO pagamentos_pix (user_id, txid, servico) VALUES (%s, %s, %s)",
            (user_id, txid, servico)
        )
        conn.commit()
        cur.close()
        conn.close()
        loc_id = charge["loc"]["id"]
        qrcode = gn.pix_generate_qrcode(params={"id": loc_id})   

        return jsonify({
              "qr_code": qrcode["qrcode"],
            "qr_code_base64": qrcode["imagemQrcode"],
            "copy_code": qrcode["qrcode"],
            "qr_link": qrcode["linkVisualizacao"]
        }) # link para abrir a cobrança
    except Exception as e:
        print("❌ Erro ao gerar cobrança PIX:", e)
        return jsonify({"error": "Erro ao gerar cobrança PIX"}), 500


@app.route("/webhook-pix", methods=["POST"])
def webhook_pix():
    payload = request.get_json()

    if not payload:
        return jsonify({"status": "erro", "mensagem": "Payload inválido"}), 400

    try:
        txid = payload["pix"][0]["txid"]

        conn = get_db_connection()
        cur = conn.cursor()

        # Confirmar cobrança no banco
        cur.execute("UPDATE pagamentos_pix SET confirmado = TRUE WHERE txid = %s", (txid,))
        cur.execute("""
            UPDATE users2 SET is_premium = TRUE
            WHERE id = (
                SELECT user_id FROM pagamentos_pix WHERE txid = %s
            )
        """, (txid,))
        conn.commit()
        cur.close()
        conn.close()

        print(f"✅ Pagamento confirmado via webhok para txid: {txid}")
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        print("❌ Erro no Webhook:", e)
        return jsonify({"status": "erro"}), 500

@app.route("/configurar-webhook")
def configurar_webhook():
    config = {
        "client_id": os.getenv("EFI_CLIENT_ID"),
        "client_secret": os.getenv("EFI_CLIENT_SECRET"),
        "certificate": os.path.join(os.path.dirname(__file__), os.getenv("EFI_CERTIFICATE_PATH")),

        "sandbox": os.getenv("EFI_SANDBOX", "false").lower() == "true"
    }

    gn = EfiPay(config)

    try:
        body = {
            "webhookUrl": "https://sitegenius.com.br/webhook-pix"
        }

        response = gn.pix_config_webhook(chave="49995ebf-1875-462b-89c0-edc4af1ac08a", body=body)
        return jsonify(response)
    except Exception as e:
        return jsonify({"erro": str(e)})


    
@app.route('/verificar-sessao')
def verificar_sessao():
    if 'user_id' in session:
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 401
    

@app.route('/payment-success')
def payment_success():
    plano = request.args.get("plano")
    payment_id = request.args.get("collection_id") or request.args.get("payment_id")

    if 'user_id' not in session or not plano or not payment_id:
        return "Acesso inválido", 401

    # Verifica status do pagamento via API do Mercado Pago
    response = requests.get(
        f"https://api.mercadopago.com/v1/payments/{payment_id}",
        headers={"Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}"}
    )

    if response.status_code != 200:
        return "Erro ao verificar pagamento", 500

    payment_info = response.json()
    if payment_info.get('status') != 'approved':
        return "Pagamento ainda não aprovado", 400

    # Verifica se esse pagamento é mesmo do usuário
    metadata = payment_info.get('metadata', {})
    user_id = session['user_id']
    if str(metadata.get("user_id")) != str(user_id) or metadata.get("plano") != plano:
        return "Pagamento não corresponde ao usuário logado", 403

    # Atualiza banco
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE users2 SET is_premium = TRUE, premium_level = %s WHERE id = %s", (plano, user_id))
        conn.commit()
        session['is_premium'] = True
        session['premium_level'] = plano
        return render_template('mensagem_sucesso.html', plano=plano)
    except psycopg2.Error:
        conn.rollback()
        return "Erro ao ativar premium", 500
    finally:
        cur.close()
        conn.close()


@app.route('/webhook', methods=['GET', 'POST'])
@app.route('/webhook/', methods=['GET', 'POST'])
def webhook():
    print("🚀 Rota /webhook acessada")
    return 'Webhook ativo', 200


@app.route('/notificacoes-mercado-pago', methods=['POST'])
def notificacoes():
    print("📦 Webhook Mercado Pago recebido")
    data = request.get_json()
    print("📩 Notificação recebida:", data)

    if data.get('type') == 'payment':
        payment_id = data.get('data', {}).get('id')
        if not payment_id:
            print("❌ ID do pagamento ausente na notificação")
            return 'ID ausente', 400

        # Consulta do pagamento via API
        response = requests.get(
            f"https://api.mercadopago.com/v1/payments/{payment_id}",
            headers={"Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}"}
        )

        if response.status_code == 200:
            payment_info = response.json()
            print("✅ Pagamento consultado:", payment_info)

            if payment_info.get('status') == 'approved':
                metadata = payment_info.get('metadata', {})
                user_id = metadata.get('user_id')
                plano = metadata.get('plano')

                if user_id and plano:
                    conn = get_db_connection()
                    cur = conn.cursor()
                    try:
                        cur.execute(
                            "UPDATE users2 SET is_premium = TRUE, premium_level = %s WHERE id = %s AND (is_premium = FALSE OR is_premium IS NULL)",
                            (plano, user_id)
                        )
                        conn.commit()
                        print(f"🎉 Plano {plano} ativado para o usuário {user_id}")
                    except Exception as e:
                        print("❌ Erro ao atualizar usuário:", str(e))
                        conn.rollback()
                    finally:
                        cur.close()
                        conn.close()
                else:
                    print("❌ Metadata incompleta (user_id ou plano ausente)")
            else:
                print("ℹ️ Pagamento ainda não aprovado")
        else:
            print("❌ Falha ao consultar pagamento:", response.text)

    return 'OK', 200




@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        nome = request.form['nome']
        email = request.form['email']
        senha = request.form['senha']

        # 1. Verifica nome completo
        if len(nome.strip().split()) < 2:
            return render_template('register.html', erro="Informe seu nome completo.")

        # 2. Valida e-mail com regex
        email_regex = r'^[\w\.-]+@[\w\.-]+\.\w+$'
        if not re.match(email_regex, email):
            return render_template('register.html', erro="E-mail em formato inválido.")

        # 3. Verifica se o e-mail existe via MailboxLayer
        api_key = 'e85b58c746b9d7dcce1425d8a7dabf65'
        try:
            response = requests.get("http://apilayer.net/api/check", params={
                'access_key': api_key,
                'email': email,
                'smtp': 1,
                'format': 1
            })
            data = response.json()
            if not data.get('smtp_check'):
                return render_template('register.html', erro="Este e-mail não parece ser real ou ativo.")
        except Exception as e:
            print(f"Erro com MailboxLayer: {e}")
            # Se der erro, segue o fluxo mesmo assim

        # 4. Upload do avatar (opcional)
        avatar_url = None
        if 'avatar' in request.files:
            avatar_file = request.files['avatar']
            if avatar_file.filename != '':
                upload_result = cloudinary.uploader.upload(avatar_file)
                avatar_url = upload_result['secure_url']

        # 5. Criptografa a senha
        hashed_senha = bcrypt.hashpw(senha.encode('utf-8'), bcrypt.gensalt()).decode()

        # 6. Verifica se o e-mail já está cadastrado
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("SELECT id FROM users2 WHERE email = %s", (email,))
            if cur.fetchone():
                return render_template('register.html', erro="Já existe um cadastro com este e-mail.")

            cur.execute(
                "INSERT INTO users2 (nome, email, senha, avatar_url) VALUES (%s, %s, %s, %s)", 
                (nome, email, hashed_senha, avatar_url)
            )
            conn.commit()
            return redirect(url_for('login'))
        except psycopg2.Error:
            conn.rollback()
            return "Erro ao cadastrar usuário", 500
        finally:
            cur.close()
            conn.close()

    return render_template('register.html')





@app.route("/sugerir-template", methods=["POST"])
def sugerir_template():
    sugestao = request.form.get("sugestao")
    if not sugestao:
        return redirect("/templates")

    corpo_email = f"Sugestão recebida no Site Genius:\n\n{sugestao}"
    
    msg = MIMEText(corpo_email)
    msg["Subject"] = "Nova sugestão de template"
    msg["From"] = "emano4775@gmail.com"
    msg["To"] = "emano4775@gmail.com"

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login("emano4775@gmail.com", "xgyq bidt ftpb fyfy ")
            server.send_message(msg)
        return redirect("/templates")
    except Exception as e:
        print("Erro ao enviar e-mail:", e)
        return "Erro ao enviar sugestão", 500
@app.route("/ajuda-suporte", methods=["POST"])
def ajuda_suporte():
    mensagem = request.form.get("mensagem")
    if not mensagem:
        return redirect("/tutorial")  # redireciona de volta para a Central de Ajuda

    corpo_email = f"Mensagem recebida na Central de Ajuda:\n\n{mensagem}"

    msg = MIMEText(corpo_email)
    msg["Subject"] = "Ajuda - Problema Técnico no Site Genius"
    msg["From"] = "emano4775@gmail.com"
    msg["To"] = "emano4775@gmail.com"

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login("emano4775@gmail.com", "xgyq bidt ftpb fyfy")
            server.send_message(msg)
        return redirect("/tutorial")  # volta pra página de ajuda após enviar
    except Exception as e:
        print("Erro ao enviar e-mail:", e)
        return "Erro ao enviar suporte", 500

@app.route("/<subdomain>/enviar-reclamacao", methods=["POST"])
def enviar_reclamacao(subdomain):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM user_templates WHERE subdomain = %s LIMIT 1", (subdomain,))
    dono = cur.fetchone()

    if not dono:
        cur.close(); conn.close()
        return "Loja não encontrada", 404

    user_id = dono[0]

    nome = request.form.get("nome")
    email = request.form.get("email")
    voo = request.form.get("voo")
    problema = request.form.get("problema")
    imagem = None
    whatsapp = request.form.get("whatsapp")

    # Envia e-mail
    corpo_email = f"Nova Reclamação de Voo:\n\nNome: {nome}\nE-mail: {email}\nNúmero do Voo: {voo}\nProblema: {problema}"
    msg = MIMEText(corpo_email)
    msg["Subject"] = "Reclamação de Voo"
    msg["From"] = "emano4775@gmail.com"
    msg["To"] = "emano4775@gmail.com"

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login("emano4775@gmail.com", "xgyq bidt ftpb fyfy")
            server.send_message(msg)
    except Exception as e:
        print("[ERRO] Email:", e)

    # Insere no banco
    cur.execute("""
    INSERT INTO reclamacoes_voo (nome, email, whatsapp, voo, problema, imagem, data_envio, user_id)
    VALUES (%s, %s, %s, %s, %s, %s, NOW(), %s)
""", (nome, email, whatsapp, voo, problema, imagem, user_id))
    conn.commit()
    cur.close(); conn.close()

    return redirect(f"/{subdomain}/index")

@app.route('/comentar/<int:reclamacao_id>', methods=['POST'])
def comentar(reclamacao_id):
    conn = get_db_connection()
    nome = request.form['nome']
    comentario = request.form['comentario']
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO comentarios (reclamacao_id, nome, comentario)
        VALUES (%s, %s, %s)
    """, (reclamacao_id, nome, comentario))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(request.referrer)


@app.route('/avaliar/<int:reclamacao_id>', methods=['POST'])
def avaliar(reclamacao_id):
    conn = get_db_connection()
    nota = int(request.form['nota'])
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO avaliacoes (reclamacao_id, nota)
        VALUES (%s, %s)
    """, (reclamacao_id, nota))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(request.referrer)


@app.route("/admin/reclamacoes")
def admin_reclamacoes():
    user_id = session.get("user_id")
    if not user_id:
        return redirect("/login")

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, nome, email, whatsapp, voo, problema, imagem, data_envio
        FROM reclamacoes_voo
        WHERE user_id = %s
        ORDER BY data_envio DESC
    """, (user_id,))
    reclamacoes = cur.fetchall()
    reclamacoes = [
    (*r[:7], parser.parse(r[7]) if isinstance(r[7], str) else r[7])
    for r in reclamacoes
    ]
    cur.close()
    conn.close()

    return render_template("admin_reclamacoes.html", reclamacoes=reclamacoes)

@app.route("/admin/reclamacoes/deletar/<int:id>", methods=["POST"])
def deletar_reclamacao(id):
    conn = get_db_connection()
    cur = conn.cursor()

    # Deleta comentários e avaliações antes de deletar a reclamação
    cur.execute("DELETE FROM comentarios WHERE reclamacao_id = %s", (id,))
    cur.execute("DELETE FROM avaliacoes WHERE reclamacao_id = %s", (id,))
    cur.execute("DELETE FROM reclamacoes_voo WHERE id = %s", (id,))

    conn.commit()
    cur.close()
    conn.close()
    return redirect("/admin/reclamacoes")





@app.route('/check-login')
def check_login():
    if 'user_id' in session:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT nome, premium_level, avatar_url, is_premium FROM users2 WHERE id = %s", (session['user_id'],))
        result = cur.fetchone()
        cur.close(); conn.close()

        if result:
            nome, plano, avatar_url, is_premium = result
            return jsonify({
                "logged_in": True,
                "user_name": nome,
                "premium_level": plano,
                "avatar_url": avatar_url,
                "is_premium": is_premium
            })

    return jsonify({"logged_in": False})




@app.route('/logout', methods=['POST'])
def logout():
    session.clear()  # Remove todas as informações da sessão
    return jsonify({"message": "Usuário deslogado com sucesso"}), 200

@app.route('/login', methods=['GET', 'POST'])
def login():
    erro = None

    if request.method == 'POST':
        email = request.form['email']
        senha = request.form['senha']

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, nome, senha, is_premium, avatar_url FROM users2 WHERE email = %s", (email,))
        user = cur.fetchone()
        print("DEBUG - Tipo do hash retornado:", type(user[2]))
        print("DEBUG - Valor do hash retornado:", user[2])

        if user:
            senha_hash = user[2]

            if bcrypt.checkpw(senha.encode('utf-8'), senha_hash.encode('utf-8') if isinstance(senha_hash, str) else senha_hash):
                user_id = user[0]
                session['user_id'] = user_id
                session['user_name'] = user[1]
                session['is_premium'] = user[3]
                session['avatar_url'] = user[4]


                session_token = str(uuid.uuid4())
                cur.execute("UPDATE users2 SET session_token = %s WHERE id = %s", (session_token, user_id))
                conn.commit()

                resp = make_response(redirect(url_for('home')))
                resp.set_cookie('session_token', session_token, samesite='None', secure=True)

                cur.close()
                conn.close()
                return resp
            else:
                erro = "Usuário ou senha incorretos"
        else:
            erro = "Usuário não encontrado"

        cur.close()
        conn.close()

    return render_template('login.html', erro=erro)




@app.route('/templates')
def templates():
    if 'user_id' not in session:
        flash("🚀 Para visualizar os templates disponíveis, por favor faça seu login ou cadastre-se — é gratuito!")
        return redirect(url_for('login'))

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT is_premium FROM users2 WHERE id = %s", (session['user_id'],))
    user_status = cur.fetchone()
    cur.close()
    conn.close()

    if user_status:
        session['is_premium'] = user_status[0]  # Atualiza o status na sessão

    is_premium = session.get('is_premium', False)
    return render_template('templates.html', is_premium=is_premium)

@app.route('/debug/template/<template_name>')
def visualizar_template(template_name):
    return render_template(f"{template_name}.html")





@app.route('/usar-template/<template_name>', methods=['POST'])
def usar_template(template_name):
    user_id = session.get('user_id')

    if not user_id:
        token = request.cookies.get('session_token')
        if not token:
            return jsonify({"success": False, "message": "Faça login para usar o template."}), 401

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM users2 WHERE session_token = %s", (token,))
        user = cur.fetchone()
        cur.close(); conn.close()

        if user:
            user_id = user[0]
            session['user_id'] = user_id
        else:
            return jsonify({"success": False, "message": "Sessão inválida. Faça login novamente."}), 401

    conn = get_db_connection()
    cur = conn.cursor()

  # Verifica status do usuário e o nível do template clicado
    cur.execute("""
        SELECT u.is_premium, u.premium_level, t.premium_level 
        FROM users2 u
        JOIN templates t ON t.name = %s
        WHERE u.id = %s
    """, (template_name, user_id))
    result = cur.fetchone()

    if not result:
        cur.close(); conn.close()
        return jsonify({"success": False, "message": "Template ou usuário inválido."}), 404

    is_premium, user_premium_level, template_level = result
    cur.execute("SELECT COUNT(*) FROM user_templates WHERE user_id=%s", (user_id,))
    total_templates = cur.fetchone()[0]

    if user_premium_level == 'free' and total_templates >= 1:
        cur.close(); conn.close()
        return jsonify({"success": False, "message": "Usuários gratuitos só podem criar 1 modelo."}), 403

  

    # Regra geral de bloqueio para usuários free tentando usar templates premium
    if template_level != 'free' and user_premium_level == 'free':
        cur.close(); conn.close()
        return jsonify({"success": False, "message": "Este template é Premium. Faça upgrade para usá-lo."}), 403
    

    

    cur.execute("""
        SELECT page_name, html, css
        FROM template_pages
        WHERE template_name = %s
    """, (template_name,))
    paginas = cur.fetchall()

    if not paginas:
        cur.close(); conn.close()
        return jsonify({"success": False, "message": "Template não encontrado."}), 404

    subdomain = f"user{user_id}-{uuid.uuid4().hex[:6]}"
    template_id = None

    def adaptar_links(html, template_name, subdomain, template_id):
        soup = BeautifulSoup(html, 'html.parser')

        for a in soup.find_all('a', href=True):
            href = a['href'].strip()
            if "{{" in href or "{%" in href:
                continue

            # Corrigir /template/... → /<subdomain>/...
            if href.startswith('/template/'):
                nova_rota = href.replace('/template/', f'/{subdomain}/')
                a['href'] = nova_rota
                continue

            # Corrigir demais padrões conforme template
            if template_name == 'template10':
                match = re.match(r"^/(template|site|[\w-]+)/(\w+)", href)
                if match:
                    page = match.group(2)
                    a['href'] = f"/{subdomain}/{page}"
            else:
                match = re.match(r"^/(template|site|[\w-]+)/(\w+)", href)
                if match:
                    page = match.group(2)
                    a['href'] = f"/{subdomain}/{page}"

        for form in soup.find_all('form', action=True):
            action = form['action'].strip()
            if "{{" in action or "{%" in action:
                continue

            # Corrige rotas específicas do sistema
            if re.match(r"^/(login-cliente|cadastrar|meus-pedidos|acompanhar-pedido.*)", action):
                rota = action.lstrip("/")
                form['action'] = f"/{subdomain}/{rota}"
            elif action.startswith('/template/'):
                nova_action = action.replace('/template/', f'/{subdomain}/')
                form['action'] = nova_action

        for script in soup.find_all('script', src=True):
            src = script['src'].strip()
            if "{{" in src or not src.startswith("/template/"):
                continue
            caminho = src.split("/template/")[-1].strip()
            script['src'] = f"/site/{template_id}/{caminho}"

        for link in soup.find_all('link', href=True):
            href = link['href'].strip()
            if "{{" in href or not href.startswith("/template/"):
                continue
            caminho = href.split("/template/")[-1].strip()
            link['href'] = f"/site/{template_id}/{caminho}"

        return str(soup)


    for idx, (page_name, html_content, css_content) in enumerate(paginas):
        html_content = html_content.replace("{{sub}}", subdomain)
        html_completo = f"""<!DOCTYPE html>
        <html lang="pt-BR">
        <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>{css_content}</style>
        </head>
        <body>
        {html_content}
        </body>
        </html>"""

        html_corrigido = adaptar_links(html_completo, template_name, subdomain, template_id)


        if idx == 0:
            cur.execute("""
                INSERT INTO user_templates (user_id, template_name, custom_html, subdomain, page_name)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
            """, (user_id, template_name, 'TEMP', subdomain, page_name))
            template_id = cur.fetchone()[0]
            html_corrigido = adaptar_links(html_completo, template_name, subdomain, template_id)
            cur.execute("UPDATE user_templates SET custom_html = %s WHERE id = %s", (html_corrigido, template_id))
        else:
            html_corrigido = adaptar_links(html_completo, template_name, subdomain, template_id)
            cur.execute("""
                INSERT INTO user_templates (user_id, template_name, custom_html, subdomain, page_name)
                VALUES (%s, %s, %s, %s, %s)
            """, (user_id, template_name, html_corrigido, subdomain, page_name))

    conn.commit()
    cur.close(); conn.close()

    return jsonify({
        "success": True,
        "message": "Template vinculado!",
        "template_id": template_id,
        "page_name": 'index',
        "template_name": template_name
    })

def adaptar_links(html, template_name, subdomain, template_id):
    soup = BeautifulSoup(html, 'html.parser')

    # Corrige todos os <a href="">
    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        if "{{" in href or "{%" in href:
            continue

        # Corrige diretamente links do tipo /template/alguma-coisa
        if href.startswith('/template/'):
            page = href.split('/template/')[-1]
            a['href'] = f"/{subdomain}/{page}"
            continue

        # Corrige links do tipo /site/123/123 ou /site/123/index
        match = re.match(r'^/site/\d+/\d+/?(\w+)?', href)
        if match:
            page = match.group(1) or "index"
            a['href'] = f"/{subdomain}/{page}"
            continue

        # Corrige links tipo /template/abc ou /qualquercoisa/page
        match = re.match(r"^/(template|site|[\w-]+)/(\w+)", href)
        if match:
            page = match.group(2)
            a['href'] = f"/{subdomain}/{page}"

    # Corrige todos os <form action="">
    for form in soup.find_all('form', action=True):
        action = form['action'].strip()
        if "{{" in action or "{%" in action:
            continue

        if re.match(r"^/(login-cliente|cadastrar|meus-pedidos|acompanhar-pedido.*)", action):
            rota = action.lstrip("/")
            form['action'] = f"/{subdomain}/{rota}"
        elif action.startswith('/template/'):
            rota = action.split('/template/')[-1]
            form['action'] = f"/{subdomain}/{rota}"

    # Corrige todos os <script src="">
    for script in soup.find_all('script', src=True):
        src = script['src'].strip()
        if "{{" in src or not src.startswith("/template/"):
            continue
        caminho = src.split("/template/")[-1].strip()
        script['src'] = f"/site/{template_id}/{caminho}"

    # Corrige todos os <link href="">
    for link in soup.find_all('link', href=True):
        href = link['href'].strip()
        if "{{" in href or not href.startswith("/template/"):
            continue
        caminho = href.split("/template/")[-1].strip()
        link['href'] = f"/site/{template_id}/{caminho}"

        html_renderizado = str(soup).replace("{{sub}}", subdomain)
        return html_renderizado





def corrigir_links_antigos():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, custom_html, template_name, subdomain, user_id
        FROM user_templates
    """)
    resultados = cur.fetchall()

    for row in resultados:
        id, html, template_name, subdomain, user_id = row
        template_id = id

        try:
            if html is None:
                print(f"❌ HTML nulo no template ID {id}, ignorando...")
                continue

            html_corrigido = adaptar_links(html, template_name, subdomain, template_id)

            cur.execute("UPDATE user_templates SET custom_html = %s WHERE id = %s", (html_corrigido, id))
            print(f"✅ Corrigido template {template_name} ID {id} para subdomínio {subdomain}")
        except Exception as e:
            print(f"❌ Erro ao corrigir template ID {id}: {e}")

    conn.commit()
    cur.close()
    conn.close()

@app.route('/atualizar-links-antigos')
def atualizar_links_antigos():
    corrigir_links_antigos()
    return "Links atualizados com sucesso!"


@app.route('/reparar-templates-nulos', methods=['GET'])
def reparar_templates_nulos():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, user_id, template_name, subdomain, page_name
        FROM user_templates
        WHERE custom_html IS NULL
    """)
    templates = cur.fetchall()

    print(f"🔎 Encontrados {len(templates)} templates com custom_html NULL")

    def adaptar_links(html, template_name, subdomain, template_id):
        soup = BeautifulSoup(html, 'html.parser')

        for a in soup.find_all('a', href=True):
            href = a['href'].strip()
            if "{{" in href or "{%" in href:
                continue
            if href.startswith('/template/'):
                a['href'] = href.replace('/template/', f'/{subdomain}/')
                continue
            match = re.match(r"^/(template|site|[\w-]+)/(\w+)", href)
            if match:
                page = match.group(2)
                a['href'] = f"/{subdomain}/{page}"

        for form in soup.find_all('form', action=True):
            action = form['action'].strip()
            if "{{" in action or "{%" in action:
                continue
            if re.match(r"^/(login-cliente|cadastrar|meus-pedidos|acompanhar-pedido.*)", action):
                form['action'] = f"/{subdomain}/{action.lstrip('/')}"
            elif action.startswith('/template/'):
                form['action'] = action.replace('/template/', f'/{subdomain}/')

        for script in soup.find_all('script', src=True):
            src = script['src'].strip()
            if "{{" in src or not src.startswith("/template/"):
                continue
            caminho = src.split("/template/")[-1].strip()
            script['src'] = f"/site/{template_id}/{caminho}"

        for link in soup.find_all('link', href=True):
            href = link['href'].strip()
            if "{{" in href or not href.startswith("/template/"):
                continue
            caminho = href.split("/template/")[-1].strip()
            link['href'] = f"/site/{template_id}/{caminho}"

        return str(soup)

    atualizados = 0

    for id, user_id, template_name, subdomain, page_name in templates:
        # busca conteúdo da base
        page_base_name = page_name
        if template_name == 'template10':
            page_base_name = page_base_name.replace("meuspedidos", "meus-pedidos")
            page_base_name = page_base_name.replace("acompanharpedido", "acompanhar-pedido")
            page_base_name = page_base_name.replace("editardados", "editar-dados")

        cur.execute("""
            SELECT html, css FROM template_pages
            WHERE template_name = %s AND page_name = %s
        """, (template_name, page_base_name))
        base = cur.fetchone()

        if not base:
            print(f"❌ Template base não encontrado: {template_name}/{page_name}")
            continue

        html, css = base
        if not html:
            print(f"❌ HTML vazio no base: {template_name}/{page_name}")
            continue

        html_modificado = html.replace("{{sub}}", subdomain)
        html_completo = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>{css or ''}</style>
</head>
<body>
{html_modificado}
</body>
</html>"""

        html_final = adaptar_links(html_completo, template_name, subdomain, id)

        cur.execute("UPDATE user_templates SET custom_html = %s WHERE id = %s", (html_final, id))
        print(f"✅ Corrigido template {template_name} ID {id} para subdomínio {subdomain}")
        atualizados += 1

    conn.commit()
    cur.close(); conn.close()
    return f"🔥 Reparação concluída: {atualizados} templates atualizados."



@app.route('/debug/<subdomain>/<page_name>')
def render_template_dinamico(subdomain, page_name):
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT custom_html
        FROM user_templates
        WHERE subdomain = %s AND page_name = %s
        LIMIT 1
    """, (subdomain, page_name))

    result = cur.fetchone()
    cur.close(); conn.close()

    if not result:
        return "Página não encontrada", 404

    return result[0]  # já é HTML completo


@app.route('/template-preview/<template_name>/<page>')
def template_preview(template_name, page):
    from bs4 import BeautifulSoup
    import re

    conn = get_db_connection()
    cur = conn.cursor()

    # Primeiro tenta buscar na tabela template_pages
    cur.execute("""
        SELECT html, css FROM template_pages
        WHERE template_name = %s AND (page_name = %s OR page_name = %s)
        LIMIT 1
    """, (template_name, page, f"{template_name}_{page}"))
    result = cur.fetchone()

    # Se não encontrou na template_pages e for uma página especial, tenta na user_templates
    if not result and page in ['login-cliente', 'cadastrar', 'meus-pedidos', 'acompanhar-pedido', 'editar-dados']:
        cur.execute("""
            SELECT custom_html FROM user_templates
            WHERE template_name = %s AND page_name = %s
            LIMIT 1
        """, (template_name, page))
        user_result = cur.fetchone()

        if not user_result:
            cur.close(); conn.close()
            return f"<h2>❌ Página '{page}' não encontrada para o template '{template_name}'.</h2>", 404

        html = user_result[0]
        css = ""  # não há CSS separado
    elif result:
        html, css = result
    else:
        cur.close(); conn.close()
        return f"<h2>❌ Página '{page}' não encontrada para o template '{template_name}'.</h2>", 404

    cur.close(); conn.close()

    # 🔧 Corrige os links
    soup = BeautifulSoup(html, 'html.parser')

    for tag in soup.find_all(['a', 'form', 'script', 'link']):
        if tag.name == 'a' and tag.has_attr('href'):
            href = tag['href'].strip()

            if href.startswith('/template/'):
                page_name = href.split('/template/')[-1]
                tag['href'] = f'/template-preview/{template_name}/{page_name}'

            elif re.match(r'^/\{\{.*sub.*\}\}/.+', href) or re.match(r'^/user\d+-[a-z0-9]+/.+', href):
                parts = href.split('/')
                if len(parts) >= 3:
                    page_name = parts[2]
                    tag['href'] = f'/template-preview/{template_name}/{page_name}'

        elif tag.name == 'form' and tag.has_attr('action'):
            action = tag['action'].strip()
            if re.match(r'^/\{\{.*sub.*\}\}/.+', action) or re.match(r'^/user\d+-[a-z0-9]+/.+', action):
                parts = action.split('/')
                if len(parts) >= 3:
                    page_name = parts[2]
                    tag['action'] = f'/template-preview/{template_name}/{page_name}'

        elif tag.name == 'script' and tag.has_attr('src'):
            src = tag['src'].strip()
            if '/template/' in src:
                page_name = src.split('/template/')[-1]
                tag['src'] = f'/site/{template_name}/{page_name}'

        elif tag.name == 'link' and tag.has_attr('href'):
            href = tag['href'].strip()
            if '/template/' in href:
                page_name = href.split('/template/')[-1]
                tag['href'] = f'/site/{template_name}/{page_name}'

    html_corrigido = str(soup).replace("{{sub}}", "subpreview")

    html_completo = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>{css}</style>
</head>
<body>
{html_corrigido}
</body>
</html>
"""
    return html_completo





@app.route('/criar-subpagina/<int:template_id>/<new_page>', methods=['POST'])
def criar_subpagina(template_id, new_page):
    if 'user_id' not in session:
        return jsonify({"success": False, "message": "Faça login para criar uma subpágina."}), 401

    conn = get_db_connection()
    cur = conn.cursor()

    # Pega o template original para puxar o template_name e subdomain
    cur.execute("""
        SELECT template_name, subdomain FROM user_templates 
        WHERE id = %s AND user_id = %s LIMIT 1
    """, (template_id, session['user_id']))

    info = cur.fetchone()

    if not info:
        return jsonify({"success": False, "message": "Template não encontrado"}), 404

    template_name, subdomain = info

    try:
        with open(f'templates/{template_name}.html', 'r', encoding='utf-8') as f:
            html_base = f.read()
    except:
        return jsonify({"success": False, "message": "Arquivo base não encontrado"}), 500

    # Cria a nova subpágina com conteúdo padrão (poderia ser outro template se quiser)
    cur.execute("""
        INSERT INTO user_templates (user_id, template_name, custom_html, subdomain, page_name)
        VALUES (%s, %s, %s, %s, %s)
    """, (session['user_id'], template_name, html_base, subdomain, new_page))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"success": True, "message": f"Subpágina '{new_page}' criada!"})

@app.route("/tutorial")
def tutorial():
    return render_template("tutorial.html")


@app.route('/meu-site')
def meu_site():
    user_id = session.get('user_id')

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT 
            template_name, subdomain, page_name,
            MAX(created_at) OVER (PARTITION BY subdomain) AS ultima_edicao,
            MIN(id) OVER (PARTITION BY subdomain) AS template_id
        FROM user_templates
        WHERE user_id = %s
    """, (user_id,))

    templates = {}
    for template_name, subdomain, page_name, ultima_edicao, template_id in cur.fetchall():
        if subdomain not in templates:
            templates[subdomain] = {
                'template_id': template_id,
                'template_name': template_name,
                'pages': [],
                'ultima_edicao': ultima_edicao
            }
        templates[subdomain]['pages'].append(page_name)

    cur.close()
    conn.close()

    return render_template(
        'meus_templates.html',
        templates=templates,
        request=request,
        multiple_sites=(len(templates) > 1)
    )

@app.route('/registrar-clique', methods=['POST'])
def registrar_clique():
    data = request.get_json()
    template = data.get('template')
    acao = data.get('acao')

    if template and acao:
        template = template.strip().lower()
        acao = acao.strip().lower()
        with open('cliques.json', 'a') as f:
            f.write(json.dumps({'template': template, 'acao': acao, 'hora': datetime.now().isoformat()}) + '\n')
        return jsonify({'status': 'ok'})
    return jsonify({'status': 'erro'}), 400
    
@app.route('/adicionar-pagina/<int:template_id>', methods=['POST'])
def adicionar_pagina(template_id):
    user_id = session.get('user_id')

    # 🧠 Se a sessão estiver vazia, tenta usar o session_token
    if not user_id:
        token = request.cookies.get('session_token')
        if not token:
            return jsonify({'message': 'Usuário não autenticado'}), 401

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM users2 WHERE session_token = %s", (token,))
        user = cur.fetchone()
        cur.close()
        conn.close()

        if user:
            user_id = user[0]
            session['user_id'] = user_id  # restaura na sessão
        else:
            return jsonify({'message': 'Token inválido'}), 401

    # 🧠 Continua com a lógica normal
    data = request.get_json()
    page_name = data.get('page_name')
    html = data.get('html', '')
    css = data.get('css', '')

    if not page_name:
        return jsonify({'message': 'Nome da página é obrigatório'}), 400

    html_completo = f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <style>{css}</style>
    </head>
    <body>{html}</body>
    </html>
    """

    conn = get_db_connection()
    cur = conn.cursor()

    # Obtém o nome e subdomínio do template
    cur.execute("""
        SELECT template_name, subdomain FROM user_templates 
        WHERE id = %s AND user_id = %s LIMIT 1
    """, (template_id, user_id))
    resultado = cur.fetchone()

    if not resultado:
        cur.close()
        conn.close()
        return jsonify({'message': 'Template original não encontrado'}), 404

    template_name, subdomain = resultado

    # Verifica o plano do usuário
    cur.execute("SELECT premium_level FROM users2 WHERE id = %s", (user_id,))
    plano_resultado = cur.fetchone()
    plano = plano_resultado[0] if plano_resultado and plano_resultado[0] else "free"

    # Conta apenas as páginas extras (sem contar a index)
    cur.execute("""
        SELECT COUNT(*) FROM user_templates 
        WHERE template_name = %s AND subdomain = %s AND user_id = %s AND page_name != 'index'
    """, (template_name, subdomain, user_id))
    total_paginas_extras = cur.fetchone()[0]

    # Se o plano não for moderado/master, limita a 1 página extra
    if plano not in ['moderado', 'master'] and total_paginas_extras >= 1:
        cur.close()
        conn.close()
        return jsonify({'message': 'Seu plano atual permite apenas 1 página extra. Faça upgrade para criar páginas ilimitadas.'}), 403

    # Cria a nova página
    cur.execute("""
        INSERT INTO user_templates (user_id, template_name, subdomain, page_name, custom_html)
        VALUES (%s, %s, %s, %s, %s)
    """, (user_id, template_name, subdomain, page_name, html_completo))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'message': f'Página "{page_name}" criada com sucesso!'})




@app.route('/debug/<subdomain>/<page_name>')
def exibir_pagina(subdomain, page_name):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT custom_html FROM user_templates
        WHERE subdomain = %s AND page_name = %s
    """, (subdomain, page_name))

    result = cur.fetchone()
    cur.close()
    conn.close()

    if not result:
        return "Página não encontrada", 404

    return result[0]

# ✅ Nova rota unificada (usará o subdomínio para tudo)
@app.route('/<subdomain>/<page_name>')
def site_usuario(subdomain, page_name):
    from bs4 import BeautifulSoup
    import re

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT template_name, user_id, template_id
        FROM user_templates
        WHERE subdomain = %s
        LIMIT 1
    """, (subdomain,))
    resultado = cur.fetchone()

    if not resultado:
        cur.close(); conn.close()
        return "Loja não encontrada", 404

    template_name, user_id, template_id = resultado

    # Protege páginas privadas
    paginas_privadas = ['meus-pedidos', 'acompanhar-pedido', 'editar-dados']
    if page_name in paginas_privadas:
        if 'cliente_id' not in session:
            return redirect(url_for('login_cliente', subdomain=subdomain))
        if 'user_id' in session:
            return "Acesso restrito a clientes.", 403

    # Template10 renderiza direto
    if template_name == 'template10':
        try:
            return render_template(f"{template_name}_{page_name}.html", subdomain=subdomain)
        except:
            return "Página não encontrada (template10)", 404

    # Outros templates => buscar HTML personalizado
    cur.execute("""
    SELECT custom_html
    FROM user_templates
    WHERE user_id = %s AND template_name = %s AND subdomain = %s AND page_name = %s
    LIMIT 1
    """, (user_id, template_name, subdomain, page_name))
    site = cur.fetchone()
    cur.close(); conn.close()

    if not site:
        return "Página não encontrada", 404

    html = site[0]
    soup = BeautifulSoup(html, 'html.parser')

    # Corrige rotas internas
    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        if re.match(r"^/(login-cliente|cadastrar|meus-pedidos|acompanhar-pedido.*)", href):
            a['href'] = f"/{subdomain}/{href.lstrip('/')}"

    for form in soup.find_all('form', action=True):
        action = form['action'].strip()
        if re.match(r"^/(login-cliente|cadastrar|meus-pedidos|acompanhar-pedido.*)", action):
            form['action'] = f"/{subdomain}/{action.lstrip('/')}"

    html_renderizado = str(soup).replace("{{sub}}", subdomain)
    return html_renderizado


# ✅ Função opcional para redirecionar antigos /site/<template_id>/<page_name>
@app.route('/site/<int:template_id>/<page_name>')
def redirecionar_site_antigo(template_id, page_name):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT subdomain FROM user_templates WHERE id = %s", (template_id,))
    sub = cur.fetchone()
    cur.close(); conn.close()
    if sub:
        return redirect(url_for('site_usuario', subdomain=sub[0], page_name=page_name))
    return "Página não encontrada", 404





@app.route('/pagina-existe/<int:template_id>/<page_name>')
def pagina_existe(template_id, page_name):
    user_id = session.get('user_id')

    # Tentativa de recuperar user_id via session_token
    if not user_id:
        token = request.cookies.get('session_token')
        if not token:
            return jsonify({'existe': False})
        
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM users2 WHERE session_token = %s", (token,))
        user = cur.fetchone()
        cur.close()
        conn.close()
        
        if user:
            user_id = user[0]
            session['user_id'] = user_id
        else:
            return jsonify({'existe': False})

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT subdomain, template_name FROM user_templates 
        WHERE id = %s AND user_id = %s
        LIMIT 1
    """, (template_id, user_id))
    template_info = cur.fetchone()

    if not template_info:
        cur.close()
        conn.close()
        return jsonify({'existe': False})

    subdomain, template_name = template_info

    cur.execute("""
        SELECT 1 FROM user_templates 
        WHERE user_id=%s AND subdomain=%s AND page_name=%s
    """, (user_id, subdomain, page_name))
    
    resultado = cur.fetchone()
    cur.close()
    conn.close()

    return jsonify({'existe': bool(resultado)})



@app.route('/listar-paginas/<int:template_id>')
def listar_paginas(template_id):
    user_id = session.get('user_id')

    if not user_id:
        token = request.cookies.get('session_token')
        if not token:
            return jsonify([])

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM users2 WHERE session_token = %s", (token,))
        user = cur.fetchone()
        cur.close()
        conn.close()

        if user:
            user_id = user[0]
            session['user_id'] = user_id
        else:
            return jsonify([])

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT page_name FROM user_templates 
        WHERE user_id = %s AND (
            id = %s OR template_name IN (
                SELECT template_name FROM user_templates WHERE id = %s
            )
        )
    """, (user_id, template_id, template_id))

    paginas = [row[0] for row in cur.fetchall() if row[0]]
    cur.close()
    conn.close()

    return jsonify(paginas)

@app.route('/preco')
def preco():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT is_premium FROM users2 WHERE id = %s", (user_id,))
    result = cur.fetchone()
    is_premium = bool(result[0]) if result else False

    cur.close()
    conn.close()
    print("🧪 is_premium do banco:", result[0], type(result[0]))
    return render_template('preco.html', is_premium=is_premium)

@app.context_processor
def inject_user_status():
    is_premium = False
    premium_level = 'free'
    if 'user_id' in session:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT is_premium, premium_level FROM users2 WHERE id = %s", (session['user_id'],))
        result = cur.fetchone()
        if result:
            is_premium = result[0]
            premium_level = result[1]
        cur.close()
        conn.close()
    return dict(is_premium=is_premium, premium_level=premium_level)



@app.route('/editar-template/<int:template_id>/<page_name>', methods=['GET', 'POST'])
def editar_pagina(template_id, page_name):
    session["template_id"] = template_id
    session["page_name"] = page_name
    user_id = session.get('user_id')

    if not user_id:
        token = request.cookies.get('session_token')
        if token:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT id FROM users2 WHERE session_token = %s", (token,))
            user = cur.fetchone()
            cur.close()
            conn.close()

            if user:
                user_id = user[0]
                session['user_id'] = user_id
            else:
                if request.method == 'POST':
                    return jsonify({"message": "Sessão expirada"}), 401
                return redirect(url_for('login'))
        else:
            if request.method == 'POST':
                return jsonify({"message": "Sessão expirada"}), 401
            return redirect(url_for('login'))

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT premium_level FROM users2 WHERE id = %s", (user_id,))
    nivel = cur.fetchone()
    premium_level = (nivel[0] if nivel and nivel[0] else 'free').strip()
    # Verifica se o usuário é premium logo de cara
    cur.execute("SELECT is_premium FROM users2 WHERE id = %s", (user_id,))
    premium_result = cur.fetchone()
    is_premium = "true" if premium_result and premium_result[0] else "false"
    # 🧠 Pega o template_name e subdomain original com base no template_id
    cur.execute("""
        SELECT template_name, subdomain 
        FROM user_templates
        WHERE id = %s AND user_id = %s
        LIMIT 1
    """, (template_id, user_id))

    template_info = cur.fetchone()

    if not template_info:
        cur.close()
        conn.close()
        if request.method == 'POST':
            return jsonify({"message": "Template não encontrado"}), 404
        return redirect(url_for('meu_site'))

    template_name, subdomain = template_info

    # 🔎 Busca a página específica com base no template_name + subdomain + page_name
    cur.execute("""
        SELECT custom_html 
        FROM user_templates
        WHERE user_id = %s AND template_name = %s AND subdomain = %s AND page_name = %s
        LIMIT 1
    """, (user_id, template_name, subdomain, page_name))

    pagina_info = cur.fetchone()

    if not pagina_info:
        cur.close()
        conn.close()
        if request.method == 'POST':
            return jsonify({"message": "Página não encontrada"}), 404
        return redirect(url_for('meu_site'))

    html_salvo = pagina_info[0]

    if request.method == 'POST':
        dados = request.get_json()
        novo_html = dados.get('html')
        novo_css = dados.get('css')

        html_completo = f"""
        <!DOCTYPE html>
        <html lang="pt-BR">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>{novo_css}</style>
        </head>
        <body>{novo_html}</body>
        </html>
        """

        cur.execute("""
            UPDATE user_templates 
            SET custom_html = %s,
                ultima_edicao = NOW()
            WHERE user_id = %s AND template_name = %s AND subdomain = %s AND page_name = %s
        """, (html_completo, user_id, template_name, subdomain, page_name))

        conn.commit()
        cur.close()
        conn.close()

        return jsonify({"message": "Página atualizada com sucesso!"})

   

    if not html_salvo:
        html_salvo = "<h1>Nova Página</h1><p>Comece a editar seu conteúdo aqui.</p>"

    is_premium = "true" if premium_result and premium_result[0] else "false"
    return render_template(
    "editar_template.html",
    html_atual=html_salvo,
    template_id=template_id,
    page_name=page_name,
    is_premium=is_premium,
    premium_level=premium_level # 👈 isso aqui é importante
)










def extrair_html_css_do_template(html):
    soup = BeautifulSoup(html, 'html.parser')

    # Extrai o conteúdo da tag <style>
    styles = soup.find_all("style")
    css = "\n".join(style.get_text() for style in styles)

    # Remove as tags <style> do corpo
    for style in styles:
        style.decompose()

    # Pega o conteúdo do body
    body = soup.body
    body_html = body.decode_contents() if body else soup.decode_contents()

    return body_html.strip(), css.strip()


@app.route('/verificar-premium/<template_name>', methods=['POST'])
def verificar_premium(template_name):
    user_id = session.get('user_id')

    if not user_id:
        return jsonify({"pode_usar": False}), 401

    conn = get_db_connection()
    cur = conn.cursor()

    # Primeiro, busca o nível do template
    cur.execute("SELECT premium_level FROM templates WHERE name = %s", (template_name,))
    template_info = cur.fetchone()

    if not template_info:
        cur.close(); conn.close()
        return jsonify({"pode_usar": False}), 404

    template_level = template_info[0]

    # Agora, busca o nível do usuário
    cur.execute("SELECT premium_level FROM users2 WHERE id = %s", (user_id,))
    user_info = cur.fetchone()
    cur.close(); conn.close()

    if not user_info:
        return jsonify({"pode_usar": False}), 404

    user_level = user_info[0]

    # Lógica para usuários gratuitos usando template gratuito
    if template_level == 'free' and user_level == 'free':
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM user_templates WHERE user_id = %s", (user_id,))
        ja_usou = cur.fetchone()[0]
        cur.close()
        conn.close()

        if ja_usou == 0:
            return jsonify({"pode_usar": "confirmar"})  # Mostrar modal de confirmação
        else:
            return jsonify({"pode_usar": False})  # Já usou o único permitido

    # 🔥 ADICIONA ESSE TRECHO DE VOLTA:
    # Se o usuário for premium (não free), pode usar qualquer template
    if user_level != 'free':
        return jsonify({"pode_usar": True})

    # Usuário free tentando usar template premium
    return jsonify({"pode_usar": False})



    
@app.route('/buy-premium', methods=['POST'])
def buy_premium():
    if 'user_id' not in session:
        return jsonify({"message": "Você precisa estar logado para adquirir premium."}), 401

    user_id = session['user_id']

    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute("UPDATE users2 SET is_premium = TRUE WHERE id = %s::INTEGER", (user_id,))
        conn.commit()
        session['is_premium'] = True  # Atualiza sessão do usuário

        return jsonify({"message": "Parabéns! Você agora é um usuário premium."}), 200
    except psycopg2.Error as e:
        conn.rollback()
        return jsonify({"error": "Erro ao processar a compra", "details": str(e)}), 500
    finally:
        cur.close()
        conn.close()


# Rota para versão premium com IA
@app.route('/generate-site', methods=['POST'])
def generate_site():
    data = request.json
    site_data = {"message": "IA irá gerar um site personalizado com base nos dados enviados"}
    return jsonify(site_data)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)