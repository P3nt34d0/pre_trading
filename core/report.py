from typing import List, Optional
from fpdf import FPDF
from datetime import datetime

# Tipos esperados (para type hints somente)
class RegraResultado:
    def __init__(self, regra: str, passou: bool, valor_atual: float, valor_proposto: float, limite: float, mensagem: str = ""):
        self.regra = regra
        self.passou = passou
        self.valor_atual = valor_atual
        self.valor_proposto = valor_proposto
        self.limite = limite
        self.mensagem = mensagem

class Ordem:
    def __init__(self, fundo: str, ativo: str, quantidade: float, preco: float, tipo: str):
        self.fundo = fundo
        self.ativo = ativo
        self.quantidade = quantidade
        self.preco = preco
        self.tipo = tipo  # "compra" | "venda"

# ---------------------- Helpers de formatação ----------------------

def _fmt_pct(x: float) -> str:
    try:
        return f"{x*100:,.2f}%".replace(",", "§").replace(".", ",").replace("§", ".")
    except Exception:
        return "-"

def _fmt_dias(x: float, dec: int = 1) -> str:
    try:
        s = f"{float(x):.{dec}f}".replace(".", ",")
        return f"{s} dias"
    except Exception:
        return "- dias"

def _fmt_brl(x: float) -> str:
    try:
        return f"R$ {float(x):,.2f}".replace(",", "§").replace(".", ",").replace("§", ".")
    except Exception:
        return f"R$ {x}"

def _total_ordem(o: Ordem) -> float:
    try:
        return float(o.quantidade) * float(o.preco)
    except Exception:
        return 0.0

# ---------------------- Classe PDF ----------------------

class SimplePDF(FPDF):
    def header(self):
        # Título sem caracteres unicode problemáticos
        self.set_font("helvetica", "B", 14)
        self.cell(0, 10, "Relatório de Pré-Trading - Validação de Regras", ln=True, align="C")
        self.ln(2)

    def footer(self):
        self.set_y(-15)
        self.set_font("helvetica", "I", 8)
        self.cell(0, 10, f"Pagina {self.page_no()}", align="C")
        # adiciona data/hora de geração (setada em build_pdf)
        dt_txt = getattr(self, "generated_at", "")
        self.cell(0, 10, f"Gerado em {dt_txt}", align="R")

# ---------------------- Seções ----------------------

def _sec_cabecalho(pdf: SimplePDF, fundo: Optional[str], pl_total: Optional[float]):
    pdf.set_font("helvetica", size=11)
    if fundo:
        pdf.cell(0, 8, f"Fundo: {fundo}", ln=True)
    if pl_total is not None:
        pdf.cell(0, 8, f"PL proposto: {_fmt_brl(pl_total)}", ln=True)
    pdf.ln(2)

def _sec_ordens(pdf: SimplePDF, ordens: List[Ordem]):
    pdf.set_font("helvetica", "B", 12)
    pdf.cell(0, 8, "Resumo das Ordens", ln=True)
    pdf.set_font("helvetica", size=10)

    # Cabeçalho
    col_w = [100, 25, 35, 30, 35]  # Ativo, Tipo, Quantidade, Preco, Total
    headers = ["Ativo", "Tipo", "Quantidade", "Preço", "Total"]
    for w, h in zip(col_w, headers):
        pdf.cell(w, 8, h, border=1, align="C")
    pdf.ln(8)

    if not ordens:
        pdf.cell(sum(col_w), 8, "Nenhuma ordem encontrada.", border=1, ln=True, align="C")
        pdf.ln(2)
        return

    # Linhas
    pdf.set_font("helvetica", size=10)
    for o in ordens:
        pdf.cell(col_w[0], 8, str(o.ativo)[:40], border=1)
        pdf.cell(col_w[1], 8, o.tipo.lower(), border=1, align="C")
        pdf.cell(col_w[2], 8, f"{float(o.quantidade):,.0f}".replace(",", "."),
                 border=1, align="R")
        pdf.cell(col_w[3], 8, _fmt_brl(o.preco), border=1, align="R")
        pdf.cell(col_w[4], 8, _fmt_brl(_total_ordem(o)), border=1, align="R")
        pdf.ln(8)
    pdf.ln(2)

