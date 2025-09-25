import pandas as pd
import io
from typing import Optional

# ----------------- Mapeamentos de nomes de colunas -----------------
_COLMAP = {
    "nome do fundo": ["nome do fundo", "nome_fundo", "fundo", "nome"],
    "tipo do fundo": ["tipo do fundo", "tipo", "tipofundo", "tipo_fundo", "tipofundoinvest"],
    "ativo": ["ativo", "id_ativo", "idativo", "ATIVO"],
    "valor": ["valor", "pl parcela", "valor(r$)", "valor r$"],
    "liquidez": ["liquidez", "liquidezativo", "liquidezativoinvest", "liquidez_ativo_invest"],
    "quantidade": ["qtde", "quantidade", "qtd", "qde"],
    "preco": ["pu/cota", "pu", "preco", "preço", "preco unitario", "preco_unitario"],
    "categoria": ["categoria"],
    "categoria 2": ["categoria 2"],
    "categoria comitê": ["categoria comitê", "categoria comite"],
    "tipo de ativo": ["tipo de ativo", "tipo ativo", "tipo_ativo", "tipoativo"],
    "prazo_dias": ["prazo_dias", "prazo", "duracao", "duration_days"],
    "cnpj": [
        "cnpj", "cnpj do ativo", "cnpj do fundo", "cnpjfundo", "cnpjfundo invest",
        "cnpjfundoinvest", "cnpjfundoinvestimento", "cnpj_fundo_invest",
        "cnpjfundoinv", "cnpjfundoinvest"
    ],
    "database": ["database", "data base", "data_base", "dtbase", "data da base", "databasefund"],
}

def _find_col(df_cols: pd.Index, targets: list[str]) -> Optional[str]:
    s = {c.strip().lower(): c for c in df_cols}
    for t in targets:
        key = t.strip().lower()
        if key in s:
            return s[key]
    return None

def _first_existing(df: pd.DataFrame, keys: list[str]) -> Optional[str]:
    return _find_col(df.columns, keys)

# ----------------- Carregador principal -----------------
def carregar_carteira(file) -> pd.DataFrame:
    """
    Lê o Excel, normaliza nomes/colunas, faz coerções de tipos e
    mantém somente a ÚLTIMA 'database' por fundo.
    """
    # leitura direta (sem copiar bytes grandes)
    try:
        df = pd.read_excel(file, sheet_name="Base", engine="openpyxl")
    except Exception:
        df = pd.read_excel(file, engine="openpyxl")

    df.columns = df.columns.str.strip().str.lower()

    col_nome_fundo = _first_existing(df, _COLMAP["nome do fundo"]) or "nome do fundo"
    col_tipo_fundo = _first_existing(df, _COLMAP["tipo do fundo"]) or "tipo do fundo"
    col_ativo      = _first_existing(df, _COLMAP["ativo"]) or "ativo"
    col_valor      = _first_existing(df, _COLMAP["valor"]) or "valor"
    col_liq        = _first_existing(df, _COLMAP["liquidez"]) or "liquidez"
    col_qtde       = _first_existing(df, _COLMAP["quantidade"])
    col_preco      = _first_existing(df, _COLMAP["preco"])
    col_cat        = _first_existing(df, _COLMAP["categoria"]) or "categoria"
    col_cat2       = _first_existing(df, _COLMAP["categoria 2"]) or "categoria 2"
    col_catc       = _first_existing(df, _COLMAP["categoria comitê"]) or "categoria comitê"
    col_tipo_ativo = _first_existing(df, _COLMAP["tipo de ativo"]) or "tipo de ativo"
    col_prazo      = _first_existing(df, _COLMAP["prazo_dias"]) or "prazo_dias"
    col_cnpj       = _first_existing(df, _COLMAP["cnpj"]) or "cnpj"
    col_db         = _first_existing(df, _COLMAP["database"]) or "database"

    # Cria colunas ausentes com default
    for c, default in [
        (col_nome_fundo, ""), (col_tipo_fundo, ""), (col_ativo, ""), (col_valor, 0.0),
        (col_liq, ""), (col_cat, ""), (col_cat2, ""), (col_catc, ""), (col_tipo_ativo, ""),
        (col_prazo, None), (col_cnpj, ""), (col_db, None)
    ]:
        if c not in df.columns:
            df[c] = default

    # Renomeia para padrão interno
    ren = {
        col_nome_fundo: "nome do fundo",
        col_tipo_fundo: "tipo do fundo",
        col_ativo: "ativo",
        col_valor: "valor",
        col_liq: "liquidez",
        col_cat: "categoria",
        col_cat2: "categoria 2",
        col_catc: "categoria comitê",
        col_tipo_ativo: "tipo de ativo",
        col_prazo: "prazo_dias",
        col_cnpj: "cnpj",
        col_db: "database",
    }
    if col_qtde: ren[col_qtde] = "quantidade"
    if col_preco: ren[col_preco] = "preco"
    df = df.rename(columns=ren)

    # Numéricos
    df["valor"] = pd.to_numeric(df["valor"], errors="coerce").fillna(0.0).astype(float)
    for c in ["quantidade", "preco", "prazo_dias"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # Datas
    if "database" in df.columns:
        df["database"] = pd.to_datetime(df["database"], errors="coerce", dayfirst=True)

    # 🔎 manter somente a ÚLTIMA data por fundo (fundos podem ter datas diferentes)
    if "database" in df.columns and "nome do fundo" in df.columns:
        # max por fundo
        max_per_fundo = df.groupby("nome do fundo")["database"].transform("max")
        # linhas que estão na data máxima (e têm data válida)
        mask_max = df["database"].notna() & (df["database"] == max_per_fundo)
        # fundos cujo 'database' é TODO NaT → manter tudo
        mask_all_nat = df.groupby("nome do fundo")["database"].transform(lambda s: s.isna().all())
        # combina: mantém linha se for a máxima OU se o fundo não tem nenhuma data válida
        df = df[mask_max | mask_all_nat].copy()

    # 🔒 Textuais → dtype "string" (bom p/ Arrow) + fillna("")
    text_cols = [
        "nome do fundo", "tipo do fundo", "ativo", "liquidez",
        "categoria", "categoria 2", "categoria comitê", "tipo de ativo", "cnpj"
    ]
    for c in text_cols:
        if c in df.columns:
            df[c] = df[c].astype("string").fillna("")

    # Normaliza liquidez e garante "string"
    def _norm_liq(x: str) -> str:
        s = str(x).strip().lower()
        if s in {"alta", "alta liquidez", "d+0", "d0", "d+1"}:
            return "alta"
        if s in {"baixa", "baixa liquidez", "d+30", "d+60", "d+90"}:
            return "baixa"
        return s or ""
    df["liquidez"] = df["liquidez"].astype("string").map(_norm_liq).astype("string")

    # Ordenação de colunas
    main = ["nome do fundo", "tipo do fundo", "ativo", "valor",
            "categoria", "categoria 2", "categoria comitê", "tipo de ativo",
            "liquidez", "quantidade", "preco", "prazo_dias", "cnpj", "database"]
    others = [c for c in df.columns if c not in main]
    return df[main + others]