import re
import unicodedata
import pandas as pd
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
from .models import Ordem, RegraResultado

_CAT_COLS = ["categoria", "categoria 2", "categoria comitê"]
_TARGET_CNPJ = "28.819.553/0001-90"

# nomes de fundos (normalizados) com isenção do CNPJ no prazo_medio
_CNPJ_EXCLUDE_FUNDS = {
    # aceitamos variações com/sem acento e sufixos:
    "rio guaimbe fim", "guaimbe fim",
    "perola negra fim",
    "ambar branco fim",
    "trindade fim",
    "tsadik fim",
    "quartzo azul fim", "quartzo fim",
    "tavola fim", "tavola", "távola fim", "távola",
    "brutus fim", "brutus",
}

def _col(df: pd.DataFrame, name: str) -> bool:
    return name in df.columns

def _norm(x):
    return str(x).strip().lower()

def _strip_accents(s: str) -> str:
    try:
        return "".join(ch for ch in unicodedata.normalize("NFKD", str(s)) if not unicodedata.combining(ch)).lower().strip()
    except Exception:
        return str(s).lower().strip()

def _is_in_set_norm(name: str, norm_set: set[str]) -> bool:
    return _strip_accents(name) in norm_set

def _get_fundo_tipo(df: pd.DataFrame, fundo: str) -> str:
    for col in ["tipo do fundo", "tipo"]:
        if col in df.columns:
            tipos = df.loc[df["nome do fundo"] == fundo, col].dropna().unique()
            if len(tipos) > 0:
                return str(tipos[0]).strip().upper()
    return "DESCONHECIDO"

def _valor_ordem(ordem: Ordem) -> float:
    return float(ordem.quantidade) * float(ordem.preco)

def _filtro_futuros(df: pd.DataFrame) -> pd.DataFrame:
    """Remove linhas com 'categoria 2' == 'Futuros' (case-insensitive)."""
    if "categoria 2" not in df.columns:
        return df
    mask = df["categoria 2"].fillna("").map(_norm) != "futuros"
    return df.loc[mask].copy()

def _pl_total(df: pd.DataFrame, ordem: Ordem) -> float:
    """PL do fundo desconsiderando 'Futuros'."""
    if not _col(df, "valor") or not _col(df, "nome do fundo"):
        return 0.0
    df_fundo = df.loc[df["nome do fundo"] == ordem.fundo].copy()
    df_fundo = _filtro_futuros(df_fundo)
    return float(df_fundo["valor"].sum())

def _pct(x: float, y: float) -> float:
    return (x / y) if (y and y != 0) else 0.0

def _cfg_for_scope(limites_regra, fundo: str, tipo: str):
    if isinstance(limites_regra, dict) and fundo in limites_regra:
        return limites_regra[fundo], "fundo"
    if isinstance(limites_regra, dict) and tipo in limites_regra:
        return limites_regra[tipo], "tipo"
    return None, None

def _row_text(row):
    parts = [str(row.get(c, "")) for c in _CAT_COLS if c in row]
    return " | ".join(parts)

def _to_date(x):
    if pd.isna(x):
        return None
    if isinstance(x, (pd.Timestamp, datetime, date)):
        return pd.Timestamp(x).date()
    try:
        return pd.to_datetime(x, dayfirst=True, errors="coerce").date()
    except Exception:
        return None

def _parse_ddmmyyyy_tail(s: str) -> date | None:
    # pega os 10 últimos chars e tenta dd/mm/yyyy
    ss = str(s).strip()
    if len(ss) >= 10:
        tail = ss[-10:]
        try:
            return pd.to_datetime(tail, format="%d/%m/%Y").date()
        except Exception:
            return None
    return None

def _parse_yyyymmdd_mid(s: str, start: int, length: int) -> date | None:
    # extrai yyyyMMdd de s[start:start+length]
    try:
        token = str(s)[start:start+length]
        return pd.to_datetime(token, format="%Y%m%d").date()
    except Exception:
        return None

def _days_diff(d_end: date | None, d_ref: date | None) -> float:
    if not d_end or not d_ref:
        return 0.0
    return float((pd.Timestamp(d_end) - pd.Timestamp(d_ref)).days)

