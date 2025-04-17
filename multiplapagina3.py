import os
import psycopg2
from bs4 import BeautifulSoup
import re

TEMPLATE_NAME = 'template20'
SUBDOMAIN_PLACEHOLDER = '{{sub}}'

ARQUIVOS = {
    'index': 'template20_index.html',
}

def extrair_css_apenas(html):
    soup = BeautifulSoup(html, 'html.parser')
    style_tags = soup.find_all('style')
    css = '\n'.join(tag.text for tag in style_tags)
    return css

def adaptar_links(html, subdomain):
    soup = BeautifulSoup(html, 'html.parser')

    for a in soup.find_all('a', href=True):
        href = a['href'].strip()

        # Caso 1: link tipo "/template/sobre"
        if href.startswith('/template/'):
            page_name = href.split('/template/')[-1]
            a['href'] = f'/{subdomain}/{page_name}'

        # Caso 2: link tipo "{{ url_for('site_usuario', subdomain=subdomain, page_name='sobre') }}"
        elif '{{ url_for' in href:
            match = re.search(r"page_name=['\"](.*?)['\"]", href)
            if match:
                page_name = match.group(1)
                a['href'] = f'/{subdomain}/{page_name}'

    return str(soup)

def salvar_paginas_no_banco():
    conn = psycopg2.connect("postgresql://postgres:Poupaqui123@406279.hstgr.cloud:5432/postgres")
    cur = conn.cursor()

    # 🧹 Remove páginas antigas desse template
    cur.execute("DELETE FROM template_pages WHERE template_name = %s", (TEMPLATE_NAME,))
    print(f"🧹 Páginas antigas de '{TEMPLATE_NAME}' removidas com sucesso.")

    # ✅ Insere as novas páginas adaptadas
    for page_name, arquivo in ARQUIVOS.items():
        caminho = os.path.join('templates', arquivo)

        with open(caminho, 'r', encoding='utf-8') as f:
            html_completo = f.read()

            # 🔁 Adapta os links (inclusive os com url_for)
            html_adaptado = adaptar_links(html_completo, SUBDOMAIN_PLACEHOLDER)
            css_extraido = extrair_css_apenas(html_adaptado)

            cur.execute("""
                INSERT INTO template_pages (template_name, page_name, html, css)
                VALUES (%s, %s, %s, %s)
            """, (TEMPLATE_NAME, page_name, html_adaptado, css_extraido))
            print(f"✅ Página '{page_name}' salva com sucesso.")

    conn.commit()
    cur.close()
    conn.close()
    print("🚀 Todas as páginas foram atualizadas no banco com sucesso!")

salvar_paginas_no_banco()
