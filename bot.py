import telebot
import os
import re
import json
import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN nao encontrado nas variaveis de ambiente.")

bot = telebot.TeleBot(TOKEN)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
}

LOJAS = {
    "mercadolivre.com.br": "Mercado Livre",
    "mercadolibre.com": "Mercado Livre",
    "amazon.com.br": "Amazon",
    "amazon.com": "Amazon",
    "americanas.com.br": "Americanas",
    "magazineluiza.com.br": "Magazine Luiza",
    "magalu.com.br": "Magazine Luiza",
    "shopee.com.br": "Shopee",
    "casasbahia.com.br": "Casas Bahia",
    "submarino.com.br": "Submarino",
    "extra.com.br": "Extra",
    "pontofrio.com.br": "Ponto Frio",
    "fastshop.com.br": "Fast Shop",
    "kabum.com.br": "KaBuM!",
    "aliexpress.com": "AliExpress",
    "shein.com": "Shein",
    "netshoes.com.br": "Netshoes",
    "centauro.com.br": "Centauro",
    "leroy.com.br": "Leroy Merlin",
    "leroymerlin.com.br": "Leroy Merlin",
    "carrefour.com.br": "Carrefour",
    "havan.com.br": "Havan",
    "renner.com.br": "Renner",
    "riachuelo.com.br": "Riachuelo",
    "dafiti.com.br": "Dafiti",
    "zattini.com.br": "Zattini",
    "nike.com.br": "Nike",
    "adidas.com.br": "Adidas",
}


def nome_loja(url):
    try:
        dominio = urlparse(url).netloc.lower().replace("www.", "")
        for chave, nome in LOJAS.items():
            if chave in dominio:
                return nome
        partes = dominio.split(".")
        if len(partes) >= 2:
            return partes[-2].capitalize()
        return dominio.capitalize()
    except Exception:
        return "Loja"


def limpar_preco(texto):
    if not texto:
        return None
    texto = texto.strip()
    texto = re.sub(r"[^\d,\.]", "", texto)
    if not texto:
        return None
    texto = texto.replace(",", ".")
    try:
        float(texto)
        return texto
    except ValueError:
        return None


def formatar_preco(valor):
    if not valor:
        return None
    try:
        numero = float(valor.replace(",", "."))
        return f"R$ {numero:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return f"R$ {valor}"


def extrair_precos_schema(soup):
    """Tenta extrair preços via JSON-LD (schema.org) — mais confiável."""
    preco_atual = None
    preco_antigo = None

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            dados = json.loads(script.string or "")
            if isinstance(dados, list):
                dados = next((d for d in dados if d.get("@type") in ("Product", "Offer")), {})

            tipo = dados.get("@type", "")
            if tipo == "Product":
                offers = dados.get("offers", {})
                if isinstance(offers, list):
                    offers = offers[0]
                preco_atual = limpar_preco(str(offers.get("price", "")))
                preco_antigo = limpar_preco(str(offers.get("highPrice", "") or offers.get("priceAnchor", "")))
            elif tipo == "Offer":
                preco_atual = limpar_preco(str(dados.get("price", "")))
                preco_antigo = limpar_preco(str(dados.get("highPrice", "")))

            if preco_atual:
                break
        except Exception:
            continue

    return preco_atual, preco_antigo


def extrair_precos_html(soup, url):
    """Tenta extrair preços via seletores CSS específicos por loja e genéricos."""
    dominio = urlparse(url).netloc.lower()
    preco_atual = None
    preco_antigo = None

    # Seletores específicos por loja: (seletor_atual, seletor_antigo)
    mapa_lojas = {
        "mercadolivre": (
            [".andes-money-amount__fraction", ".price-tag-fraction"],
            [".andes-money-amount--previous .andes-money-amount__fraction", ".price-tag--del .price-tag-fraction"],
        ),
        "amazon": (
            [".a-price-whole", "#priceblock_ourprice", "#priceblock_dealprice", ".priceToPay .a-offscreen"],
            [".a-text-strike .a-offscreen", "#listPrice", ".a-price.a-text-price .a-offscreen"],
        ),
        "magazineluiza": (
            ["[data-testid='price-value']", ".price__current", ".sc-ckVGcZ"],
            ["[data-testid='original-price']", ".price__original", ".sc-bdVTJa"],
        ),
        "americanas": (
            ["[data-testid='price']", ".priceSales", ".sales-price"],
            ["[data-testid='list-price']", ".priceStandard", ".list-price"],
        ),
        "shopee": (
            [".pmmxKx", "._3n5NQx", ".price"],
            [".WTFwws", "._1FGCO7", ".price--original"],
        ),
        "casasbahia": (
            [".price__current--value", "[data-testid='price-value']"],
            [".price__old--value", "[data-testid='original-price']"],
        ),
        "kabum": (
            [".regularPrice", ".finalPrice", "[itemprop='price']"],
            [".oldPrice", ".oldPriceValue"],
        ),
    }

    seletores_atual = [".price", ".preco", "[itemprop='price']", ".sale-price", ".offer-price"]
    seletores_antigo = [".old-price", ".preco-antigo", ".price-before", ".de", ".was-price", "s", "del", ".strike"]

    for chave, (s_atual, s_antigo) in mapa_lojas.items():
        if chave in dominio:
            seletores_atual = s_atual + seletores_atual
            seletores_antigo = s_antigo + seletores_antigo
            break

    for sel in seletores_atual:
        el = soup.select_one(sel)
        if el:
            preco_atual = limpar_preco(el.get_text())
            if preco_atual:
                break

    for sel in seletores_antigo:
        el = soup.select_one(sel)
        if el:
            texto = el.get_text()
            candidato = limpar_preco(texto)
            if candidato and candidato != preco_atual:
                preco_antigo = candidato
                break

    return preco_atual, preco_antigo


