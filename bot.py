import os
import re
import json
import asyncio
import cloudscraper
from bs4 import BeautifulSoup
from urllib.parse import urlparse

from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN nao encontrado")

scraper = cloudscraper.create_scraper()

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "pt-BR,pt;q=0.9"
}

# ─────────────────────────────
# LOJAS
# ─────────────────────────────

LOJAS = {
    "amazon": "Amazon",
    "mercadolivre": "Mercado Livre",
    "shopee": "Shopee",
    "magazineluiza": "Magazine Luiza",
    "kabum": "KaBuM"
}

def nome_loja(url):

    dominio = urlparse(url).netloc.lower()

    for chave in LOJAS:
        if chave in dominio:
            return LOJAS[chave]

    return dominio.replace("www.","")

# ─────────────────────────────
# LIMPAR PREÇO
# ─────────────────────────────

def limpar_preco(texto):

    if not texto:
        return None

    texto = str(texto)

    texto = re.sub(r'\d+\s*x', '', texto)

    match = re.search(r'\d{1,3}(?:\.\d{3})*,\d{2}|\d+\.\d{2}', texto)

    if not match:
        return None

    valor = match.group()

    valor = valor.replace(".", "").replace(",", ".")

    try:
        return float(valor)
    except:
        return None

# ─────────────────────────────
# FORMATAR PREÇO
# ─────────────────────────────

def formatar_preco(valor):

    if not valor:
        return None

    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X",".")

# ─────────────────────────────
# EXTRAIR IMAGEM
# ─────────────────────────────

def extrair_imagem(soup):

    og = soup.find("meta", {"property":"og:image"})

    if og and og.get("content"):
        return og["content"]

    img = soup.select_one("#landingImage")

    if img and img.get("src"):
        return img["src"]

    img = soup.select_one("img")

    if img:
        return img.get("src")

    return None

# ─────────────────────────────
# EXTRAIR PREÇO
# ─────────────────────────────

def extrair_preco(soup):

    seletores = [
        "[itemprop=price]",
        ".a-offscreen",
        ".andes-money-amount__fraction",
        ".price",
        ".sale-price"
    ]

    for sel in seletores:

        el = soup.select_one(sel)

        if el:

            val = limpar_preco(el.get_text())

            if val and val > 0:
                return val

    return None

# ─────────────────────────────
# SCRAPER
# ─────────────────────────────

def pegar_dados(link):

    titulo = "Produto"
    loja = nome_loja(link)
    preco = None
    imagem = None

    try:

        r = scraper.get(link, headers=HEADERS, timeout=15)

        soup = BeautifulSoup(r.text, "html.parser")

        og = soup.find("meta", {"property":"og:title"})

        if og:
            titulo = og.get("content")

        elif soup.title:
            titulo = soup.title.text

        preco = extrair_preco(soup)

        imagem = extrair_imagem(soup)

        print(f"""
DEBUG
titulo: {titulo}
preco: {preco}
imagem: {imagem}
""")

    except Exception as e:

        print("erro:", e)

    return titulo, loja, preco, imagem

# ─────────────────────────────
# LINK
# ─────────────────────────────

def extrair_link(texto):

    match = re.search(r'https?://\S+', texto)

    if match:
        return match.group()

    return None

# ─────────────────────────────
# MENSAGEM
# ─────────────────────────────

def montar_mensagem(link, titulo, loja, preco):

    preco_f = formatar_preco(preco)

    msg = "🔥 PROMOÇÃO ENCONTRADA\n\n"

    msg += f"🛒 {titulo}\n\n"

    msg += f"🏪 Loja: {loja}\n\n"

    if preco_f:
        msg += f"💰 Preço: {preco_f}\n"

    msg += f"\nCOMPRAR ⤵️\n{link}"

    return msg

# ─────────────────────────────
# TELEGRAM
# ─────────────────────────────

async def responder(update: Update, context: ContextTypes.DEFAULT_TYPE):

    texto = update.message.text

    if not texto:
        return

    link = extrair_link(texto)

    if not link:
        return

    await update.message.reply_text("🔎 Buscando produto...")

    loop = asyncio.get_running_loop()

    titulo, loja, preco, imagem = await loop.run_in_executor(
        None,
        pegar_dados,
        link
    )

    mensagem = montar_mensagem(link, titulo, loja, preco)

    if imagem:

        try:

            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=imagem,
                caption=mensagem
            )

            return

        except:
            pass

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=mensagem
    )

# ─────────────────────────────
# BOT
# ─────────────────────────────

def main():

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(MessageHandler(filters.TEXT, responder))

    print("BOT PROMO RODANDO")

    app.run_polling()

if __name__ == "__main__":
    main()
