import io

import pytest
from PIL import Image

from studio import briefing as bf
from studio.compose import compose_flyer
from tests.conftest import image_bytes


def make_briefing(fmt, count):
    products = [
        {"nome": f"Produto número {i} com nome bem comprido", "preco": f"R$ {i + 1},99", "preco_de": "R$ 99,90" if i % 2 else "", "unidade": "kg" if i % 3 == 0 else "", "asset_id": f"a{i}"}
        for i in range(count)
    ]
    return {**bf.empty_briefing(), "formato": fmt, "loja": "Mercado Teste", "validade": "01/11 a 05/11", "produtos": products}


@pytest.mark.parametrize("fmt", list(bf.FORMATS))
@pytest.mark.parametrize("count", [1, 4, 9, 24])
def test_compose_sizes(fmt, count):
    brief = make_briefing(fmt, count)
    assets = {f"a{i}": image_bytes(size=(300, 400), color=(i * 10 % 255, 120, 200)) for i in range(count)}
    out = compose_flyer(brief, scene_bytes=image_bytes(size=(768, 1024)), assets=assets,
                        logo_bytes=image_bytes(size=(400, 150)), licenses=["Open Food Facts contributors (CC BY-SA 3.0)"])
    img = Image.open(io.BytesIO(out))
    assert img.format == "JPEG"
    assert img.size == (bf.FORMATS[fmt]["largura"], bf.FORMATS[fmt]["altura"])


def test_compose_without_scene_or_photos():
    out = compose_flyer(make_briefing("feed_4x5", 3))
    assert Image.open(io.BytesIO(out)).size == (1080, 1350)
