"""Compositor HTML/CSS/SVG livre: desenha os elementos resolvidos sobre o cenário, com tipografia expressiva e
integração fotográfica (sombra de contato, reflexo, luz da cena) sem alterar as embalagens reais.
Também embute a medição (data-k em cada elemento) usada pelo validador objetivo."""
import html
import math

from studio import htmlflyer

from . import efeitos, recursos


def _burst(points=18):
    pts = []
    for i in range(points * 2):
        r = 50 if i % 2 == 0 else 40
        a = math.pi * i / points
        pts.append(f"{50 + r * math.cos(a):.1f}% {50 + r * math.sin(a):.1f}%")
    return "polygon(" + ",".join(pts) + ")"


def _fam(key):
    if str(key).startswith("g:"):
        return key[2:]
    return recursos.FONTS.get(key, recursos.FONTS["anton"])[1]


def _text_fx(ef, col, c2):
    ext = ",".join(f"{i}px {i}px 0 {c2}" for i in range(1, 9))
    return {
        "neon": f"color:#fff8f0;text-shadow:0 0 6px {col},0 0 18px {col},0 0 42px {col};",
        "metal": f"background:linear-gradient(180deg,#fff 0%,{col} 45%,#4a3608 55%,{col} 80%,#fff 100%);-webkit-background-clip:text;"
                 "background-clip:text;color:transparent;filter:drop-shadow(0 6px 10px rgba(0,0,0,.6));",
        "3d": f"color:{col};text-shadow:{ext},12px 14px 18px rgba(0,0,0,.5);",
        "adesivo": f"color:{col};-webkit-text-stroke:calc(var(--hpx)*.09) {c2};paint-order:stroke fill;filter:drop-shadow(0 8px 10px rgba(0,0,0,.45));",
        "gradiente": f"background:linear-gradient(180deg,{col} 20%,{c2} 95%);-webkit-background-clip:text;background-clip:text;color:transparent;"
                     "filter:drop-shadow(0 5px 8px rgba(0,0,0,.55));",
        "contorno": f"color:transparent;-webkit-text-stroke:calc(var(--hpx)*.035) {col};text-shadow:0 0 18px {col}88;",
        "faixa": f"color:{col};background:{c2};padding:0 .25em;box-shadow:0 10px 24px rgba(0,0,0,.4);",
        "italico": f"color:{col};font-style:italic;text-shadow:-6px 6px 0 {c2},0 10px 20px rgba(0,0,0,.45);",
        "sombra": f"color:{col};text-shadow:0 6px 16px rgba(0,0,0,.6);",
    }.get(ef, f"color:{col};text-shadow:0 6px 16px rgba(0,0,0,.6);")


def _split_price(text):
    """'R$ 17,90' -> ('17', ',90', ''); 'R$ 1.800/mês' -> ('1.800', '', '/mês'); nunca inventa centavos."""
    import re  # noqa: PLC0415
    t = (text or "").replace("R$", "").strip()
    m = re.match(r"^([\d.]+)(,\d{1,2})?\s*(.*)$", t)
    if not m:
        return t, "", ""
    return m.group(1), m.group(2) or "", m.group(3).strip()


