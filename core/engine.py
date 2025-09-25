import pandas as pd
import yaml
from . import rules
from .models import Ordem

def aplicar_ordens_no_df(df: pd.DataFrame, fundo: str, ordens: list[Ordem]) -> pd.DataFrame:
    if df is None or df.empty:
        raise ValueError("DataFrame da carteira está vazio.")
    for col in ["nome do fundo", "ativo", "valor"]:
        if col not in df.columns:
            raise ValueError(f"Coluna obrigatória ausente: {col}")

    dfp = df.copy()
    dfp["valor"] = dfp["valor"].astype(float)

    for o in ordens:
        if o.fundo != fundo:
            raise ValueError(f"Ordem com fundo diferente do selecionado: {o.fundo} != {fundo}")
        mask = (dfp["nome do fundo"] == fundo) & (dfp["ativo"] == o.ativo)
        if not mask.any():
            raise ValueError(f"Ativo '{o.ativo}' não encontrado no fundo '{fundo}'.")
        delta = float(o.quantidade) * float(o.preco)
        if o.tipo == "venda":
            delta = -delta
        dfp.loc[mask, "valor"] = dfp.loc[mask, "valor"].astype(float) + delta

    return dfp

def carregar_regras(yaml_path: str) -> dict:
    with open(yaml_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

RULES_REGISTRY = {
    "enquadramento_cvm": getattr(rules, "enquadramento_cvm", None),
    "enquadramento_tributario": getattr(rules, "enquadramento_tributario", None),
    "prazo_medio": getattr(rules, "prazo_medio", None),
}

def aplicar_regras(df: pd.DataFrame, ordem: Ordem, config: dict) -> list:
    resultados = []
    limits = (config or {}).get("limits", {})
    for nome, fn in RULES_REGISTRY.items():
        if fn is None:
            continue
        lim = limits.get(nome, {})
        try:
            res = fn(df, ordem, lim)
            resultados.append(res)
        except Exception as e:
            from .models import RegraResultado
            resultados.append(
                RegraResultado(
                    regra=nome,
                    passou=False,
                    valor_atual=0.0,
                    valor_proposto=0.0,
                    limite=0.0,
                    mensagem=f"Erro ao executar a regra: {e}",
                )
            )
    return resultados