#!/usr/bin/env python3
"""Debug script para analisar a extração de preços"""

import sys
sys.path.insert(0, '/c/Users/wolkendo/Documents/GitHub/Bot')

import requests
from bs4 import BeautifulSoup
import re

url = 'https://meli.la/2Uz7iws'
session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8',
})

try:
    response = session.get(url, allow_redirects=True, timeout=15)
    final_url = response.url

    print(f'URL Final: {final_url}\n')

    soup = BeautifulSoup(response.content, 'html.parser')

    # Procurar por padrões de preço
    print('=== PREÇOS ENCONTRADOS NO HTML ===')
    html_text = response.text
    precos = re.findall(r'188\.43|188,43|252|297', html_text)
    print(f'Padrões encontrados: {precos[:20]}\n')

    print('=== ELEMENTOS .andes-money-amount (primeiros 10) ===')
    for i, elem in enumerate(soup.select('.andes-money-amount')[:10]):
        fracao = elem.select_one('.andes-money-amount__fraction')
        centavos = elem.select_one('.andes-money-amount__cents')
        if fracao or centavos:
            frac_text = fracao.get_text() if fracao else ''
            cent_text = centavos.get_text() if centavos else ''
            print(f'  [{i}] Fração: {frac_text} | Centavos: {cent_text}')

    print('\n=== DIVS COM CLASSE ui-pdp-price (primeiras 5) ===')
    for div in soup.select('[class*="ui-pdp-price"]')[:5]:
        class_name = div.get('class', [])
        texto = div.get_text(strip=True)[:100]
        print(f'  Classe: {class_name}')
        print(f'  Texto: {texto}\n')

    print('\n=== PROCURANDO POR 188 e 188.43 ===')
    for elem in soup.find_all(string=re.compile(r'188')):
        parent = elem.parent
        if parent:
            print(f'  Texto: {elem}')
            print(f'  Parent: <{parent.name} class="{parent.get("class", [])}">')
            print()

except Exception as e:
    print(f'Erro: {e}')
    import traceback
    traceback.print_exc()

