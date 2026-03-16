import unittest

import bot
from bs4 import BeautifulSoup


class BotValidationTests(unittest.TestCase):
    def test_accepts_supported_store_domain(self):
        self.assertEqual(bot.validar_url_inicial("https://www.amazon.com.br/produto"), "www.amazon.com.br")

    def test_blocks_unknown_domain(self):
        with self.assertRaises(bot.BotValidationError):
            bot.validar_url_inicial("https://example.com/oferta")

    def test_blocks_localhost_and_private_ip(self):
        with self.assertRaises(bot.BotValidationError):
            bot.validar_url_inicial("http://localhost:8000")
        with self.assertRaises(bot.BotValidationError):
            bot.validar_url_inicial("http://192.168.0.10/item")

    def test_final_url_must_be_store_not_shortener(self):
        with self.assertRaises(bot.BotValidationError):
            bot.validar_url_final("https://amzn.to/abc123")
        self.assertEqual(bot.validar_url_final("https://www.kabum.com.br/produto"), "www.kabum.com.br")

    def test_montar_mensagem_uses_price_and_final_link(self):
        mensagem = bot.montar_mensagem(
            "https://meli.la/abc123",
            "Notebook",
            "Amazon",
            "1299.90",
            "1499.90",
        )
        self.assertIn("R$ 1.299,90", mensagem)
        self.assertIn("https://meli.la/abc123", mensagem)

    def test_extracts_amazon_image_from_dynamic_image(self):
        soup = BeautifulSoup(
            """
            <div id="imgTagWrapperId">
              <img data-a-dynamic-image='{"https://images.amazon.com/produto.jpg":[500,500]}' />
            </div>
            """,
            "html.parser",
        )
        self.assertEqual(
            bot.extrair_imagem(soup, "https://www.amazon.com.br/produto"),
            "https://images.amazon.com/produto.jpg",
        )

    def test_extracts_mercadolivre_price_from_fraction_and_cents(self):
        soup = BeautifulSoup(
            """
            <div class="ui-pdp-price__main-container">
              <span class="andes-money-amount">
                <span class="andes-money-amount__fraction">129</span>
                <span class="andes-money-amount__cents">00</span>
              </span>
            </div>
            <div class="ui-pdp-price__subtitles">
              <span class="andes-money-amount--previous">
                <span class="andes-money-amount__fraction">150</span>
                <span class="andes-money-amount__cents">00</span>
              </span>
            </div>
            """,
            "html.parser",
        )
        self.assertEqual(bot.extrair_precos_loja(soup, "https://www.mercadolivre.com.br/item"), ("129.00", "150.00"))

    def test_extracts_mercadolivre_old_price_from_strikethrough_text(self):
        soup = BeautifulSoup(
            """
            <div class="ui-pdp-price__main-container">
              <span class="andes-money-amount">
                <span class="andes-money-amount__fraction">129</span>
                <span class="andes-money-amount__cents">00</span>
              </span>
            </div>
            <div class="ui-pdp-price__subtitles">
              <s>R$ 150,00</s>
            </div>
            """,
            "html.parser",
        )
        self.assertEqual(bot.extrair_precos_loja(soup, "https://www.mercadolivre.com.br/item"), ("129.00", "150.00"))

    def test_extracts_mercadolivre_main_price_not_installment_value(self):
        soup = BeautifulSoup(
            """
            <div class="ui-pdp-price__current-value">
              <span class="andes-money-amount">
                <span class="andes-money-amount__fraction">188</span>
                <span class="andes-money-amount__cents">43</span>
              </span>
            </div>
            <div class="ui-pdp-price__subtitles">
              <s>R$ 252</s>
            </div>
            <div class="ui-pdp-price__installments">
              8x R$ 23,55 sem juros
            </div>
            """,
            "html.parser",
        )
        self.assertEqual(bot.extrair_precos_loja(soup, "https://www.mercadolivre.com.br/item"), ("188.43", "252"))

    def test_extracts_real_product_url_from_mercadolivre_social_page(self):
        soup = BeautifulSoup(
            """
            <html>
              <head>
                <meta property="og:url" content="https://www.mercadolivre.com.br/terno-masculino-slim-fit-italiano-blazer-e-calca-social/p/MLB123456" />
              </head>
            </html>
            """,
            "html.parser",
        )
        self.assertEqual(
            bot.extrair_url_produto_mercadolivre(
                soup,
                "https://www.mercadolivre.com.br/social/usuario?forceInApp=true",
            ),
            "https://www.mercadolivre.com.br/terno-masculino-slim-fit-italiano-blazer-e-calca-social/p/MLB123456",
        )

    def test_extracts_mercadolivre_prices_from_embedded_script_json(self):
        soup = BeautifulSoup(
            """
            <script>
              {"price":188.43,"original_price":252,"currency_id":"BRL","available_promotions":[{"original_value":252,"value":188.43}]}
            </script>
            """,
            "html.parser",
        )
        self.assertEqual(bot.extrair_precos_mercadolivre(soup), ("188.43", "252.00"))

    def test_extracts_mercadolivre_pix_price_before_installment_price(self):
        soup = BeautifulSoup(
            """
            <div class="ui-pdp-price__second-line">
              <span class="andes-money-amount">
                <span class="andes-money-amount__fraction">179</span>
                <span class="andes-money-amount__cents">01</span>
              </span>
              <span class="ui-pdp-price__second-line__text">no Pix</span>
            </div>
            <div class="ui-pdp-price__subtitles">
              <span data-testid="price-part" class="ui-pdp-price__part__container">
                <span class="andes-money-amount">
                  <span class="andes-money-amount__fraction">188</span>
                  <span class="andes-money-amount__cents">43</span>
                </span>
              </span>
              <span>em 6x</span>
              <span data-testid="price-part" class="ui-pdp-price__part__container">
                <span class="andes-money-amount">
                  <span class="andes-money-amount__fraction">31</span>
                  <span class="andes-money-amount__cents">40</span>
                </span>
              </span>
              <s>R$ 252</s>
            </div>
            """,
            "html.parser",
        )
        self.assertEqual(bot.extrair_precos_mercadolivre(soup), ("179.01", "252"))

    def test_combinar_precos_preenche_preco_antigo_de_fonte_secundaria(self):
        self.assertEqual(
            bot.combinar_precos(("129.00", None), (None, "150.00"), (None, None)),
            ("129.00", "150.00"),
        )

    def test_combinar_precos_corrige_ordem_invertida(self):
        self.assertEqual(bot.combinar_precos(("150.00", "129.00")), ("150.00", None))

    def test_limpar_titulo_produto_remove_preco_embutido(self):
        self.assertEqual(
            bot.limpar_titulo_produto("Notebook Gamer - R$ 8.279,10 em 10x de R$ 827,91"),
            "Notebook Gamer",
        )

    def test_combinar_precos_nao_promove_antigo_para_atual(self):
        self.assertEqual(bot.combinar_precos((None, "252"), (None, None)), (None, None))


if __name__ == "__main__":
    unittest.main()