def _calc_prazo_row(row: pd.Series) -> float:
    """
    Calcula 'prazo_dias_calculado' por ativo. 
    Retorna SEMPRE float; quando não aplicável, retorna 0.0.
    """
    cat  = str(row.get("categoria", "")).strip()
    cat2 = str(row.get("categoria 2", "")).strip()
    tpa  = str(row.get("tipo de ativo", "")).strip()
    ativo = str(row.get("ativo", "")).strip()
    fundo = str(row.get("nome do fundo", "")).strip()
    ref_date = _to_date(row.get("database"))  # data base da linha

    # 1) Cotas de Fundos + (Multimercado|Renda Fixa|FIDC) → 366
    if cat2 == "Cotas de Fundos" and cat in {"Multimercado", "Renda Fixa", "FIDC"}:
        return 366.0

    # 2) Caixa e tipo != CONTAS CORRENTES → 1
    if cat == "Caixa" and tpa != "CONTAS CORRENTES":
        return 1.0

    # 3) Tipo = CONTAS CORRENTES e ativo = C/C → 1
    if tpa == "CONTAS CORRENTES" and ativo == "C/C":
        return 1.0

    # 4) LFT - dd/mm/yyyy (ex: "LFT - 01/09/2027")
    if ativo.startswith("LFT - "):
        d_mat = _parse_ddmmyyyy_tail(ativo)
        return _days_diff(d_mat, ref_date)

    # 5) LFTyyyyMMdd - ... (ex: "LFT20290901 - 210100")
    elif ativo.startswith("LFT"):
        d_mat = _parse_yyyymmdd_mid(ativo, start=3, length=8)  # logo após "LFT"
        if d_mat:
            return _days_diff(d_mat, ref_date)

    # 6) NTN-B - dd/mm/yyyy (cupons semestrais)
    if ativo.startswith("NTN-B") and not ativo.endswith("Over"):
        d_mat = _parse_ddmmyyyy_tail(ativo)
        return _prazo_ntnb_com_cupons(row, d_mat, ref_date)

    # 7) NTNBYYYYMMDD - ... (e não PHIP11): cupons semestrais
    if ativo.startswith("NTNB") and fundo != "PHIP11":
        d_mat = _parse_yyyymmdd_mid(ativo, start=4, length=8)
        return _prazo_ntnb_com_cupons(row, d_mat, ref_date)

    # 8) NTNB... e fundo == PHIP11: usa dd/mm/yyyy do final
    if ativo.startswith("NTNB") and fundo == "PHIP11":
        d_mat = _parse_ddmmyyyy_tail(ativo)
        return _prazo_ntnb_com_cupons(row, d_mat, ref_date)

    # 9) Fallback: usar 'prazo_dias' se vier (senão 0.0)
    try:
        pdias = pd.to_numeric(row.get("prazo_dias"), errors="coerce")
        return float(pdias) if pd.notna(pdias) and pdias > 0 else 0.0
    except Exception:
        return 0.0

def _prazo_ntnb_com_cupons(row: pd.Series, data_maturity: date | None, ref_date: date | None) -> float:
    """
    Calcula prazo efetivo para NTN-B/NTNB com cupons semestrais:
    - Cupom semestral: 3% do valor no primeiro, depois *0.97 recursivo (como no VBA enviado)
    - SomaProduto de cada cupom com seu prazo até a data-base + parcela final (principal remanescente)
    """
    if not data_maturity or not ref_date:
        return 0.0

    valorntnb = float(row.get("valor", 0.0) or 0.0)
    if valorntnb <= 0:
        # sem valor nessa linha → não contribui
        return 0.0

    # 1) Conta quantos cupons semestrais faltam a partir de "hoje" até o vencimento
    today = date.today()
    d = data_maturity
    contador = 0
    while (pd.Timestamp(d) - pd.Timestamp(today)).days > 0:
        d = (pd.Timestamp(d) - relativedelta(months=6)).date()
        contador += 1

    # 2) Reconstrói datas de cupom para frente e faz a SOMAR_PRODUTO dos cupons
    somaprod = 0.0
    somacupom = 0.0
    d = d  # já está a última data <= hoje
    cupom = 0.0
    for i in range(contador):
        if i == 0:
            cupom = valorntnb * 0.03
        else:
            cupom = cupom * 0.97
        # avança 6 meses
        d = (pd.Timestamp(d) + relativedelta(months=6)).date()
        prazo = _days_diff(d, ref_date)
        somaprod += cupom * prazo
        somacupom += cupom

    # 3) Parcela final (principal remanescente) no vencimento
    principal = max(valorntnb - somacupom, 0.0)
    prazo_final = _days_diff(data_maturity, ref_date)
    somaprod += principal * prazo_final

    # 4) Retorna "prazo médio" efetivo desta linha = somaprod / valor total (linha)
    # (para o agregado do fundo vamos fazer SOMAR_PRODUTO(valor, prazo)/SOMA(valor))
    if valorntnb > 0:
        return somaprod / valorntnb
    return 0.0