def _price_html(p, st, emphasis):
    whole, cents, suffix = _split_price(p.get("preco"))
    bg, fg = st["bg"], st["fg"]
    shape = {
        "faixa": f"background:linear-gradient(180deg,{bg},{_shade(bg, .82)});border-radius:22px;transform:skewX(-8deg);"
                 f"box-shadow:0 0 30px {bg}88,0 14px 0 {_shade(bg, .6)};",
        "etiqueta": f"background:{bg};border-radius:18px 80px 80px 18px;box-shadow:0 10px 0 {_shade(bg, .6)};",
        "circulo": f"background:{bg};border-radius:50%;aspect-ratio:1;box-shadow:0 10px 0 {_shade(bg, .6)};",
        "bloco": f"background:linear-gradient(135deg,{bg},{_shade(bg, .75)});border-radius:16px;box-shadow:0 12px 30px rgba(0,0,0,.45);",
        "explosao": "",
    }[st["estilo"]]
    burst = (f'<i style="position:absolute;left:50%;top:50%;height:{"150%" if emphasis else "135%"};aspect-ratio:1;transform:translate(-50%,-50%);'
             f'background:{bg};clip-path:{_burst()};z-index:0"></i>' if st["estilo"] == "explosao" else "")
    old = f'<div class="de">de {html.escape(p["preco_de"])}</div>' if p.get("preco_de") else ""
    offer = f'<div class="oferta">{html.escape(p["oferta"])}</div>' if p.get("oferta") else ""
    return (f'{burst}{old}<div class="preco{" pm" if emphasis else ""}" style="{shape}color:{fg};font-family:\'{_fam(st["fonte"])}\'">'
            f'<span class="rs">R$</span><span class="int">{html.escape(whole or "")}</span>'
            + (f'<span class="cts">{html.escape(cents)}</span>' if cents else "")
            + (f'<span class="suf">{html.escape(suffix)}</span>' if suffix else "") + f'</div>{offer}')


def _lx(plan, d):
    luz = plan.get("_luz")
    return round(-luz["dx"] * d, 1) if luz else 0


def _ly(plan, d):
    luz = plan.get("_luz")
    return round(max(0.35, -luz["dy"]) * d, 1) if luz else d  # sombra de produto sempre um pouco para baixo (apoio)


def _tone(plan, a):
    luz = plan.get("_luz")
    if not luz:
        return f"rgba(0,0,0,{a})"
    h = luz["tom"]
    return f"rgba({int(h[1:3], 16)},{int(h[3:5], 16)},{int(h[5:7], 16)},{a + 0.1})"


def _scrim(text_color):
    """Suporte visual suave SEM caixa: halo difuso que acompanha as letras (escuro atrás de letra clara, claro atrás
    de letra escura). Separa o texto do fundo sem placa nem retângulo."""
    if htmlflyer._luma(htmlflyer._hex(text_color, "#ffffff")) > 128:
        return "filter:drop-shadow(0 0 6px rgba(10,10,14,.95)) drop-shadow(0 0 16px rgba(10,10,14,.8)) drop-shadow(0 0 34px rgba(10,10,14,.6));"
    # halo claro mais contido (um brilho largo vira "nuvem" branca e parece remendo)
    return "filter:drop-shadow(0 0 3px rgba(255,255,255,.95)) drop-shadow(0 0 9px rgba(255,255,255,.6));"


def _shade(hx, f):
    h = htmlflyer._hex(hx, "#888888")
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(int(h[i:i + 2], 16) * f))) for i in (1, 3, 5))


