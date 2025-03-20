from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import os
import psycopg2
import bcrypt
import requests


app = Flask(__name__)
app.secret_key = os.urandom(24)

MERCADO_PAGO_ACCESS_TOKEN = "TEST-2694841338174545-032011-47a44a1e41293d41ed26c3769a2d3992-2342791238"


@app.route('/')
def home():
    return render_template('index.html')

@app.route('/generate-payment', methods=['POST'])
def generate_payment():
    if 'user_id' not in session:
        return jsonify({"message": "Você precisa estar logado para pagar."}), 401

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
    "email": "emano4775@gmail.com"
},
        "back_urls": {
            "success": "http://127.0.0.1:5000/payment-success",
            "failure": "http://127.0.0.1:5000/payment-failure"
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


# Conexão com o Banco de Dados
def get_db_connection():
    return psycopg2.connect("postgresql://postgres:Poupaqui123@406279.hstgr.cloud:5432/postgres")

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
        return jsonify({"logged_in": True})
    return jsonify({"logged_in": False})


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        senha = request.form['senha']

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, nome, senha::TEXT, is_premium FROM users2 WHERE email = %s", (email,))
        user = cur.fetchone()

        cur.close()
        conn.close()

        if user:
            senha_hash = bytes.fromhex(user[2][2:]).decode()  # Decodifica do formato bytea
            print(f"Senha armazenada no banco (decodificada): {senha_hash}")

            if bcrypt.checkpw(senha.encode('utf-8'), senha_hash.encode('utf-8')):
                session['user_id'] = user[0]
                session['user_name'] = user[1]
                session['is_premium'] = user[3]
                return redirect(url_for('templates'))
            else:
                return "Usuário ou senha incorretos", 401
        else:
            return "Usuário não encontrado", 404

    return render_template('login.html')


@app.route('/templates')
def templates():
    if 'user_id' not in session:  # Verifica se o usuário está logado
        return redirect(url_for('login'))  # Redireciona para a página de login
    
    return render_template('templates.html')

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
    app.run(debug=True)
