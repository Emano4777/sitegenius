import os
import psycopg2
from bs4 import BeautifulSoup
import re
import html

def adaptar_links(html_original, template_id, subdomain):
    soup = BeautifulSoup(html.unescape(html_original), 'html.parser')
    atualizado = False

    for a in soup.find_all('a', href=True):
        href = a['href'].strip()

        # 🔁 Tratar blocos Jinja do tipo url_for como links fixos
        if "url_for(" in href:
            match = re.search(r"page_name=['\"](.*?)['\"]", href)
            if match:
                page_name = match.group(1)
                a['href'] = f"/site/{template_id}/{page_name}"
                atualizado = True
            continue

        if href.startswith("/template/"):
            page_name = href.split("/template/")[-1].strip()
            a['href'] = f"/site/{template_id}/{page_name}"
            atualizado = True

        elif re.match(r"^/(login-cliente|cadastrar|meus-pedidos|acompanhar-pedido.*)", href):
            rota = href.lstrip("/")
            a['href'] = f"/{subdomain}/{rota}"
            atualizado = True

    for form in soup.find_all('form', action=True):
        action = form['action'].strip()
        if "url_for(" in action:
            match = re.search(r"page_name=['\"](.*?)['\"]", action)
            if match:
                rota = match.group(1)
                form['action'] = f"/{subdomain}/{rota}"
                atualizado = True
            continue

        if re.match(r"^/(login-cliente|cadastrar|meus-pedidos|acompanhar-pedido.*)", action):
            rota = action.lstrip("/")
            form['action'] = f"/{subdomain}/{rota}"
            atualizado = True

    for script in soup.find_all('script', src=True):
        src = script['src'].strip()
        if src.startswith("/template/"):
            caminho = src.split("/template/")[-1].strip()
            script['src'] = f"/site/{template_id}/{caminho}"
            atualizado = True

    for link in soup.find_all('link', href=True):
        href = link['href'].strip()
        if href.startswith("/template/"):
            caminho = href.split("/template/")[-1].strip()
            link['href'] = f"/site/{template_id}/{caminho}"
            atualizado = True

    return str(soup), atualizado


def corrigir_links_no_banco():
    conn = psycopg2.connect("postgresql://postgres:Poupaqui123@406279.hstgr.cloud:5432/postgres")
    cur = conn.cursor()

    cur.execute("""
        SELECT id, template_name, subdomain, user_id, page_name, custom_html
        FROM user_templates
    """)
    templates = cur.fetchall()

    total_corrigidos = 0

    for template_id, template_name, subdomain, user_id, page_name, html in templates:
        html_corrigido, atualizado = adaptar_links(html, template_id, subdomain)

        if atualizado:
            cur.execute("""
                UPDATE user_templates
                SET custom_html = %s
                WHERE id = %s
            """, (html_corrigido, template_id))
            print(f"🔧 Página '{page_name}' do subdomínio '{subdomain}' corrigida com sucesso.")
            total_corrigidos += 1

    conn.commit()
    cur.close()
    conn.close()
    print(f"\n✅ Correção finalizada. Total de páginas atualizadas: {total_corrigidos}")

corrigir_links_no_banco()