def build(plan, solved, briefing, photos_uri, scene_uri, size, hide_text=False):
    """photos_uri: {indice: data-uri do recorte}. hide_text: versão sem textos (para medir o contraste real)."""
    W, H = size
    prods = briefing.get("produtos") or []
    luz = plan["cenario"]["luz"]
    dark_scene = plan.get("_cena_escura", True)
    used_fonts = {plan["titulo"]["fonte"], plan["preco"]["fonte"], plan["nomes"]["fonte"], "poppins", "anton"}
    if plan["chamada"]:
        used_fonts.add(plan["chamada"]["fonte"])
    out = []
    hidden = "visibility:hidden;" if hide_text else ""
    for e in sorted(solved["elementos"], key=lambda e: e["z"]):
        x, y, w, h = e["b"]
        key = f'{e["papel"]}:{e.get("produto", "")}'
        box = f"left:{x:.2f}%;top:{y:.2f}%;width:{w:.2f}%;height:{h:.2f}%;z-index:{e['z']};--hpx:{h * H / 100:.0f}px;"
        t = e["tipo"]
        if t == "texto":
            txt = {"titulo": plan["titulo"]["txt"], "loja": plan["loja"], "chamada": (plan["chamada"] or {}).get("txt", "")}[e["papel"]]
            st = plan["titulo"] if e["papel"] == "titulo" else e  # chamada/empresa: valores do elemento (o reparo de contraste atua nele)
            if e["papel"] == "titulo" and e.get("cor_forcada"):
                st = {**st, "cor": e["cor_forcada"]}  # cor da própria paleta escolhida pelo reparo de legibilidade
            font = st.get("fonte", "poppins")
            if recursos.FONTS.get(font, ("", "", "", False))[3]:
                txt = txt.upper()
            ef = e.get("ef_forcado") or st.get("ef", "sombra")  # o reparo pode trocar um efeito ilegível
            efx = None if e.get("ef_forcado") else st.get("efx")  # efeito descrito por propriedades (efeitos.py)
            weight = f"font-weight:{int(st['peso'])};" if st.get("peso") else ""
            ring = e.get("reforco")  # contorno de contraste posto pelo reparo de legibilidade
            if ef == "arco":
                inner = (f'<svg viewBox="0 0 1000 320" preserveAspectRatio="none" style="width:100%;height:100%;overflow:visible">'
                         f'<path id="arc{e["papel"]}" d="M40 300 Q500 -40 960 300" fill="none"/><text font-family="{_fam(font)}" '
                         f'font-size="170" fill="{st["cor"]}" stroke="{ring or st["cor2"]}" stroke-width="{24 if ring else 10}" '
                         f'paint-order="stroke" stroke-linejoin="round" text-anchor="middle">'
                         f'<textPath href="#arc{e["papel"]}" startOffset="50%" textLength="880" lengthAdjust="spacingAndGlyphs">{html.escape(txt)}'
                         f'</textPath></text></svg>')
                out.append(f'<div class="l" data-k="{key}" data-cor="{st["cor"]}" style="{box}{hidden}">{inner}</div>')
            else:
                plate = _scrim(st.get("cor") if e["papel"] != "titulo" else e.get("cor_forcada") or st["cor"]) if e.get("placa") else ""
                fx = efeitos.css(efx, e["b"][3] * size[1] / 100, plan.get("_luz")) if efx else _text_fx(ef, st["cor"], st["cor2"])
                fx += weight
                if ring:
                    fx += f"-webkit-text-stroke:calc(var(--hpx)*.05) {ring};paint-order:stroke fill;"
                out.append(f'<div class="l txt fit" data-k="{key}" data-cor="{st["cor"]}" style="{box}{hidden}font-family:\'{_fam(font)}\';'
                           f'{fx}{plate}font-size:calc(var(--hpx)*.86)">{html.escape(txt)}</div>')
        elif t == "produto" and e.get("cartao"):
            src = photos_uri[e["produto"]]
            out.append(f'<div class="l prod cartao" data-k="{key}" style="{box}"><img class="foto" src="{src}"></div>')
        elif t == "produto":
            src = photos_uri[e["produto"]]
            # reflexo só em superfície brilhante (pedestal em cena escura); madeira/tecido não refletem
            refl = f'<img class="refl" src="{src}">' if dark_scene and plan["cenario"]["superficie"] == "pedestal" else ""
            out.append(f'<div class="l prod" data-k="{key}" style="{box}"><div class="contato"></div>{refl}<img class="foto" src="{src}">'
                       f'<div class="luz" style="background:linear-gradient(180deg,{luz}cc 0%,transparent 55%,rgba(0,0,0,.35) 100%);'
                       f'-webkit-mask-image:url({src});mask-image:url({src})"></div></div>')
        elif t == "preco":
            p = prods[e["produto"]]
            main = e["papel"] == "preco_principal"
            out.append(f'<div class="l precobox" data-k="{key}" style="{box}{hidden}">{_price_html(p, plan["preco"], main)}</div>')
        elif t == "nome":
            p = prods[e["produto"]]
            col = e.get("cor") or plan["nomes"]["cor"]
            plate = _scrim(col) if e.get("placa") else ""
            big = 0.7 if e.get("destaque") else 0.6
            out.append(f'<div class="l nome fit" data-k="{key}" data-cor="{col}" style="{box}{hidden}{plate}color:{col};'
                       f'font-family:\'{_fam(plan["nomes"]["fonte"])}\';font-size:calc(var(--hpx)*{big});'
                       f'text-shadow:{"0 2px 10px rgba(0,0,0,.85)" if htmlflyer._luma(col) > 140 else "0 2px 8px rgba(255,255,255,.7)"}">'
                       f'{html.escape(p["nome"])}</div>')
        elif t == "lista":
            rows = "".join(
                f'<div class="row"><span class="ln fit1">{html.escape(prods[i]["nome"])}</span>'
                f'<span class="lp" style="background:{plan["preco"]["bg"]};color:{plan["preco"]["fg"]};font-family:\'{_fam(plan["preco"]["fonte"])}\'">'
                f'{html.escape(prods[i].get("preco") or "")}</span></div>' for i in e["itens"])
            out.append(f'<div class="l lista" data-k="{key}" style="{box}{hidden}">{rows}</div>')
        elif t == "selo":
            shape = {"estrela": f"clip-path:{_burst(16)};", "circulo": "border-radius:50%;",
                     "fita": "clip-path:polygon(0 0,100% 0,94% 50%,100% 100%,0 100%,6% 50%);",
                     "etiqueta": "border-radius:14px;transform:skewX(-10deg);",
                     "carimbo": f"border-radius:50%;border:5px dashed {e['cor2']};outline:3px solid {e['cor']};outline-offset:4px;",
                     "balao": "border-radius:22px;clip-path:polygon(0 0,100% 0,100% 74%,46% 74%,26% 100%,30% 74%,0 74%);",
                     "hexagono": "clip-path:polygon(25% 0,75% 0,100% 50%,75% 100%,25% 100%,0 50%);",
                     "escudo": "clip-path:polygon(0 0,100% 0,100% 58%,50% 100%,0 58%);border-radius:10px;"}.get(e["forma"], "border-radius:50%;")
            out.append(f'<div class="l selo" data-k="{key}" style="{box}transform:rotate({e.get("giro", -8)}deg);filter:drop-shadow(0 10px 18px rgba(0,0,0,.5)) '
                       f'drop-shadow(0 0 16px {e["cor"]}88)"><div style="position:absolute;inset:0;background:radial-gradient(circle at 35% 30%,'
                       f'#fff8 0,transparent 45%),{e["cor"]};{shape}"></div><div class="fit" style="position:relative;width:70%;height:58%;'
                       f'display:flex;align-items:center;justify-content:center;{hidden}font-family:\'{_fam(plan["preco"]["fonte"])}\';color:{e["cor2"]};text-align:center;'
                       f'line-height:.92;font-size:calc(var(--hpx)*.3);text-transform:uppercase">{html.escape(e["txt"])}</div></div>')
            used_fonts.add("kanit")
        elif t == "icone":
            out.append(f'<div class="l icone" data-k="icone:{e["nome"]}" style="{box}filter:drop-shadow(0 0 10px {e["cor"]}aa)">'
                       f'{recursos.icon_svg(e["nome"], e["cor"])}</div>')
        elif t == "info":
            lines = "".join(f"<div>{html.escape(s)}</div>" for s in e["linhas"])
            out.append(f'<div class="l info fit" data-k="{key}" style="{box}{hidden}">{lines}</div>')
    css = f"""{recursos.font_css(used_fonts)}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{width:{W}px;height:{H}px;overflow:hidden;position:relative;background:#111 url({scene_uri}) center/cover}}
.l{{position:absolute;display:flex;align-items:center;justify-content:center}}
.txt{{line-height:.95;text-align:center;white-space:normal}}
.prod{{align-items:flex-end}}
.cartao{{align-items:center}}
.cartao .foto{{width:100%;height:100%;object-fit:cover;border-radius:22px;border:7px solid #fff;box-sizing:border-box;
  box-shadow:0 22px 46px rgba(0,0,0,.45),0 4px 10px rgba(0,0,0,.25);transform:rotate(-2deg)}}
.prod .foto{{position:relative;width:100%;height:100%;object-fit:contain;object-position:bottom;z-index:2;
  filter:drop-shadow({_lx(plan, 18)}px {_ly(plan, 18)}px 18px {_tone(plan, .45)}) drop-shadow(0 0 24px {luz}55)}}
.prod .luz{{position:absolute;inset:0;z-index:3;mix-blend-mode:soft-light;opacity:.5;-webkit-mask-size:contain;mask-size:contain;
  -webkit-mask-repeat:no-repeat;mask-repeat:no-repeat;-webkit-mask-position:bottom;mask-position:bottom}}
.prod .contato{{position:absolute;left:6%;right:6%;bottom:-3%;height:8%;z-index:1;border-radius:50%;
  background:radial-gradient(ellipse at center,rgba(0,0,0,.8) 0%,rgba(0,0,0,.35) 45%,transparent 72%);filter:blur(4px)}}
.prod .refl{{position:absolute;left:0;top:100%;width:100%;height:40%;object-fit:cover;object-position:bottom;transform:scaleY(-1);z-index:1;
  opacity:.25;-webkit-mask-image:linear-gradient(to top,#000 0%,transparent 75%);mask-image:linear-gradient(to top,#000 0%,transparent 75%)}}
.nome{{text-align:center;line-height:1.05}}
.precobox{{flex-direction:column}}
.precobox>.preco,.precobox>.de,.precobox>.oferta{{position:relative;z-index:1}}
.preco{{position:relative;isolation:isolate;display:flex;align-items:flex-start;justify-content:center;line-height:1;padding:.06em .32em;white-space:nowrap;
  font-size:calc(var(--hpx)*.78)}}
.preco.pm{{font-size:calc(var(--hpx)*.84);width:100%;flex:1 1 auto;min-height:0;align-items:center;overflow:hidden}}
.preco.pm .rs{{margin-top:0;align-self:center}}
.preco.pm .cts{{align-self:flex-start;margin-top:.12em}}
.preco .rs{{font-size:.3em;margin:.55em .12em 0 0}}
.preco .cts{{font-size:.45em;margin-top:.08em}}
.preco .suf{{font-size:.32em;align-self:flex-end;margin:0 0 .12em .12em}}
.de{{font-family:'PoppinsX';font-size:calc(var(--hpx)*.17);color:#fff;text-decoration:line-through;text-shadow:0 2px 6px #000}}
.oferta{{font-family:'PoppinsX';font-size:calc(var(--hpx)*.15);background:#ffd400;color:#111;padding:2px 12px;border-radius:99px;margin-top:4px}}
.lista{{flex-direction:column;justify-content:space-evenly;background:rgba(255,255,255,.93);border-radius:22px;padding:1.2% 3%;
  box-shadow:0 18px 40px rgba(0,0,0,.35)}}
.lista .row{{display:flex;width:100%;align-items:center;justify-content:space-between;gap:14px;height:calc(100%/{max(1, max([len(e.get("itens", [])) for e in solved["elementos"] if e["tipo"] == "lista"] or [1]))})}}
.lista .ln{{font-family:'PoppinsX';color:#1e1e22;font-size:30px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.lista .lp{{font-size:40px;padding:2px 16px;border-radius:12px;white-space:nowrap}}
.info{{flex-direction:column;font-family:'PoppinsX';color:#fff;font-size:calc(var(--hpx)*.2);text-shadow:0 2px 8px #000;text-align:center}}
.icone svg{{width:100%;height:100%}}"""
    js = QA_JS
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head><body>{"".join(out)}'
            f'<script>{js}</script></body></html>')


