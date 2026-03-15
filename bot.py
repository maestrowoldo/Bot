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
# Mapeamento e Headers
# ──────────────────────────────────────────────
LOJAS = {
    "mercadolivre.com.br": "Mercado Livre",
    "mercadolibre.com":    "Mercado Livre",
    "meli.com":            "Mercado Livre",
    "meli.la":             "Mercado Livre",
    "amazon.com.br":       "Amazon",
    "amzn.to":             "Amazon",
    "magalu.com.br":       "Magazine Luiza",
    "shopee.com.br":       "Shopee",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
}

# ──────────────────────────────────────────────
# Utilitários de Limpeza
# ──────────────────────────────────────────────
def limpar_preco(texto):
    if not texto: return None
    # Remove tudo que não é dígito, vírgula ou ponto
    texto = re.sub(r'[^\d.,]', '', str(texto).strip())
    
    # Caso especial: "1.299,00" -> "1299.00"
    if ',' in texto and '.' in texto:
        if texto.rfind(',') > texto.rfind('.'):
            texto = texto.replace('.', '').replace(',', '.')
        else:
            texto = texto.replace(',', '')
    # Caso: "1299,00" -> "1299.00"
    elif ',' in texto:
        texto = texto.replace(',', '.')
        
    try:
        return str(float(texto))
    except:
        return None

def formatar_preco(valor):
    if not valor: return None
    try:
        return f"R$ {float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return f"R$ {valor}"

# ──────────────────────────────────────────────
# Lógica de Extração Refinada
# ──────────────────────────────────────────────
def extrair_dados_especificos(soup, url):
    dominio = urlparse(url).netloc.lower()
    preco_atual = None
    preco_antigo = None
    imagem = None

    # --- MERCADO LIVRE ---
    if "mercadolivre" in dominio or "mercadolibre" in dominio:
        # Preço Atual (ML separa fração e centavos)
        meta_price = soup.find("meta", {"itemprop": "price"})
        if meta_price:
            preco_atual = limpar_preco(meta_price.get("content"))
        
        if not preco_atual:
            container = soup.select_one(".ui-pdp-price__second-line .andes-money-amount__fraction")
            if container: preco_atual = limpar_preco(container.text)

        # Preço Antigo
        del_tag = soup.select_one(".ui-pdp-price__old .andes-money-amount__fraction")
        if del_tag:
            preco_antigo = limpar_preco(del_tag.text)

    # --- AMAZON ---
    elif "amazon" in dominio or "amzn" in dominio:
        # Preço Atual
        # A Amazon costuma usar a-offscreen para o valor puro
        p_element = soup.select_one(".priceToPay .a-offscreen") or soup.select_one(".a-price .a-offscreen")
        if p_element:
            preco_atual = limpar_preco(p_element.text)
        
        # Preço Antigo
        old_p = soup.select_one(".basisPrice .a-offscreen") or soup.select_one(".a-text-strike")
        if old_p:
            preco_antigo = limpar_preco(old_p.text)

        # Imagem Amazon (Específica)
        img_el = soup.select_one("#landingImage") or soup.select_one("#main-image") or soup.select_one(".a-dynamic-image")
        if img_el:
            # Pega o atributo data-old-hires ou a maior imagem do data-a-dynamic-image
            if img_el.get("data-old-hires"):
                imagem = img_el.get("data-old-hires")
            elif img_el.get("data-a-dynamic-image"):
                try:
                    imgs_dict = json.loads(img_el.get("data-a-dynamic-image"))
                    imagem = list(imgs_dict.keys())[-1] # Pega a de maior resolução
                except: pass
            if not imagem:
                imagem = img_el.get("src")

    # --- FALLBACK GERAL (Schema.org) ---
    if not preco_atual:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string)
                if isinstance(data, list): data = data[0]
                if data.get("@type") == "Product":
                    offers = data.get("offers", {})
                    if isinstance(offers, list): offers = offers[0]
                    preco_atual = limpar_preco(offers.get("price"))
                    break
            except: continue

    # Imagem Fallback
    if not imagem:
        og_img = soup.find("meta", {"property": "og:image"})
        imagem = og_img.get("content") if og_img else None

    return preco_atual, preco_antigo, imagem

# ──────────────────────────────────────────────
# Scraping Principal
# ──────────────────────────────────────────────
def pegar_dados(link):
    titulo = "Produto"
    loja = "Loja"
    
    try:
        # User agent rotativo ou fixo robusto para evitar block
        session = requests.Session()
        r = session.get(link, headers=HEADERS, timeout=15, allow_redirects=True)
        url_final = r.url
        
        # Identificar Loja
        dominio = urlparse(url_final).netloc.lower().replace("www.", "")
        for k, v in LOJAS.items():
            if k in dominio:
                loja = v
                break
        
        soup = BeautifulSoup(r.text, "html.parser")
        
        # Título
        title_tag = soup.find("meta", {"property": "og:title"}) or soup.find("title")
        if title_tag:
            titulo = title_tag.get("content", title_tag.text).strip()
            if "Amazon.com.br" in titulo: titulo = titulo.replace(": Amazon.com.br", "")

        # Extração de Preços e Imagem
        preco_atual, preco_antigo, imagem = extrair_dados_especificos(soup, url_final)

        # Log para Debug
        print(f"[DEBUG] {loja} | Atual: {preco_atual} | Antigo: {preco_antigo} | Img: {bool(imagem)}")

        return titulo, loja, preco_atual, preco_antigo, imagem

    except Exception as e:
        print(f"[ERRO] Scraping {link}: {e}")
        return "Produto", "Loja", None, None, None

# ──────────────────────────────────────────────
# Handlers e Bot
# ──────────────────────────────────────────────
async def responder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    
    match = re.search(r'https?://[^\s]+', update.message.text)
    if not match: return
    
    link = match.group()
    msg_status = await update.message.reply_text("⏳ Analisando link...")

    loop = asyncio.get_event_loop()
    titulo, loja, preco_atual, preco_antigo, imagem = await loop.run_in_executor(None, pegar_dados, link)

    # Formatação Final
    preco_f = formatar_preco(preco_atual)
    preco_o = formatar_preco(preco_antigo)
    
    texto_promo = f"🔥 <b>{titulo}</b>\n\n"
    texto_promo += f"🛒 Loja: <b>{loja}</b>\n"
    if preco_o and preco_f:
        texto_promo += f"<s>De: {preco_o}</s>\n"
        texto_promo += f"✅ <b>Por: {preco_f}</b>\n"
    elif preco_f:
        texto_promo += f"✅ <b>Preço: {preco_f}</b>\n"
    else:
        texto_promo += "⚠️ Confira o preço no site\n"
        
    texto_promo += f"\n🔗 <a href='{link}'>Clique aqui para comprar</a>"

    try:
        # Enviar para o Grupo e apagar msg de status
        if imagem:
            await context.bot.send_photo(chat_id=GRUPO_ID, photo=imagem, caption=texto_promo, parse_mode="HTML")
            await update.message.reply_photo(photo=imagem, caption=texto_promo, parse_mode="HTML")
        else:
            await context.bot.send_message(chat_id=GRUPO_ID, text=texto_promo, parse_mode="HTML", disable_web_page_preview=False)
            await update.message.reply_text(texto_promo, parse_mode="HTML")
            
        await msg_status.delete()
    except Exception as e:
        await msg_status.edit_text(f"❌ Erro ao postar: {e}")

async def main():
    garantir_instancia_unica()
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), responder))
    
    print("Bot rodando...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        limpar_pid()