def _mask_por_alvo(df_fundo: pd.DataFrame, alvo: dict) -> pd.Series:
    # ... (mesma implementação anterior) ...
    if df_fundo.empty:
        return pd.Series(False, index=df_fundo.index)

    # groups: OR entre grupos
    if isinstance(alvo, dict) and "groups" in alvo and isinstance(alvo["groups"], list):
        m = pd.Series(False, index=df_fundo.index)
        for sub in alvo["groups"]:
            m = m | _mask_por_alvo(df_fundo, sub)
        return m

    # all: AND entre condições
    if isinstance(alvo, dict) and "all" in alvo and isinstance(alvo["all"], list):
        m = pd.Series(True, index=df_fundo.index)
        for sub in alvo["all"]:
            m = m & _mask_por_alvo(df_fundo, sub)
        return m

    # ativo_equals / ativo_prefix
    if isinstance(alvo, dict):
        if "ativo_equals" in alvo:
            vals = set(_norm(x) for x in alvo.get("ativo_equals", []))
            if "ativo" in df_fundo.columns:
                return df_fundo["ativo"].fillna("").map(_norm).isin(vals)
        if "ativo_prefix" in alvo:
            prefs = [str(x) for x in alvo.get("ativo_prefix", [])]
            if "ativo" in df_fundo.columns:
                return df_fundo["ativo"].fillna("").astype(str).apply(lambda s: any(s.startswith(p) for p in prefs))

    # any: bate em qualquer coluna de categoria
    any_list = []
    if isinstance(alvo, dict) and "any" in alvo:
        any_list = [_norm(x) for x in alvo.get("any", [])]
    m_any = pd.Series(False, index=df_fundo.index)
    if any_list:
        for col in _CAT_COLS:
            if col in df_fundo.columns:
                m_any = m_any | df_fundo[col].fillna("").map(_norm).isin(any_list)

    # por coluna específica
    m_cols = pd.Series(False, index=df_fundo.index)
    for col in list(_CAT_COLS) + ["tipo de ativo"]:
        if isinstance(alvo, dict) and col in alvo and isinstance(alvo[col], (list, tuple)):
            vals = [_norm(x) for x in alvo[col]]
            if col in df_fundo.columns:
                m_cols = m_cols | df_fundo[col].fillna("").map(_norm).isin(vals)

    # regex no texto combinado
    m_rx = pd.Series(False, index=df_fundo.index)
    if isinstance(alvo, dict) and isinstance(alvo.get("regex"), str) and alvo.get("regex").strip():
        rx = re.compile(alvo["regex"], flags=re.IGNORECASE)
        m_rx = df_fundo.apply(lambda row: bool(rx.search(_row_text(row))), axis=1)

    return m_any | m_cols | m_rx

