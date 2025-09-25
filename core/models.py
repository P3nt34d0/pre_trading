from dataclasses import dataclass
from typing import Optional

@dataclass
class Ordem:
    """
    Representa uma única ordem de compra/venda usada para simular impacto
    nas regras de pré-trading.

    Atributos
    ---------
    fundo : str
        Nome do fundo (deve existir na coluna 'nome do fundo' da carteira).
    ativo : str
        Identificador do ativo (coluna 'ativo').
    quantidade : float
        Quantidade (sempre positiva; o sinal é dado por 'tipo').
    preco : float
        Preço unitário.
    tipo : str
        'compra' ou 'venda' (lowercase).
    """
    fundo: str
    ativo: str
    quantidade: float
    preco: float
    tipo: str  # "compra" | "venda"

    def __post_init__(self):
        self.fundo = str(self.fundo).strip()
        self.ativo = str(self.ativo).strip()
        self.tipo = str(self.tipo).strip().lower()
        if self.tipo not in {"compra", "venda"}:
            raise ValueError("tipo deve ser 'compra' ou 'venda'")
        try:
            self.quantidade = float(self.quantidade)
            self.preco = float(self.preco)
        except Exception as e:
            raise ValueError(f"quantidade/preco inválidos: {e}")
        if self.quantidade < 0 or self.preco < 0:
            raise ValueError("quantidade e preco devem ser não-negativos")

@dataclass
class RegraResultado:
    """
    Resultado da avaliação de uma regra específica.
    """
    regra: str
    passou: bool
    valor_atual: float
    valor_proposto: float
    limite: float
    mensagem: Optional[str] = ""

    def as_dict(self) -> dict:
        return {
            "regra": self.regra,
            "passou": self.passou,
            "valor_atual": self.valor_atual,
            "valor_proposto": self.valor_proposto,
            "limite": self.limite,
            "mensagem": self.mensagem or "",
        }

__all__ = ["Ordem", "RegraResultado"]