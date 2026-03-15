import telebot
import os
import re

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN nao encontrado nas variaveis de ambiente.")

bot = telebot.TeleBot(TOKEN)

def extrair_link(texto):
    padrao = r'https?://[^\s]+'
    links = re.findall(padrao, texto)
    return links[0] if links else None

@bot.message_handler(func=lambda message: message.text and re.search(r'https?://', message.text))
def link_handler(message):
    link = extrair_link(message.text)
    if not link:
        return

    resposta = f"""🔥 SUPER PROMOÇÃO

🛒 Produto em oferta
💰 Veja o preço no link

👉 COMPRAR AGORA
{link}

⚡ Aproveite antes que acabe!"""

    bot.send_message(message.chat.id, resposta)

print("Bot mypromo iniciado! Aguardando mensagens...")
bot.infinity_polling()
