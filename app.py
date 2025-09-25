import streamlit as st
import pandas as pd
import os
from datetime import datetime

from core.loader import carregar_carteira
from core.models import Ordem
from core.engine import aplicar_ordens_no_df, carregar_regras, aplicar_regras
from core.report import build_pdf

# ======================== Config da página ========================
st.set_page_config(page_title="Pré-Trading", layout="wide")
st.title("📈 Pré-Trading")

with st.sidebar.expander("📘 Regras e limites", expanded=False):
    st.markdown("**Política de investimento (Regulamento)**")
    st.markdown(
        "- **FIDC**: 67% em Direitos Creditórios — *Risco:* 6% • *Margem ideal:* **73%**\n"
        "- **FIA**: 67% em ativos de Renda Variável — *Risco:* 6% • *Margem ideal:* **73%**\n"
        "- **ETF**: 95% em ativos que pertencem ao Índice de referência — *Risco:* 2% • *Margem ideal:* **97%**\n"
        "- **FIM (com política em cotas)**: 95% em cotas de Fundos — *Risco:* 2% • *Margem ideal:* **97%**"
    )

    st.markdown("**Enquadramento Tributário — Lei 14.754**")
    st.markdown(
        "- **FIDC**: 67% em Direitos Creditórios — *Risco:* 6% • *Margem ideal:* **73%**\n"
        "- **FIM**: 95% em Entidades de Investimento + FIA's + FII's — *Risco:* 2% • *Margem ideal:* **97%**\n"
        "- **FIA**: 67% em ativos de Renda Variável — *Risco:* 6% • *Margem ideal:* **73%**"
    )

    st.markdown("**Prazo Médio — IN RFB nº 1585**")
    st.markdown("- **Fundos LP**: Base legal **365 dias** • *Risco Phronesis:* **5 dias** • *Margem ideal:* **370 dias**")


# ======================== Helpers ========================
def _norm(s: str) -> str:
    return (s or "").strip().lower()

def pretty(s: str) -> str:
    return s.title() if s else s

def fmt_brl_md(x: float) -> str:
    """Formata moeda pt-BR para Markdown (escapa $)."""
    try:
        v = f"{float(x):,.2f}".replace(",", "§").replace(".", ",").replace("§", ".")
        return f"R\\$ {v}"
    except Exception:
        return f"R\\$ {x}"

def _pl_ex_futuros(df, fundo: str) -> float:
    """Soma o PL do fundo desconsiderando linhas com 'categoria 2' == 'Futuros'."""
    if df is None or df.empty or not fundo:
        return 0.0
    dff = df.loc[df["nome do fundo"] == fundo].copy()
    if "categoria 2" in dff.columns:
        dff = dff[dff["categoria 2"].fillna("").str.strip().str.lower() != "futuros"]
    return float(dff["valor"].sum())

