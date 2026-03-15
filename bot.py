import os
import re
import json
import time
import asyncio
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse

from telegram import Update
from telegram.error import Conflict
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GRUPO_ID = os.environ.get("TELEGRAM_GRUPO_ID")

if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN nao encontrado nas variaveis de ambiente.")
if not GRUPO_ID:
    raise ValueError("TELEGRAM_GRUPO_ID nao encontrado nas variaveis de ambiente.")

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
    match = re.search(r'\d[\d.,]*', texto)
    if not match:
        return None
    valor = match.group().replace(",", ".")
    try:
        float(valor)
        return valor
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
    dominio = urlparse(url).netloc.lower()
    preco_atual = None
    preco_antigo = None

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
            ["[data-testid='price-value']", ".price__current"],
            ["[data-testid='original-price']", ".price__original"],
        ),
        "americanas": (
            ["[data-testid='price']", ".priceSales"],
            ["[data-testid='list-price']", ".priceStandard"],
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
    seletores_antigo = [".old-price", ".preco-antigo", ".price-before", ".was-price", "s", "del"]

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
            candidato = limpar_preco(el.get_text())
            if candidato and candidato != preco_atual:
                preco_antigo = candidato
                break

    return preco_atual, preco_antigo


def extrair_imagem(soup, url):
    """Tenta pegar a melhor imagem do produto."""
    # Open Graph (mais confiável)
    og = soup.find("meta", {"property": "og:image"})
    if og and og.get("content"):
        return og["content"]

    # Schema.org
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            dados = json.loads(script.string or "")
            if isinstance(dados, list):
                dados = next((d for d in dados if d.get("@type") == "Product"), {})
            imagem = dados.get("image")
            if isinstance(imagem, list):
                imagem = imagem[0]
            if isinstance(imagem, dict):
                imagem = imagem.get("url")
            if imagem:
                return imagem
        except Exception:
            continue

    # Primeira imagem grande da página
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if src and src.startswith("http") and not src.endswith(".svg"):
            return src

    return None


def pegar_dados(link):
    """Acessa o link e extrai título, preços, imagem e loja."""
    loja = nome_loja(link)
    titulo = "Produto"
    preco_atual = None
    preco_antigo = None
    imagem = None

    try:
        r = requests.get(link, headers=HEADERS, timeout=10, allow_redirects=True)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Título
        og_title = soup.find("meta", {"property": "og:title"})
        if og_title and og_title.get("content"):
            titulo = og_title["content"].strip()
        elif soup.title:
            titulo = soup.title.text.strip()

        # Preços (schema.org primeiro, depois HTML)
        preco_atual, preco_antigo = extrair_precos_schema(soup)
        if not preco_atual:
            meta = soup.find("meta", {"property": "product:price:amount"}) or \
                   soup.find("meta", {"itemprop": "price"})
            if meta:
                preco_atual = limpar_preco(meta.get("content", ""))
        if not preco_atual:
            preco_atual, preco_antigo = extrair_precos_html(soup, link)

        # Imagem
        imagem = extrair_imagem(soup, link)

    except Exception as e:
        print(f"Erro ao acessar {link}: {e}")

    return titulo, loja, preco_atual, preco_antigo, imagem


def extrair_link(texto):
    match = re.search(r'https?://[^\s]+', texto)
    return match.group() if match else None


def montar_mensagem(link, titulo, loja, preco_atual, preco_antigo):
    preco_atual_fmt = formatar_preco(preco_atual)
    preco_antigo_fmt = formatar_preco(preco_antigo)

    if preco_atual_fmt and preco_antigo_fmt:
        linha_preco = f"🏷️ De: {preco_antigo_fmt}\n💰 Por: {preco_atual_fmt}"
    elif preco_atual_fmt:
        linha_preco = f"💰 Preço: {preco_atual_fmt}"
    else:
        linha_preco = "💰 Ver preço no link"

    return (
        f"🔥 PROMOÇÃO ENCONTRADA\n\n"
        f"🛒 {titulo}\n\n"
        f"🏪 Loja: {loja}\n"
        f"{linha_preco}\n\n"
        f"🚚 Pode ter frete grátis\n\n"
        f"👉 COMPRAR AGORA\n{link}\n\n"
        f"⚡ Oferta pode acabar a qualquer momento"
    )


async def responder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text
    if not texto or "http" not in texto:
        return

    link = extrair_link(texto)
    if not link:
        return

    await update.message.reply_text("🔍 Buscando informações do produto...")

    titulo, loja, preco_atual, preco_antigo, imagem = pegar_dados(link)
    mensagem = montar_mensagem(link, titulo, loja, preco_atual, preco_antigo)

    # Envia para quem mandou o link
    if imagem:
        try:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=imagem,
                caption=mensagem
            )
        except Exception:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=mensagem
            )
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=mensagem
        )

    # Encaminha para o grupo
    try:
        if imagem:
            await context.bot.send_photo(
                chat_id=GRUPO_ID,
                photo=imagem,
                caption=mensagem
            )
        else:
            await context.bot.send_message(
                chat_id=GRUPO_ID,
                text=mensagem
            )
    except Exception as e:
        print(f"Erro ao encaminhar para o grupo: {e}")


async def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT, responder))

    print("Bot mypromo iniciado! Aguardando mensagens...")

    while True:
        try:
            await app.initialize()
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True)
            await asyncio.Event().wait()
        except Conflict:
            print("Conflito 409 detectado. Aguardando 5s para instancia anterior encerrar...")
            try:
                await app.updater.stop()
                await app.stop()
                await app.shutdown()
            except Exception:
                pass
            await asyncio.sleep(5)
        except Exception as e:
            print(f"Erro: {e}. Reiniciando em 3s...")
            try:
                await app.updater.stop()
                await app.stop()
                await app.shutdown()
            except Exception:
                pass
            await asyncio.sleep(3)

if __name__ == "__main__":
    asyncio.run(main())
