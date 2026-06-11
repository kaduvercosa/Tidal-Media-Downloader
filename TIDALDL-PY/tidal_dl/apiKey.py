#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
apiKey.py  —  stub de compatibilidade.

O tidal-dl original usava clientId/clientSecret hardcoded de apps
de terceiros (Fire TV, Android Auto) que foram revogados pelo Tidal
em março/2026. Agora as credenciais são gerenciadas internamente
pelo tidalapi. Estas funções existem apenas para não quebrar o
restante do código que as importa.
"""


def getNum() -> int:
    return 1


def getItem(index: int = 0) -> dict:
    return {
        'platform': 'tidalapi (gerenciado automaticamente)',
        'formats':  'Normal / High / HiFi / Master / Max',
        'clientId':     '',
        'clientSecret': '',
        'valid': 'True',
    }


def getItems() -> list:
    return [getItem(0)]


def isItemValid(index: int = 0) -> bool:
    """Sempre True — tidalapi gerencia suas próprias credenciais."""
    return True


def getLimitIndexs() -> list:
    return ['0']


def getVersion() -> str:
    return 'tidalapi-managed'