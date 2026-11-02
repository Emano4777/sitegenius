from studio import briefing as bf


def test_rules_extract_products_prices_format_and_validity():
    text = (
        "Encarte story tema hortifruti\n"
        "Tomate kg 6,99\n"
        "Banana prata kg - R$ 4,49\n"
        "Café Pilão 500g de 19,90 por 15,99\n"
        "Loja: Mercado Bom Preço\n"
        "Válido até 05/11"
    )
    out = bf.normalize_briefing(bf.interpret_rules(text), [text])
    assert out["formato"] == "story"
    assert out["tema"] == "hortifruti"
    assert out["loja"] == "Mercado Bom Preço"
    assert out["validade"] == "05/11"
    by_name = {p["nome"]: p for p in out["produtos"]}
    assert by_name["Tomate"]["preco"] == "R$ 6,99"
    assert by_name["Tomate"]["unidade"] == "kg"
    assert by_name["Banana prata"]["preco"] == "R$ 4,49"
    cafe = by_name["Café Pilão 500g"]
    assert cafe["preco"] == "R$ 15,99" and cafe["preco_de"] == "R$ 19,90"


def test_preposition_inside_name_is_kept():
    text = "Suco de uva integral 1L 14,90\nPão de queijo de 19,90 por 15,90"
    names = [p["nome"] for p in bf.interpret_rules(text)["produtos"]]
    assert names == ["Suco de uva integral 1L", "Pão de queijo"]


def test_ean_is_separated_from_name():
    text = "7894900011517 Coca-Cola 2L 9,99"
    out = bf.normalize_briefing(bf.interpret_rules(text), [text])
    product = out["produtos"][0]
    assert product["ean"] == "7894900011517"
    assert product["nome"] == "Coca-Cola 2L"


def test_invented_price_from_ai_is_dropped():
    user_texts = ["Quero encarte com Arroz 5kg 24,90 e Feijão 1kg"]
    proposed = {
        "produtos": [
            {"nome": "Arroz 5kg", "preco": "24,90"},
            {"nome": "Feijão 1kg", "preco": "7,49"},  # inventado pela IA
        ]
    }
    out = bf.normalize_briefing(proposed, user_texts)
    prices = {p["nome"]: p["preco"] for p in out["produtos"]}
    assert prices == {"Arroz 5kg": "R$ 24,90", "Feijão 1kg": ""}
    assert any("Feijão" in p for p in out["pendencias"])
    ok, motivo = bf.ready_for_render(out)
    assert not ok and "Feijão" in motivo


def test_trusted_form_prices_and_invalid_old_price():
    out = bf.normalize_briefing(
        {"produtos": [{"nome": "Leite", "preco": "4.99", "preco_de": "3,00"}]}, trust_prices=True
    )
    assert out["produtos"][0]["preco"] == "R$ 4,99"
    # "de" menor que "por" não é oferta: descartado.
    assert out["produtos"][0]["preco_de"] == ""


def test_unowned_assets_are_removed():
    foreign = "11111111-1111-1111-1111-111111111111"
    out = bf.normalize_briefing(
        {"logo_asset_id": foreign, "produtos": [{"nome": "X", "preco": "1,00", "asset_id": foreign}]},
        trust_prices=True,
        owned_asset_ids=set(),
    )
    assert out["logo_asset_id"] is None
    assert out["produtos"][0]["asset_id"] is None


def test_scene_prompt_always_has_guardrails():
    prompt = bf.scene_prompt({"cenario": "mesa de madeira", "tema": "natal"})
    assert bf.SCENE_GUARDRAILS in prompt
    assert "christmas" in prompt


def test_price_parsing():
    assert bf.price_to_cents("1.234,56") == 123456
    assert bf.price_to_cents("R$ 9,9") == 990
    assert bf.price_to_cents("abc") is None
    assert bf.format_brl(123456) == "R$ 1.234,56"