# ================== REGRAS ====================================================
def enquadramento_cvm(df: pd.DataFrame, ordem: Ordem, limites_regra) -> RegraResultado:
    tipo = _get_fundo_tipo(df, ordem.fundo)
    entry, scope = _cfg_for_scope(limites_regra, ordem.fundo, tipo)
    if entry in (None, "~", "NA", "N/A"):
        return RegraResultado("enquadramento_cvm", True, 0.0, 0.0, 0.0, f"Politica de investimento (Regulamento): Fundo {ordem.fundo} isento de observância.")

    if isinstance(entry, (int, float)):
        entry = {"min": float(entry), "alvo": {"any": []}}

    alvo = entry.get("alvo", {})
    limite = float(entry.get("min", 0.0))

    # Fundo filtrado: remove FUTUROS
    df_f = df.loc[df["nome do fundo"] == ordem.fundo].copy()
    df_f = _filtro_futuros(df_f)

    if df_f.empty or not _col(df, "valor"):
        return RegraResultado("enquadramento_cvm", False, 0.0, 0.0, limite, "Politica de investimento (Regulamento): Sem linhas/coluna 'valor' para o fundo.")

    mask = _mask_por_alvo(df_f, alvo)
    pos_atual = float(df_f.loc[mask, "valor"].sum())

    # Impacto da ordem apenas se o ativo faz parte do alvo
    ativo_row = df_f.loc[df_f["ativo"] == ordem.ativo]
    conta_ordem_no_alvo = False
    if not ativo_row.empty:
        conta_ordem_no_alvo = bool(_mask_por_alvo(ativo_row, alvo).any())

    vord = _valor_ordem(ordem)
    pos_proposta = pos_atual + (vord if (conta_ordem_no_alvo and ordem.tipo == "compra") else 0.0) - (vord if (conta_ordem_no_alvo and ordem.tipo == "venda") else 0.0)

    pl = _pl_total(df, ordem)
    perc_atual = _pct(pos_atual, pl if pl != 0 else float(df_f["valor"].sum()))
    perc_proposto = _pct(pos_proposta, pl)
    passou = perc_proposto >= limite

    return RegraResultado(
        regra="enquadramento_cvm",
        passou=passou,
        valor_atual=perc_atual,
        valor_proposto=perc_proposto,
        limite=limite,
        mensagem=f"Politica de investimento (Regulamento): {perc_proposto:.2%} do PL (mín {limite:.2%})."
    )

def enquadramento_tributario(df: pd.DataFrame, ordem: Ordem, limites_regra) -> RegraResultado:
    tipo = _get_fundo_tipo(df, ordem.fundo)
    entry, scope = _cfg_for_scope(limites_regra, ordem.fundo, tipo)
    if entry in (None, "~", "NA", "N/A"):
        return RegraResultado("enquadramento_tributario", True, 0.0, 0.0, 0.0, f"Enquadramento Tributário: Fundo {ordem.fundo} isento de observância.")

    if isinstance(entry, (int, float)):
        entry = {"min": float(entry), "alvo": {"any": []}}

    alvo = entry.get("alvo", {})
    limite = float(entry.get("min", 0.0))

    # Fundo filtrado: remove FUTUROS
    df_f = df.loc[df["nome do fundo"] == ordem.fundo].copy()
    df_f = _filtro_futuros(df_f)
    if _col(df_f, "cnpj") and _is_in_set_norm(ordem.fundo, _CNPJ_EXCLUDE_FUNDS):
        df_f = df_f.loc[df_f["cnpj"].astype(str).str.strip() != _TARGET_CNPJ].copy()

    if df_f.empty or not _col(df, "valor"):
        return RegraResultado("enquadramento_tributario", False, 0.0, 0.0, limite, "Enquadramento Tributário: Sem linhas/coluna 'valor' para o fundo.")

    mask = _mask_por_alvo(df_f, alvo)
    pos_atual = float(df_f.loc[mask, "valor"].sum())

    ativo_row = df_f.loc[df_f["ativo"] == ordem.ativo]
    conta_ordem_no_alvo = False
    if not ativo_row.empty:
        conta_ordem_no_alvo = bool(_mask_por_alvo(ativo_row, alvo).any())

    vord = _valor_ordem(ordem)
    pos_proposta = pos_atual + (vord if (conta_ordem_no_alvo and ordem.tipo == "compra") else 0.0) - (vord if (conta_ordem_no_alvo and ordem.tipo == "venda") else 0.0)

    pl = _pl_total(df, ordem)
    perc_atual = _pct(pos_atual, pl if pl != 0 else float(df_f["valor"].sum()))
    perc_proposto = _pct(pos_proposta, pl)
    passou = perc_proposto >= limite

    return RegraResultado(
        regra="enquadramento_tributario",
        passou=passou,
        valor_atual=perc_atual,
        valor_proposto=perc_proposto,
        limite=limite,
        mensagem=f"Enquadramento Tributário: {perc_proposto:.2%} do PL (mín {limite:.2%})."
    )

