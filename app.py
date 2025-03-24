from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import os
import psycopg2
import bcrypt
import requests
import uuid
from flask_cors import CORS
import logging
import re
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)

app.secret_key = os.urandom(24)
CORS(app, supports_credentials=True)

# Configurações para rodar no Vercel (HTTPS)
app.config['SESSION_COOKIE_SAMESITE'] = 'None'
app.config['SESSION_COOKIE_SECURE'] = True

app.config['SESSION_COOKIE_SAMESITE'] = 'None'
app.config['SESSION_COOKIE_SECURE'] = True



app.config['SESSION_COOKIE_SAMESITE'] = 'None'
app.config['SESSION_COOKIE_SECURE'] = True
MERCADO_PAGO_ACCESS_TOKEN = "APP_USR-1315085087526645-032014-15c678db98cbc5337a726127790ad8d1-2339390291"
# Conexão com o Banco de Dados


def get_db_connection():
    return psycopg2.connect("postgresql://postgres:Poupaqui123@406279.hstgr.cloud:5432/postgres")


@app.route('/')
def home():
    return render_template('index.html')

@app.route('/generate-payment', methods=['POST'])
def generate_payment():
    if 'user_id' not in session:
        return jsonify({"message": "Você precisa estar logado para pagarr."}), 401

    user_id = session['user_id']

    # Conectar ao banco para obter o email do usuário
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT email FROM users2 WHERE id = %s", (user_id,))
    user_email = cur.fetchone()
    cur.close()
    conn.close()

    if not user_email:
        return jsonify({"error": "Usuário não encontrado"}), 404

    # Dados do pagamento
    payment_data = {
        "items": [
            {
                "title": "Plano Premium - Site Genius",
                "quantity": 1,
                "currency_id": "BRL",
                "unit_price": 49.90  # Valor do plano premium
            }
        ],
    "payer": {
        "email": "test_user_423840156@testuser.com"  # E-mail do comprador de teste
    },
        "back_urls": {
    "success": "https://sitegenius.vercel.app//payment-success",
    "failure": "https://sitegenius.vercel.app/payment-failure"
},
        "auto_return": "approved"
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}"
    }

    response = requests.post("https://api.mercadopago.com/checkout/preferences", json=payment_data, headers=headers)



    if response.status_code == 201:
        payment_url = response.json()["init_point"]
        return jsonify({"payment_url": payment_url}), 200
    else:
        return jsonify({
            "error": "Erro ao criar pagamento",
            "status_code": response.status_code,
            "details": response.json()
        }), response.status_code
    
@app.route('/verificar-sessao')
def verificar_sessao():
    if 'user_id' in session:
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 401
    

@app.route('/payment-success')   
def payment_success():
    if 'user_id' in session:
        user_id = session['user_id']

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("UPDATE users2 SET is_premium = TRUE WHERE id = %s", (user_id,))
            conn.commit()
            session['is_premium'] = True  # Atualiza sessão

            return redirect(url_for('templates'))  # Redireciona para área premium
        except psycopg2.Error:
            conn.rollback()
            return "Erro ao ativar premium", 500
        finally:
            cur.close()
            conn.close()

    return "Usuário não autenticado", 401



# Rota para cadastrar usuário
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        nome = request.form['nome']
        email = request.form['email']
        senha = request.form['senha']

        hashed_senha = bcrypt.hashpw(senha.encode('utf-8'), bcrypt.gensalt())

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("INSERT INTO users2 (nome, email, senha) VALUES (%s, %s, %s)", 
                        (nome, email, hashed_senha))
            conn.commit()
            return redirect(url_for('login'))
        except psycopg2.Error:
            conn.rollback()
            return "Erro ao cadastrar usuário", 500
        finally:
            cur.close()
            conn.close()

    return render_template('register.html')

@app.route('/check-login')
def check_login():
    if 'user_id' in session:
        return jsonify({
            "logged_in": True,
            "user_name": session.get('user_name', '')  # Retorna o nome do usuário se estiver logado
        })
    return jsonify({"logged_in": False, "user_name": ""})  # Retorna falso se não estiver logado


@app.route('/logout', methods=['POST'])
def logout():
    session.clear()  # Remove todas as informações da sessão
    return jsonify({"message": "Usuário deslogado com sucesso"}), 200

