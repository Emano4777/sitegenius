"""Textos na imagem: extração literal do pedido, arte sem letras, composição tipográfica e validação."""
import io

from PIL import Image, ImageDraw

from studio import creative, textplan, typography
from tests.test_creative import render_all, send
from tests.test_studio_api import new_conversation


def art(size=(880, 880), busy=False):
    img = Image.new("RGB", size, (30, 60, 110))
    draw = ImageDraw.Draw(img)
    for y in range(size[1]):
        draw.line([(0, y), (size[0], y)], fill=(30 + y * 120 // size[1], 60, 110))
    if busy:
        for x in range(0, size[0], 9):
            draw.line([(x, 0), (x, size[1])], fill=(255, 255, 255) if x % 18 else (0, 0, 0), width=3)
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


# ------------------------------------------------------------ Etapa A: interpretação


def test_unquoted_name_is_extracted_exactly():
    plan = textplan.plan_from_request("Crie uma arte futurista com o nome Emanuel Tecnologia")
    assert textplan.plan_texts(plan) == ["Emanuel Tecnologia"]
    assert plan["estilo"] == "futurista"


def test_title_subtitle_price_and_positions():
    msg = ('Anúncio de fone, título "Som Sem Limites" no topo, subtítulo "Graves potentes e 30 horas de bateria", '
           'de R$ 299,90 por R$ 199,90 no canto inferior direito, botão "Compre agora"')
    plan = textplan.plan_from_request(msg)
    roles = {b["texto"]: b["papel"] for b in plan["blocos"]}
    assert roles == {"Som Sem Limites": "titulo", "Graves potentes e 30 horas de bateria": "subtitulo",
                     "R$ 299,90": "preco_de", "R$ 199,90": "preco", "Compre agora": "cta"}
    pos = {b["texto"]: b["posicao"] for b in plan["blocos"]}
    assert pos["Som Sem Limites"] == "topo" and pos["R$ 199,90"] == "base_direita"


def test_corner_position_and_no_text_request():
    plan = textplan.plan_from_request('praia ao entardecer com o texto "Verão 2026" no canto superior direito')
    assert plan["blocos"] == [{"texto": "Verão 2026", "papel": "titulo", "posicao": "topo_direita"}]
    assert textplan.plan_from_request("paisagem de montanha nevada ao amanhecer") is None


def test_model_suggestions_must_be_literal():
    msg = "logo para a padaria Pão Dourado, moderno"
    plan = textplan.plan_from_request(msg, model_blocks=[
        {"texto": "Pão Dourado", "papel": "marca", "posicao": "centro"},
        {"texto": "Pao Dourado Ltda", "papel": "subtitulo"},  # não está no pedido: descartado
        {"texto": "Desde 1990", "papel": "subtitulo"},  # inventado: descartado
    ])
    assert textplan.plan_texts(plan) == ["Pão Dourado"]


def test_art_prompt_never_contains_requested_text_or_prices():
    spec = creative.build_spec(
        prompt_original='anúncio de fone, título "Som Sem Limites", preço R$ 199,90',
        prompt_visual='advertisement poster of wireless headphones with the title "Som Sem Limites" and price tag',
        style="produto", aspect="4:5", quality="qualidade", user_texts=[])
    prompt = spec["prompt"]
    assert "Som Sem Limites" not in prompt and "199" not in prompt and "poster" not in prompt
    assert "no text" in prompt and "negative space" in prompt and "no people" in prompt
    assert textplan.plan_texts(spec["text_plan"]) == ["Som Sem Limites", "R$ 199,90"]


def test_people_only_when_requested():
    spec = creative.build_spec(prompt_original="retrato de uma mulher sorrindo", prompt_visual="portrait of a woman",
                               style="foto", aspect="4:5", quality="qualidade", user_texts=[])
    assert "no people" not in spec["prompt"]


def test_single_ad_with_price_is_free_image_but_product_list_is_flyer():
    assert creative.classify('Anúncio de fone, preço R$ 199,90') == "free_image"
    assert creative.classify("Story de bebidas: Guaraná 2L 7,99") == "ad_composite"
    assert creative.classify("arte com R$ 1,99 R$ 2,99 R$ 3,99") == "ad_composite"


# ------------------------------------------------------------ Etapas C e D: composição e validação


def _checks(report):
    return {c["nome"]: c["ok"] for c in report["verificacoes"]}


def test_compose_keeps_exact_text_and_passes_validation():
    plan = textplan.plan_from_request(
        'post quadrado: título "Emanuel Tecnologia", subtítulo "Soluções em informática", preço R$ 1.299,90')
    data, report = typography.compose(art((880, 880)), plan)
    assert report["ok"], report
    drawn = next(c for c in report["verificacoes"] if c["nome"] == "textos_completos_e_identicos")
    assert sorted(drawn["desenhado"]) == sorted(["Emanuel Tecnologia", "Soluções em informática", "R$ 1.299,90"])
    assert Image.open(io.BytesIO(data)).size == (1080, 1080)  # entregue em tamanho de rede social


def test_busy_background_gets_contrast_treatment():
    plan = textplan.plan_from_request('arte com "Promoção de Inverno" no centro')
    _, report = typography.compose(art((768, 960), busy=True), plan)
    assert _checks(report)["contraste"] and report["grupos"][0]["tratamento"] in ("veu", "painel", "halo")


def test_long_text_fits_inside_safe_area_in_all_formats():
    text = "Grande liquidação de verão com descontos imperdíveis em toda a loja"
    for size in [(656, 1168), (1168, 656), (880, 880), (768, 960)]:
        plan = textplan.plan_from_request(f'arte com "{text}" e "Só neste fim de semana" no rodapé')
        _, report = typography.compose(art(size), plan)
        checks = _checks(report)
        assert checks["dentro_da_area"] and checks["textos_completos_e_identicos"] and checks["sem_sobreposicao"], size


def test_caps_only_fonts_never_change_case():
    plan = {"blocos": [{"texto": "Emanuel Tecnologia", "papel": "titulo", "posicao": "centro"}], "estilo": "impacto",
            "efeito": "auto", "cor": None}
    family, _ = typography.choose_font(typography.THEMES["impacto"], "titulo", "Emanuel Tecnologia")
    assert not typography.FAMILIES[family].get("caps")
    _, report = typography.compose(art(), plan)
    assert report["ok"]


# ------------------------------------------------------------ fluxo completo (Studio + agente do Acer)


def test_full_flow_applies_texts_after_generation_and_edits_use_clean_art(user, render_worker, tmp_path):
    conv = new_conversation(user)
    data = send(user, conv, 'arte quadrada com o nome Emanuel Tecnologia, estilo tecnológico')
    job = data["jobs"][0]
    comfy = render_all(render_worker, tmp_path)
    prompt = comfy.calls[0]["5"]["inputs"]["text"]
    assert "Emanuel" not in prompt and "no text" in prompt

    done = user.get(f"/studio/api/jobs/{job['id']}").get_json()["job"]
    assert done["status"] == "done"
    final = Image.open(io.BytesIO(user.get(f"/studio/api/assets/{done['resultado_asset_id']}").data))
    assert final.size == (1080, 1080)

    # Ajuste só de texto: refeito na hora sobre a mesma arte, sem ir para o gerador.
    data = send(user, conv, 'troque o nome para "Emanuel Tech" e coloque no topo')
    edit = data["jobs"][0]
    assert edit["tipo"] == "text_edit" and edit["status"] == "done"
    assert render_worker.claim() is None

    # Ajuste da cena: usa a arte limpa (sem letras) como base.
    send(user, conv, "deixe o fundo mais escuro")
    comfy = render_all(render_worker, tmp_path)
    assert comfy.calls, "ajuste de cena precisa gerar de novo"
    from studio.db import get_conn
    with user.client.application.app_context():
        row = get_conn().one("SELECT payload FROM sg_jobs WHERE conversation_id = ? AND gen_type = 'free_image' "
                             "ORDER BY created_at DESC LIMIT 1", (conv,))
    import json
    payload = json.loads(row["payload"])
    with user.client.application.app_context():
        kind = get_conn().one("SELECT kind FROM sg_assets WHERE id = ?", (payload["init_asset_id"],))["kind"]
    assert kind == "art"
    assert textplan.plan_texts(payload["spec"]["text_plan"]) == ["Emanuel Tech"]