def prazo_medio(df: pd.DataFrame, ordem: Ordem, limites_regra) -> RegraResultado:
    # resolve limite como antes (ETF=720, FIA/FII isento, demais=365; ou conforme YAML)
    tipo = _get_fundo_tipo(df, ordem.fundo).upper()
    def _resolve(limites, fundo, tipo):
        if isinstance(limites, dict) and fundo in limites:
            ent = limites[fundo]
        elif isinstance(limites, dict) and tipo in limites:
            ent = limites[tipo]
        else:
            ent = None
        if ent is None:
            if tipo == "ETF": return True, 720.0
            if tipo in {"FIA", "FII"}: return False, 0.0
            return True, 365.0
        if isinstance(ent, bool): return bool(ent), 0.0
        if isinstance(ent, (int, float)): return True, float(ent)
        if isinstance(ent, dict):
            return bool(ent.get("exige", True)), float(ent.get("dias", ent.get("limite", 0.0)))
        return True, 365.0

    exige, limite = _resolve(limites_regra, ordem.fundo, tipo)
    if not exige:
        return RegraResultado("prazo_medio", True, 0.0, 0.0, 0.0, f"Prazo Médio: Fundo {ordem.fundo} isento de observância.")

    # subconjunto do fundo + filtros (ex-Futuros e exclusões por CNPJ se aplicarem)
    df_f = df.loc[df["nome do fundo"] == ordem.fundo].copy()
    df_f = _filtro_futuros(df_f)

    if df_f.empty or not _col(df_f, "valor"):
        return RegraResultado("prazo_medio", False, 0.0, 0.0, limite, "Prazo Médio: Sem dados suficientes para cálculo.")

    # === NOVO: prazo calculado SEMPRE numérico e sem NaN ===
    df_f["__prazo_calc__"] = df_f.apply(_calc_prazo_row, axis=1).astype(float)
    df_f["__prazo_calc__"] = df_f["__prazo_calc__"].fillna(0.0)

    # somente linhas com prazo > 0 entram na média
    df_valid = df_f.loc[(df_f["__prazo_calc__"] > 0) & df_f["valor"].notna()].copy()

    soma_pesos = float(df_valid["valor"].sum())
    soma_prod = float((df_valid["valor"] * df_valid["__prazo_calc__"]).sum())
    atual = (soma_prod / soma_pesos) if soma_pesos > 0 else 0.0
    if not pd.notna(atual):
        atual = 0.0

    # impacto da ordem: só entra no denominador se o prazo_da_ordem > 0
    prazo_ordem = 0.0
    linha_ativo = df_f.loc[df_f["ativo"] == ordem.ativo].tail(1)
    if not linha_ativo.empty:
        prazo_ordem = float(_calc_prazo_row(linha_ativo.iloc[0])) or 0.0

    vord = _valor_ordem(ordem)
    sinal = 1.0 if ordem.tipo == "compra" else -1.0
    inclui_ordem = prazo_ordem > 0.0

    soma_pesos_prop = soma_pesos + (sinal * vord if inclui_ordem else 0.0)
    soma_prod_prop  = soma_prod  + (sinal * vord * prazo_ordem if inclui_ordem else 0.0)

    proposto = (soma_prod_prop / soma_pesos_prop) if soma_pesos_prop > 0 else atual
    if not pd.notna(proposto):
        proposto = atual

    passou = proposto > limite

    return RegraResultado(
        regra="prazo_medio",
        passou=passou,
        valor_atual=atual,
        valor_proposto=proposto,
        limite=limite,
        mensagem=f"Prazo Médio: {proposto:,.1f} dias (mín {limite:,.0f} dias)."
    )