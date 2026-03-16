import unittest

import bot


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
            "https://www.amazon.com.br/produto",
            "Notebook",
            "Amazon",
            "1299.90",
            "1499.90",
        )
        self.assertIn("R$ 1.299,90", mensagem)
        self.assertIn("https://www.amazon.com.br/produto", mensagem)


if __name__ == "__main__":
    unittest.main()
