from bs4 import BeautifulSoup
import psycopg2
import os

conn = psycopg2.connect("postgresql://postgres:Poupaqui123@406279.hstgr.cloud:5432/postgres")
cur = conn.cursor()

TEMPLATE_NAME = "template2"
ARQUIVOS = {
    'index': 'template2_index.html',
    'sobre': 'template2_sobre.html'
}

for page_name, arquivo in ARQUIVOS.items():
    path = os.path.join("templates", arquivo)
    with open(path, 'r', encoding='utf-8') as f:
        html = f.read()
        soup = BeautifulSoup(html, 'html.parser')
        imagens = soup.find_all('img')

        for idx, img in enumerate(imagens):
            src = img.get('src')
            descricao = img.get('alt', '')
            cur.execute("""
                INSERT INTO template_images (template_name, page_name, image_url, descricao, ordem)
                VALUES (%s, %s, %s, %s, %s)
            """, (TEMPLATE_NAME, page_name, src, descricao, idx+1))

conn.commit()
cur.close(); conn.close()