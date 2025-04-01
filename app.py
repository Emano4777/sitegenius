from flask import Flask, render_template, request, redirect, url_for, session, jsonify,make_response
import os
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

# Configuração
cloudinary.config(
    cloud_name='dyyrgll7h',
    api_key='121426271799432',
    api_secret='pJuEZvImfvoRQQE3p5cWFxK9erA'
)

logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)

app.secret_key = 'minha-chave-segura'
CORS(app, supports_credentials=True)


# CONFIG ESSENCIAL
app.config['SESSION_COOKIE_SAMESITE'] = 'None'
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_PATH'] = '/'


MERCADO_PAGO_ACCESS_TOKEN = "APP_USR-2694841338174545-032011-c45585f95cff8baeac33dda92abce761-2342791238"
# Conexão com o Banco de Dados
BASE_URL = "https://sitegenius.com.br"

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




@app.route('/api/quem-sou-eu')
def quem_sou_eu():
    if 'user_id' in session:
        return jsonify({'logado': True, 'tipo': 'dono', 'user_id': session['user_id']})
    elif 'cliente_id' in session:
        # opcional: descobrir o subdomínio da loja
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT subdomain
            FROM user_templates
            WHERE id = (SELECT loja_id FROM clientes_loja WHERE id = %s)
        """, (session['cliente_id'],))
        loja = cur.fetchone()
        cur.close(); conn.close()
        return jsonify({'logado': True, 'tipo': 'cliente', 'cliente_id': session['cliente_id'], 'subdomain': loja[0] if loja else None})
    else:
        return jsonify({'logado': False})

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

    return render_template('template10_meuspedidos.html', pedidos=pedidos, subdomain=subdomain)




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


@app.route('/<subdomain>')
@app.route('/<subdomain>/index')
def index_loja(subdomain):
    print("Sessão atual:", dict(session))
    conn = get_db_connection()
    cur = conn.cursor()

    # Buscar id do dono da loja pelo subdomínio
    cur.execute("SELECT user_id FROM user_templates WHERE subdomain = %s LIMIT 1", (subdomain,))
    dono = cur.fetchone()

    if not dono:
        return "Loja não encontrada", 404

    dono_id = dono[0]

    # Buscar produtos cadastrados por esse dono
    cur.execute("SELECT id, nome, descricao, preco, imagem FROM produtos WHERE user_id = %s", (dono_id,))
    produtos = cur.fetchall()
    cur.close(); conn.close()

    return render_template("template10_index.html", produtos=produtos, subdomain=subdomain)



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

    if not session.get('is_premium'):
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

    if not session.get('is_premium'):
        return redirect('/preco')  # Redireciona para a página de planos

    conn = get_db_connection()
    cur = conn.cursor()

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
        "essential": {"price": 10.00, "title": "Premium Essential"},
        "moderado": {"price": 59.90, "title": "Premium Moderado"},
        "master": {"price": 120.90, "title": "Premium Master"}
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

    response = requests.post("https://api.mercadopago.com/checkout/preferences", json=payment_data, headers=headers)

    if response.status_code == 201:
        return redirect(response.json()["init_point"])
    else:
        print("Erro:", response.text)  # Pode ajudar no debug
        return "Erro ao gerar pagamento", 500


    
@app.route('/verificar-sessao')
def verificar_sessao():
    if 'user_id' in session:
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 401
    

@app.route('/payment-success')
def payment_success():
    plano = request.args.get("plano")
    if 'user_id' not in session or not plano:
        return "Acesso inválido", 401

    user_id = session['user_id']

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE users2 SET is_premium = TRUE, premium_level = %s WHERE id = %s", (plano, user_id))
        conn.commit()
        session['is_premium'] = True
        session['premium_level'] = plano
        return redirect(url_for('admin_dashboard'))
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



# Rota para cadastrar usuário
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        nome = request.form['nome']
        email = request.form['email']
        senha = request.form['senha']
        avatar_url = None
    if 'avatar' in request.files:
        avatar_file = request.files['avatar']
        if avatar_file.filename != '':
            upload_result = cloudinary.uploader.upload(avatar_file)
            avatar_url = upload_result['secure_url']
        hashed_senha = bcrypt.hashpw(senha.encode('utf-8'), bcrypt.gensalt())

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("INSERT INTO users2 (nome, email, senha, avatar_url) VALUES (%s, %s, %s, %s)", 
                (nome, email, hashed_senha.hex(), avatar_url))


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
        cur.execute("SELECT id, nome, senha::TEXT, is_premium, avatar_url FROM users2 WHERE email = %s", (email,))
        user = cur.fetchone()

        if user:
            senha_hash = bytes.fromhex(user[2][2:]).decode()

            if bcrypt.checkpw(senha.encode('utf-8'), senha_hash.encode('utf-8')):
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

@app.route('/template/<template_name>')
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

    cur.execute("SELECT is_premium FROM users2 WHERE id=%s", (user_id,))
    user_status = cur.fetchone()
    is_premium = user_status[0] if user_status else False

    cur.execute("SELECT COUNT(*) FROM user_templates WHERE user_id=%s", (user_id,))
    total_templates = cur.fetchone()[0]

    if not is_premium and total_templates >= 1:
        cur.close(); conn.close()
        return jsonify({"success": False, "message": "Usuários não Premium só podem criar 1 modelo."}), 403

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

            if template_name == 'template10':
                match = re.match(r"^/(template|site|[\w-]+)/(\w+)", href)
                if match:
                    page = match.group(2)
                    a['href'] = f"/{subdomain}/{page}"
            else:
                match = re.match(r"^/(template|site|[\w-]+)/(\w+)", href)
                if match:
                    page = match.group(2)
                    a['href'] = f"/site/{template_id}/{page}"

        for form in soup.find_all('form', action=True):
            action = form['action'].strip()
            if "{{" in action or "{%" in action:
                continue
            if re.match(r"^/(login-cliente|cadastrar|meus-pedidos|acompanhar-pedido.*)", action):
                rota = action.lstrip("/")
                form['action'] = f"/{subdomain}/{rota}"

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
        "page_name": 'index'
    })
@app.route('/<subdomain>/<page_name>')
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
        SELECT id, template_name, subdomain, page_name
        FROM user_templates 
        WHERE user_id = %s
    """, (user_id,))
    
    rows = cur.fetchall()
    cur.close()
    conn.close()

    # Agrupando corretamente por subdomínio
    templates = {}
    for template_id, template_name, subdomain, page_name in rows:
        if subdomain not in templates:
            templates[subdomain] = {
                'template_id': template_id,
                'template_name': template_name,
                'pages': []
            }
        templates[subdomain]['pages'].append(page_name)

    return render_template('meus_templates.html', templates=templates, multiple_sites=(len(templates) > 1))


    
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




