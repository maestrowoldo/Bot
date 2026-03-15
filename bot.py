import os
import re
import sys
import json
import signal
import asyncio
import requests
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
# Instância única via PID file
# ──────────────────────────────────────────────
PID_FILE = "/tmp/mypromo_bot.pid"

def garantir_instancia_unica():
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                pid_antigo = int(f.read().strip())
            if pid_antigo != os.getpid():
                os.kill(pid_antigo, signal.SIGKILL)
                print(f"Processo antigo (PID {pid_antigo}) encerrado.")
        except (ProcessLookupError, ValueError):
            pass
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

def limpar_pid():
    try:
        os.remove(PID_FILE)
    except FileNotFoundError:
        pass

# ──────────────────────────────────────────────
# Mapeamento de lojas (incluindo URLs curtas)
# ──────────────────────────────────────────────
LOJAS = {
    # Mercado Livre — domínios completos e URLs curtas
    "mercadolivre.com.br": "Mercado Livre",
    "mercadolibre.com":    "Mercado Livre",
    "meli.com":            "Mercado Livre",
    "meli.la":             "Mercado Livre",
    "mlv.io":              "Mercado Livre",
    "ml.com.br":           "Mercado Livre",
    # Amazon — domínios completos e URLs curtas
    "amazon.com.br":       "Amazon",
    "amazon.com":          "Amazon",
    "amzn.to":             "Amazon",
    "amzn.com":            "Amazon",
    "a.co":                "Amazon",
    # Demais lojas
    "americanas.com.br":   "Americanas",
    "magazineluiza.com.br":"Magazine Luiza",
    "magalu.com.br":       "Magazine Luiza",
    "shopee.com.br":       "Shopee",
    "casasbahia.com.br":   "Casas Bahia",
    "submarino.com.br":    "Submarino",
    "extra.com.br":        "Extra",
    "pontofrio.com.br":    "Ponto Frio",
    "fastshop.com.br":     "Fast Shop",
    "kabum.com.br":        "KaBuM!",
    "aliexpress.com":      "AliExpress",
    "shein.com":           "Shein",
    "netshoes.com.br":     "Netshoes",
    "centauro.com.br":     "Centauro",
    "leroymerlin.com.br":  "Leroy Merlin",
    "carrefour.com.br":    "Carrefour",
    "havan.com.br":        "Havan",
    "renner.com.br":       "Renner",
    "riachuelo.com.br":    "Riachuelo",
    "dafiti.com.br":       "Dafiti",
    "zattini.com.br":      "Zattini",
    "nike.com.br":         "Nike",
    "adidas.com.br":       "Adidas",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ──────────────────────────────────────────────
# Utilitários de preço
# ──────────────────────────────────────────────
def nome_loja(url):
    try:
        dominio = urlparse(url).netloc.lower().replace("www.", "")
        for chave, nome in LOJAS.items():
            if chave in dominio:
                return nome
        partes = dominio.split(".")
        return partes[-2].capitalize() if len(partes) >= 2 else dominio.capitalize()
    except Exception:
        return "Loja"


def limpar_preco(texto):
    if not texto:
        return None
    texto = str(texto).strip()
    # Aceita padrões como 1.299,99 ou 1299.99 ou 299,90
    match = re.search(r'(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})|\d+[.,]\d{2}|\d+)', texto)
    if not match:
        return None
    valor = match.group()
    # Normaliza para ponto decimal
    if re.search(r',\d{2}$', valor):
        valor = valor.replace(".", "").replace(",", ".")
    elif re.search(r'\.\d{2}$', valor):
        valor = valor.replace(",", "")
    try:
        float(valor)
        return valor
    except ValueError:
        return None


def formatar_preco(valor):
    if not valor:
        return None
    try:
        numero = float(valor)
        return f"R$ {numero:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return f"R$ {valor}"


# ──────────────────────────────────────────────
# Extração de preços
# ──────────────────────────────────────────────
def extrair_precos_schema(soup):
    preco_atual = None
    preco_antigo = None
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            dados = json.loads(script.string or "")
            # Pode ser lista de objetos
            if isinstance(dados, list):
                obj = next((d for d in dados if d.get("@type") == "Product"), None)
                if obj is None:
                    obj = next((d for d in dados if d.get("@type") == "Offer"), None)
                dados = obj or {}

            tipo = dados.get("@type", "")

            if tipo == "Product":
                offers = dados.get("offers", {})
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                if isinstance(offers, dict):
                    preco_atual = limpar_preco(str(offers.get("price", "")))
                    antigo_raw = (
                        offers.get("priceAnchor")
                        or offers.get("listPrice")
                    )
                    if antigo_raw:
                        preco_antigo = limpar_preco(str(antigo_raw))

            elif tipo == "Offer":
                preco_atual = limpar_preco(str(dados.get("price", "")))
                antigo_raw = dados.get("priceAnchor") or dados.get("listPrice")
                if antigo_raw:
                    preco_antigo = limpar_preco(str(antigo_raw))

            # Garante que antigo > atual (senão descarta)
            if preco_atual and preco_antigo:
                try:
                    if float(preco_antigo) <= float(preco_atual):
                        preco_antigo = None
                except (ValueError, TypeError):
                    preco_antigo = None

            if preco_atual:
                break
        except Exception:
            continue
    return preco_atual, preco_antigo


def extrair_precos_meta(soup):
    for prop in ["product:price:amount", "og:price:amount"]:
        meta = soup.find("meta", {"property": prop})
        if meta and meta.get("content"):
            return limpar_preco(meta["content"]), None
    meta = soup.find("meta", {"itemprop": "price"})
    if meta and meta.get("content"):
        return limpar_preco(meta["content"]), None
    return None, None


def extrair_precos_html(soup, url):
    dominio = urlparse(url).netloc.lower()

    seletores_atual = [
        "[itemprop='price']",
        ".price", ".preco", ".sale-price", ".offer-price",
        ".product-price", ".product__price",
    ]
    seletores_antigo = [
        ".old-price", ".price-before", ".was-price",
        ".preco-antigo", ".price__old", "s", "del", "strike",
    ]

    mapa_lojas = {
        "mercadolivre": (
            [".andes-money-amount__fraction", ".price-tag-fraction",
             ".ui-pdp-price__second-line .andes-money-amount__fraction"],
            ["s.andes-money-amount--previous", ".andes-money-amount--previous",
             ".price-tag--del .price-tag-fraction"],
        ),
        "amazon": (
            [".a-price .a-offscreen", ".priceToPay .a-offscreen",
             "#priceblock_ourprice", "#priceblock_dealprice"],
            [".a-text-strike .a-offscreen", "#listPrice",
             ".a-price.a-text-price .a-offscreen"],
        ),
        "magazineluiza": (
            ["[data-testid='price-value']", ".price__current", ".sc-ckVGcZ"],
            ["[data-testid='original-price']", ".price__original"],
        ),
        "americanas": (
            ["[data-testid='price']", ".priceSales"],
            ["[data-testid='list-price']", ".priceStandard"],
        ),
        "casasbahia": (
            ["[data-testid='price-value']", ".price__current--value"],
            ["[data-testid='original-price']", ".price__old--value"],
        ),
        "kabum": (
            ["[itemprop='price']", ".regularPrice", ".finalPrice"],
            [".oldPrice", ".oldPriceValue"],
        ),
    }

    for chave, (s_atual, s_antigo) in mapa_lojas.items():
        if chave in dominio:
            seletores_atual = s_atual + seletores_atual
            seletores_antigo = s_antigo + seletores_antigo
            break

    preco_atual = None
    for sel in seletores_atual:
        el = soup.select_one(sel)
        if el:
            val = limpar_preco(el.get_text(" ", strip=True))
            if val and float(val) > 0:
                preco_atual = val
                break

    preco_antigo = None

    # 1. Tenta via seletores CSS específicos
    for sel in seletores_antigo:
        el = soup.select_one(sel)
        if el:
            candidato = limpar_preco(el.get_text(" ", strip=True))
            if candidato and candidato != preco_atual:
                preco_antigo = candidato
                break

    # 2. ML específico: aria-label="Antes: X reais" ou "Era: X"
    if not preco_antigo and "mercadolivre" in dominio:
        for el in soup.find_all(attrs={"aria-label": True}):
            label = el["aria-label"]
            if re.search(r'(Antes|Era|anterior)[:\s]', label, re.IGNORECASE):
                candidato = limpar_preco(label)
                if candidato and candidato != preco_atual:
                    preco_antigo = candidato
                    break

    # 3. Fallback genérico: qualquer <s> ou <del> com preço maior que o atual
    if not preco_antigo:
        for tag in soup.find_all(["s", "del"]):
            candidato = limpar_preco(tag.get_text(" ", strip=True))
            if not candidato:
                continue
            try:
                if preco_atual and float(candidato) <= float(preco_atual):
                    continue  # ignora se for menor ou igual (não faz sentido como "De")
            except (ValueError, TypeError):
                pass
            if candidato != preco_atual:
                preco_antigo = candidato
                break

    return preco_atual, preco_antigo


# ──────────────────────────────────────────────
# Extração de imagem
# ──────────────────────────────────────────────
def extrair_imagem(soup):
    og = soup.find("meta", {"property": "og:image"})
    if og and og.get("content"):
        return og["content"]
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
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if src.startswith("http") and not src.endswith(".svg"):
            return src
    return None


# ──────────────────────────────────────────────
# Função principal de scraping
# ──────────────────────────────────────────────
def pegar_dados(link):
    titulo = "Produto"
    loja = nome_loja(link)  # nome pela URL curta
    preco_atual = None
    preco_antigo = None
    imagem = None

    try:
        r = requests.get(link, headers=HEADERS, timeout=12, allow_redirects=True)
        r.raise_for_status()
        url_final = r.url  # URL após redirecionamentos (ex: amzn.to → amazon.com.br)
        loja = nome_loja(url_final)  # re-detecta pela URL final

        soup = BeautifulSoup(r.text, "html.parser")

        # Título
        og_title = soup.find("meta", {"property": "og:title"})
        if og_title and og_title.get("content"):
            titulo = og_title["content"].strip()
        elif soup.title:
            titulo = soup.title.text.strip()

        # Preços: schema.org → meta tags → HTML
        preco_atual, preco_antigo = extrair_precos_schema(soup)
        if not preco_atual:
            preco_atual, preco_antigo = extrair_precos_meta(soup)
        if not preco_atual:
            preco_atual, preco_antigo = extrair_precos_html(soup, url_final)
        elif not preco_antigo:
            # Preço atual encontrado via schema/meta, mas sem preço antigo:
            # tenta extrair o preço riscado diretamente do HTML da página
            _, preco_antigo = extrair_precos_html(soup, url_final)

        print(f"[DEBUG] Loja: {loja} | Preço: {preco_atual} | Antigo: {preco_antigo} | URL final: {url_final}")

        # Imagem
        imagem = extrair_imagem(soup)

    except Exception as e:
        print(f"[ERRO] {link}: {e}")

    return titulo, loja, preco_atual, preco_antigo, imagem


# ──────────────────────────────────────────────
# Montagem da mensagem
# ──────────────────────────────────────────────
def extrair_link(texto):
    match = re.search(r'https?://[^\s]+', texto)
    return match.group() if match else None


def escapar_html(texto):
    return (
        str(texto)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def montar_mensagem(link, titulo, loja, preco_atual, preco_antigo):
    preco_atual_fmt = formatar_preco(preco_atual)
    preco_antigo_fmt = formatar_preco(preco_antigo)

    if preco_atual_fmt and preco_antigo_fmt:
        linha_preco = (
            f"<s>De: {escapar_html(preco_antigo_fmt)}</s>\n"
            f"Por: {escapar_html(preco_atual_fmt)}"
        )
    elif preco_atual_fmt:
        linha_preco = f"Por: {escapar_html(preco_atual_fmt)}"
    else:
        linha_preco = "Ver preço no link"

    return (
        f"🔥 PROMOÇÃO ENCONTRADA\n\n"
        f"🛒 {escapar_html(titulo)}\n\n"
        f"Loja: {escapar_html(loja)}\n\n"
        f"{linha_preco}\n\n"
        f"COMPRAR AGORA ⤵️\n"
        f"{escapar_html(link)}\n\n"
        f"⚡ Oferta pode acabar a qualquer momento"
    )


# ──────────────────────────────────────────────
# Handler do Telegram
# ──────────────────────────────────────────────
async def responder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text
    if not texto or "http" not in texto:
        return

    link = extrair_link(texto)
    if not link:
        return

    await update.message.reply_text("🔍 Buscando informações do produto...")

    loop = asyncio.get_event_loop()
    titulo, loja, preco_atual, preco_antigo, imagem = await loop.run_in_executor(
        None, pegar_dados, link
    )
    mensagem = montar_mensagem(link, titulo, loja, preco_atual, preco_antigo)

    async def enviar(chat_id):
        if imagem:
            try:
                await context.bot.send_photo(
                    chat_id=chat_id, photo=imagem,
                    caption=mensagem, parse_mode="HTML"
                )
                return
            except Exception:
                pass
        await context.bot.send_message(chat_id=chat_id, text=mensagem, parse_mode="HTML")

    await enviar(update.effective_chat.id)
    try:
        await enviar(GRUPO_ID)
    except Exception as e:
        print(f"[ERRO] Grupo: {e}")


# ──────────────────────────────────────────────
# Inicialização
# ──────────────────────────────────────────────
async def main():
    garantir_instancia_unica()

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
            print("Conflito 409. Aguardando 6s...")
            try:
                await app.updater.stop()
                await app.stop()
                await app.shutdown()
            except Exception:
                pass
            await asyncio.sleep(6)
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
    try:
        asyncio.run(main())
    finally:
        limpar_pid()