def _coerce_text_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Converte colunas textuais para dtype 'string' (compatível com Arrow/pyarrow)."""
    df = df.copy()
    for c in ["nome do fundo","tipo do fundo","ativo","liquidez",
              "categoria","categoria 2","categoria comitê","tipo de ativo","cnpj"]:
        if c in df.columns:
            df[c] = df[c].astype("string").fillna("")
    return df

# Presets de classificação por "tipo de ativo"
CLASS_PRESETS = {
    "acoes": [
        {"label": "Ações → RV/Outros/RV",
         "categoria": "Renda Variável", "categoria 2": "Outros", "categoria comitê": "Renda Variável"},
    ],
    "caixa": [
        {"label": "Caixa → Caixa/Caixa/Outros",
         "categoria": "Caixa", "categoria 2": "Caixa", "categoria comitê": "Outros"},
    ],
    "contas correntes": [
        {"label": "Contas Correntes → Caixa/Caixa/Outros",
         "categoria": "Caixa", "categoria 2": "Caixa", "categoria comitê": "Outros"},
    ],
    "contas_pagar_receber": [
        {"label": "Contas Pagar/Receber → Outros/Outros/Outros",
         "categoria": "Outros", "categoria 2": "Outros", "categoria comitê": "Outros"},
    ],
    "cotas de fundos": [
        {"label": "Cotas (Renda Fixa) → RF/Cotas de Fundos/RF",
         "categoria": "Renda Fixa", "categoria 2": "Cotas de Fundos", "categoria comitê": "Renda Fixa"},
        {"label": "Cotas (Multimercado) → MM/Cotas de Fundos/MM",
         "categoria": "Multimercado", "categoria 2": "Cotas de Fundos", "categoria comitê": "Multimercado"},
        {"label": "Cotas (Renda Variável) → RV/Cotas de Fundos/RV",
         "categoria": "Renda Variável", "categoria 2": "Cotas de Fundos", "categoria comitê": "Renda Variável"},
        {"label": "Cotas (Participações) → Part./Cotas de Fundos/Part.",
         "categoria": "Participações", "categoria 2": "Cotas de Fundos", "categoria comitê": "Participações"},
        {"label": "Cotas (FIDC) → FIDC/Cotas de Fundos/FIDC",
         "categoria": "FIDC", "categoria 2": "Cotas de Fundos", "categoria comitê": "FIDC"},
        {"label": "Cotas (FIDC - Gestão Prop.) → FIDC/Cotas de Fundos/FIDC - Gestão Prop.",
         "categoria": "FIDC", "categoria 2": "Cotas de Fundos", "categoria comitê": "FIDC - Gestão Prop."},
        {"label": "Cotas (Imobiliário) → Imob./Cotas de Fundos/Real Estate",
         "categoria": "Imobiliário", "categoria 2": "Cotas de Fundos", "categoria comitê": "Real Estate"},
        {"label": "Cotas (RV - Gestão Prop.) → RV/Cotas de Fundos/RV - Gestão Prop.",
         "categoria": "Renda Variável", "categoria 2": "Cotas de Fundos", "categoria comitê": "Renda Variável - Gestão Prop."},
    ],
    "creditórios": [
        {"label": "Creditórios → Creditórios/Creditórios/Creditórios",
         "categoria": "Creditórios", "categoria 2": "Creditórios", "categoria comitê": "Creditórios"},
    ],
    "debênture": [
        {"label": "Debênture → Debênture/Creditórios/Debênture",
         "categoria": "Debênture", "categoria 2": "Creditórios", "categoria comitê": "Debênture"},
    ],
    "debentures": [
        {"label": "Debêntures → Debênture/Creditórios/Debênture",
         "categoria": "Debênture", "categoria 2": "Creditórios", "categoria comitê": "Debênture"},
    ],
    "despesas": [
        {"label": "Despesas → Outros/Outros/Outros",
         "categoria": "Outros", "categoria 2": "Outros", "categoria comitê": "Outros"},
    ],
    "fundos": [
        {"label": "FUNDOS (RF) → RF/Cotas de Fundos/RF",
         "categoria": "Renda Fixa", "categoria 2": "Cotas de Fundos", "categoria comitê": "Renda Fixa"},
        {"label": "FUNDOS (FIDC - Gestão Prop.) → FIDC/Cotas de Fundos/FIDC - Gestão Prop.",
         "categoria": "FIDC", "categoria 2": "Cotas de Fundos", "categoria comitê": "FIDC - Gestão Prop."},
        {"label": "FUNDOS (FIDC) → FIDC/Cotas de Fundos/FIDC",
         "categoria": "FIDC", "categoria 2": "Cotas de Fundos", "categoria comitê": "FIDC"},
        {"label": "Fundos (Imobiliário) → Imob./Imobiliário/Real Estate",
         "categoria": "Imobiliário", "categoria 2": "Imobiliário", "categoria comitê": "Real Estate"},
        {"label": "FUNDOS (RV) → RV/Cotas de Fundos/RV",
         "categoria": "Renda Variável", "categoria 2": "Cotas de Fundos", "categoria comitê": "Renda Variável"},
        {"label": "Fundos (Participações) → Part./Cotas de Fundos/Participações",
         "categoria": "Participações", "categoria 2": "Cotas de Fundos", "categoria comitê": "Participações"},
    ],
    "futuro": [
        {"label": "Futuro → Futuros/Futuros/Futuros",
         "categoria": "Futuros", "categoria 2": "Futuros", "categoria comitê": "Futuros"},
    ],
    "imóvel": [
        {"label": "Imóvel → Imobiliário/Imobiliário/Real Estate",
         "categoria": "Imobiliário", "categoria 2": "Imobiliário", "categoria comitê": "Real Estate"},
    ],
    "opção ação": [
        {"label": "Opção Ação → Derivativos/Derivativos/Outros",
         "categoria": "Derivativos", "categoria 2": "Derivativos", "categoria comitê": "Outros"},
    ],
    "outros ativos": [
        {"label": "Outros Ativos → Outros/Outros/Outros",
         "categoria": "Outros", "categoria 2": "Outros", "categoria comitê": "Outros"},
    ],
    "pdd": [
        {"label": "PDD → PDD/PDD/PDD",
         "categoria": "PDD", "categoria 2": "PDD", "categoria comitê": "PDD"},
    ],
    "provisão": [
        {"label": "Provisão → Outros/Outros/Outros",
         "categoria": "Outros", "categoria 2": "Outros", "categoria comitê": "Outros"},
    ],
    "títulos públicos": [
        {"label": "Títulos Públicos → RF/Outros/RF",
         "categoria": "Renda Fixa", "categoria 2": "Outros", "categoria comitê": "Renda Fixa"},
    ],
    "titulos_publicos": [
        {"label": "Titulos_Publicos → RF/Outros/RF",
         "categoria": "Renda Fixa", "categoria 2": "Outros", "categoria comitê": "Renda Fixa"},
    ],
}
# opções legíveis para o select
TIPO_ATIVO_OPCOES = sorted(list({k for k in CLASS_PRESETS.keys()}))

# --- normalização e alias para "tipo de ativo" vindos da base ---
ALIAS_TIPO = {
    "fundos": "fundos",
    "fundos de investimento": "fundos",
    "fundos_de_investimento": "fundos",
    "fundos de investimentos": "fundos",
    "fundos de fundos": "fundos",
    "fundos multimercado": "fundos",
    "fundos imobiliarios": "fundos",
    "fundos imobiliários": "fundos",
    "fundos fidc": "fundos",
    "fundos (fidc)": "fundos",
    "fundos (rv)": "fundos",
    "fundos (rf)": "fundos",
    "outros_fundos_de_investimento": "fundos",
    "outros fundos de investimento": "fundos",
    "cotas de fundos": "cotas de fundos",  # já é chave válida
    "acoes": "acoes",
    "ação": "acoes",
    "ações": "acoes",
    "caixa": "caixa",
    "contas correntes": "contas correntes",
    "contas_correntes": "contas correntes",
    "contas pagar receber": "contas_pagar_receber",
    "contas_pagar_receber": "contas_pagar_receber",
    "creditorios": "creditórios",
    "creditórios": "creditórios",
    "debenture": "debênture",
    "debênture": "debênture",
    "debentures": "debentures",
    "despesas": "despesas",
    "futuro": "futuro",
    "imovel": "imóvel",
    "imóvel": "imóvel",
    "opcao acao": "opção ação",
    "opção ação": "opção ação",
    "outros ativos": "outros ativos",
    "pdd": "pdd",
    "provisao": "provisão",
    "provisão": "provisão",
    "titulos publicos": "titulos_publicos",
    "títulos públicos": "títulos públicos",
    "títulos publicos": "títulos públicos",
    "titulos_publicos": "titulos_publicos",
}

def normalize_tipo_ativo(valor: str) -> str:
    v = (valor or "").strip().lower().replace("-", " ").replace(".", " ").replace("/", " ")
    v = " ".join(v.split())  # colapsa espaços
    return ALIAS_TIPO.get(v, v)  # se não achar, devolve normalizado (pode não estar nos presets)

def set_if_changed(key: str, new_value: str):
    """Atualiza um widget state somente se for diferente (evita piscar)."""
    if st.session_state.get(key) != new_value:
        st.session_state[key] = new_value

if "uploader_key" not in st.session_state:
    st.session_state.uploader_key = 0

# ======================== Upload da carteira ========================
uploaded_file = st.file_uploader("Carregar carteira", type=["xlsx", "xlsm"], accept_multiple_files=False, key=f"uploader_{st.session_state.uploader_key}")

if uploaded_file and "df" not in st.session_state:
    st.session_state.df = carregar_carteira(uploaded_file)
    st.success("Carteira carregada com sucesso.", icon=":material/assignment_turned_in:")
    st.session_state.uploader_key += 1
    st.rerun()

if "df" not in st.session_state:
    st.info("Importe uma carteira para começar")
    st.stop()

tab_fundo, tab_all = st.tabs(["💰 Fundo selecionado", "🪙 Arquivo completo"])

fundos_opts = (
    st.session_state.df["nome do fundo"].unique().tolist()
    if st.session_state.df is not None and "nome do fundo" in st.session_state.df.columns
    else []
)
fundo = st.selectbox("Nome do Fundo", fundos_opts, index=0 if fundos_opts else None)
st.session_state.fundo = fundo

with tab_all:
    st.markdown("#### 🪙 Arquivo com todas as carteiras")
    st.dataframe(_coerce_text_cols(st.session_state.df), width="stretch")

with tab_fundo:
    st.markdown("#### 💰 Apenas o fundo selecionado")
    if fundo:
        df_fundo = st.session_state.df.loc[st.session_state.df["nome do fundo"] == fundo].copy()
        st.dataframe(_coerce_text_cols(df_fundo), width="stretch")
        pl_view = _pl_ex_futuros(st.session_state.df, fundo)
        st.caption(f"PL do fundo **{fundo}**: {fmt_brl_md(pl_view)}")
    else:
        st.info("Selecione um fundo para ver o recorte.")

# ======================== ORDENS (lote) ========================
st.markdown("### 🧾 Ordens (lote)")

# --- Schema & estado inicial (fixo para não resetar) ---
ORDERS_SCHEMA = {"ativo": str, "quantidade": float, "preco": float, "tipo": str}

def _ensure_orders_state():
    if "orders_df" not in st.session_state:
        st.session_state.orders_df = pd.DataFrame(
            [{"ativo": "", "quantidade": 0.0, "preco": 0.0, "tipo": "compra"}]
        )
    if "cad_novos" not in st.session_state:
        st.session_state.cad_novos = pd.DataFrame(
            columns=["ativo", "tipo de ativo", "categoria", "categoria 2", "categoria comitê"]
        )
    if "permitir_ativo_novo" not in st.session_state:
        st.session_state.permitir_ativo_novo = False

_ensure_orders_state()

# --- Ações auxiliares (não recriam o DF inteiro) ---
def _add_line():
    df = st.session_state.orders_df.copy()
    df.loc[len(df)] = {"ativo": "", "quantidade": 0.0, "preco": 0.0, "tipo": "compra"}
    st.session_state.orders_df = df

def _clear_lines():
    st.session_state.orders_df = pd.DataFrame(
        [{"ativo": "", "quantidade": 0.0, "preco": 0.0, "tipo": "compra"}]
    )

# --- Opções de ativos do fundo atual ---
ativos_opts = []
if st.session_state.df is not None and fundo:
    mask_fundo = st.session_state.df["nome do fundo"] == fundo
    if "ativo" in st.session_state.df.columns:
        ativos_opts = sorted(
            st.session_state.df.loc[mask_fundo, "ativo"].dropna().astype(str).unique().tolist()
        )

# --- Botões e toggle (fora do editor) ---
c1, c2, c3 = st.columns([1, 1, 2])
with c1:
    st.button("➕ Adicionar linha", on_click=_add_line)
with c2:
    st.button("🧹 Limpar ordens", on_click=_clear_lines)
with c3:
    st.session_state.permitir_ativo_novo = st.toggle(
        "Permitir ativos novos (fora da carteira)",
        value=st.session_state.permitir_ativo_novo,
        help="Habilite para cadastrar ativos que não estão na carteira e definir suas classificações."
    )

def _to_orders_df(obj) -> pd.DataFrame:
    """
    Converte qualquer payload do data_editor para DataFrame com colunas padronizadas.
    Aceita: DataFrame, lista de dicts, dict de listas (colunas), dict de dicts (linhas).
    """
    import pandas as pd

    # Caso já seja DataFrame
    if isinstance(obj, pd.DataFrame):
        df = obj.copy()

    # Lista de registros
    elif isinstance(obj, list):
        # ex.: [{"ativo": "...", "quantidade": ...}, ...]
        df = pd.DataFrame.from_records(obj) if obj else pd.DataFrame()

    # Dicionário
    elif isinstance(obj, dict):
        # 1) dict de listas (colunas)
        if all(isinstance(v, (list, tuple)) for v in obj.values()):
            df = pd.DataFrame(obj)
        # 2) dict de dicts (linhas): {0: {...}, 1: {...}}
        elif all(isinstance(v, dict) for v in obj.values()):
            df = pd.DataFrame.from_dict(obj, orient="index")
        else:
            # 3) um único registro {"ativo": "...", ...}
            df = pd.DataFrame([obj])
    else:
        # fallback
        try:
            df = pd.DataFrame(obj)
        except Exception:
            df = pd.DataFrame()

    # Garante colunas e ordem
    cols = ["ativo", "quantidade", "preco", "tipo"]
    for c, default in zip(cols, ["", 0.0, 0.0, "compra"]):
        if c not in df.columns:
            df[c] = default
    df = df[cols]

    # Tipos
    df["ativo"] = df["ativo"].astype(str).fillna("")
    df["quantidade"] = pd.to_numeric(df["quantidade"], errors="coerce").fillna(0.0)
    df["preco"] = pd.to_numeric(df["preco"], errors="coerce").fillna(0.0)
    df["tipo"] = df["tipo"].astype(str).str.lower()
    df.loc[~df["tipo"].isin(["compra", "venda"]), "tipo"] = "compra"

    return df.reset_index(drop=True)

col_ativo = (
    st.column_config.TextColumn("Ativo")
    if st.session_state.permitir_ativo_novo
    else st.column_config.SelectboxColumn("Ativo", options=ativos_opts, help="Escolha um ativo do fundo.")
)

edited_payload = st.data_editor(
    st.session_state.orders_df,
    key="orders_editor",
    num_rows="dynamic",
    width="stretch",
    column_config={
        "ativo": col_ativo,
        "quantidade": st.column_config.NumberColumn("Quantidade", step=1.0, min_value=0.0),
        "preco": st.column_config.NumberColumn("Preço Unitário", step=0.01, min_value=0.0, format="%.2f"),
        "tipo": st.column_config.SelectboxColumn("Tipo", options=["compra", "venda"]),
    },
)

def _ensure_cols(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["ativo", "quantidade", "preco", "tipo"]
    for c in cols:
        if c not in df.columns:
            df[c] = "" if c in ("ativo", "tipo") else 0.0
    return df[cols]

if edited_payload is not None:
    if isinstance(edited_payload, pd.DataFrame):
        st.session_state.orders_df = _ensure_cols(edited_payload.copy())
    elif isinstance(edited_payload, list):
        st.session_state.orders_df = _ensure_cols(pd.DataFrame.from_records(edited_payload))
    elif isinstance(edited_payload, dict):
        # tenta colunas; se não for, tenta linhas
        try:
            st.session_state.orders_df = _ensure_cols(pd.DataFrame(edited_payload))
        except Exception:
            st.session_state.orders_df = _ensure_cols(pd.DataFrame.from_dict(edited_payload, orient="index"))


# ------------------ Classificação de ativos novos ------------------
cad = st.session_state.cad_novos.copy()

if st.session_state.permitir_ativo_novo and fundo:
    # Pega tudo o que foi digitado (já sincronizado via on_change)
    todos_digitados = [
        a.strip() for a in st.session_state.orders_df["ativo"].astype(str).tolist()
        if a and a.strip()
    ]
    if todos_digitados:
        # Garante que cada ativo digitado tenha uma linha em cad (já neste rerun)
        for a in todos_digitados:
            if cad[cad["ativo"] == a].empty:
                herd = {"tipo de ativo": "", "categoria": "", "categoria 2": "", "categoria comitê": ""}
                if a in ativos_opts:
                    base = st.session_state.df.loc[
                        (st.session_state.df["nome do fundo"] == fundo) &
                        (st.session_state.df["ativo"] == a)
                    ]
                    if not base.empty:
                        herd["tipo de ativo"] = normalize_tipo_ativo(str(base.get("tipo de ativo", pd.Series([""])).iloc[0]))
                        herd["categoria"] = str(base.get("categoria", pd.Series([""])).iloc[0])
                        herd["categoria 2"] = str(base.get("categoria 2", pd.Series([""])).iloc[0])
                        herd["categoria comitê"] = str(base.get("categoria comitê", pd.Series([""])).iloc[0])
                cad.loc[len(cad)] = {"ativo": a, **herd}

        st.markdown("#### 🏷️ Classificação de ativos")
        novos_ativos = [a for a in todos_digitados if a not in ativos_opts]
        if novos_ativos:
            st.caption("Ativos novos detectados: " + ", ".join(f"`{a}`" for a in novos_ativos))
        else:
            st.caption("Todos os ativos digitados já existem na carteira (você pode ajustar as classificações).")

        # Editor simples por ativo (keys estáveis por nome do ativo)
        for a in todos_digitados:
            st.divider()
            st.markdown(f"**Ativo:** `{a}`" + ("" if a in novos_ativos else " — *(já existente na carteira)*"))
            linha = cad.loc[cad["ativo"] == a].iloc[-1].to_dict()
            tipo_atual = normalize_tipo_ativo(linha.get("tipo de ativo", ""))

            # opções exibidas (mantém as chaves dos presets)
            preset_keys_sorted = sorted(list(CLASS_PRESETS.keys()))
            options_tipo = [""] + preset_keys_sorted

            # calcula o índice com segurança
            try:
                idx_inicial = options_tipo.index(tipo_atual) if tipo_atual in options_tipo else 0
            except ValueError:
                idx_inicial = 0

            col1, col2, col3, col4 = st.columns(4)
            with col1:
                tipo_escolhido = st.selectbox(
                    "Tipo de Ativo",
                    options=options_tipo,
                    index=idx_inicial,
                    key=f"tipo_{a}",
                    format_func=pretty,
                    help="Selecione o tipo ou utilize um dos modelos para preencher categorias."
                )

            with col2:
                # presets para preencher rápido
                presets = CLASS_PRESETS.get(tipo_escolhido, [])
                preset_labels = ["(não aplicar preset)"] + [p["label"] for p in presets]
                preset_idx = st.selectbox(
                    "Modelo",
                    options=list(range(len(preset_labels))),
                    format_func=lambda i: preset_labels[i],
                    index=0,
                    key=f"preset_{a}",
                )

                # valores atuais (o que estiver no state tem prioridade)
                cat_state_key, cat2_state_key, catc_state_key = f"cat_{a}", f"cat2_{a}", f"catc_{a}"
                cat_current = st.session_state.get(cat_state_key, str(linha.get("categoria", "")))
                cat2_current = st.session_state.get(cat2_state_key, str(linha.get("categoria 2", "")))
                catc_current = st.session_state.get(catc_state_key, str(linha.get("categoria comitê", "")))

                # se um preset foi escolhido, escreve diretamente no state ANTES de renderizar os inputs
                if preset_idx > 0:
                    pr = presets[preset_idx - 1]
                    set_if_changed(cat_state_key, pr["categoria"])
                    set_if_changed(cat2_state_key, pr["categoria 2"])
                    set_if_changed(catc_state_key, pr["categoria comitê"])
                    # atualiza variáveis locais para refletir no value
                    cat_current = pr["categoria"]
                    cat2_current = pr["categoria 2"]
                    catc_current = pr["categoria comitê"]

                with col3:
                    cat_val = st.text_input("Categoria", value=cat_current, key=cat_state_key)
                with col4:
                    cat2_val = st.text_input("Categoria 2", value=cat2_current, key=cat2_state_key)
                catc_val = st.text_input("Categoria Comitê", value=catc_current, key=catc_state_key)

            cad.loc[cad["ativo"] == a, ["tipo de ativo", "categoria", "categoria 2", "categoria comitê"]] = [
                tipo_escolhido,
                st.session_state.get(cat_state_key, ""),
                st.session_state.get(cat2_state_key, ""),
                st.session_state.get(catc_state_key, ""),
            ]

        # mostra resumo do cadastro dos digitados
        st.dataframe(
            cad.loc[cad["ativo"].isin(todos_digitados)][
                ["ativo","tipo de ativo","categoria","categoria 2","categoria comitê"]
            ],
            width="stretch",
        )

# salva o cadastro (importante para persistir entre reruns)
st.session_state.cad_novos = cad.copy()

# ------------------ Botão de validação do lote -------------------
validar_lote = st.button("✅ Validar Lote")

# ======================== PROCESSAMENTO DO CLIQUE ========================
if validar_lote:
    # Trabalhe numa cópia coerida (não mexe no editor)
    orders_safe = st.session_state.orders_df.copy()

    # Coerções e saneamento AQUI (somente nesta etapa)
    orders_safe["ativo"] = orders_safe["ativo"].astype(str).fillna("").str.strip()
    orders_safe["tipo"] = orders_safe["tipo"].astype(str).str.lower()
    orders_safe.loc[~orders_safe["tipo"].isin(["compra","venda"]), "tipo"] = "compra"
    orders_safe["quantidade"] = pd.to_numeric(orders_safe["quantidade"], errors="coerce").fillna(0.0)
    orders_safe["preco"] = pd.to_numeric(orders_safe["preco"], errors="coerce").fillna(0.0)

    # use orders_safe no loop abaixo
    ordens = []
    invalidos = 0
    novos_para_inserir = []

    for _, row in orders_safe.iterrows():
        ativo = row["ativo"]
        if not ativo:
            continue
        q = float(row["quantidade"])
        p = float(row["preco"])
        t = row["tipo"]
        if q <= 0 or p <= 0 or t not in {"compra", "venda"}:
            invalidos += 1
            continue

        if st.session_state.permitir_ativo_novo and ativo not in ativos_opts:
            novos_para_inserir.append(ativo)

        ordens.append(Ordem(fundo=fundo, ativo=ativo, quantidade=q, preco=p, tipo=t))

        if invalidos:
            st.info(f"{invalidos} linha(s) inválidas foram ignoradas.")
        if not ordens:
            st.error("Nenhuma ordem válida para processar.")
        else:
            try:
                # base da carteira
                df_base = st.session_state.df.copy()

                # =========== VALIDAÇÕES DE VENDA ===========
                erros = []
                for o in ordens:
                    if o.tipo != "venda":
                        continue
                    mask_pos = (df_base["nome do fundo"] == fundo) & (df_base["ativo"].astype(str) == o.ativo)
                    valor_em_carteira = float(df_base.loc[mask_pos, "valor"].sum()) if "valor" in df_base.columns else 0.0

                    if valor_em_carteira <= 0:
                        erros.append(f"Você tentou vender **{o.ativo}**, mas o fundo **{fundo}** não possui posição desse ativo.")
                        continue

                    valor_da_venda = float(o.quantidade) * float(o.preco)
                    if valor_da_venda > valor_em_carteira + 1e-9:
                        erros.append(
                            f"A venda de **{o.ativo}** ({fmt_brl_md(valor_da_venda)}) "
                            f"excede o que o fundo possui ({fmt_brl_md(valor_em_carteira)})."
                        )

                if erros:
                    st.error("Não foi possível validar o lote por causa de:")
                    for e in erros:
                        st.markdown(f"- {e}")
                    st.stop()
                # ===========================================

                # inserir ativos novos com as classificações escolhidas
                if novos_para_inserir:
                    mask_fundo = df_base["nome do fundo"] == fundo
                    tipo_fundo = ""
                    if "tipo do fundo" in df_base.columns and mask_fundo.any():
                        tipos = df_base.loc[mask_fundo, "tipo do fundo"].dropna().unique()
                        if len(tipos) > 0:
                            tipo_fundo = str(tipos[0]).strip()

                    cad_now = st.session_state.cad_novos.copy()
                    for ativo_new in novos_para_inserir:
                        exists = ((df_base["nome do fundo"] == fundo) & (df_base["ativo"] == ativo_new)).any()
                        if not exists:
                            linha_cad = cad_now.loc[cad_now["ativo"] == ativo_new].tail(1)
                            tipo_ativo = linha_cad["tipo de ativo"].iloc[0] if not linha_cad.empty else ""
                            cat = linha_cad["categoria"].iloc[0] if not linha_cad.empty else ""
                            cat2 = linha_cad["categoria 2"].iloc[0] if not linha_cad.empty else ""
                            catc = linha_cad["categoria comitê"].iloc[0] if not linha_cad.empty else ""

                            nova = {
                                "nome do fundo": fundo, "tipo do fundo": tipo_fundo,
                                "ativo": ativo_new, "valor": 0.0,
                                "categoria": cat, "categoria 2": cat2, "categoria comitê": catc,
                                "tipo de ativo": tipo_ativo, "liquidez": "",
                                "quantidade": None, "preco": None, "prazo_dias": None,
                            }
                            # garante colunas e insere sem concat (evita FutureWarning)
                            for col in nova.keys():
                                if col not in df_base.columns:
                                    df_base[col] = None
                            df_base.loc[len(df_base)] = nova

                # aplica ordens no fundo
                df_prop = aplicar_ordens_no_df(df_base, fundo, ordens)

                # PL proposto ex-Futuros
                st.session_state.pl_total = _pl_ex_futuros(df_prop, fundo)

                # ordem "dummy" só para contexto
                ordem_dummy = Ordem(fundo=fundo, ativo=ordens[0].ativo, quantidade=0.0, preco=0.0, tipo="compra")

                yaml_path_try = ["config/rules_config.yaml", "rules_config.yaml"]
                yaml_path = next((p for p in yaml_path_try if os.path.exists(p)), None)
                if not yaml_path:
                    st.error("Arquivo de configuração de regras não encontrado (tente 'config/rules_config.yaml' ou 'rules_config.yaml').")
                else:
                    rules_cfg = carregar_regras(yaml_path)
                    st.session_state.resultados = aplicar_regras(df_prop, ordem_dummy, rules_cfg)
                    st.session_state.fundo = fundo
                    st.session_state.ordens_lote = ordens
            except Exception as e:
                st.exception(e)

# ======================== RESULTADOS + PDF ========================
if st.session_state.get("resultados"):
    st.subheader("Resultado das Regras (lote)")
    for r in st.session_state.resultados:
        if r.regra == "enquadramento_cvm":
            regra_texto = "Política de investimento (Regulamento)"
        elif r.regra == "enquadramento_tributario":
            regra_texto = "Enquadramento Tributário"
        elif r.regra == "prazo_medio":
            regra_texto = "Prazo Médio"
        st.write(f"**{regra_texto}** → {'✅ OK' if r.passou else '❌ FALHOU'}")
        st.write(r.mensagem)

    pdf_bytes = build_pdf(
        resultados=st.session_state.resultados,
        ordens=st.session_state.get("ordens_lote", []),
        pl_total=st.session_state.get("pl_total"),
        fundo=st.session_state.get("fundo"),
    )
    st.download_button(
        label="⬇️ Baixar PDF",
        data=pdf_bytes,
        file_name=f"pre_trading - {fundo} - {str(datetime.now())[:10]}.pdf",
        mime="application/pdf",
    )