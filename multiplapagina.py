import os
import psycopg2
from bs4 import BeautifulSoup

TEMPLATE_NAME = 'template1'

ARQUIVOS = {
    'index': 'template1_index.html',
    'sobre': 'template1_sobre.html',
    'contato': 'template1_contato.html'
}

def extrair_css_apenas(html):
    soup = BeautifulSoup(html, 'html.parser')
    style_tags = soup.find_all('style')
    css = '\n'.join(tag.text for tag in style_tags)
    return css

def salvar_paginas_no_banco():
    conn = psycopg2.connect("postgresql://postgres:Poupaqui123@406279.hstgr.cloud:5432/postgres")
    cur = conn.cursor()

    # 🧹 Remove páginas antigas desse template (se existirem)
    cur.execute("DELETE FROM template_pages WHERE template_name = %s", (TEMPLATE_NAME,))
    print(f"🧹 Páginas antigas de '{TEMPLATE_NAME}' removidas com sucesso.")

    # ✅ Insere as novas páginas completas
    for page_name, arquivo in ARQUIVOS.items():
        caminho = os.path.join('templates', arquivo)

        with open(caminho, 'r', encoding='utf-8') as f:
            html_completo = f.read()
            css_extraido = extrair_css_apenas(html_completo)

            cur.execute("""
                INSERT INTO template_pages (template_name, page_name, html, css)
                VALUES (%s, %s, %s, %s)
            """, (TEMPLATE_NAME, page_name, html_completo, css_extraido))
            print(f"✅ Página '{page_name}' salva com sucesso.")

    conn.commit()
    cur.close()
    conn.close()
    print("🚀 Todas as páginas foram atualizadas no banco com sucesso!")

salvar_paginas_no_banco()