@app.route('/<subdomain>/<page_name>')
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

@app.route('/<subdomain>/<page_name>')
def site_usuario(subdomain, page_name):
    from bs4 import BeautifulSoup
    import re

    conn = get_db_connection()
    cur = conn.cursor()

    # Pega o template_name vinculado ao subdomínio
    cur.execute("""
        SELECT template_name, user_id
        FROM user_templates
        WHERE subdomain = %s
        LIMIT 1
    """, (subdomain,))
    resultado = cur.fetchone()

    if not resultado:
        cur.close(); conn.close()
        return "Loja não encontrada", 404

    template_name, user_id = resultado

    # 🛡️ Protege páginas privadas
    paginas_privadas = ['meus-pedidos', 'acompanhar-pedido', 'editar-dados']
    if page_name in paginas_privadas:
        if 'cliente_id' not in session:
            return redirect(url_for('login_cliente', subdomain=subdomain))
        if 'user_id' in session:
            return "Acesso restrito a clientes.", 403

    # 🔁 Se for template10, renderiza diretamente
    if template_name == 'template10':
        try:
            return render_template(f"{template_name}_{page_name}.html", subdomain=subdomain)
        except:
            return "Página não encontrada (template10)", 404

    # 🔍 Para outros templates, busca o conteúdo dinâmico
    cur.execute("""
        SELECT custom_html
        FROM user_templates
        WHERE subdomain = %s AND page_name = %s AND template_name = %s
        LIMIT 1
    """, (subdomain, page_name, template_name))
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

    return str(soup)




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
            SET custom_html = %s
            WHERE user_id = %s AND template_name = %s AND subdomain = %s AND page_name = %s
        """, (html_completo, user_id, template_name, subdomain, page_name))

        conn.commit()
        cur.close()
        conn.close()

        return jsonify({"message": "Página atualizada com sucesso!"})

   # Descobre se o usuário é premium antes de fechar a conexão
    cur.execute("SELECT is_premium FROM users2 WHERE id = %s", (user_id,))
    premium_result = cur.fetchone()

    cur.close()
    conn.close()

    if not html_salvo:
        html_salvo = "<h1>Nova Página</h1><p>Comece a editar seu conteúdo aqui.</p>"

    is_premium = "true" if premium_result and premium_result[0] else "false"
    return render_template(
    "editar_template.html",
    html_atual=html_salvo,
    template_id=template_id,
    page_name=page_name,
    is_premium=is_premium  # 👈 isso aqui é importante
)



@app.route('/site/<int:template_id>/<page_name>')
def site_usuario_completo(template_id, page_name):
    from bs4 import BeautifulSoup
    import re

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT template_name, subdomain, user_id 
        FROM user_templates 
        WHERE id = %s
        LIMIT 1
    """, (template_id,))
    template_info = cur.fetchone()

    if not template_info:
        cur.close(); conn.close()
        return "Template não encontrado", 404

    template_name, subdomain, user_id = template_info

    cur.execute("""
        SELECT custom_html
        FROM user_templates
        WHERE user_id = %s AND template_name = %s AND subdomain = %s AND page_name = %s
        LIMIT 1
    """, (user_id, template_name, subdomain, page_name))

    result = cur.fetchone()
    cur.close(); conn.close()

    if not result:
        return "Página não encontrada", 404

    html = result[0]

    # 🧠 Corrigir todos os <a>, <form>, <script> e <link>
    soup = BeautifulSoup(html, 'html.parser')

    # Corrige <a href="...">
    for a in soup.find_all('a', href=True):
        href = a['href'].strip()

        if "/template/" in href:
            page = href.split("/template/")[-1].strip()
            a['href'] = f"/site/{template_id}/{page}"


    return str(soup)






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
        return jsonify({"pode_usar": False})

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT is_premium FROM users2 WHERE id = %s", (user_id,))
    status = cur.fetchone()
    cur.close(); conn.close()

    is_premium = status[0] if status else False

    # Aqui você define quais templates são considerados premium
    templates_premium = ['template10', 'template4']
    if template_name in templates_premium and not is_premium:
        return jsonify({"pode_usar": False})

    return jsonify({"pode_usar": True})

    
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