import uuid
from flask import make_response

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        senha = request.form['senha']

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, nome, senha::TEXT, is_premium FROM users2 WHERE email = %s", (email,))
        user = cur.fetchone()

        if user:
            senha_hash = bytes.fromhex(user[2][2:]).decode()

            if bcrypt.checkpw(senha.encode('utf-8'), senha_hash.encode('utf-8')):
                user_id = user[0]
                session['user_id'] = user_id
                session['user_name'] = user[1]
                session['is_premium'] = user[3]

                # 🔐 Gerar session_token único
                session_token = str(uuid.uuid4())

                # Salvar no banco
                cur.execute("UPDATE users2 SET session_token = %s WHERE id = %s", (session_token, user_id))
                conn.commit()
                print(f"Atualizando session_token para o user_id {user_id}: {session_token}")

                # 🔁 Criar resposta com cookie
                resp = make_response(redirect(url_for('home')))
                resp.set_cookie('session_token', session_token, samesite='None', secure=True)

                cur.close()
                conn.close()
                return resp

            else:
                cur.close()
                conn.close()
                return "Usuário ou senha incorretos", 401
        else:
            cur.close()
            conn.close()
            return "Usuário não encontrado", 404

    return render_template('login.html')



@app.route('/templates')
def templates():
    if 'user_id' in session:
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
        cur.close()
        conn.close()

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
        cur.close()
        conn.close()
        return jsonify({"success": False, "message": "Usuários não Premium só podem criar 1 modelo."}), 403

    try:
        with open(f'templates/{template_name}.html', 'r', encoding='utf-8') as f:
            original_html = f.read()

            html_body, css = extrair_html_css_do_template(original_html)

            html_completo = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>{css}</style>
</head>
<body>
{html_body}
</body>
</html>
"""
    except Exception as e:
        cur.close()
        conn.close()
        return jsonify({"success": False, "message": f"Erro ao ler o template: {str(e)}"}), 500

    subdomain = f"user{user_id}-{uuid.uuid4().hex[:6]}"
    page_name = 'index'

    cur.execute("""
        INSERT INTO user_templates (user_id, template_name, custom_html, subdomain, page_name)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
    """, (user_id, template_name, html_completo, subdomain, page_name))

    template_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({
        "success": True,
        "message": "Template vinculado!",
        "template_id": template_id,
        "page_name": page_name
    })




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

    cur.execute("""
        SELECT template_name, subdomain FROM user_templates 
        WHERE id = %s AND user_id = %s LIMIT 1
    """, (template_id, user_id))
    template = cur.fetchone()

    if not template:
        cur.close()
        conn.close()
        return jsonify({'message': 'Template original não encontrado'}), 404

    template_name, subdomain = template

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


@app.route('/<subdomain>')
def exibir_site(subdomain):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT page_name FROM user_templates 
        WHERE subdomain=%s
    """, (subdomain,))
    
    pages = [p[0] for p in cur.fetchall()]
    cur.close()
    conn.close()

    return render_template('base.html', subdomain=subdomain, pages=pages)


@app.route('/<subdomain>/<page_name>')
def site_usuario(subdomain, page_name):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT custom_html FROM user_templates 
        WHERE subdomain=%s AND page_name=%s
    """, (subdomain, page_name))
    site = cur.fetchone()
    cur.close()
    conn.close()

    if site:
        return site[0]  # Retorna o HTML da página específica do subdomínio
    else:
        return "Página não encontrada.", 404



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
    if 'user_id' in session:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT is_premium FROM users2 WHERE id = %s", (session['user_id'],))
        result = cur.fetchone()
        if result:
            is_premium = result[0]
        cur.close()
        conn.close()
    return dict(is_premium=is_premium)


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

    cur.close()
    conn.close()

    if not html_salvo:
        html_salvo = "<h1>Nova Página</h1><p>Comece a editar seu conteúdo aqui.</p>"

    return render_template('editar_template.html',
                           html_atual=html_salvo,
                           template_id=template_id,
                           page_name=page_name)


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


## Rota para exibir os templates dinâmicos
@app.route('/template/<template_name>')
def show_template(template_name):
    premium_templates = ["template3", "template4"]

    # Bloqueia templates premium se o usuário não for pagante
    if template_name in premium_templates and not session.get('is_premium'):
            return "Este template é exclusivo para usuários Premium. Adquira o plano!", 403
    
    try:
        return render_template(f"{template_name}.html")
    except:
        return "Template não encontrado", 404
    
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