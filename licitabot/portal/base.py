"""Exceções e tipos comuns aos conectores de portal."""

from __future__ import annotations


class PortalError(RuntimeError):
    """Erro genérico de automação do portal (com screenshot já salvo, quando possível)."""


class SessaoExpirada(PortalError):
    """Sessão gov.br/portal expirada: o humano precisa rodar `licitabot portal login`."""


class SeletorNaoEncontrado(PortalError):
    """Nenhum seletor (primary/fallbacks) resolveu: a interface do portal mudou."""


class DivergenciaTela(PortalError):
    """O que está na tela não bate com o snapshot aprovado. Nunca enviar."""
