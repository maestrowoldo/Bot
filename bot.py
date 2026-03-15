import os
import re
import json
import signal
import asyncio
import cloudscraper
from bs4 import BeautifulSoup
from urllib.parse import urlparse

from telegram import Update
from telegram.error import Conflict
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

# ──────────────────────────────────────────────
# Configuração
# ──────────────────────────────────────────────
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GRUPO_ID = os.environ.get("TELEGRAM_GRUPO_ID")

if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN nao encontrado.")
if not GRUPO_ID:
    raise ValueError("TELEGRAM_GRUPO_ID nao encontrado.")

# ──────────────────────────────────────────────
# PID único
# ──────────────────────────────────────────────
PID_FILE = "/tmp/mypromo_bot.pid"

def garantir_instancia_unica():
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                pid_antigo = int(f.read().strip())
            if pid_antigo != os.getpid():
                os.kill(pid_antigo, signal.SIGKILL)
        except:
            pass

    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

def limpar_pid():
    try:
        os.remove(PID_FILE)
    except:
        pass

# ──────────────────────────────────────────────
# LOJAS
# ──────────────────────────────────────────────
LOJAS = {
    "mercadolivre.com.br": "Mercado Livre",
    "mercadolibre.com": "Mercado Livre",
    "amazon.com.br": "Amazon",
    "amazon.com": "Amazon",
    "shopee.com.br": "Shopee",
    "magazineluiza.com.br": "Magazine Luiza",
    "magalu.com.br": "Magazine Luiza",
    "americanas.com.br": "Americanas",
    "casasbahia.com.br": "Casas Bahia",
    "kabum.com.br": "KaBuM",
    "aliexpress.com": "AliExpress"
}

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "pt-BR,pt;q=0.9"
}

scraper = cloudscraper.create_scraper()

# ──────────────────────────────────────────────
# Nome da loja
# ──────────────────────────────────────────────
def nome_loja(url):
    try:
        dominio = urlparse(url).netloc.lower().replace("www.", "")
        for chave, nome in LOJAS.items():
            if chave in dominio:
                return nome
        partes = dominio.split(".")
        return partes[-2].capitalize()
    except:
        return "Loja"

# ──────────────────────────────────────────────
# LIMPAR PREÇO (VERSÃO CORRIGIDA)
# ──────────────────────────────────────────────
def limpar_preco(texto):

    if not texto:
        return None

    texto = str(texto)

    texto = re.sub(r'(\d+\s*x)', '', texto, flags=re.IGNORECASE)

    padroes = [
        r'\d{1,3}(?:\.\d{3})*,\d{2}',
        r'\d+\.\d{2}',
        r'\d+,\d{2}'
    ]

    for padrao in padroes:
        match = re.search(padrao, texto)

        if match:
            valor = match.group()

            valor = valor.replace(".", "").replace(",", ".")

            try:
                return str(float(valor))
            except:
                continue

    return None

# ──────────────────────────────────────────────
# FORMATAR PREÇO
# ──────────────────────────────────────────────
def formatar_preco(valor):

    if not valor:
        return None

    try:
        numero = float(valor)

        return f"R$ {numero:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return f"R$ {valor}"

# ──────────────────────────────────────────────
# EXTRAIR PREÇOS
# ──────────────────────────────────────────────
def extrair_precos_schema(soup):

    for script in soup.find_all("script", type="application/ld+json"):

        try:
            dados = json.loads(script.string or "")

            if isinstance(dados, list):
                dados = next((d for d in dados if d.get("@type") == "Product"), {})

            offers = dados.get("offers", {})

            if isinstance(offers, list):
                offers = offers[0]

            preco = limpar_preco(str(offers.get("price")))

            if preco:
                return preco, None

        except:
            continue

    return None, None

# ──────────────────────────────────────────────
# EXTRAIR PREÇO HTML
# ──────────────────────────────────────────────
def extrair_precos_html(soup):

    seletores = [
        "[itemprop='price']",
        ".price",
        ".preco",
        ".sale-price",
        ".a-offscreen",
        ".andes-money-amount__fraction"
    ]

    for sel in seletores:

        el = soup.select_one(sel)

        if el:
            val = limpar_preco(el.get_text())

            if val:
                return val, None

    return None, None

# ──────────────────────────────────────────────
# IMAGEM
# ──────────────────────────────────────────────
def extrair_imagem(soup):

    og = soup.find("meta", {"property": "og:image"})

    if og and og.get("content"):
        return og["content"]

    return None

# ──────────────────────────────────────────────
# SCRAPING PRINCIPAL
# ──────────────────────────────────────────────
def pegar_dados(link):

    titulo = "Produto"
    loja = nome_loja(link)
    preco_atual = None
    preco_antigo = None
    imagem = None

    try:

        r = scraper.get(link, headers=HEADERS, timeout=15)

        url_final = r.url

        loja = nome_loja(url_final)

        soup = BeautifulSoup(r.text, "html.parser")

        og_title = soup.find("meta", {"property": "og:title"})

        if og_title and og_title.get("content"):
            titulo = og_title["content"].strip()

        elif soup.title:
            titulo = soup.title.text.strip()

        preco_atual, preco_antigo = extrair_precos_schema(soup)

        if not preco_atual:
            preco_atual, preco_antigo = extrair_precos_html(soup)

        imagem = extrair_imagem(soup)

        print(f"""
[DEBUG PRODUTO]

Loja: {loja}
Preço: {preco_atual}
URL: {url_final}

""")

    except Exception as e:

        print(f"Erro scraping: {e}")

    return titulo, loja, preco_atual, preco_antigo, imagem

# ──────────────────────────────────────────────
# MENSAGEM
# ──────────────────────────────────────────────
def extrair_link(texto):

    match = re.search(r'https?://[^\s]+', texto)

    if match:
        return match.group()

    return None


def escapar_html(texto):

    return (
        str(texto)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )

def montar_mensagem(link, titulo, loja, preco_atual, preco_antigo):

    preco = formatar_preco(preco_atual)

    return (
        f"🔥 PROMOÇÃO ENCONTRADA\n\n"
        f"🛒 {escapar_html(titulo)}\n\n"
        f"Loja: {escapar_html(loja)}\n\n"
        f"Preço: {escapar_html(preco)}\n\n"
        f"COMPRAR ⤵️\n"
        f"{link}"
    )

# ──────────────────────────────────────────────
# TELEGRAM
# ──────────────────────────────────────────────
async def responder(update: Update, context: ContextTypes.DEFAULT_TYPE):

    texto = update.message.text

    if not texto or "http" not in texto:
        return

    link = extrair_link(texto)

    if not link:
        return

    await update.message.reply_text("🔍 Buscando produto...")

    loop = asyncio.get_event_loop()

    titulo, loja, preco_atual, preco_antigo, imagem = await loop.run_in_executor(
        None, pegar_dados, link
    )

    mensagem = montar_mensagem(link, titulo, loja, preco_atual, preco_antigo)

    if imagem:
        try:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=imagem,
                caption=mensagem,
                parse_mode="HTML"
            )
            return
        except:
            pass

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=mensagem,
        parse_mode="HTML"
    )

# ──────────────────────────────────────────────
# BOT
# ──────────────────────────────────────────────
async def main():

    garantir_instancia_unica()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(MessageHandler(filters.TEXT, responder))

    print("BOT PROMO INICIADO")

    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    await asyncio.Event().wait()

# ──────────────────────────────────────────────
# START
# ──────────────────────────────────────────────
if __name__ == "__main__":

    try:
        asyncio.run(main())
    finally:
        limpar_pid()
