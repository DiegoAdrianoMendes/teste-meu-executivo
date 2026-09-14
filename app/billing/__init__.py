"""Billing da PLATAFORMA (assinatura do SaaS Meu Executivo).

Suporta provedores globais Stripe, Asaas e Mercado Pago. É deliberadamente
distinto de `app/payments`: ali o Asaas pertence a cada escritório e cobra os
clientes daquele workspace; aqui o pagador é o próprio workspace, pagando a
mensalidade da plataforma.
"""
from __future__ import annotations