def extrair_precos_meta(soup):
    """Tenta extrair preço via meta tags Open Graph / itemprop."""
    preco_atual = None

    meta = soup.find("meta", {"property": "product:price:amount"}) or \
           soup.find("meta", {"itemprop": "price"}) or \
           soup.find("meta", {"property": "og:price:amount"})

    if meta:
        preco_atual = limpar_preco(meta.get("content", ""))

    return preco_atual, None


def buscar_info_link(url):
    """Busca nome da loja e preços a partir do link."""
    loja = nome_loja(url)
    preco_atual = None
    preco_antigo = None

    try:
        resposta = requests.get(url, headers=HEADERS, timeout=10, allow_redirects=True)
        resposta.raise_for_status()
        soup = BeautifulSoup(resposta.text, "lxml")

        # 1. Tenta schema.org (mais confiável)
        preco_atual, preco_antigo = extrair_precos_schema(soup)

        # 2. Se não achou, tenta meta tags
        if not preco_atual:
            preco_atual, preco_antigo = extrair_precos_meta(soup)

        # 3. Fallback: seletores HTML
        if not preco_atual:
            preco_atual, preco_antigo = extrair_precos_html(soup, url)

    except Exception as e:
        print(f"Erro ao acessar {url}: {e}")

    return loja, preco_atual, preco_antigo


def extrair_link(texto):
    padrao = r'https?://[^\s]+'
    links = re.findall(padrao, texto)
    return links[0] if links else None


def montar_mensagem(link, loja, preco_atual, preco_antigo):
    preco_atual_fmt = formatar_preco(preco_atual)
    preco_antigo_fmt = formatar_preco(preco_antigo)

    linhas_preco = ""
    if preco_atual_fmt and preco_antigo_fmt:
        linhas_preco = f"🏷️ De: ~~{preco_antigo_fmt}~~\n💰 Por: *{preco_atual_fmt}*"
    elif preco_atual_fmt:
        linhas_preco = f"💰 Preço: *{preco_atual_fmt}*"
    else:
        linhas_preco = "💰 Veja o preço no link"

    return (
        f"🔥 *SUPER PROMOÇÃO*\n\n"
        f"🏪 Loja: *{loja}*\n"
        f"{linhas_preco}\n\n"
        f"👉 [COMPRAR AGORA]({link})\n\n"
        f"⚡ Aproveite antes que acabe\\!"
    )


@bot.message_handler(func=lambda message: message.text and re.search(r'https?://', message.text))
def link_handler(message):
    link = extrair_link(message.text)
    if not link:
        return

    bot.send_message(message.chat.id, "🔍 Buscando informações do produto\\.\\.\\.", parse_mode="MarkdownV2")

    loja, preco_atual, preco_antigo = buscar_info_link(link)
    resposta = montar_mensagem(link, loja, preco_atual, preco_antigo)

    bot.send_message(message.chat.id, resposta, parse_mode="MarkdownV2", disable_web_page_preview=False)


print("Bot mypromo iniciado! Aguardando mensagens...")

while True:
    try:
        bot.polling(non_stop=True, timeout=30, long_polling_timeout=10)
    except Exception as e:
        error_msg = str(e)
        if "409" in error_msg:
            print("Conflito 409 detectado. Aguardando 5s para o processo anterior encerrar...")
            time.sleep(5)
        else:
            print(f"Erro no polling: {e}. Reiniciando em 3s...")
            time.sleep(3)
