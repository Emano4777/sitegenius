import os
import psycopg2
from bs4 import BeautifulSoup
import re


def adaptar_links(html, template_id, subdomain, template_name):
    soup = BeautifulSoup(html, 'html.parser')
    atualizado = False

    for a in soup.find_all('a', href=True):
        href = a['href'].strip()

        if "{{" in href or "{%" in href:
            continue

        # 🔁 Se for template10 → mantém /<subdomain>/<pagina>
        if template_name == 'template10':
            match = re.match(r"^/[\w-]+/(\w+)", href)
            if match:
                page_name = match.group(1)
                novo = f"/{subdomain}/{page_name}"
                if href != novo:
                    print(f"➡️ Corrigido: {href} → {novo}")
                    a['href'] = novo
                    atualizado = True
        else:
            match = re.match(r"^/(template|site|[\w-]+)/(\w+)", href)
            if match:
                page_name = match.group(2)
                novo = f"/site/{template_id}/{page_name}"
                if href != novo:
                    print(f"➡️ Corrigido: {href} → {novo}")
                    a['href'] = novo
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
        # ⛔ pula o template10 — ele usa rotas com subdomínio
        if template_name == 'template10':
            continue

        html_corrigido, atualizado = adaptar_links(html, template_id, subdomain, template_name)

        if atualizado:
            cur.execute("""
                UPDATE user_templates
                SET custom_html = %s
                WHERE id = %s
            """, (html_corrigido, template_id))
            print(f"🔧 Página '{page_name}' do subdomínio '{subdomain}' (template {template_name}) corrigida com sucesso.")
            total_corrigidos += 1

    conn.commit()
    cur.close()
    conn.close()
    print(f"\n✅ Correção finalizada. Total de páginas atualizadas: {total_corrigidos}")


corrigir_links_no_banco()