# Ajuste de fonte (nunca corta) + medição para o validador: retângulos, texto cortado, visibilidade, fonte final.
QA_JS = """
function natw(t,fs){const c=document.createElement('canvas').getContext('2d');c.font=fs+'px '+getComputedStyle(t).fontFamily;
 return c.measureText(t.textContent).width||1;}
function over(e){if(!e.textContent.trim())return false;const rg=document.createRange();rg.selectNodeContents(e);
 const g=rg.getBoundingClientRect(),b=e.getBoundingClientRect();return g.width>b.width+2||g.height>b.height*1.08;}
function fit(){document.querySelectorAll('svg text').forEach(t=>{if(!t.querySelector('textPath'))return;
  t.setAttribute('font-size',Math.min(200,170*880/natw(t,170)).toFixed(1));});
 document.querySelectorAll('.fit').forEach(e=>{let s=parseFloat(getComputedStyle(e).fontSize);
  while((e.scrollWidth>e.clientWidth+2||e.scrollHeight>e.clientHeight*1.06||over(e))&&s>10){s-=1;e.style.fontSize=s+'px';}});
 document.querySelectorAll('.preco').forEach(e=>{let s=parseFloat(getComputedStyle(e).fontSize);const b=e.parentElement;
  if(e.classList.contains('pm')){while((e.scrollWidth>e.clientWidth+1||e.scrollHeight>e.clientHeight+1)&&s>14){s-=2;e.style.fontSize=s+'px';}return;}
  while(e.getBoundingClientRect().width>b.clientWidth*1.04&&s>14){s-=2;e.style.fontSize=s+'px';}});
 document.querySelectorAll('.lista .ln').forEach(e=>{let s=parseFloat(getComputedStyle(e).fontSize);while(e.scrollWidth>e.clientWidth+1&&s>14){s-=1;e.style.fontSize=s+'px';}});}
function qa(){const pg=document.body.getBoundingClientRect();const BW=pg.width,BH=pg.height;const out=[];
 for(const e of document.querySelectorAll('[data-k]')){const r=e.getBoundingClientRect();const k=e.dataset.k;const t=k.split(':')[0];
  let vis=e.querySelector('.precobox>i')||e.querySelector('.preco')||e;let rr=vis.getBoundingClientRect();
  const clip=e.style.backgroundClip==='text'||e.style.webkitBackgroundClip==='text';
  if((e.classList.contains('txt')||e.classList.contains('nome'))&&(!e.style.background||clip)&&e.textContent.trim()){
   const rg=document.createRange();rg.selectNodeContents(e);const g=rg.getBoundingClientRect();if(g.width>0)rr=g;}
  const im=e.classList.contains('prod')&&!e.classList.contains('cartao')?e.querySelector('.foto'):null;
  if(im&&im.naturalWidth){const s=Math.min(r.width/im.naturalWidth,r.height/im.naturalHeight);const iw=im.naturalWidth*s,ih=im.naturalHeight*s;
   rr={left:r.left+(r.width-iw)/2,top:r.bottom-ih,width:iw,height:ih,right:r.left+(r.width+iw)/2,bottom:r.bottom};}
  const it={k,x:rr.left,y:rr.top,w:rr.width,h:rr.height,bx:r.left,by:r.top,bw:r.width,bh:r.height};
  const f=e.querySelector('.preco')||e;it.fonte=parseFloat(getComputedStyle(f).fontSize);it.arco=!!e.querySelector('textPath');
  if(it.arco){const tx=e.querySelector('text'),sv=e.querySelector('svg').getBoundingClientRect();
   const fs=parseFloat(tx.getAttribute('font-size'));const sy=sv.height/320;
   it.arco_escala=(880/natw(tx,fs))*((sv.width/1000)/sy);it.fonte=fs*sy;}
  if(e.classList.contains('fit'))it.corta=e.scrollWidth>e.clientWidth+3||e.scrollHeight>e.clientHeight*1.12||(clip&&over(e));
  let v=0,tot=0;for(let i=1;i<=4;i++)for(let j=1;j<=3;j++){const px=rr.left+rr.width*i/5,py=rr.top+rr.height*j/4;tot++;
   if(px<0||py<0||px>BW||py>BH)continue;const top=document.elementFromPoint(px,py);if(top&&(top===e||e.contains(top)))v++;}
  it.visivel=tot?v/tot:0;it.fora=rr.left<-2||rr.top<-2||rr.right>BW+2||rr.bottom>BH+2;out.push(it);}
 const pre=document.createElement('pre');pre.id='qa';pre.style.display='none';pre.textContent=JSON.stringify(out);document.body.appendChild(pre);}
document.fonts.ready.then(()=>{fit();requestAnimationFrame(()=>{fit();qa();document.body.dataset.ready=1;});});
"""
