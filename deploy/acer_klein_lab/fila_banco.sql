-- Fila isolada do laboratório Klein (NÃO aplicado; só com autorização).
-- Schema próprio e papel que só enxerga esta tabela: nada do Studio de produção nem do dns-ecommerce fica acessível.
CREATE SCHEMA IF NOT EXISTS sitegenius_lab;
CREATE TABLE IF NOT EXISTS sitegenius_lab.sg_lab_jobs (
    id text PRIMARY KEY,
    payload jsonb NOT NULL,
    status text NOT NULL DEFAULT 'fila' CHECK (status IN ('fila', 'rodando', 'pronto', 'erro')),
    result jsonb,
    imagem bytea,
    ampliada bytea,
    criado timestamptz NOT NULL DEFAULT now(),
    atualizado timestamptz NOT NULL DEFAULT now()
);
-- papel do executor do Acer (senha gerada na hora da criação, nunca versionada)
-- CREATE ROLE sitegenius_lab_worker LOGIN PASSWORD '<gerada>';
-- GRANT USAGE ON SCHEMA sitegenius_lab TO sitegenius_lab_worker;
-- GRANT SELECT, UPDATE ON sitegenius_lab.sg_lab_jobs TO sitegenius_lab_worker;
-- REVOKE ALL ON SCHEMA public FROM sitegenius_lab_worker;
