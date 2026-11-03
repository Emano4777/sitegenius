"""Composição livre (experimental, ainda não ligada à produção).

Fluxo genérico para qualquer briefing (com 1 produto, vários ou nenhum):
  plano semântico (diretor criativo) -> prompt do cenário com zonas reservadas -> cenário (IA) -> conferência do
  cenário (OCR + zonas) -> resolvedor de geometria com hierarquia -> HTML/CSS/SVG -> validação objetiva no Chromium
  -> reparos que preservam a hierarquia.
O modelo decide intenção, prioridades, estilo e relações; nunca coordenadas. Nada é específico de um exemplo.
"""
