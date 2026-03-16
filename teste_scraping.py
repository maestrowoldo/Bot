#!/usr/bin/env python3
"""
Script para testar o scraping de URLs sem rodar o bot do Telegram.
Uso: python teste_scraping.py <url>
Exemplo: python teste_scraping.py https://www.amazon.com.br/seu-produto
"""

import sys
import bot

if len(sys.argv) < 2:
    print("❌ Uso: python teste_scraping.py <url>")
    print("Exemplo: python teste_scraping.py https://a.co/d/0cmHGuMR")
    sys.exit(1)

url = sys.argv[1]

print(f"🔍 Testando: {url}\n")

try:
    dados = bot.pegar_dados(url)
    
    print("✅ SUCESSO! Dados extraídos:")
    print("=" * 50)
    print(f"📦 Título: {dados['titulo']}")
    print(f"🏪 Loja: {dados['loja']}")
    print(f"💰 Preço Atual: {bot.formatar_preco(dados['preco_atual'])}")
    if dados['preco_antigo']:
        print(f"❌ Preço Antigo: {bot.formatar_preco(dados['preco_antigo'])}")
    else:
        print(f"❌ Preço Antigo: Não encontrado")
    print(f"📸 Imagem: {dados['imagem']}")
    print(f"🔗 URL Final: {dados['url_final']}")
    print("=" * 50)
    
    # Mostra a mensagem formatada
    mensagem = bot.montar_mensagem(
        url,
        dados['titulo'],
        dados['loja'],
        dados['preco_atual'],
        dados['preco_antigo']
    )
    print("\n📱 Mensagem que seria enviada:")
    print("-" * 50)
    print(mensagem)
    print("-" * 50)
    
except bot.BotValidationError as exc:
    print(f"⚠️  Erro de validação: {exc}")
    sys.exit(1)
except bot.ScrapeError as exc:
    print(f"❌ Erro ao fazer scraping: {exc}")
    sys.exit(1)
except Exception as exc:
    print(f"💥 Erro inesperado: {exc}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