def _sec_regras(pdf: SimplePDF, resultados: List[RegraResultado]):
    pdf.set_font("helvetica", "B", 12)
    pdf.cell(0, 8, "Detalhamento das Regras", ln=True)

    pdf.set_font("helvetica", "B", 10)
    # Tabela: Regra | Status | Atual | Proposto | Limite
    col_w = [55, 35, 55, 55, 25]
    headers = ["Regra", "Status", "Atual", "Proposto", "Limite"]
    for w, h in zip(col_w, headers):
        pdf.cell(w, 8, h, border=1, align="C")
    pdf.ln(8)

    pdf.set_font("helvetica", size=10)
    if not resultados:
        pdf.cell(sum(col_w), 8, "Sem resultados.", border=1, ln=True, align="C")
        return

    for r in resultados:
        # Colunas fixas
        if r.regra == "enquadramento_cvm":
            regra_texto = "Enquadramento CVM"
        elif r.regra == "enquadramento_tributario":
            regra_texto = "Enquadramento Tributário"
        elif r.regra == "prazo_medio":
            regra_texto = "Prazo Médio"
        pdf.cell(col_w[0], 8, regra_texto, border=1)
        pdf.cell(col_w[1], 8, "OK" if r.passou else "FALHOU", border=1, align="C")

        # Formatação condicional
        if r.regra == "prazo_medio":
            atual_txt = _fmt_dias(float(r.valor_atual), dec=1)
            prop_txt  = _fmt_dias(float(r.valor_proposto), dec=1)
            # limite geralmente inteiro (ex.: 365)
            try:
                limite_txt = _fmt_dias(float(r.limite), dec=0)
            except Exception:
                limite_txt = _fmt_dias(0.0, dec=0)
            pdf.cell(col_w[2], 8, atual_txt, border=1, align="R")
            pdf.cell(col_w[3], 8, prop_txt, border=1, align="R")
            pdf.cell(col_w[4], 8, limite_txt, border=1, align="R")
        else:
            # Demais regras em %
            atual_txt = _fmt_pct(float(r.valor_atual))
            prop_txt  = _fmt_pct(float(r.valor_proposto))
            # limite: se vier como fração (0–1), mostramos em %; se vier já em 0–100, também.
            try:
                lim = float(r.limite)
                # Se for típico (0.95), exibir 95,00%; se vier como 95, também formatamos como 95,00%
                lim_pct = lim if lim > 1 else lim * 100.0
                limite_txt = f"{lim_pct:,.2f}%".replace(",", "§").replace(".", ",").replace("§", ".")
            except Exception:
                limite_txt = "-"
            pdf.cell(col_w[2], 8, atual_txt, border=1, align="R")
            pdf.cell(col_w[3], 8, prop_txt, border=1, align="R")
            pdf.cell(col_w[4], 8, limite_txt, border=1, align="R")

        pdf.ln(8)

    pdf.ln(2)

def _sec_mensagens(pdf: SimplePDF, resultados: List[RegraResultado]):
    # Mensagens detalhadas por regra (se houver)
    msgs = [r for r in resultados if getattr(r, "mensagem", None)]
    if not msgs:
        return
    pdf.set_font("helvetica", "B", 12)
    pdf.cell(0, 8, "Observações", ln=True)
    pdf.set_font("helvetica", size=10)
    for r in msgs:
        pdf.multi_cell(0, 6, f"{r.mensagem}")
        pdf.ln(1)

# ---------------------- Builder principal ----------------------

def build_pdf(
    resultados: List[RegraResultado],
    ordens: Optional[List[Ordem]] = None,
    pl_total: Optional[float] = None,
    fundo: Optional[str] = None,
) -> bytes:
    """
    Monta o PDF e devolve bytes para o download.
    - 'resultados': lista de RegraResultado
    - 'ordens': lista completa de ordens do lote (mostra todas)
    - 'pl_total': PL proposto (para o cabeçalho)
    - 'fundo': nome do fundo
    """
    pdf = SimplePDF(orientation="L", unit="mm", format="A4")  # paisagem para caber a tabela
    pdf.set_auto_page_break(auto=True, margin=12)
    
    # define timestamp de geração (dd/mm/aaaa HH:MM:SS) para o rodapé
    pdf.generated_at = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    pdf.add_page()
    _sec_cabecalho(pdf, fundo, pl_total)
    _sec_ordens(pdf, ordens or [])
    _sec_regras(pdf, resultados or [])
    _sec_mensagens(pdf, resultados or [])

    # fpdf2 >= 2.7.x retorna 'bytes' (ou 'bytearray' em algumas versões).
    out = pdf.output(dest="S")
    return bytes(out)  # força bytes mesmo se vier bytearray