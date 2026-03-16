import asyncio
from io import BytesIO
import ipaddress
import json
import logging
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram.error import Conflict
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

# Linux/Replit target: keep the runtime assumptions explicit.
PID_FILE = Path(os.environ.get("MYPROMO_PID_FILE", "/tmp/mypromo_bot.pid"))
MAX_REDIRECTS = 5
MAX_HTML_BYTES = 1_500_000
REQUEST_TIMEOUT = 12

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("mypromo_bot")


LOJAS = {
    # Mercado Livre
    "mercadolivre.com.br": "Mercado Livre",
    "mercadolibre.com": "Mercado Livre",
    "meli.com": "Mercado Livre",
    "mlv.io": "Mercado Livre",
    "ml.com.br": "Mercado Livre",
    # Amazon
    "amazon.com.br": "Amazon",
    "amazon.com": "Amazon",
    "amzn.to": "Amazon",
    "amzn.com": "Amazon",
    "a.co": "Amazon",
    # Demais lojas
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

DOMINIOS_REDIRECIONADORES = {"meli.com", "mlv.io", "ml.com.br", "amzn.to", "amzn.com", "a.co"}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

BOT_TOKEN = None


class BotValidationError(Exception):
    """User-facing validation failure for blocked or malformed links."""


class ScrapeError(Exception):
    """Raised when scraping fails in a way the user should be notified about."""


def carregar_config():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN nao encontrado.")

    grupo_id = os.environ.get("TELEGRAM_GRUPO_ID")
    if grupo_id:
        logger.info("TELEGRAM_GRUPO_ID definido, mas o reenvio automatico esta desativado.")

    return token


def processo_ativo(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def identidade_do_processo(pid):
    try:
        return Path(f"/proc/{pid}/cmdline").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def garantir_instancia_unica():
    if PID_FILE.exists():
        try:
            pid_antigo = int(PID_FILE.read_text(encoding="utf-8").strip())
        except ValueError:
            logger.warning("PID file invalido em %s; sobrescrevendo.", PID_FILE)
        else:
            if pid_antigo != os.getpid() and processo_ativo(pid_antigo):
                identidade = identidade_do_processo(pid_antigo)
                if "bot.py" in identidade:
                    raise RuntimeError(f"Outra instancia do bot ja esta em execucao (PID {pid_antigo}).")
                raise RuntimeError(
                    f"PID file aponta para um processo ativo nao relacionado (PID {pid_antigo}). "
                    "Remova o PID file manualmente."
                )

    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")


def limpar_pid():
    try:
        if PID_FILE.exists() and PID_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
            PID_FILE.unlink()
    except OSError as exc:
        logger.warning("Falha ao limpar PID file %s: %s", PID_FILE, exc)


def normalizar_host(url):
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise BotValidationError("Apenas links http/https sao aceitos.")
    if not parsed.hostname:
        raise BotValidationError("Nao consegui identificar o dominio do link.")
    return parsed.hostname.lower()


def host_eh_ip(host):
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast


def host_bloqueado(host):
    return host in {"localhost"} or host.endswith(".local") or host_eh_ip(host)


def dominio_conhecido(host):
    return any(host == chave or host.endswith(f".{chave}") for chave in LOJAS)


def nome_loja(url):
    try:
        dominio = normalizar_host(url).replace("www.", "")
        for chave, nome in LOJAS.items():
            if dominio == chave or dominio.endswith(f".{chave}"):
                return nome
        partes = dominio.split(".")
        return partes[-2].capitalize() if len(partes) >= 2 else dominio.capitalize()
    except BotValidationError:
        return "Loja"
    except Exception:
        return "Loja"


def validar_url_inicial(url):
    host = normalizar_host(url)
    if host_bloqueado(host):
        raise BotValidationError("Nao aceito links locais, IPs ou enderecos internos.")
    if not dominio_conhecido(host):
        raise BotValidationError("Aceito apenas links de lojas conhecidas.")
    return host


def validar_url_final(url):
    host = normalizar_host(url)
    if host_bloqueado(host):
        raise BotValidationError("O redirecionamento terminou em um host bloqueado.")
    if not dominio_conhecido(host) or host in DOMINIOS_REDIRECIONADORES:
        raise BotValidationError("O link final nao pertence a uma loja suportada.")
    return host


def abrir_url_html(session, link):
    validar_url_inicial(link)
    atual = link

    for _ in range(MAX_REDIRECTS + 1):
        resposta = session.get(
            atual,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=False,
            stream=True,
        )

        if 300 <= resposta.status_code < 400:
            destino = resposta.headers.get("Location")
            resposta.close()
            if not destino:
                raise ScrapeError("O site respondeu com redirecionamento invalido.")
            atual = urljoin(atual, destino)
            validar_url_inicial(atual)
            continue

        resposta.raise_for_status()
        url_final = resposta.url
        validar_url_final(url_final)

        content_type = resposta.headers.get("Content-Type", "").lower()
        if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
            resposta.close()
            raise ScrapeError("O link nao retornou uma pagina HTML valida.")

        content_length = resposta.headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) > MAX_HTML_BYTES:
                    resposta.close()
                    raise ScrapeError("A pagina e grande demais para ser processada com seguranca.")
            except ValueError:
                logger.warning("Content-Length invalido recebido de %s: %s", url_final, content_length)

        chunks = []
        total = 0
        for chunk in resposta.iter_content(chunk_size=32_768, decode_unicode=False):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_HTML_BYTES:
                resposta.close()
                raise ScrapeError("A pagina excedeu o limite de tamanho permitido.")
            chunks.append(chunk)

        encoding = resposta.encoding or resposta.apparent_encoding or "utf-8"
        html = b"".join(chunks).decode(encoding, errors="replace")
        resposta.close()
        return url_final, html

    raise ScrapeError("O link excedeu o numero maximo de redirecionamentos permitidos.")


def limpar_preco(texto):
    if not texto:
        return None
    texto = str(texto).strip()
    match = re.search(r"(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})|\d+[.,]\d{2}|\d+)", texto)
    if not match:
        return None
    valor = match.group()
    if re.search(r",\d{2}$", valor):
        valor = valor.replace(".", "").replace(",", ".")
    elif re.search(r"\.\d{2}$", valor):
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


def montar_valor_partes(fracao, centavos=None):
    fracao_limpa = re.sub(r"\D", "", fracao or "")
    centavos_limpos = re.sub(r"\D", "", centavos or "")
    if not fracao_limpa:
        return None
    if centavos is None:
        return fracao_limpa
    centavos_limpos = (centavos_limpos or "00")[:2].ljust(2, "0")
    return f"{fracao_limpa}.{centavos_limpos}"


def extrair_preco_de_texto(soup, selectors):
    for selector in selectors:
        el = soup.select_one(selector)
        if not el:
            continue
        valor = limpar_preco(el.get("content") or el.get_text(" ", strip=True))
        if valor and float(valor) > 0:
            return valor
    return None


def extrair_preco_por_partes(soup, amount_selectors, fraction_selector, cents_selector):
    for selector in amount_selectors:
        bloco = soup.select_one(selector)
        if not bloco:
            continue
        fracao = bloco.select_one(fraction_selector)
        if not fracao:
            continue
        centavos = bloco.select_one(cents_selector)
        valor = montar_valor_partes(fracao.get_text(" ", strip=True), centavos.get_text(" ", strip=True) if centavos else None)
        if valor and float(valor) > 0:
            return valor
    return None


def extrair_precos_amazon(soup):
    preco_atual = extrair_preco_de_texto(
        soup,
        [
            "#corePrice_feature_div .priceToPay .a-offscreen",
            "#corePrice_feature_div .a-price .a-offscreen",
            "#corePriceDisplay_desktop_feature_div .priceToPay .a-offscreen",
            "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
            "#apex_desktop .apexPriceToPay .a-offscreen",
            "#apex_desktop .a-price .a-offscreen",
            "#desktop_buybox .priceToPay .a-offscreen",
            "#priceblock_dealprice",
            "#priceblock_ourprice",
            "#price_inside_buybox",
        ],
    )
    preco_antigo = extrair_preco_de_texto(
        soup,
        [
            "#corePrice_feature_div .basisPrice .a-offscreen",
            "#corePriceDisplay_desktop_feature_div .basisPrice .a-offscreen",
            "#apex_desktop .basisPrice .a-offscreen",
            "#corePrice_feature_div .a-price.a-text-price .a-offscreen",
            "#corePriceDisplay_desktop_feature_div .a-price.a-text-price .a-offscreen",
            ".a-price.a-text-price .a-offscreen",
            ".basisPrice .a-offscreen",
            ".priceBlockStrikePriceString",
            "#listPrice",
        ],
    )
    return preco_atual, preco_antigo


def extrair_precos_mercadolivre(soup):
    preco_atual = extrair_preco_por_partes(
        soup,
        [
            ".ui-pdp-price__main-container .andes-money-amount",
            ".ui-pdp-price__second-line .andes-money-amount",
            "[data-testid='price-part'] .andes-money-amount",
            ".price-tag",
        ],
        ".andes-money-amount__fraction, .price-tag-fraction",
        ".andes-money-amount__cents, .price-tag-cents",
    )
    preco_antigo = extrair_preco_por_partes(
        soup,
        [
            ".ui-pdp-price__subtitles .andes-money-amount--previous",
            ".andes-money-amount--previous",
            ".price-tag--del",
        ],
        ".andes-money-amount__fraction, .price-tag-fraction",
        ".andes-money-amount__cents, .price-tag-cents",
    )
    return preco_atual, preco_antigo


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
        ".price",
        ".preco",
        ".sale-price",
        ".offer-price",
        ".product-price",
        ".product__price",
    ]
    seletores_antigo = [
        ".old-price",
        ".price-before",
        ".was-price",
        ".preco-antigo",
        ".price__old",
        "s",
        "del",
        "strike",
    ]

    mapa_lojas = {
        "mercadolivre": (
            [
                ".andes-money-amount__fraction",
                ".price-tag-fraction",
                ".ui-pdp-price__second-line .andes-money-amount__fraction",
            ],
            [
                ".andes-money-amount--previous .andes-money-amount__fraction",
                ".price-tag--del .price-tag-fraction",
            ],
        ),
        "amazon": (
            [
                ".a-price .a-offscreen",
                ".priceToPay .a-offscreen",
                "#priceblock_ourprice",
                "#priceblock_dealprice",
            ],
            [
                ".a-text-strike .a-offscreen",
                "#listPrice",
                ".a-price.a-text-price .a-offscreen",
            ],
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
    for sel in seletores_antigo:
        el = soup.select_one(sel)
        if el:
            candidato = limpar_preco(el.get_text(" ", strip=True))
            if candidato and candidato != preco_atual:
                preco_antigo = candidato
                break

    return preco_atual, preco_antigo


def extrair_precos_loja(soup, url):
    dominio = normalizar_host(url).replace("www.", "")
    if "amazon." in dominio or dominio in {"a.co", "amzn.to", "amzn.com"}:
        return extrair_precos_amazon(soup)
    if "mercadolivre" in dominio or "mercadolibre" in dominio or dominio in {"meli.com", "mlv.io", "ml.com.br"}:
        return extrair_precos_mercadolivre(soup)
    return None, None


def combinar_precos(*fontes):
    preco_atual = None
    preco_antigo = None

    for atual, antigo in fontes:
        if not preco_atual and atual:
            preco_atual = atual
        if not preco_antigo and antigo:
            preco_antigo = antigo

    if preco_antigo and preco_atual:
        try:
            if float(preco_antigo) <= float(preco_atual):
                preco_antigo = None
        except ValueError:
            pass

    return preco_atual, preco_antigo


def extrair_imagem_amazon(soup):
    for selector in ["#landingImage", "#imgTagWrapperId img"]:
        el = soup.select_one(selector)
        if not el:
            continue
        for attr in ["data-old-hires", "src"]:
            valor = el.get(attr)
            if valor and valor.startswith("http"):
                return valor
        dinamica = el.get("data-a-dynamic-image")
        if dinamica:
            try:
                dados = json.loads(dinamica)
            except json.JSONDecodeError:
                pass
            else:
                for url in dados:
                    if isinstance(url, str) and url.startswith("http"):
                        return url
    meta = soup.find("meta", {"name": "twitter:image"})
    if meta and meta.get("content"):
        return meta["content"]
    for script in soup.find_all("script"):
        conteudo = script.string or script.get_text(" ", strip=True)
        if not conteudo:
            continue
        match = re.search(r'"hiRes"\s*:\s*"([^"]+)"', conteudo)
        if match:
            return match.group(1).replace("\\u0026", "&").replace("\\/", "/")
        match = re.search(r'"large"\s*:\s*"([^"]+)"', conteudo)
        if match:
            return match.group(1).replace("\\u0026", "&").replace("\\/", "/")
    return None


def extrair_imagem(soup, url=None):
    if url:
        dominio = normalizar_host(url).replace("www.", "")
        if "amazon." in dominio or dominio in {"a.co", "amzn.to", "amzn.com"}:
            imagem_amazon = extrair_imagem_amazon(soup)
            if imagem_amazon:
                return imagem_amazon

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


def baixar_imagem(url):
    with requests.Session() as session:
        resposta = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, stream=True)
        resposta.raise_for_status()
        content_type = resposta.headers.get("Content-Type", "").lower()
        if not content_type.startswith("image/"):
            resposta.close()
            raise ScrapeError("A URL da imagem nao retornou um arquivo de imagem valido.")

        buffer = BytesIO()
        total = 0
        for chunk in resposta.iter_content(chunk_size=32_768):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_HTML_BYTES:
                resposta.close()
                raise ScrapeError("A imagem do produto excede o limite de tamanho permitido.")
            buffer.write(chunk)
        resposta.close()
        buffer.seek(0)
        buffer.name = "produto.jpg"
        return buffer


def pegar_dados(link):
    with requests.Session() as session:
        try:
            url_final, html = abrir_url_html(session, link)
        except requests.RequestException as exc:
            logger.warning("Erro HTTP ao buscar %s: %s", link, exc)
            raise ScrapeError("Nao consegui acessar esse link agora.") from exc

        soup = BeautifulSoup(html, "html.parser")
        titulo = "Produto"
        loja = nome_loja(url_final)

        og_title = soup.find("meta", {"property": "og:title"})
        if og_title and og_title.get("content"):
            titulo = og_title["content"].strip()
        elif soup.title:
            titulo = soup.title.text.strip()

        precos_loja = extrair_precos_loja(soup, url_final)
        precos_html = extrair_precos_html(soup, url_final)
        precos_schema = extrair_precos_schema(soup)
        precos_meta = extrair_precos_meta(soup)
        preco_atual, preco_antigo = combinar_precos(
            precos_loja,
            precos_schema,
            precos_meta,
            precos_html,
        )

        imagem = extrair_imagem(soup, url_final)
        logger.info(
            "Scraping concluido | loja=%s preco=%s antigo=%s url_final=%s fontes=%s/%s/%s/%s",
            loja,
            preco_atual,
            preco_antigo,
            url_final,
            precos_loja,
            precos_schema,
            precos_meta,
            precos_html,
        )

        if not preco_atual:
            raise ScrapeError("Nao consegui identificar o preco desse produto.")

        return {
            "titulo": titulo,
            "loja": loja,
            "preco_atual": preco_atual,
            "preco_antigo": preco_antigo,
            "imagem": imagem,
            "url_final": url_final,
        }


def extrair_link(texto):
    match = re.search(r"https?://[^\s]+", texto)
    return match.group() if match else None


def escapar_html(texto):
    return str(texto).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def montar_mensagem(link, titulo, loja, preco_atual, preco_antigo):
    preco_atual_fmt = formatar_preco(preco_atual)
    preco_antigo_fmt = formatar_preco(preco_antigo)

    if preco_atual_fmt and preco_antigo_fmt:
        linha_preco = f"<s>De: {escapar_html(preco_antigo_fmt)}</s>\nPor: {escapar_html(preco_atual_fmt)}"
    elif preco_atual_fmt:
        linha_preco = f"Por: {escapar_html(preco_atual_fmt)}"
    else:
        linha_preco = "Ver preco no link"

    return (
        "🔥 PROMOÇÃO ENCONTRADA\n\n"
        f"🛒 {escapar_html(titulo)}\n\n"
        f"Loja: {escapar_html(loja)}\n\n"
        f"{linha_preco}\n\n"
        "COMPRAR AGORA ⤵️\n"
        f"{escapar_html(link)}\n\n"
        "⚡ Oferta pode acabar a qualquer momento"
    )


async def responder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    texto = update.message.text
    if "http" not in texto:
        return

    link = extrair_link(texto)
    if not link:
        return

    try:
        validar_url_inicial(link)
    except BotValidationError as exc:
        await update.message.reply_text(str(exc))
        return

    await update.message.reply_text("🔍 Buscando informações do produto...")

    loop = asyncio.get_running_loop()
    try:
        dados = await loop.run_in_executor(None, pegar_dados, link)
    except BotValidationError as exc:
        await update.message.reply_text(str(exc))
        return
    except ScrapeError as exc:
        await update.message.reply_text(str(exc))
        return
    except Exception:
        logger.exception("Falha inesperada processando link %s", link)
        await update.message.reply_text("Ocorreu um erro inesperado ao processar esse link.")
        return

    mensagem = montar_mensagem(
        link,
        dados["titulo"],
        dados["loja"],
        dados["preco_atual"],
        dados["preco_antigo"],
    )

    if dados["imagem"]:
        try:
            imagem_bytes = baixar_imagem(dados["imagem"])
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=imagem_bytes,
                caption=mensagem,
                parse_mode="HTML",
            )
            return
        except Exception:
            logger.exception("Falha ao enviar foto para o chat %s", update.effective_chat.id)

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=mensagem,
        parse_mode="HTML",
    )


async def main():
    global BOT_TOKEN
    BOT_TOKEN = carregar_config()
    garantir_instancia_unica()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, responder))

    logger.info("Bot mypromo iniciado e aguardando mensagens.")

    while True:
        try:
            await app.initialize()
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True)
            await asyncio.Event().wait()
        except Conflict:
            logger.warning("Conflito 409 detectado. Tentando retomar em 6s.")
            try:
                await app.updater.stop()
                await app.stop()
                await app.shutdown()
            except Exception:
                logger.exception("Falha ao reiniciar app apos conflito.")
            await asyncio.sleep(6)
        except Exception:
            logger.exception("Erro fatal no loop principal. Reiniciando em 3s.")
            try:
                await app.updater.stop()
                await app.stop()
                await app.shutdown()
            except Exception:
                logger.exception("Falha ao encerrar app apos erro fatal.")
            await asyncio.sleep(3)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError as exc:
        logger.error("%s", exc)
        sys.exit(1)
    finally:
        limpar_pid()
