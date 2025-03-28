import os
import psycopg2
from bs4 import BeautifulSoup
import re

TEMPLATE_NAME = 'template10'
SUBDOMAIN_PLACEHOLDER = '{{sub}}'

# Vai pegar automaticamente todos os arquivos HTML do template10
PASTA_TEMPLATE = 'templates'
PAGINAS_ESPECIAIS = {
    'index': 'template10_index.html',
    'meus-pedidos': 'template10_meuspedidos.html',
    'acompanhar-pedido': 'template10_acompanhar_pedido.html',
    'editar-dados': 'template10_editar_dados.html',
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

        if href.startswith('/template/'):
            page_name = href.split('/template/')[-1]
            a['href'] = f'/{subdomain}/{page_name}'

        elif '{{ url_for' in href:
            match = re.search(r"page_name=['\"](.*?)['\"]", href)
            if match:
                page_name = match.group(1)
                a['href'] = f'/{subdomain}/{page_name}'

    return str(soup)

def salvar_paginas_no_banco():
    conn = psycopg2.connect("postgresql://postgres:Poupaqui123@406279.hstgr.cloud:5432/postgres")
    cur = conn.cursor()

    # Remove antigas
    cur.execute("DELETE FROM template_pages WHERE template_name = %s", (TEMPLATE_NAME,))
    print(f"🧹 Páginas antigas de '{TEMPLATE_NAME}' removidas com sucesso.")

    total = 0

    for page_name, nome_arquivo in PAGINAS_ESPECIAIS.items():
        caminho = os.path.join(PASTA_TEMPLATE, nome_arquivo)

        if not os.path.isfile(caminho):
            print(f"⚠️ Arquivo não encontrado: {nome_arquivo} — pulando.")
            continue

        with open(caminho, 'r', encoding='utf-8') as f:
            html_completo = f.read()

            html_adaptado = adaptar_links(html_completo, SUBDOMAIN_PLACEHOLDER)
            css_extraido = extrair_css_apenas(html_adaptado)

            cur.execute("""
                INSERT INTO template_pages (template_name, page_name, html, css)
                VALUES (%s, %s, %s, %s)
            """, (TEMPLATE_NAME, page_name, html_adaptado, css_extraido))

            print(f"✅ Página '{page_name}' salva com sucesso.")
            total += 1

    conn.commit()
    cur.close()
    conn.close()
    print(f"🚀 Total de páginas atualizadas no banco: {total}")

salvar_paginas_no_banco()
