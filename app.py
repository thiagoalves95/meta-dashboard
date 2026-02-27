import io
import os
import re
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

from windsor_api import WindsorClient, GA4Client

load_dotenv()

st.set_page_config(
    page_title="Meta Dashboard — Performance Analytics",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ═══════════════════════════════════════════════════════════════════════════════
#  CONSTANTS & HELPERS
# ═══════════════════════════════════════════════════════════════════════════════
OBJECTIVE_CATEGORIES = {
    "Conversão (Vendas)": [
        "CONVERSIONS", "PRODUCT_CATALOG_SALES", "OUTCOME_SALES",
        "OUTCOME_LEADS", "LEAD_GENERATION",
    ],
    "Topo de Funil (Alcance/Engajamento)": [
        "REACH", "BRAND_AWARENESS", "OUTCOME_AWARENESS",
        "POST_ENGAGEMENT", "PAGE_LIKES", "EVENT_RESPONSES",
        "OUTCOME_ENGAGEMENT", "VIDEO_VIEWS", "MESSAGES",
        "LINK_CLICKS", "OUTCOME_TRAFFIC", "OUTCOME_APP_PROMOTION",
        "APP_INSTALLS",
    ],
}


def classify_objective(obj) -> str:
    if not obj or pd.isna(obj):
        return "Outros"
    obj_upper = str(obj).upper().strip()
    for cat, kws in OBJECTIVE_CATEGORIES.items():
        if obj_upper in kws:
            return cat
    return "Outros"


def safe_div(a, b, mult=1):
    return (a / b * mult) if b else 0


def col_sum(df, col):
    return df[col].sum() if col in df.columns else 0


def col_mean(df, col):
    return df[col].mean() if col in df.columns else 0


# ── Formatação brasileira ─────────────────────────────────────────────────────
def brl(v):
    """Formata valor como Real brasileiro: R$ 10.000,00"""
    if pd.isna(v) or v == 0:
        return "R$ 0,00"
    s = f"{abs(v):,.2f}"          # "10,000.00"
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")  # "10.000,00"
    return f"R$ {s}" if v >= 0 else f"-R$ {s}"


def fmt_int(v):
    """Formata inteiro com separador de milhar brasileiro: 10.000"""
    if pd.isna(v):
        return "0"
    s = f"{int(v):,}".replace(",", ".")
    return s


def fmt_pct(v, decimals=2):
    """Formata percentual: 12,34%"""
    if pd.isna(v):
        return "0,00%"
    return f"{v:.{decimals}f}".replace(".", ",") + "%"


def fmt_dec(v, decimals=2, suffix=""):
    """Formata decimal genérico com vírgula: 1,50x"""
    if pd.isna(v):
        return f"0,{'0' * decimals}{suffix}"
    return f"{v:.{decimals}f}".replace(".", ",") + suffix


FATIGUE_THRESHOLD = 2.5


def rag_status(value, target, inverse=False):
    """Return RAG (Red/Amber/Green) status based on target.
    inverse=True means lower is better (e.g. CPA)."""
    if target <= 0:
        return "neutral"
    if inverse:
        if value <= target:
            return "green"
        elif value <= target * 1.3:
            return "amber"
        return "red"
    else:
        if value >= target:
            return "green"
        elif value >= target * 0.7:
            return "amber"
        return "red"


def rag_html(status):
    """Return RAG colored indicator."""
    icons = {"green": "🟢", "amber": "🟡", "red": "🔴", "neutral": "⚪"}
    return icons.get(status, "⚪")


def _generate_smart_insights(total_spend, roas, cpa, ctr, avg_freq, total_purch,
                              target_roas, target_cpa, monthly_budget, d_roas, d_cpa):
    """Generate automatic actionable insights based on data."""
    insights = []

    # ROAS analysis
    if target_roas > 0:
        if roas >= target_roas:
            insights.append(f"✅ ROAS de **{fmt_dec(roas, suffix='x')}** está **acima** da meta ({fmt_dec(target_roas, suffix='x')}). Considere escalar o investimento.")
        elif roas >= target_roas * 0.7:
            insights.append(f"⚠️ ROAS de **{fmt_dec(roas, suffix='x')}** está **próximo** da meta ({fmt_dec(target_roas, suffix='x')}). Otimize criativos e públicos para atingir.")
        else:
            insights.append(f"🔴 ROAS de **{fmt_dec(roas, suffix='x')}** está **abaixo** da meta ({fmt_dec(target_roas, suffix='x')}). Revise estratégia de lance e público-alvo.")

    # CPA analysis
    if target_cpa > 0:
        if cpa <= target_cpa:
            insights.append(f"✅ CPA de **{brl(cpa)}** dentro da meta ({brl(target_cpa)}). Performance de conversão saudável.")
        else:
            _excess = cpa - target_cpa
            _waste = _excess * total_purch if total_purch > 0 else 0
            insights.append(f"🔴 CPA de **{brl(cpa)}** excede meta em **{brl(_excess)}/conversão**. Desperdício estimado: **{brl(_waste)}** no período.")

    # Frequency alert
    if avg_freq >= FATIGUE_THRESHOLD:
        insights.append(f"🔥 Frequência média de **{fmt_dec(avg_freq, 1)}** acima do limiar de fadiga ({FATIGUE_THRESHOLD}). Renove criativos para evitar ad fatigue.")

    # CTR benchmark
    if ctr < 1.0:
        insights.append(f"⚠️ CTR de **{fmt_pct(ctr)}** abaixo de 1%. Revise copywriting, headlines e criativos.")
    elif ctr > 3.0:
        insights.append(f"✅ CTR de **{fmt_pct(ctr)}** excelente (acima de 3%). Os criativos estão gerando alto engajamento.")

    # Trend analysis
    if d_roas is not None:
        if d_roas < -20:
            insights.append(f"📉 ROAS caiu **{abs(d_roas):.1f}%** vs período anterior. Investigue criativos saturados e mudanças no público.")
        elif d_roas > 20:
            insights.append(f"📈 ROAS subiu **{d_roas:.1f}%** vs período anterior. Identifique o que está funcionando e escale.")

    return insights


def _generate_recommendations(roas, cpa, ctr, avg_freq, target_roas, target_cpa, ca_df=None):
    """Generate automatic recommendations based on performance data."""
    recs = []

    if target_roas > 0 and roas < target_roas * 0.7:
        recs.append(("🛑 Revisão Urgente de Estratégia",
                      "ROAS muito abaixo da meta. Pause campanhas com ROAS < 1x e redirecione orçamento para campanhas eficientes."))

    if target_cpa > 0 and cpa > target_cpa * 1.5:
        recs.append(("💰 Otimizar CPA",
                      f"CPA {fmt_pct(((cpa / target_cpa) - 1) * 100)} acima da meta. Teste novos públicos, ajuste lances e revise páginas de destino."))

    if avg_freq >= FATIGUE_THRESHOLD:
        recs.append(("🔄 Rotação de Criativos",
                      f"Frequência média de {fmt_dec(avg_freq, 1)} indica saturação. Crie 3-5 novos criativos variando formato, copy e CTA."))

    if ctr < 1.0:
        recs.append(("✍️ Melhorar Criativos",
                      "CTR abaixo de 1% indica baixa relevância. Teste headlines com urgência, prova social e benefícios claros."))

    if ca_df is not None and not ca_df.empty and "purchases" in ca_df.columns:
        zero_conv = ca_df[(ca_df["spend"] > 0) & (ca_df["purchases"] == 0)]
        if not zero_conv.empty:
            _waste = zero_conv["spend"].sum()
            recs.append(("🗑️ Eliminar Desperdício",
                          f"{len(zero_conv)} criativos com gasto ({brl(_waste)}) e 0 conversões. Pause ou otimize."))

    if not recs:
        recs.append(("✅ Performance Saudável",
                      "Métricas dentro dos parâmetros. Continue monitorando e teste variações incrementais."))

    return recs


def _is_paid_traffic(df):
    """Filter GA4 data for paid Meta/Facebook/Instagram traffic."""
    if df.empty:
        return df
    mask = pd.Series(False, index=df.index)
    if "source" in df.columns:
        src = df["source"].astype(str).str.lower()
        mask = mask | src.isin(["facebook", "fb", "meta", "instagram", "ig"])
    if "medium" in df.columns:
        med = df["medium"].astype(str).str.lower()
        mask = mask | med.str.contains("cpc|paid|cpm", na=False, regex=True)
    return df[mask]


def _normalise_campaign_name(name):
    """Normalise campaign name for fuzzy matching."""
    if pd.isna(name):
        return ""
    return re.sub(r"[^a-z0-9]", "", str(name).lower().strip())


PLOTLY_TRANSPARENT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#E5E7EB", family="Inter, sans-serif", size=12),
)

CHART_DEFAULTS = dict(
    **PLOTLY_TRANSPARENT,
    hoverlabel=dict(
        bgcolor="rgba(30,30,46,0.95)", font_size=13, font_color="#FAFAFA",
        bordercolor="rgba(255,255,255,0.1)",
        font_family="Inter, sans-serif",
    ),
    hovermode="x unified",
)

# Reusable axis / legend / margin defaults (applied per-chart to avoid kwarg conflicts)
_GRID = "rgba(255,255,255,0.04)"
_ZERO = "rgba(255,255,255,0.06)"
_LEGEND_BOTTOM = dict(orientation="h", y=-0.15, xanchor="center", x=0.5,
                      font=dict(size=11, color="#9CA3AF"), bgcolor="rgba(0,0,0,0)")
_MARGIN = dict(l=10, r=10, t=10, b=10)


def _add_annotations(fig, x_series, y_series, fmt_fn=None):
    """Add max, min, mean annotations to a plotly figure."""
    if y_series is None or len(y_series) == 0:
        return
    y_vals = pd.to_numeric(y_series, errors="coerce").dropna()
    if y_vals.empty:
        return
    max_i = y_vals.idxmax()
    min_i = y_vals.idxmin()
    mean_v = y_vals.mean()
    fmt = fmt_fn or (lambda v: f"{v:,.0f}")
    fig.add_annotation(
        x=x_series.loc[max_i], y=y_vals.loc[max_i],
        text=f"Max: {fmt(y_vals.loc[max_i])}",
        showarrow=True, arrowhead=2, arrowcolor="#4ADE80",
        font=dict(color="#4ADE80", size=11, family="Inter, sans-serif"),
        bgcolor="rgba(26,26,46,0.85)", borderpad=4,
        bordercolor="rgba(74,222,128,0.3)", borderwidth=1,
    )
    fig.add_annotation(
        x=x_series.loc[min_i], y=y_vals.loc[min_i],
        text=f"Min: {fmt(y_vals.loc[min_i])}",
        showarrow=True, arrowhead=2, arrowcolor="#F87171",
        font=dict(color="#F87171", size=11, family="Inter, sans-serif"),
        bgcolor="rgba(26,26,46,0.85)", borderpad=4,
        bordercolor="rgba(248,113,113,0.3)", borderwidth=1,
    )
    fig.add_hline(
        y=mean_v, line_dash="dot", line_color="rgba(255,255,255,0.12)",
        annotation_text=f"Média: {fmt(mean_v)}",
        annotation_font_color="#9CA3AF", annotation_font_size=10,
    )


def _delta_pct(curr, prev):
    """Compute percentage change; returns None if previous is 0."""
    if not prev:
        return None
    return (curr - prev) / prev * 100


def _delta_str(val):
    """Format delta as string for st.metric."""
    if val is None:
        return None
    return f"{val:+.1f}%"


def _insight_badge(val, good_threshold, bad_threshold, inverse=False):
    """Return HTML badge based on thresholds."""
    if val is None:
        return ""
    if inverse:
        if val <= good_threshold:
            return '<span class="badge-good">Bom</span>'
        elif val >= bad_threshold:
            return '<span class="badge-bad">Ruim</span>'
        return '<span class="badge-warn">Atenção</span>'
    else:
        if val >= good_threshold:
            return '<span class="badge-good">Bom</span>'
        elif val <= bad_threshold:
            return '<span class="badge-bad">Ruim</span>'
        return '<span class="badge-warn">Atenção</span>'


def _to_csv(df):
    """Convert dataframe to CSV bytes for download."""
    buf = io.BytesIO()
    df.to_csv(buf, index=False, encoding="utf-8-sig")
    return buf.getvalue()

# ═══════════════════════════════════════════════════════════════════════════════
#  CSS
# ═══════════════════════════════════════════════════════════════════════════════
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');

    :root{
        --color-positive:#22C55E;--color-negative:#EF4444;--color-warning:#F59E0B;
        --color-info:#3B82F6;--color-meta:#FF6B00;--color-ga4:#0EA5E9;
        --color-surface:rgba(26,26,46,0.65);--color-surface-solid:#1a1a2e;
        --color-bg:#0E1117;--color-border:rgba(255,255,255,0.06);
        --color-text:#FAFAFA;--color-text-secondary:#9CA3AF;
        --shadow-sm:0 2px 8px rgba(0,0,0,0.25);
        --shadow-md:0 4px 16px rgba(0,0,0,0.35);
        --shadow-lg:0 8px 32px rgba(0,0,0,0.45);
        --radius-sm:8px;--radius-md:12px;--radius-lg:16px;
        --font-main:'Inter',sans-serif;
    }

    /* ── Global Font ──────────────────────────────────────────────── */
    html,body,[class*="css"],
    .stMarkdown,.stButton>button,
    [data-testid="stSidebar"],
    .stSelectbox,[data-testid="stForm"],
    input,textarea,select{
        font-family:var(--font-main)!important;
    }

    /* ── Hide Streamlit Chrome ────────────────────────────────────── */
    #MainMenu{visibility:hidden}
    footer{visibility:hidden}
    [data-testid="stHeader"]{background:transparent!important;backdrop-filter:blur(8px)}
    [data-testid="stToolbar"]{display:none!important}
    [data-testid="stDecoration"]{display:none!important}
    .block-container{padding-top:1rem;padding-bottom:1rem}

    /* ── Custom Scrollbar ─────────────────────────────────────────── */
    ::-webkit-scrollbar{width:6px;height:6px}
    ::-webkit-scrollbar-track{background:transparent}
    ::-webkit-scrollbar-thumb{background:rgba(255,255,255,0.1);border-radius:3px}
    ::-webkit-scrollbar-thumb:hover{background:rgba(255,255,255,0.2)}

    /* ── Section Headers ──────────────────────────────────────────── */
    .section-header{
        position:relative;padding:10px 18px 10px 20px;
        margin:32px 0 16px;font-size:1rem;font-weight:700;
        color:var(--color-text);letter-spacing:-0.01em;
        border-left:none;border-radius:var(--radius-sm);
        background:linear-gradient(135deg,rgba(255,107,0,0.06) 0%,transparent 60%);
    }
    .section-header::before{
        content:'';position:absolute;left:0;top:0;bottom:0;width:4px;
        border-radius:4px;
        background:linear-gradient(180deg,var(--color-meta),#FF9E45);
    }
    .sh-blue{background:linear-gradient(135deg,rgba(59,130,246,0.06) 0%,transparent 60%)}
    .sh-blue::before{background:linear-gradient(180deg,var(--color-info),#60A5FA)!important}
    .sh-green{background:linear-gradient(135deg,rgba(34,197,94,0.06) 0%,transparent 60%)}
    .sh-green::before{background:linear-gradient(180deg,var(--color-positive),#4ADE80)!important}
    .sh-purple{background:linear-gradient(135deg,rgba(168,85,247,0.06) 0%,transparent 60%)}
    .sh-purple::before{background:linear-gradient(180deg,#A855F7,#C084FC)!important}
    .sh-red{background:linear-gradient(135deg,rgba(239,68,68,0.06) 0%,transparent 60%)}
    .sh-red::before{background:linear-gradient(180deg,var(--color-negative),#F87171)!important}
    .sh-teal{background:linear-gradient(135deg,rgba(14,165,233,0.06) 0%,transparent 60%)}
    .sh-teal::before{background:linear-gradient(180deg,var(--color-ga4),#38BDF8)!important}

    /* ── KPI Grid ─────────────────────────────────────────────────── */
    .kpi-grid{
        display:grid;gap:12px;margin:8px 0 20px;
        grid-template-columns:repeat(auto-fit,minmax(160px,1fr));
    }
    .kpi-card{
        background:var(--color-surface);
        backdrop-filter:blur(12px);
        border:1px solid var(--color-border);
        border-radius:var(--radius-md);
        padding:16px 18px 14px;
        transition:all 0.25s ease;
        position:relative;overflow:hidden;
        box-shadow:var(--shadow-sm);
    }
    .kpi-card:hover{
        transform:translateY(-2px);
        box-shadow:var(--shadow-md);
        border-color:rgba(255,255,255,0.12);
    }
    .kpi-card::before{
        content:'';position:absolute;top:0;left:0;right:0;height:3px;
        background:linear-gradient(90deg,var(--color-meta),#FF9E45);opacity:0.5;
        border-radius:var(--radius-md) var(--radius-md) 0 0;
    }
    .kpi-card.rag-green::before{background:linear-gradient(90deg,var(--color-positive),#4ADE80);opacity:0.8}
    .kpi-card.rag-amber::before{background:linear-gradient(90deg,var(--color-warning),#FBBF24);opacity:0.8}
    .kpi-card.rag-red::before{background:linear-gradient(90deg,var(--color-negative),#F87171);opacity:0.8}
    .kpi-card .kpi-icon{font-size:1.3rem;margin-bottom:4px;display:block;opacity:0.85}
    .kpi-card .kpi-label{
        font-size:.72rem;font-weight:600;text-transform:uppercase;
        letter-spacing:0.06em;color:var(--color-text-secondary);margin-bottom:6px;
    }
    .kpi-card .kpi-value{
        font-size:1.6rem;font-weight:800;color:var(--color-text);
        line-height:1.2;letter-spacing:-0.02em;
    }
    .kpi-card .kpi-delta{
        font-size:.78rem;font-weight:600;margin-top:6px;display:inline-flex;
        align-items:center;gap:3px;padding:2px 8px;border-radius:20px;
    }
    .kpi-card .kpi-delta.positive{color:#4ADE80;background:rgba(34,197,94,0.12)}
    .kpi-card .kpi-delta.negative{color:#F87171;background:rgba(239,68,68,0.12)}
    .kpi-card .kpi-delta.neutral{color:var(--color-text-secondary);background:rgba(255,255,255,0.05)}

    /* ── KPI Group (legacy wrapper) ───────────────────────────────── */
    .kpi-group{
        background:transparent;border-radius:var(--radius-md);
        padding:0;margin:8px 0 18px;
    }

    /* ── Insight Box ───────────────────────────────────────────────── */
    .insight-box{
        background:linear-gradient(135deg,rgba(59,130,246,0.06) 0%,rgba(59,130,246,0.02) 100%);
        border:1px solid rgba(59,130,246,0.18);backdrop-filter:blur(8px);
        border-radius:var(--radius-md);padding:16px 20px;
        margin:0 0 18px;font-size:.9rem;line-height:1.75;
        color:#D1D5DB;position:relative;overflow:hidden;
    }
    .insight-box::before{
        content:'';position:absolute;left:0;top:0;bottom:0;width:4px;
        background:linear-gradient(180deg,var(--color-info),#60A5FA);
        border-radius:4px 0 0 4px;
    }
    .insight-box b{color:var(--color-text)}

    /* ── Badges ────────────────────────────────────────────────────── */
    .badge-good{
        display:inline-block;background:linear-gradient(135deg,#22C55E,#16A34A);color:#fff;
        padding:3px 12px;border-radius:20px;font-size:.75rem;font-weight:600;
        box-shadow:0 2px 6px rgba(34,197,94,0.3);
    }
    .badge-bad{
        display:inline-block;background:linear-gradient(135deg,#EF4444,#DC2626);color:#fff;
        padding:3px 12px;border-radius:20px;font-size:.75rem;font-weight:600;
        box-shadow:0 2px 6px rgba(239,68,68,0.3);
    }
    .badge-warn{
        display:inline-block;background:linear-gradient(135deg,#F59E0B,#D97706);color:#fff;
        padding:3px 12px;border-radius:20px;font-size:.75rem;font-weight:600;
        box-shadow:0 2px 6px rgba(245,158,11,0.3);
    }

    /* ── Main Title ────────────────────────────────────────────────── */
    .main-title{
        background:linear-gradient(135deg,rgba(30,30,46,0.8) 0%,rgba(26,26,46,0.6) 100%);
        border:1px solid var(--color-border);backdrop-filter:blur(16px);
        border-radius:var(--radius-lg);padding:20px 28px;
        text-align:center;margin-bottom:24px;box-shadow:var(--shadow-md);
        position:relative;overflow:hidden;
    }
    .main-title::before{
        content:'';position:absolute;top:0;left:0;right:0;height:3px;
        background:linear-gradient(90deg,var(--color-meta),var(--color-ga4));
    }
    .main-title h1{
        font-size:1.35rem;font-weight:800;letter-spacing:-0.02em;
        background:linear-gradient(135deg,#FAFAFA 0%,#D1D5DB 100%);
        -webkit-background-clip:text;-webkit-text-fill-color:transparent;
        background-clip:text;margin:0 0 6px;
    }
    .main-title .subtitle{font-size:.8rem;color:var(--color-text-secondary);margin:0;font-weight:400}
    .main-title .ic-meta{
        display:inline-flex;align-items:center;justify-content:center;
        width:22px;height:22px;border-radius:6px;font-size:.7rem;
        background:linear-gradient(135deg,#FF6B00,#FF9E45);
        color:#fff;margin-right:8px;vertical-align:middle;font-weight:800;
    }
    .main-title .ic-ga4{
        display:inline-flex;align-items:center;justify-content:center;
        width:22px;height:22px;border-radius:6px;font-size:.7rem;
        background:linear-gradient(135deg,#0EA5E9,#38BDF8);
        color:#fff;margin-left:8px;vertical-align:middle;font-weight:800;
    }

    /* ── Alert Boxes ───────────────────────────────────────────────── */
    .alert-box{
        background:linear-gradient(135deg,rgba(198,40,40,0.15),rgba(198,40,40,0.08));
        border:1px solid rgba(198,40,40,0.4);color:#fff;
        padding:14px 18px;border-radius:var(--radius-md);margin:8px 0;font-weight:600;
        backdrop-filter:blur(8px);
    }
    .alert-box-warn{
        background:linear-gradient(135deg,rgba(230,81,0,0.15),rgba(230,81,0,0.08));
        border:1px solid rgba(230,81,0,0.4);color:#fff;
        padding:14px 18px;border-radius:var(--radius-md);margin:8px 0;font-weight:600;
        backdrop-filter:blur(8px);
    }

    /* ── RAG Colors ────────────────────────────────────────────────── */
    .rag-green{color:var(--color-positive);font-weight:700}
    .rag-amber{color:var(--color-warning);font-weight:700}
    .rag-red{color:var(--color-negative);font-weight:700}

    /* ── Recommendation Box ────────────────────────────────────────── */
    .recommendation-box{
        background:linear-gradient(135deg,rgba(34,197,94,0.06) 0%,rgba(34,197,94,0.02) 100%);
        border:1px solid rgba(34,197,94,0.18);backdrop-filter:blur(8px);
        border-radius:var(--radius-md);padding:16px 20px;
        margin:8px 0;font-size:.9rem;line-height:1.75;color:#D1D5DB;
        position:relative;overflow:hidden;
    }
    .recommendation-box::before{
        content:'';position:absolute;left:0;top:0;bottom:0;width:4px;
        background:linear-gradient(180deg,var(--color-positive),#4ADE80);
        border-radius:4px 0 0 4px;
    }
    .recommendation-box b{color:var(--color-text)}

    /* ── Bottleneck Box ────────────────────────────────────────────── */
    .bottleneck-box{
        background:linear-gradient(135deg,rgba(239,68,68,0.06) 0%,rgba(239,68,68,0.02) 100%);
        border:1px solid rgba(239,68,68,0.2);backdrop-filter:blur(8px);
        border-radius:var(--radius-md);padding:16px 20px;
        margin:8px 0;font-size:.9rem;line-height:1.75;color:#D1D5DB;
        position:relative;overflow:hidden;
    }
    .bottleneck-box::before{
        content:'';position:absolute;left:0;top:0;bottom:0;width:4px;
        background:linear-gradient(180deg,var(--color-negative),#F87171);
        border-radius:4px 0 0 4px;
    }
    .bottleneck-box b{color:var(--color-text)}

    /* ── Pacing Boxes ──────────────────────────────────────────────── */
    .pacing-ok{
        background:linear-gradient(135deg,rgba(34,197,94,0.08),rgba(34,197,94,0.03));
        border:1px solid rgba(34,197,94,0.2);backdrop-filter:blur(8px);
        border-radius:var(--radius-md);padding:14px 18px;margin:8px 0;
    }
    .pacing-warn{
        background:linear-gradient(135deg,rgba(245,158,11,0.08),rgba(245,158,11,0.03));
        border:1px solid rgba(245,158,11,0.2);backdrop-filter:blur(8px);
        border-radius:var(--radius-md);padding:14px 18px;margin:8px 0;
    }
    .pacing-danger{
        background:linear-gradient(135deg,rgba(239,68,68,0.08),rgba(239,68,68,0.03));
        border:1px solid rgba(239,68,68,0.2);backdrop-filter:blur(8px);
        border-radius:var(--radius-md);padding:14px 18px;margin:8px 0;
    }

    /* ── Cost of Inaction ──────────────────────────────────────────── */
    .cost-inaction{
        background:linear-gradient(135deg,rgba(239,68,68,0.08),rgba(239,68,68,0.03));
        border:1px solid rgba(239,68,68,0.25);backdrop-filter:blur(8px);
        border-radius:var(--radius-md);padding:16px 20px;margin:8px 0;font-size:.9rem;
        position:relative;overflow:hidden;
    }
    .cost-inaction::before{
        content:'';position:absolute;left:0;top:0;bottom:0;width:4px;
        background:linear-gradient(180deg,var(--color-negative),#F87171);
        border-radius:4px 0 0 4px;
    }

    /* ── Tabs Styling ──────────────────────────────────────────────── */
    .stTabs [data-baseweb="tab-list"]{
        background:rgba(26,26,46,0.5);border-radius:var(--radius-md);
        padding:4px;gap:4px;border:1px solid var(--color-border);
    }
    .stTabs [data-baseweb="tab"]{
        border-radius:var(--radius-sm)!important;padding:8px 20px!important;
        font-weight:600!important;font-size:.85rem!important;
        color:var(--color-text-secondary)!important;
        transition:all 0.2s ease!important;
        background:transparent!important;border:none!important;
    }
    .stTabs [data-baseweb="tab"]:hover{
        color:var(--color-text)!important;
        background:rgba(255,255,255,0.04)!important;
    }
    .stTabs [aria-selected="true"]{
        background:linear-gradient(135deg,rgba(255,107,0,0.15),rgba(255,107,0,0.08))!important;
        color:var(--color-text)!important;
        box-shadow:0 2px 8px rgba(255,107,0,0.15)!important;
    }
    .stTabs [data-baseweb="tab-highlight"]{display:none!important}
    .stTabs [data-baseweb="tab-border"]{display:none!important}

    /* ── Sidebar ───────────────────────────────────────────────────── */
    [data-testid="stSidebar"]{
        background:linear-gradient(180deg,#12121f 0%,#0E1117 100%)!important;
        border-right:1px solid var(--color-border)!important;
    }
    [data-testid="stSidebar"] .stButton>button{
        border-radius:var(--radius-sm)!important;font-weight:600!important;
        transition:all 0.2s ease!important;border:1px solid var(--color-border)!important;
    }
    [data-testid="stSidebar"] .stButton>button:hover{
        border-color:rgba(255,107,0,0.4)!important;
        box-shadow:0 2px 8px rgba(255,107,0,0.15)!important;
    }

    /* ── Dataframe / Table Styling ─────────────────────────────────── */
    [data-testid="stDataFrame"]{
        border-radius:var(--radius-md)!important;overflow:hidden;
        border:1px solid var(--color-border)!important;
    }

    /* ── Expander Styling ──────────────────────────────────────────── */
    .streamlit-expanderHeader{
        font-weight:600!important;font-size:.9rem!important;
        border-radius:var(--radius-sm)!important;
    }

    /* ── Download Button ───────────────────────────────────────────── */
    .stDownloadButton>button{
        border-radius:var(--radius-sm)!important;font-weight:600!important;
        border:1px solid var(--color-border)!important;
        transition:all 0.2s ease!important;
    }
    .stDownloadButton>button:hover{
        border-color:rgba(255,107,0,0.4)!important;
        box-shadow:0 2px 8px rgba(255,107,0,0.15)!important;
    }

    /* ── Form Submit Button ────────────────────────────────────────── */
    [data-testid="stForm"] .stButton>button[kind="primaryFormSubmit"]{
        background:linear-gradient(135deg,#FF6B00,#FF8C00)!important;
        color:#fff!important;border:none!important;font-weight:700!important;
        border-radius:var(--radius-sm)!important;
        box-shadow:0 4px 12px rgba(255,107,0,0.3)!important;
        transition:all 0.2s ease!important;
    }
    [data-testid="stForm"] .stButton>button[kind="primaryFormSubmit"]:hover{
        box-shadow:0 6px 20px rgba(255,107,0,0.4)!important;
        transform:translateY(-1px)!important;
    }

    /* ── Sidebar Branding ──────────────────────────────────────────── */
    .sidebar-brand{
        text-align:center;padding:16px 12px 20px;
        border-bottom:1px solid var(--color-border);margin-bottom:12px;
    }
    .sidebar-brand .brand-logo{
        font-size:1.6rem;font-weight:800;letter-spacing:-0.03em;
        background:linear-gradient(135deg,var(--color-meta),#FF9E45);
        -webkit-background-clip:text;-webkit-text-fill-color:transparent;
        background-clip:text;
    }
    .sidebar-brand .brand-sub{
        font-size:.72rem;color:var(--color-text-secondary);
        text-transform:uppercase;letter-spacing:0.1em;margin-top:2px;
    }

    /* ── Status Badge (Live) ───────────────────────────────────────── */
    .status-live{
        display:inline-flex;align-items:center;gap:6px;
        background:rgba(34,197,94,0.12);border:1px solid rgba(34,197,94,0.25);
        padding:3px 12px;border-radius:20px;font-size:.72rem;font-weight:600;
        color:#4ADE80;
    }
    .status-live .dot{
        width:6px;height:6px;border-radius:50%;background:#4ADE80;
        animation:pulse-dot 2s ease-in-out infinite;
    }
    @keyframes pulse-dot{
        0%,100%{opacity:1;transform:scale(1)}
        50%{opacity:0.5;transform:scale(0.8)}
    }

    /* ── Hide default metric styling (replaced by kpi_card) ──────── */
    [data-testid="stMetricValue"]{font-size:1.6rem!important;font-weight:800!important;letter-spacing:-0.02em!important}
    [data-testid="stMetricLabel"]{font-size:.72rem!important;color:var(--color-text-secondary)!important;text-transform:uppercase!important;letter-spacing:0.05em!important;font-weight:600!important}
    [data-testid="stMetricDelta"]{font-size:.78rem!important;font-weight:600!important}

    /* ── Preset Buttons ────────────────────────────────────────────── */
    .preset-btn button{font-size:.78rem!important;padding:4px 8px!important}
</style>
""", unsafe_allow_html=True)

H = lambda text, cls="": f'<div class="section-header {cls}">{text}</div>'


def kpi_card(label, value, delta=None, icon=None, rag=None, delta_inverse=False):
    """Render a professional KPI card as HTML."""
    rag_cls = f" rag-{rag}" if rag and rag in ("green", "amber", "red") else ""
    icon_html = f'<span class="kpi-icon">{icon}</span>' if icon else ""

    delta_html = ""
    if delta is not None:
        ds = str(delta)
        if ds.startswith("+"):
            if delta_inverse:
                dcls = "negative"
                arrow = "&#9650;"
            else:
                dcls = "positive"
                arrow = "&#9650;"
        elif ds.startswith("-"):
            if delta_inverse:
                dcls = "positive"
                arrow = "&#9660;"
            else:
                dcls = "negative"
                arrow = "&#9660;"
        else:
            dcls = "neutral"
            arrow = ""
        delta_html = f'<span class="kpi-delta {dcls}">{arrow} {ds}</span>'

    return (
        f'<div class="kpi-card{rag_cls}">'
        f'{icon_html}'
        f'<div class="kpi-label">{label}</div>'
        f'<div class="kpi-value">{value}</div>'
        f'{delta_html}'
        f'</div>'
    )


def kpi_row(cards_html):
    """Wrap KPI cards in a responsive grid."""
    return f'<div class="kpi-grid">{"".join(cards_html)}</div>'


_title_placeholder = st.empty()

# ═══════════════════════════════════════════════════════════════════════════════
#  SIDEBAR — SEARCH FORM (batched — no reload until "Buscar" is clicked)
# ═══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown(
        '<div class="sidebar-brand">'
        '<div class="brand-logo">Meta Dashboard</div>'
        '<div class="brand-sub">Analytics &amp; Performance</div>'
        '</div>',
        unsafe_allow_html=True,
    )
    api_key = st.text_input(
        "Windsor.ai API Key",
        value=os.getenv("WINDSOR_API_KEY", ""),
        type="password",
    )
    st.caption("Período rápido")
    _pc = st.columns(5)
    for _i, (_lbl, _days) in enumerate([("7d", 7), ("14d", 14), ("30d", 30), ("60d", 60), ("90d", 90)]):
        if _pc[_i].button(_lbl, key=f"preset_{_days}", use_container_width=True):
            st.session_state["_pf"] = date.today() - timedelta(days=_days)
            st.session_state["_pt"] = date.today()
    _def_from = st.session_state.get("_pf", date.today() - timedelta(days=30))
    _def_to = st.session_state.get("_pt", date.today())
    with st.form("search_form"):
        st.header("Configurações")
        date_from = st.date_input("Data inicial", value=_def_from)
        date_to = st.date_input("Data final", value=_def_to)

        cached_accounts = st.session_state.get("_accounts", [])
        sel_account = st.selectbox("Conta de Anúncios", ["Todas as contas"] + cached_accounts)

        fetch = st.form_submit_button("🔍 Buscar dados", use_container_width=True)

if not api_key:
    st.info("Insira sua API Key na barra lateral para começar.")
    st.stop()

acct = None if sel_account == "Todas as contas" else sel_account


@st.cache_data(ttl=600, show_spinner=False)
def load_accounts(key, dfrom, dto):
    return WindsorClient(key).get_accounts(dfrom, dto, progress_cb=None)


# ═══════════════════════════════════════════════════════════════════════════════
#  DATA LOADING — only triggered by "Buscar dados" button
# ═══════════════════════════════════════════════════════════════════════════════
if fetch:
    try:
        c = WindsorClient(api_key)
        dfrom, dto = str(date_from), str(date_to)

        progress = st.progress(0, text="Carregando contas…")
        accounts = load_accounts(api_key, dfrom, dto)
        st.session_state["_accounts"] = accounts

        progress.progress(0.15, text="Carregando campanhas (agregado mensal)…")
        camp = c.get_campaign_data(dfrom, dto, acct)

        progress.progress(0.40, text="Carregando conjuntos de anúncios…")
        adset = c.get_adset_data(dfrom, dto, acct)

        progress.progress(0.60, text="Carregando anúncios / criativos…")
        ad = c.get_ad_data(dfrom, dto, acct)

        progress.progress(0.80, text="Carregando período anterior…")
        _period_days = (date_to - date_from).days
        _prev_to = date_from - timedelta(days=1)
        _prev_from = _prev_to - timedelta(days=_period_days)
        try:
            camp_prev = c.get_campaign_data(str(_prev_from), str(_prev_to), acct)
        except Exception:
            camp_prev = pd.DataFrame()

        progress.progress(1.0, text="Dados carregados!")
        progress.empty()

        st.session_state.update(
            camp=camp, adset=adset, ad=ad, camp_prev=camp_prev,
            _data_loaded=True,
            # Clear lazy caches so they reload on next access
            _demo=None, _placement=None, _region=None,
            _daily_camp=None, _daily_ad=None,
            # Clear GA4 lazy caches
            _ga4_traffic=None, _ga4_conv=None, _ga4_device=None,
            _ga4_geo=None, _ga4_pages=None, _ga4_daily=None,
        )
    except Exception as exc:
        st.error(f"Erro ao buscar dados: {exc}")
        st.stop()

if "camp" not in st.session_state:
    st.info("Configure os filtros e clique em **🔍 Buscar dados** para carregar.")
    st.stop()

with st.sidebar:
    st.markdown('<div style="border-top:1px solid rgba(255,255,255,0.06);margin:8px 0 12px"></div>', unsafe_allow_html=True)
    obj_mode = st.radio(
        "Tipo de Campanha",
        ["Todas", "Conversão (Vendas)", "Topo de Funil (Alcance/Engajamento)"],
        help="Filtra campanhas pelo objetivo e adapta métricas.",
    )
    st.markdown('<div style="border-top:1px solid rgba(255,255,255,0.06);margin:8px 0 12px"></div>', unsafe_allow_html=True)
    st.markdown('<p style="font-size:.85rem;font-weight:700;color:#FAFAFA;margin:0 0 8px;letter-spacing:-0.01em">🎯 Metas & Orçamento</p>', unsafe_allow_html=True)
    monthly_budget = st.number_input(
        "Orçamento Mensal (R$)", min_value=0.0, value=0.0, step=500.0,
        help="Defina o orçamento mensal para acompanhar o pacing.",
    )
    target_roas = st.number_input(
        "Meta de ROAS", min_value=0.0, value=0.0, step=0.5,
        help="ROAS alvo. Usado para classificar performance (RAG).",
    )
    target_cpa = st.number_input(
        "Meta de CPA (R$)", min_value=0.0, value=0.0, step=10.0,
        help="CPA máximo aceitável. Usado para classificar performance (RAG).",
    )


def _lazy(key, loader):
    """Load data lazily and cache in session_state."""
    if st.session_state.get(key) is None:
        with st.spinner(f"Carregando {key.strip('_')}…"):
            st.session_state[key] = loader()
    return st.session_state[key]

df_camp = st.session_state["camp"].copy()
df_adset = st.session_state["adset"].copy()
df_ad = st.session_state["ad"].copy()

# ── Classify objectives (core dataframes) ────────────────────────────────────
def _classify(df):
    if "campaign_objective" in df.columns:
        df["_cat"] = df["campaign_objective"].apply(classify_objective)
    else:
        df["_cat"] = "Outros"
    return df

df_camp = _classify(df_camp)
df_adset = _classify(df_adset)
df_ad = _classify(df_ad)

if obj_mode != "Todas":
    df_camp = df_camp[df_camp["_cat"] == obj_mode]
    df_adset = df_adset[df_adset["_cat"] == obj_mode]
    df_ad = df_ad[df_ad["_cat"] == obj_mode]


# ── Helper: filter by campaign_id set (robust cross-level matching) ──────────
def _filter_by_ids(df, ids):
    """Filter dataframe by campaign_id set."""
    if "campaign_id" in df.columns:
        return df[df["campaign_id"].isin(ids)]
    if "campaign" in df.columns:
        # Fallback: map campaign names from df_camp for those IDs
        names = df_camp[df_camp["campaign_id"].isin(ids)]["campaign"].unique() \
            if "campaign_id" in df_camp.columns else set()
        return df[df["campaign"].isin(names)]
    return df


# ── Campaign filter ──────────────────────────────────────────────────────────
campaigns = (
    ["Todas"] + sorted(df_camp["campaign"].dropna().unique().tolist())
    if not df_camp.empty and "campaign" in df_camp.columns else ["Todas"]
)
with st.sidebar:
    sel_campaign = st.selectbox("Campanha", campaigns)

# Use campaign_id for cross-level filtering
sel_campaign_ids = set()
if sel_campaign != "Todas" and not df_camp.empty:
    if "campaign_id" in df_camp.columns:
        sel_campaign_ids = set(
            df_camp[df_camp["campaign"] == sel_campaign]["campaign_id"].dropna().unique()
        )
    # Filter core dataframes
    df_camp = df_camp[df_camp["campaign"] == sel_campaign]
    df_adset = _filter_by_ids(df_adset, sel_campaign_ids) if sel_campaign_ids else \
        df_adset[df_adset["campaign"] == sel_campaign] if not df_adset.empty else df_adset
    df_ad = _filter_by_ids(df_ad, sel_campaign_ids) if sel_campaign_ids else \
        df_ad[df_ad["campaign"] == sel_campaign] if not df_ad.empty else df_ad


# ── Keyword search filter ────────────────────────────────────────────────────
with st.sidebar:
    keyword = st.text_input(
        "Buscar por palavra-chave",
        placeholder="Ex: remarketing, vídeo, promo…",
        help="Filtra por nome de Campanha, Conjunto de Anúncios ou Criativo.",
    )

matched_ids = set()
if keyword:
    kw = keyword.strip().lower()

    def _kw_match(df, cols):
        """Return rows where any of `cols` contains the keyword (case-insensitive)."""
        mask = pd.Series(False, index=df.index)
        for c in cols:
            if c in df.columns:
                mask = mask | df[c].astype(str).str.lower().str.contains(kw, na=False)
        return df[mask]

    # Find matching campaign_ids across ALL levels
    for _df, _cols in [
        (df_camp, ["campaign"]),
        (df_adset, ["campaign", "adset_name"]),
        (df_ad, ["campaign", "adset_name", "ad_name"]),
    ]:
        if not _df.empty:
            hits = _kw_match(_df, _cols)
            if "campaign_id" in hits.columns:
                matched_ids.update(hits["campaign_id"].dropna().unique())
            elif "campaign" in hits.columns:
                # Fallback: resolve IDs via df_camp
                names = hits["campaign"].dropna().unique()
                if "campaign_id" in df_camp.columns:
                    matched_ids.update(
                        df_camp[df_camp["campaign"].isin(names)]["campaign_id"].dropna().unique()
                    )

    # Filter: keep FULL campaign if keyword matches at ANY level
    if matched_ids:
        df_camp = _filter_by_ids(df_camp, matched_ids) if not df_camp.empty else df_camp
        df_adset = _filter_by_ids(df_adset, matched_ids) if not df_adset.empty else df_adset
        df_ad = _filter_by_ids(df_ad, matched_ids) if not df_ad.empty else df_ad
    else:
        df_camp = df_camp.iloc[0:0]
        df_adset = df_adset.iloc[0:0]
        df_ad = df_ad.iloc[0:0]

if df_camp.empty:
    st.warning("Nenhum dado encontrado para os filtros selecionados.")
    st.stop()

# ── Lazy-loaded data helper ──────────────────────────────────────────────────
_c = WindsorClient(api_key)
_dfrom, _dto = str(date_from), str(date_to)

def _apply_filters(df):
    """Apply objective, campaign_id, and keyword filters to a lazy-loaded df."""
    df = _classify(df)
    if obj_mode != "Todas":
        df = df[df["_cat"] == obj_mode]
    if sel_campaign_ids:
        df = _filter_by_ids(df, sel_campaign_ids)
    elif sel_campaign != "Todas" and "campaign" in df.columns:
        df = df[df["campaign"] == sel_campaign]
    if keyword and matched_ids:
        df = _filter_by_ids(df, matched_ids)
    elif keyword:
        df = df.iloc[0:0]
    return df

def _get_demo():
    return _apply_filters(_lazy("_demo", lambda: _c.get_demo_data(_dfrom, _dto, acct)))

def _get_placement():
    return _apply_filters(_lazy("_placement", lambda: _c.get_placement_data(_dfrom, _dto, acct)))

def _get_region():
    return _apply_filters(_lazy("_region", lambda: _c.get_region_data(_dfrom, _dto, acct)))

def _get_daily_camp():
    return _apply_filters(_lazy("_daily_camp", lambda: _c.get_campaign_daily(_dfrom, _dto, acct)))

def _get_daily_ad():
    df = _lazy("_daily_ad", lambda: _c.get_ad_daily(_dfrom, _dto, acct))
    # Filter by ad_names from the already-filtered df_ad
    if not df_ad.empty and "ad_name" in df.columns:
        valid_ads = df_ad["ad_name"].unique()
        df = df[df["ad_name"].isin(valid_ads)]
    elif sel_campaign != "Todas" or keyword:
        df = df.iloc[0:0]
    return df


# ═══════════════════════════════════════════════════════════════════════════════
#  COMPUTED METRICS
# ═══════════════════════════════════════════════════════════════════════════════
S = lambda c: col_sum(df_camp, c)

total_imp = S("impressions")
total_clicks = S("clicks")
total_spend = S("spend")
total_reach = S("reach")
avg_freq = col_mean(df_camp, "frequency")
total_link_clicks = S("actions_link_click")
total_lpv = S("actions_landing_page_view")
total_atc = S("actions_add_to_cart")
total_ic = S("actions_initiate_checkout")
total_purch = S("actions_purchase")
total_rev = S("action_values_purchase")
total_leads = S("actions_lead")
total_engagement = S("actions_post_engagement")
total_reactions = S("actions_post_reaction")
total_comments = S("actions_comment")
total_saves = S("actions_post_save")
total_vv = S("video_views")
total_thruplay = S("video_thruplay_watched")

ctr = safe_div(total_clicks, total_imp, 100)
cpc = safe_div(total_spend, total_clicks)
cpm = safe_div(total_spend, total_imp, 1000)
cpa = safe_div(total_spend, total_purch)
roas = safe_div(total_rev, total_spend)
ticket_medio = safe_div(total_rev, total_purch)
cpl = safe_div(total_spend, total_leads)
cpr = safe_div(total_spend, total_reach, 1000)
cost_per_eng = safe_div(total_spend, total_engagement)
hook_rate = safe_div(total_vv, total_imp, 100)
hold_rate = safe_div(total_thruplay, total_vv, 100)

is_conv = obj_mode == "Conversão (Vendas)"
is_tofu = obj_mode == "Topo de Funil (Alcance/Engajamento)"

# ═══════════════════════════════════════════════════════════════════════════════
#  PREVIOUS-PERIOD DELTAS
# ═══════════════════════════════════════════════════════════════════════════════
_has_prev = "camp_prev" in st.session_state and not st.session_state["camp_prev"].empty
if _has_prev:
    _df_prev = _classify(st.session_state["camp_prev"].copy())
    if obj_mode != "Todas":
        _df_prev = _df_prev[_df_prev["_cat"] == obj_mode]
    _SP = lambda c: col_sum(_df_prev, c)
    _p_imp = _SP("impressions"); _p_clicks = _SP("clicks"); _p_spend = _SP("spend")
    _p_reach = _SP("reach"); _p_purch = _SP("actions_purchase")
    _p_rev = _SP("action_values_purchase"); _p_leads = _SP("actions_lead")
    _p_eng = _SP("actions_post_engagement"); _p_vv = _SP("video_views")
    _p_ctr = safe_div(_p_clicks, _p_imp, 100)
    _p_cpc = safe_div(_p_spend, _p_clicks)
    _p_cpm = safe_div(_p_spend, _p_imp, 1000)
    _p_cpa = safe_div(_p_spend, _p_purch)
    _p_roas = safe_div(_p_rev, _p_spend)
    _p_cpl = safe_div(_p_spend, _p_leads)
    _p_cpr = safe_div(_p_spend, _p_reach, 1000)
    _p_cost_eng = safe_div(_p_spend, _p_eng)
    d_imp = _delta_pct(total_imp, _p_imp); d_clicks = _delta_pct(total_clicks, _p_clicks)
    d_spend = _delta_pct(total_spend, _p_spend); d_reach = _delta_pct(total_reach, _p_reach)
    d_ctr = _delta_pct(ctr, _p_ctr); d_cpc = _delta_pct(cpc, _p_cpc)
    d_cpm = _delta_pct(cpm, _p_cpm); d_cpa = _delta_pct(cpa, _p_cpa)
    d_roas = _delta_pct(roas, _p_roas); d_purch = _delta_pct(total_purch, _p_purch)
    d_rev = _delta_pct(total_rev, _p_rev); d_cpl = _delta_pct(cpl, _p_cpl)
    d_cpr = _delta_pct(cpr, _p_cpr); d_cost_eng = _delta_pct(cost_per_eng, _p_cost_eng)
    d_eng = _delta_pct(total_engagement, _p_eng)
else:
    d_imp = d_clicks = d_spend = d_reach = d_ctr = d_cpc = d_cpm = d_cpa = None
    d_roas = d_purch = d_rev = d_cpl = d_cpr = d_cost_eng = d_eng = None

# ── Dynamic title ────────────────────────────────────────────────────────────
_acct_label = sel_account if sel_account != "Todas as contas" else "Todas as contas"
_camp_label = sel_campaign if sel_campaign != "Todas" else "Todas as campanhas"
_title_placeholder.markdown(
    f'<div class="main-title">'
    f'<h1><span class="ic-meta">M</span> PAINEL DE PERFORMANCE — META ADS + GA4 <span class="ic-ga4">G4</span></h1>'
    f'<p class="subtitle">'
    f'<span class="status-live"><span class="dot"></span>Live</span> '
    f'{date_from.strftime("%d %b %Y")} — {date_to.strftime("%d %b %Y")} · '
    f'Conta: {_acct_label} · {_camp_label}</p></div>',
    unsafe_allow_html=True,
)

# ═══════════════════════════════════════════════════════════════════════════════
#  GA4 LAZY LOADERS (must be defined before tabs that use them)
# ═══════════════════════════════════════════════════════════════════════════════
_ga4 = GA4Client(api_key)


def _get_ga4_traffic():
    return _lazy("_ga4_traffic", lambda: _ga4.get_ga4_traffic(_dfrom, _dto))


def _get_ga4_conv():
    return _lazy("_ga4_conv", lambda: _ga4.get_ga4_conversions(_dfrom, _dto))


def _get_ga4_device():
    return _lazy("_ga4_device", lambda: _ga4.get_ga4_device(_dfrom, _dto))


def _get_ga4_geo():
    return _lazy("_ga4_geo", lambda: _ga4.get_ga4_geo(_dfrom, _dto))


def _get_ga4_pages():
    return _lazy("_ga4_pages", lambda: _ga4.get_ga4_pages(_dfrom, _dto))


def _get_ga4_daily():
    return _lazy("_ga4_daily", lambda: _ga4.get_ga4_daily(_dfrom, _dto))


def _ga4_col(df, col):
    """Safely get a GA4 column, trying camelCase then snake_case."""
    if col in df.columns:
        return df[col]
    snake = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", col).lower()
    if snake in df.columns:
        return df[snake]
    return pd.Series(0, index=df.index)


def _ga4_col_sum(df, col):
    return _ga4_col(df, col).sum()


def _ga4_weighted_mean(df, metric_col, weight_col="sessions"):
    """Weighted average (rates weighted by sessions)."""
    m = _ga4_col(df, metric_col)
    w = _ga4_col(df, weight_col)
    total_w = w.sum()
    if total_w == 0:
        return 0
    return (m * w).sum() / total_w


# ═══════════════════════════════════════════════════════════════════════════════
#  TABS
# ═══════════════════════════════════════════════════════════════════════════════
tab_overview, tab_diagnostic, tab_funnel, tab_creative, tab_audience = st.tabs([
    "📊 Performance",
    "🩺 Diagnóstico & Ações",
    "🔻 Funil & Conversões",
    "🎨 Criativos",
    "👥 Audiência & Canais",
])

# ─────────────────────────────────────────────────────────────────────────────
#  TAB 1 — PERFORMANCE (overview + budget pacing + smart insights)
# ─────────────────────────────────────────────────────────────────────────────
with tab_overview:

    _n_camps = df_camp["campaign"].nunique() if "campaign" in df_camp.columns else 0

    # ── Budget Pacing ────────────────────────────────────────────────────
    if monthly_budget > 0:
        _period_days = (date_to - date_from).days + 1
        _days_in_month = 30
        _ideal_spend = monthly_budget * (_period_days / _days_in_month)
        _pacing_pct = safe_div(total_spend, _ideal_spend, 100)
        _remaining_budget = monthly_budget - total_spend
        _days_remaining = max(1, _days_in_month - _period_days)
        _daily_needed = safe_div(_remaining_budget, _days_remaining)

        if _pacing_pct <= 110:
            _pacing_cls = "pacing-ok"
            _pacing_icon = "✅"
        elif _pacing_pct <= 130:
            _pacing_cls = "pacing-warn"
            _pacing_icon = "⚠️"
        else:
            _pacing_cls = "pacing-danger"
            _pacing_icon = "🔴"

        st.markdown(
            f'<div class="{_pacing_cls}">'
            f'{_pacing_icon} <b>Budget Pacing:</b> {fmt_pct(_pacing_pct)} do ideal · '
            f'Gasto: <b>{brl(total_spend)}</b> de <b>{brl(monthly_budget)}</b> · '
            f'Restante: <b>{brl(_remaining_budget)}</b> · '
            f'Diário necessário: <b>{brl(_daily_needed)}/dia</b>'
            f'</div>', unsafe_allow_html=True,
        )

    # ── Insight Box com RAG ──────────────────────────────────────────────
    _roas_rag = rag_html(rag_status(roas, target_roas)) if target_roas > 0 else ""
    _cpa_rag = rag_html(rag_status(cpa, target_cpa, inverse=True)) if target_cpa > 0 else ""
    _roas_badge = _insight_badge(d_roas, 0, 0) if d_roas is not None else ""
    _prev_txt = f" {_roas_badge} vs período anterior" if d_roas is not None else ""

    st.markdown(
        f'<div class="insight-box">'
        f'Investimento de <b>{brl(total_spend)}</b> no período · '
        f'ROAS: {_roas_rag} <b>{fmt_dec(roas, suffix="x")}</b>{_prev_txt}<br>'
        f'CPA: {_cpa_rag} <b>{brl(cpa)}</b> · '
        f'<b>{_n_camps}</b> campanhas · CTR: <b>{fmt_pct(ctr)}</b> · '
        f'Frequência: <b>{fmt_dec(avg_freq, 1)}</b>'
        f'</div>', unsafe_allow_html=True,
    )

    # ── KPIs Tier 1 (max 6 — cognitive load research) ────────────────────
    _roas_rag_s = rag_status(roas, target_roas) if target_roas > 0 else None
    _cpa_rag_s = rag_status(cpa, target_cpa, inverse=True) if target_cpa > 0 else None

    if is_conv or obj_mode == "Todas":
        st.markdown(H("KPIs Estratégicos"), unsafe_allow_html=True)
        st.markdown(kpi_row([
            kpi_card("Valor Gasto", brl(total_spend), _delta_str(d_spend), "💰"),
            kpi_card("ROAS", fmt_dec(roas, suffix="x"), _delta_str(d_roas), "📈", _roas_rag_s),
            kpi_card("CPA", brl(cpa), _delta_str(d_cpa), "🎯", _cpa_rag_s, delta_inverse=True),
            kpi_card("Conversões", fmt_int(total_purch), _delta_str(d_purch), "🛒"),
            kpi_card("Receita", brl(total_rev), _delta_str(d_rev), "💎"),
            kpi_card("CTR", fmt_pct(ctr), _delta_str(d_ctr), "👆"),
        ]), unsafe_allow_html=True)

        with st.expander("📋 KPIs Secundários"):
            st.markdown(kpi_row([
                kpi_card("Impressões", fmt_int(total_imp), _delta_str(d_imp), "👁️"),
                kpi_card("Cliques", fmt_int(total_clicks), _delta_str(d_clicks), "🖱️"),
                kpi_card("CPC", brl(cpc), _delta_str(d_cpc), "💵", delta_inverse=True),
                kpi_card("CPM", brl(cpm), _delta_str(d_cpm), "📊", delta_inverse=True),
                kpi_card("Ticket Médio", brl(ticket_medio), icon="🧾"),
                kpi_card("Frequência", fmt_dec(avg_freq, 1), icon="🔄"),
            ]), unsafe_allow_html=True)

    if is_tofu:
        st.markdown(H("KPIs Estratégicos", "sh-blue"), unsafe_allow_html=True)
        st.markdown(kpi_row([
            kpi_card("Valor Gasto", brl(total_spend), _delta_str(d_spend), "💰"),
            kpi_card("Alcance", fmt_int(total_reach), _delta_str(d_reach), "📡"),
            kpi_card("CPM", brl(cpm), _delta_str(d_cpm), "📊", delta_inverse=True),
            kpi_card("CTR", fmt_pct(ctr), _delta_str(d_ctr), "👆"),
            kpi_card("Engajamento", fmt_int(total_engagement), _delta_str(d_eng), "❤️"),
            kpi_card("Custo/Engajamento", brl(cost_per_eng), _delta_str(d_cost_eng), "💵", delta_inverse=True),
        ]), unsafe_allow_html=True)

        with st.expander("📋 KPIs Secundários"):
            st.markdown(kpi_row([
                kpi_card("Impressões", fmt_int(total_imp), _delta_str(d_imp), "👁️"),
                kpi_card("Cliques", fmt_int(total_clicks), _delta_str(d_clicks), "🖱️"),
                kpi_card("CPC", brl(cpc), _delta_str(d_cpc), "💵", delta_inverse=True),
                kpi_card("Frequência", fmt_dec(avg_freq, 1), icon="🔄"),
                kpi_card("CPR (custo/1k alcance)", brl(cpr), _delta_str(d_cpr), "📡", delta_inverse=True),
            ]), unsafe_allow_html=True)

    # ── Smart Insights (gerados automaticamente) ─────────────────────────
    _auto_insights = _generate_smart_insights(
        total_spend, roas, cpa, ctr, avg_freq, total_purch,
        target_roas, target_cpa, monthly_budget, d_roas, d_cpa
    )
    if _auto_insights:
        st.markdown(H("Insights Automáticos", "sh-green"), unsafe_allow_html=True)
        for _ins in _auto_insights:
            st.markdown(_ins)

    # ── Trend line (uses daily data — lazy loaded) ─────────────────────
    st.markdown(H("Tendência Diária (com média móvel 7d)"), unsafe_allow_html=True)
    daily_df = _get_daily_camp()
    if not daily_df.empty and "date" in daily_df.columns:
        daily = (
            daily_df.groupby("date", as_index=False)
            .agg(spend=("spend", "sum"), impressions=("impressions", "sum"),
                 clicks=("clicks", "sum"), reach=("reach", "sum"),
                 purchases=("actions_purchase", "sum"))
            .sort_values("date")
        )
        daily["ctr"] = daily.apply(lambda r: safe_div(r["clicks"], r["impressions"], 100), axis=1)
        daily["cpa"] = daily.apply(lambda r: safe_div(r["spend"], r["purchases"]), axis=1)
        daily["spend_ma7"] = daily["spend"].rolling(7, min_periods=1).mean()
        daily["ctr_ma7"] = daily["ctr"].rolling(7, min_periods=1).mean()

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=daily["date"], y=daily["spend"], name="Spend",
            line=dict(color="#FF8C00", width=1), opacity=0.4,
            fill="tozeroy", fillcolor="rgba(255,140,0,0.07)",
        ))
        fig.add_trace(go.Scatter(
            x=daily["date"], y=daily["spend_ma7"], name="Spend MA7",
            line=dict(color="#FF8C00", width=3),
        ))
        fig.add_trace(go.Scatter(
            x=daily["date"], y=daily["ctr_ma7"], name="CTR MA7 (%)",
            yaxis="y2", line=dict(color="#4FC3F7", width=3),
        ))
        _add_annotations(fig, daily["date"], daily["spend_ma7"], fmt_fn=lambda v: brl(v))
        fig.update_layout(
            **CHART_DEFAULTS, height=350,
            margin=dict(l=10, r=10, t=10, b=10),
            yaxis=dict(title="Spend (R$)", showgrid=True, gridcolor="rgba(255,255,255,0.06)"),
            yaxis2=dict(title="CTR (%)", overlaying="y", side="right", showgrid=False),
            xaxis=dict(showgrid=False),
            legend=dict(orientation="h", y=-0.15, xanchor="center", x=0.5),
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Dados diários não disponíveis para o período.")

    # ── Campaign overview table ──────────────────────────────────────────
    st.markdown(H("Visão Geral por Campanha"), unsafe_allow_html=True)
    ov = df_camp.groupby("campaign", as_index=False).agg(
        impressions=("impressions", "sum"), clicks=("clicks", "sum"),
        spend=("spend", "sum"), reach=("reach", "sum"),
        purchases=("actions_purchase", "sum"),
        revenue=("action_values_purchase", "sum"),
        engagement=("actions_post_engagement", "sum") if "actions_post_engagement" in df_camp.columns else ("impressions", "count"),
    )
    ov["CTR"] = ov.apply(lambda r: safe_div(r["clicks"], r["impressions"], 100), axis=1)
    ov["CPA"] = ov.apply(lambda r: safe_div(r["spend"], r["purchases"]), axis=1)
    ov["ROAS"] = ov.apply(lambda r: safe_div(r["revenue"], r["spend"]), axis=1)
    ov["CPM"] = ov.apply(lambda r: safe_div(r["spend"], r["impressions"], 1000), axis=1)
    ov = ov.sort_values("spend", ascending=False)

    # Top 10 with column_config
    _max_spend_ov = ov["spend"].max() if not ov.empty else 1
    _ov_top = ov.head(10)
    st.dataframe(
        _ov_top,
        column_config={
            "campaign": st.column_config.TextColumn("Campanha"),
            "spend": st.column_config.ProgressColumn("Spend", format="R$ %.2f", min_value=0, max_value=_max_spend_ov),
            "impressions": st.column_config.NumberColumn("Impressões", format="%d"),
            "clicks": st.column_config.NumberColumn("Cliques", format="%d"),
            "reach": st.column_config.NumberColumn("Alcance", format="%d"),
            "purchases": st.column_config.NumberColumn("Conversões", format="%d"),
            "revenue": st.column_config.NumberColumn("Receita", format="R$ %.2f"),
            "engagement": st.column_config.NumberColumn("Engajamento", format="%d"),
            "CTR": st.column_config.NumberColumn("CTR", format="%.2f%%"),
            "CPA": st.column_config.NumberColumn("CPA", format="R$ %.2f"),
            "ROAS": st.column_config.NumberColumn("ROAS", format="%.2fx"),
            "CPM": st.column_config.NumberColumn("CPM", format="R$ %.2f"),
        },
        use_container_width=True, hide_index=True,
    )
    if len(ov) > 10:
        with st.expander(f"Ver todas ({len(ov)} campanhas)"):
            _ov_display = ov.copy()
            _ov_display = _ov_display.rename(columns={
                "campaign": "Campanha", "impressions": "Impressões", "clicks": "Cliques",
                "spend": "Valor Gasto", "reach": "Alcance", "purchases": "Conversões",
                "revenue": "Receita", "engagement": "Engajamento",
            })
            for c in ["Impressões", "Cliques", "Alcance", "Conversões", "Engajamento"]:
                if c in _ov_display.columns:
                    _ov_display[c] = _ov_display[c].apply(fmt_int)
            for c in ["Valor Gasto", "Receita", "CPA", "CPM"]:
                if c in _ov_display.columns:
                    _ov_display[c] = _ov_display[c].apply(brl)
            if "CTR" in _ov_display.columns:
                _ov_display["CTR"] = _ov_display["CTR"].apply(fmt_pct)
            if "ROAS" in _ov_display.columns:
                _ov_display["ROAS"] = _ov_display["ROAS"].apply(lambda v: fmt_dec(v, suffix="x"))
            st.dataframe(_ov_display, use_container_width=True, hide_index=True)

    # CSV export
    _ov_csv = ov.copy()
    _ov_csv = _ov_csv.rename(columns={"campaign": "Campanha"})
    st.download_button("📥 Exportar campanhas CSV", _to_csv(_ov_csv), "campanhas.csv", "text/csv", key="dl_ov")

    # ── Adset overview table (df_adset was fetched but never displayed!) ──
    if not df_adset.empty:
        with st.expander(f"📋 Visão Geral por Conjunto de Anúncios ({len(df_adset)} adsets)"):
            _agg_adset = {"impressions": ("impressions", "sum"), "clicks": ("clicks", "sum"),
                          "spend": ("spend", "sum")}
            if "actions_purchase" in df_adset.columns:
                _agg_adset["purchases"] = ("actions_purchase", "sum")
            if "action_values_purchase" in df_adset.columns:
                _agg_adset["revenue"] = ("action_values_purchase", "sum")
            _grp_cols = ["adset_name"]
            if "campaign" in df_adset.columns:
                _grp_cols = ["campaign", "adset_name"]
            adset_ov = df_adset.groupby(_grp_cols, as_index=False).agg(**_agg_adset)
            adset_ov["CTR"] = adset_ov.apply(lambda r: safe_div(r["clicks"], r["impressions"], 100), axis=1)
            if "purchases" in adset_ov.columns:
                adset_ov["CPA"] = adset_ov.apply(lambda r: safe_div(r["spend"], r["purchases"]), axis=1)
                adset_ov["ROAS"] = adset_ov.apply(lambda r: safe_div(r.get("revenue", 0), r["spend"]), axis=1)
            adset_ov = adset_ov.sort_values("spend", ascending=False)

            _as_display = adset_ov.copy()
            _as_rename = {"adset_name": "Conjunto", "campaign": "Campanha",
                          "impressions": "Impressões", "clicks": "Cliques",
                          "spend": "Spend", "purchases": "Conversões", "revenue": "Receita"}
            _as_display = _as_display.rename(columns={k: v for k, v in _as_rename.items() if k in _as_display.columns})
            for c in ["Impressões", "Cliques", "Conversões"]:
                if c in _as_display.columns:
                    _as_display[c] = _as_display[c].apply(fmt_int)
            for c in ["Spend", "Receita", "CPA"]:
                if c in _as_display.columns:
                    _as_display[c] = _as_display[c].apply(brl)
            if "CTR" in _as_display.columns:
                _as_display["CTR"] = _as_display["CTR"].apply(fmt_pct)
            if "ROAS" in _as_display.columns:
                _as_display["ROAS"] = _as_display["ROAS"].apply(lambda v: fmt_dec(v, suffix="x"))
            st.dataframe(_as_display, use_container_width=True, hide_index=True)
            st.download_button("📥 Exportar adsets CSV", _to_csv(adset_ov), "adsets.csv", "text/csv", key="dl_adset")

    # ── Bar meses + Desempenho mensal → collapsible ──────────────────────
    with st.expander("📅 Desempenho por Mês & Investimento"):
        col_pie, col_monthly = st.columns([2, 3])
        with col_pie:
            st.markdown(H("Investimento por Mês"), unsafe_allow_html=True)
            if "date" in df_camp.columns:
                ds = df_camp.groupby("date", as_index=False).agg(spend=("spend", "sum")).sort_values("date")
                ds["label"] = ds["date"].dt.strftime("%m/%Y")
                fig = go.Figure(go.Bar(
                    x=ds["label"], y=ds["spend"],
                    marker_color="#FF8C00",
                    text=ds["spend"].apply(brl), textposition="auto",
                ))
                fig.update_layout(**CHART_DEFAULTS, height=350, margin=dict(l=10, r=10, t=10, b=10),
                                  xaxis=dict(title="Mês"), yaxis=dict(title="Investimento (R$)"))
                st.plotly_chart(fig, use_container_width=True)

        with col_monthly:
            st.markdown(H("Desempenho por Mês"), unsafe_allow_html=True)
            if "date" in df_camp.columns:
                dd = (
                    df_camp.groupby("date", as_index=False)
                    .agg(impressions=("impressions", "sum"), clicks=("clicks", "sum"),
                         purchases=("actions_purchase", "sum"), spend=("spend", "sum"))
                    .sort_values("date", ascending=False)
                )
                dd["CPA"] = dd.apply(lambda r: safe_div(r["spend"], r["purchases"]), axis=1)
                dd["CTR"] = dd.apply(lambda r: safe_div(r["clicks"], r["impressions"], 100), axis=1)
                dd["Mês"] = dd["date"].dt.strftime("%m/%Y")
                dd_show = dd[["Mês", "impressions", "clicks", "purchases", "CTR", "CPA", "spend"]].rename(columns={
                    "impressions": "Impressões", "clicks": "Cliques",
                    "purchases": "Conversões", "spend": "Valor Gasto",
                }).copy()
                for c in ["Impressões", "Cliques", "Conversões"]:
                    dd_show[c] = dd_show[c].apply(fmt_int)
                dd_show["CTR"] = dd_show["CTR"].apply(fmt_pct)
                dd_show["CPA"] = dd_show["CPA"].apply(brl)
                dd_show["Valor Gasto"] = dd_show["Valor Gasto"].apply(brl)
                st.dataframe(dd_show, use_container_width=True, hide_index=True, height=350)

# ─────────────────────────────────────────────────────────────────────────────
#  TAB 2 — FUNIL DE CONVERSÃO
# ─────────────────────────────────────────────────────────────────────────────
with tab_funnel:

    # ── Insight Box ──────────────────────────────────────────────────────
    _cr_total = safe_div(total_purch, total_imp, 100)
    _cr_click = safe_div(total_purch, total_link_clicks if total_link_clicks else total_clicks, 100)
    st.markdown(
        f'<div class="insight-box">'
        f'Taxa de conversão geral (impressão→compra): <b>{fmt_pct(_cr_total)}</b><br>'
        f'Taxa de conversão de clique→compra: <b>{fmt_pct(_cr_click)}</b><br>'
        f'<b>{fmt_int(total_purch)}</b> compras de <b>{fmt_int(total_imp)}</b> impressões no período'
        f'</div>', unsafe_allow_html=True,
    )

    # ── Detecção Automática de Gargalo ────────────────────────────────────
    _funnel_steps = [
        ("Impressões", "Cliques no Link", total_imp, total_link_clicks if total_link_clicks else total_clicks),
        ("Cliques no Link", "Visualização de Página", total_link_clicks if total_link_clicks else total_clicks, total_lpv),
        ("Visualização de Página", "Adição ao Carrinho", total_lpv, total_atc),
        ("Adição ao Carrinho", "Início de Checkout", total_atc, total_ic),
        ("Início de Checkout", "Compra", total_ic, total_purch),
    ]
    _max_drop = 0
    _bottleneck_from = ""
    _bottleneck_to = ""
    _bottleneck_rate = 0
    for _f, _t, _fv, _tv in _funnel_steps:
        _drop = safe_div(_fv - _tv, _fv, 100) if _fv > 0 else 0
        if _drop > _max_drop and _fv > 0:
            _max_drop = _drop
            _bottleneck_from = _f
            _bottleneck_to = _t
            _bottleneck_rate = safe_div(_tv, _fv, 100)
    if _max_drop > 0:
        st.markdown(
            f'<div class="bottleneck-box">'
            f'🚨 <b>Maior gargalo:</b> {_bottleneck_from} → {_bottleneck_to} '
            f'(taxa de {fmt_pct(_bottleneck_rate)}, perda de {fmt_pct(_max_drop)})<br>'
            f'<b>Ação:</b> Investigue a experiência entre essas etapas — '
            f'página de destino, formulário, checkout ou oferta.'
            f'</div>', unsafe_allow_html=True,
        )

    st.markdown(H("Funil Completo de Conversão", "sh-green"), unsafe_allow_html=True)

    funnel_data = [
        ("Impressões", total_imp),
        ("Cliques no Link", total_link_clicks if total_link_clicks else total_clicks),
        ("Visualização de Página", total_lpv),
        ("Adição ao Carrinho", total_atc),
        ("Início de Checkout", total_ic),
        ("Compra", total_purch),
    ]
    funnel_labels = [f[0] for f in funnel_data]
    funnel_values = [f[1] for f in funnel_data]

    col_f, col_rates = st.columns([3, 2])

    with col_f:
        colors = ["#60A5FA", "#3B82F6", "#2563EB", "#1D4ED8", "#1E40AF", "#1E3A8A"]
        # Add cumulative % from top
        _top_val = funnel_values[0] if funnel_values[0] else 1
        _funnel_text = [f"<b>{fl}</b><br>{fv:,.0f} ({safe_div(fv, _top_val, 100):.1f}% do topo)"
                        for fl, fv in zip(funnel_labels, funnel_values)]
        fig = go.Figure(go.Funnel(
            y=funnel_labels, x=funnel_values,
            textinfo="text",
            text=_funnel_text,
            marker=dict(color=colors, line=dict(width=0)),
            connector=dict(line=dict(color="#1E1E2E", width=0)),
        ))
        fig.update_layout(**CHART_DEFAULTS, height=450,
                          margin=dict(l=20, r=20, t=10, b=10), showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

    with col_rates:
        st.markdown(H("Taxas entre Etapas", "sh-green"), unsafe_allow_html=True)
        _rate_cards = []
        for i in range(1, len(funnel_data)):
            prev_label, prev_val = funnel_data[i - 1]
            curr_label, curr_val = funnel_data[i]
            rate = safe_div(curr_val, prev_val, 100)
            drop = 100 - rate
            _d = f"-{drop:.1f}% drop" if drop > 0 else "0%"
            _rate_cards.append(kpi_card(
                f"{prev_label} → {curr_label}", f"{rate:.1f}%", _d, "🔻", delta_inverse=True,
            ))
        st.markdown("".join(_rate_cards), unsafe_allow_html=True)

    # ── Funnel by campaign ───────────────────────────────────────────────
    st.markdown(H("Funil por Campanha", "sh-green"), unsafe_allow_html=True)
    if not df_camp.empty:
        fc = df_camp.groupby("campaign", as_index=False).agg(
            impressions=("impressions", "sum"),
            link_clicks=("actions_link_click", "sum") if "actions_link_click" in df_camp.columns else ("clicks", "sum"),
            lpv=("actions_landing_page_view", "sum") if "actions_landing_page_view" in df_camp.columns else ("clicks", "sum"),
            atc=("actions_add_to_cart", "sum") if "actions_add_to_cart" in df_camp.columns else ("impressions", "count"),
            purchases=("actions_purchase", "sum"),
            spend=("spend", "sum"),
        )
        fc["CR Click→Compra"] = fc.apply(lambda r: safe_div(r["purchases"], r["link_clicks"], 100), axis=1)
        fc["CPA"] = fc.apply(lambda r: safe_div(r["spend"], r["purchases"]), axis=1)
        fc = fc.rename(columns={
            "campaign": "Campanha", "impressions": "Impressões",
            "link_clicks": "Cliques Link", "lpv": "LPV", "atc": "Adição Carrinho",
            "purchases": "Compras", "spend": "Spend",
        })
        for c in ["Impressões", "Cliques Link", "LPV", "Adição Carrinho", "Compras"]:
            if c in fc.columns:
                fc[c] = fc[c].apply(fmt_int)
        if "Spend" in fc.columns:
            fc["Spend"] = fc["Spend"].apply(brl)
        if "CPA" in fc.columns:
            fc["CPA"] = fc["CPA"].apply(brl)
        if "CR Click→Compra" in fc.columns:
            fc["CR Click→Compra"] = fc["CR Click→Compra"].apply(fmt_pct)
        st.dataframe(fc, use_container_width=True, hide_index=True)


# ─────────────────────────────────────────────────────────────────────────────
#  TAB 3 — CRIATIVOS (com galeria visual)
# ─────────────────────────────────────────────────────────────────────────────

def _get_thumb(row):
    """Return the best available thumbnail URL for a creative."""
    for col in ["image_url", "thumbnail_url", "promoted_post_full_picture",
                "desktop_feed_standard_preview_url"]:
        val = row.get(col)
        if val and pd.notna(val) and str(val).startswith("http"):
            return str(val)
    return None


def _render_creative_card(row, rank: int | None = None, badge: str = ""):
    """Render a single creative card with thumbnail + metrics."""
    thumb = _get_thumb(row)
    name = row.get("ad_name", "—")
    title = row.get("title", "") or row.get("name", "")
    body = row.get("body", "")
    if pd.isna(title):
        title = ""
    if pd.isna(body):
        body = ""

    rank_text = f"**#{rank}** — " if rank else ""
    badge_html = f' <span style="background:{badge};color:#fff;padding:2px 8px;border-radius:10px;font-size:.75rem;font-weight:600;margin-left:6px">'
    if badge == "#66BB6A":
        badge_html += "WINNER</span>"
    elif badge == "#EF5350":
        badge_html += "UNDERPERFORMER</span>"
    elif badge == "#E65100":
        badge_html += "FADIGA</span>"
    else:
        badge_html = ""

    st.markdown(f"##### {rank_text}{name}{badge_html}", unsafe_allow_html=True)

    col_img, col_metrics = st.columns([1, 2])

    with col_img:
        if thumb:
            st.image(thumb, use_container_width=True)
        else:
            st.markdown(
                '<div style="background:#2a2a3a;border-radius:8px;padding:40px;'
                'text-align:center;color:#666;font-size:.9rem">Sem preview</div>',
                unsafe_allow_html=True,
            )
        if title:
            st.caption(f"**Headline:** {title[:120]}")
        if body:
            st.caption(f"**Copy:** {body[:200]}{'…' if len(body) > 200 else ''}")

    with col_metrics:
        _cr_cards = [
            kpi_card("Spend", brl(row.get('spend', 0)), icon="💰"),
            kpi_card("Impressões", fmt_int(row.get('impressions', 0)), icon="👁️"),
            kpi_card("Cliques", fmt_int(row.get('clicks', 0)), icon="🖱️"),
            kpi_card("CTR", fmt_pct(row.get('CTR', 0)), icon="👆"),
            kpi_card("Conversões", fmt_int(row.get('purchases', 0)), icon="🛒"),
            kpi_card("CPA", brl(row.get('CPA', 0)), icon="🎯"),
            kpi_card("ROAS", fmt_dec(row.get('ROAS', 0), suffix="x"), icon="📈"),
            kpi_card("Engajamento", fmt_int(row.get('engagement', 0)), icon="❤️"),
        ]
        if row.get("Hook Rate", 0) > 0 or row.get("Hold Rate", 0) > 0:
            _cr_cards.extend([
                kpi_card("Hook Rate", fmt_pct(row.get('Hook Rate', 0)), icon="🪝"),
                kpi_card("Hold Rate", fmt_pct(row.get('Hold Rate', 0)), icon="⏱️"),
                kpi_card("Video Views", fmt_int(row.get('vv', 0)), icon="🎬"),
                kpi_card("Frequência", fmt_dec(row.get('avg_freq', 0), 1), icon="🔄"),
            ])
        st.markdown(kpi_row(_cr_cards), unsafe_allow_html=True)

    st.markdown("---")


with tab_creative:

    if df_ad.empty:
        st.warning(
            "Sem dados de criativos para os filtros selecionados.\n\n"
            "A API não retornou dados no nível de anúncio para essas campanhas. "
            "Isso pode ocorrer quando os criativos não tiveram veiculação suficiente no período, "
            "ou quando a campanha utiliza criativos dinâmicos (DCO). "
            "Tente ampliar o período ou remover o filtro de palavra-chave."
        )
    else:
        # ── Build creative aggregate with asset info ─────────────────────
        # Keep first asset URL and text per ad_name
        # (insight box rendered after 'ca' is built)
        agg_dict = {
            "impressions": ("impressions", "sum"),
            "clicks": ("clicks", "sum"),
            "spend": ("spend", "sum"),
            "reach": ("reach", "sum"),
            "purchases": ("actions_purchase", "sum"),
            "revenue": ("action_values_purchase", "sum"),
            "avg_freq": ("frequency", "mean"),
        }
        if "actions_post_engagement" in df_ad.columns:
            agg_dict["engagement"] = ("actions_post_engagement", "sum")
        if "video_views" in df_ad.columns:
            agg_dict["vv"] = ("video_views", "sum")
        if "video_thruplay_watched" in df_ad.columns:
            agg_dict["thru"] = ("video_thruplay_watched", "sum")

        ca = df_ad.groupby("ad_name", as_index=False).agg(**agg_dict)

        # Get first asset URL per ad
        for asset_col in ["image_url", "thumbnail_url", "promoted_post_full_picture",
                          "desktop_feed_standard_preview_url",
                          "body", "title", "name", "object_type"]:
            if asset_col in df_ad.columns:
                first_vals = df_ad.dropna(subset=[asset_col]).groupby("ad_name")[asset_col].first()
                ca = ca.merge(first_vals.rename(asset_col), on="ad_name", how="left")

        ca["CTR"] = ca.apply(lambda r: safe_div(r["clicks"], r["impressions"], 100), axis=1)
        ca["CPA"] = ca.apply(lambda r: safe_div(r["spend"], r["purchases"]), axis=1)
        ca["ROAS"] = ca.apply(lambda r: safe_div(r["revenue"], r["spend"]), axis=1)
        ca["Hook Rate"] = ca.apply(lambda r: safe_div(r.get("vv", 0), r["impressions"], 100), axis=1)
        ca["Hold Rate"] = ca.apply(lambda r: safe_div(r.get("thru", 0), r.get("vv", 1), 100), axis=1)
        ca = ca.sort_values("spend", ascending=False)

        # ── Insight Box ──────────────────────────────────────────────────
        _n_creatives = len(ca)
        _top_name = ca.iloc[0]["ad_name"][:50] if not ca.empty else "—"
        _top_cpa_cr = brl(ca[ca["purchases"] > 0].nsmallest(1, "CPA")["CPA"].values[0]) if not ca[ca["purchases"] > 0].empty else "—"
        _top_cr_name = ca[ca["purchases"] > 0].nsmallest(1, "CPA")["ad_name"].values[0][:50] if not ca[ca["purchases"] > 0].empty else "—"
        _n_fatigued = len(ca[ca["avg_freq"] >= 2.5])
        _fatigue_txt = f" · <b>{_n_fatigued}</b> criativos com frequência ≥ 2,5 — considerar rotação" if _n_fatigued > 0 else ""
        st.markdown(
            f'<div class="insight-box">'
            f'<b>{_n_creatives}</b> criativos ativos · Top performer: <b>{_top_cr_name}</b> com CPA de <b>{_top_cpa_cr}</b><br>'
            f'Maior spend: <b>{_top_name}</b> ({brl(ca.iloc[0]["spend"])}){_fatigue_txt}'
            f'</div>', unsafe_allow_html=True,
        )

        # ── KPIs de Vídeo ────────────────────────────────────────────────
        st.markdown(H("Performance de Vídeo", "sh-purple"), unsafe_allow_html=True)
        st.markdown(kpi_row([
            kpi_card("Video Views", fmt_int(total_vv), icon="🎬"),
            kpi_card("ThruPlay", fmt_int(total_thruplay), icon="▶️"),
            kpi_card("Hook Rate (views/imp)", fmt_pct(hook_rate), icon="🪝"),
            kpi_card("Hold Rate (thru/views)", fmt_pct(hold_rate), icon="⏱️"),
        ]), unsafe_allow_html=True)

        # ── Winners — Best ROAS with conversions ─────────────────────────
        winners = ca[ca["purchases"] > 0].nsmallest(3, "CPA")
        if not winners.empty:
            st.markdown(H("🏆 Top Performers — Menor CPA", "sh-green"), unsafe_allow_html=True)
            for i, (_, row) in enumerate(winners.iterrows(), 1):
                _render_creative_card(row, rank=i, badge="#66BB6A")

        # ── Losers — Worst CPA with spend ────────────────────────────────
        losers = ca[ca["purchases"] > 0].nlargest(3, "CPA")
        if not losers.empty and len(ca[ca["purchases"] > 0]) > 3:
            st.markdown(H("⚠️ Underperformers — Maior CPA", "sh-red"), unsafe_allow_html=True)
            for i, (_, row) in enumerate(losers.iterrows(), 1):
                _render_creative_card(row, rank=i, badge="#EF5350")

        # ── Fatigue alert creatives ──────────────────────────────────────
        fatigued = ca[ca["avg_freq"] >= 2.5].sort_values("avg_freq", ascending=False).head(3)
        if not fatigued.empty:
            st.markdown(H("🔥 Criativos com Fadiga (Frequência ≥ 2,5)", "sh-red"), unsafe_allow_html=True)
            for _, row in fatigued.iterrows():
                _render_creative_card(row, badge="#E65100")

        # ── Full gallery → collapsible ────────────────────────────────────
        with st.expander(f"🖼️ Galeria Completa de Criativos ({len(ca)})"):
            cols_per_row = 3
            for i in range(0, len(ca), cols_per_row):
                cols = st.columns(cols_per_row)
                for j, col in enumerate(cols):
                    idx = i + j
                    if idx >= len(ca):
                        break
                    row = ca.iloc[idx]
                    with col:
                        thumb = _get_thumb(row)
                        if thumb:
                            st.image(thumb, use_container_width=True)
                        else:
                            st.markdown(
                                '<div style="background:#2a2a3a;border-radius:8px;'
                                'padding:30px;text-align:center;color:#555;font-size:.8rem">'
                                'Sem preview</div>',
                                unsafe_allow_html=True,
                            )
                        st.markdown(f"**{row['ad_name'][:50]}**")
                        headline = row.get("title", "") or row.get("name", "")
                        if headline and pd.notna(headline):
                            st.caption(f"_{str(headline)[:80]}_")
                        st.markdown(
                            f"Spend: **{brl(row['spend'])}** · "
                            f"CTR: **{fmt_pct(row['CTR'])}** · "
                            f"CPA: **{brl(row['CPA'])}**"
                        )
                        if row["purchases"] > 0:
                            st.markdown(
                                f"Conv: **{fmt_int(row['purchases'])}** · "
                                f"ROAS: **{fmt_dec(row['ROAS'], suffix='x')}**"
                            )
                        if row.get("avg_freq", 0) >= 2.5:
                            st.markdown(
                                f'<span style="background:#E65100;color:#fff;padding:2px 8px;'
                                f'border-radius:10px;font-size:.75rem">Freq: {row["avg_freq"]:.1f}</span>',
                                unsafe_allow_html=True,
                            )

        # ── Comparison table → collapsible ────────────────────────────────
        with st.expander(f"📋 Tabela Comparativa Completa ({len(ca)} criativos)"):
            table_cols = {
                "ad_name": "Criativo", "impressions": "Impressões", "clicks": "Cliques",
                "spend": "Spend", "reach": "Alcance", "purchases": "Conversões",
                "revenue": "Receita",
            }
            if "engagement" in ca.columns:
                table_cols["engagement"] = "Engajamento"
            if "vv" in ca.columns:
                table_cols["vv"] = "Video Views"
            extra = {
                "CTR": "CTR", "CPA": "CPA", "ROAS": "ROAS",
                "Hook Rate": "Hook Rate", "Hold Rate": "Hold Rate",
                "avg_freq": "Frequência",
            }
            table_cols.update(extra)
            display_ca = ca[[c for c in table_cols if c in ca.columns]].rename(
                columns={k: v for k, v in table_cols.items() if k in ca.columns}
            )
            for c in ["Impressões", "Cliques", "Alcance", "Conversões", "Engajamento", "Video Views"]:
                if c in display_ca.columns:
                    display_ca[c] = display_ca[c].apply(fmt_int)
            for c in ["Spend", "Receita", "CPA"]:
                if c in display_ca.columns:
                    display_ca[c] = display_ca[c].apply(brl)
            for c in ["CTR", "Hook Rate", "Hold Rate"]:
                if c in display_ca.columns:
                    display_ca[c] = display_ca[c].apply(fmt_pct)
            if "ROAS" in display_ca.columns:
                display_ca["ROAS"] = display_ca["ROAS"].apply(lambda v: fmt_dec(v, suffix="x"))
            if "Frequência" in display_ca.columns:
                display_ca["Frequência"] = display_ca["Frequência"].apply(lambda v: fmt_dec(v, 1))
            st.dataframe(display_ca, use_container_width=True, hide_index=True)
            st.download_button("📥 Exportar criativos CSV", _to_csv(display_ca), "criativos.csv", "text/csv", key="dl_cr")

        # ── Charts ───────────────────────────────────────────────────────
        col_bar, col_scatter = st.columns(2)
        with col_bar:
            st.markdown(H("Top Criativos por Spend", "sh-purple"), unsafe_allow_html=True)
            top10 = ca.nlargest(10, "spend")
            fig = go.Figure()
            fig.add_trace(go.Bar(
                y=top10["ad_name"], x=top10["spend"], name="Spend",
                orientation="h", marker_color="#FF8C00",
                text=top10["spend"].apply(brl), textposition="auto",
            ))
            fig.add_trace(go.Bar(
                y=top10["ad_name"], x=top10["purchases"], name="Conversões",
                orientation="h", marker_color="#4FC3F7",
                text=top10["purchases"].apply(fmt_int), textposition="auto",
            ))
            fig.update_layout(
                **CHART_DEFAULTS, barmode="group", height=400,
                margin=dict(l=10, r=10, t=10, b=10),
                xaxis=dict(showgrid=False),
                yaxis=dict(autorange="reversed"),
                legend=dict(orientation="h", y=-0.1, xanchor="center", x=0.5),
            )
            st.plotly_chart(fig, use_container_width=True)

        with col_scatter:
            st.markdown(H("CPA vs ROAS por Criativo", "sh-purple"), unsafe_allow_html=True)
            scatter_df = ca[ca["purchases"] > 0].copy()
            if not scatter_df.empty:
                fig = px.scatter(
                    scatter_df, x="CPA", y="ROAS",
                    size="spend", hover_name="ad_name",
                    color="ROAS", color_continuous_scale=["#EF5350", "#FFCA28", "#66BB6A"],
                )
                fig.update_layout(
                    **CHART_DEFAULTS, height=400,
                    margin=dict(l=10, r=10, t=10, b=10),
                    xaxis=dict(title="CPA (R$)"), yaxis=dict(title="ROAS"),
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Sem conversões suficientes para scatter.")

        # ── Creative fatigue detection (lazy daily ad data) ─────────────
        st.markdown(H("Fadiga de Criativo — Frequência vs CTR ao Longo do Tempo", "sh-red"), unsafe_allow_html=True)
        daily_ad_df = _get_daily_ad()
        if not daily_ad_df.empty and "date" in daily_ad_df.columns:
            top5_ads = (
                daily_ad_df.groupby("ad_name", as_index=False)["spend"].sum()
                .nlargest(5, "spend")["ad_name"].tolist()
            )
            fatigue = daily_ad_df[daily_ad_df["ad_name"].isin(top5_ads)].copy()
            if not fatigue.empty:
                fat_daily = (
                    fatigue.groupby(["date", "ad_name"], as_index=False)
                    .agg(impressions=("impressions", "sum"), clicks=("clicks", "sum"),
                         frequency=("frequency", "mean"))
                )
                fat_daily["ctr"] = fat_daily.apply(
                    lambda r: safe_div(r["clicks"], r["impressions"], 100), axis=1
                )
                col_ctr, col_freq = st.columns(2)
                with col_ctr:
                    fig = px.line(fat_daily, x="date", y="ctr", color="ad_name",
                                  title="CTR por Criativo ao Longo do Tempo")
                    fig.update_layout(**CHART_DEFAULTS, height=350,
                                      margin=dict(l=10, r=10, t=40, b=10))
                    st.plotly_chart(fig, use_container_width=True)
                with col_freq:
                    fig = px.line(fat_daily, x="date", y="frequency", color="ad_name",
                                  title="Frequência por Criativo ao Longo do Tempo")
                    fig.update_layout(**CHART_DEFAULTS, height=350,
                                      margin=dict(l=10, r=10, t=40, b=10))
                    st.plotly_chart(fig, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
#  TAB 4 — AUDIÊNCIA & PLACEMENT
# ─────────────────────────────────────────────────────────────────────────────
with tab_audience:

    # ── Insight Box ──────────────────────────────────────────────────────
    st.markdown(
        f'<div class="insight-box">'
        f'Investimento total de <b>{brl(total_spend)}</b> · Veja como o desempenho se distribui por idade, gênero, posicionamento e região'
        f'</div>', unsafe_allow_html=True,
    )

    # ── Age x Gender ─────────────────────────────────────────────────────
    st.markdown(H("Performance por Idade e Gênero", "sh-blue"), unsafe_allow_html=True)
    df_demo = _get_demo()
    if not df_demo.empty and "age" in df_demo.columns and "gender" in df_demo.columns:
        demo_agg = df_demo.groupby(["age", "gender"], as_index=False).agg(
            spend=("spend", "sum"), impressions=("impressions", "sum"),
            clicks=("clicks", "sum"),
            purchases=("actions_purchase", "sum") if "actions_purchase" in df_demo.columns else ("spend", "count"),
        )
        demo_agg["CPA"] = demo_agg.apply(lambda r: safe_div(r["spend"], r["purchases"]), axis=1)
        demo_agg["CTR"] = demo_agg.apply(lambda r: safe_div(r["clicks"], r["impressions"], 100), axis=1)

        col_age, col_gender = st.columns(2)
        with col_age:
            age_agg = demo_agg.groupby("age", as_index=False).agg(
                spend=("spend", "sum"), purchases=("purchases", "sum"))
            age_agg["CPA"] = age_agg.apply(lambda r: safe_div(r["spend"], r["purchases"]), axis=1)
            fig = go.Figure()
            fig.add_trace(go.Bar(x=age_agg["age"], y=age_agg["spend"], name="Spend", marker_color="#FF8C00"))
            fig.add_trace(go.Scatter(x=age_agg["age"], y=age_agg["CPA"], name="CPA", yaxis="y2",
                                     line=dict(color="#EF5350", width=3), mode="lines+markers"))
            fig.update_layout(**CHART_DEFAULTS, height=350, margin=dict(l=10, r=10, t=10, b=10),
                              yaxis=dict(title="Spend"), yaxis2=dict(title="CPA", overlaying="y", side="right"),
                              legend=dict(orientation="h", y=-0.15, xanchor="center", x=0.5))
            st.plotly_chart(fig, use_container_width=True)

        with col_gender:
            gender_agg = demo_agg.groupby("gender", as_index=False).agg(
                spend=("spend", "sum"), purchases=("purchases", "sum"))
            gender_agg["CPA"] = gender_agg.apply(lambda r: safe_div(r["spend"], r["purchases"]), axis=1)
            fig = px.bar(gender_agg, x="gender", y="spend", color="gender",
                         text=gender_agg["spend"].apply(brl),
                         color_discrete_sequence=["#4FC3F7", "#FF8C00", "#AB47BC"])
            fig.update_layout(**CHART_DEFAULTS, height=350, margin=dict(l=10, r=10, t=10, b=10),
                              showlegend=False)
            st.plotly_chart(fig, use_container_width=True)

        with st.expander("📋 Tabela Demográfica Detalhada"):
            demo_show = demo_agg.rename(columns={
                "age": "Idade", "gender": "Gênero", "spend": "Spend",
                "impressions": "Impressões", "clicks": "Cliques", "purchases": "Conversões",
            }).copy()
            for c in ["Impressões", "Cliques", "Conversões"]:
                if c in demo_show.columns:
                    demo_show[c] = demo_show[c].apply(fmt_int)
            demo_show["Spend"] = demo_show["Spend"].apply(brl)
            demo_show["CPA"] = demo_show["CPA"].apply(brl)
            demo_show["CTR"] = demo_show["CTR"].apply(fmt_pct)
            st.dataframe(demo_show, use_container_width=True, hide_index=True)
    else:
        st.info("Dados demográficos não disponíveis.")

    # ── Placement ────────────────────────────────────────────────────────
    st.markdown(H("Performance por Posicionamento", "sh-blue"), unsafe_allow_html=True)
    df_place = _get_placement()
    if not df_place.empty and "publisher_platform" in df_place.columns:
        pl = df_place.groupby(
            ["publisher_platform", "platform_position"], as_index=False
        ).agg(spend=("spend", "sum"), impressions=("impressions", "sum"),
              clicks=("clicks", "sum"),
              purchases=("actions_purchase", "sum") if "actions_purchase" in df_place.columns else ("spend", "count"))
        pl["CPA"] = pl.apply(lambda r: safe_div(r["spend"], r["purchases"]), axis=1)
        pl["CTR"] = pl.apply(lambda r: safe_div(r["clicks"], r["impressions"], 100), axis=1)
        pl["CPM"] = pl.apply(lambda r: safe_div(r["spend"], r["impressions"], 1000), axis=1)
        pl["placement"] = pl["publisher_platform"] + " — " + pl["platform_position"].fillna("")

        col_pl1, col_pl2 = st.columns(2)
        with col_pl1:
            fig = px.bar(pl.nlargest(10, "spend"), x="placement", y="spend",
                         color="spend", color_continuous_scale=["#01579B", "#FF8C00"],
                         text=pl.nlargest(10, "spend")["spend"].apply(brl))
            fig.update_layout(**CHART_DEFAULTS, height=350, margin=dict(l=10, r=10, t=10, b=10),
                              xaxis_tickangle=-45, showlegend=False)
            st.plotly_chart(fig, use_container_width=True)

        with col_pl2:
            fig = px.bar(pl.nlargest(10, "spend"), x="placement", y="CPA",
                         color="CPA", color_continuous_scale=["#66BB6A", "#EF5350"],
                         text=pl.nlargest(10, "spend")["CPA"].apply(brl))
            fig.update_layout(**CHART_DEFAULTS, height=350, margin=dict(l=10, r=10, t=10, b=10),
                              xaxis_tickangle=-45, showlegend=False)
            st.plotly_chart(fig, use_container_width=True)

        with st.expander("📋 Tabela de Posicionamentos Detalhada"):
            pl_show = pl.rename(columns={
                "publisher_platform": "Plataforma", "platform_position": "Posição",
                "spend": "Spend", "impressions": "Impressões", "clicks": "Cliques",
                "purchases": "Conversões",
            }).drop(columns=["placement"], errors="ignore").copy()
            for c in ["Impressões", "Cliques", "Conversões"]:
                if c in pl_show.columns:
                    pl_show[c] = pl_show[c].apply(fmt_int)
            for c in ["Spend", "CPA", "CPM"]:
                if c in pl_show.columns:
                    pl_show[c] = pl_show[c].apply(brl)
            if "CTR" in pl_show.columns:
                pl_show["CTR"] = pl_show["CTR"].apply(fmt_pct)
            st.dataframe(pl_show, use_container_width=True, hide_index=True)
    else:
        st.info("Dados de posicionamento não disponíveis.")

    # ── Region ───────────────────────────────────────────────────────────
    st.markdown(H("Performance por Região", "sh-blue"), unsafe_allow_html=True)
    df_region = _get_region()
    if not df_region.empty and "region" in df_region.columns:
        rg = df_region.groupby("region", as_index=False).agg(
            spend=("spend", "sum"), impressions=("impressions", "sum"),
            clicks=("clicks", "sum"),
            purchases=("actions_purchase", "sum") if "actions_purchase" in df_region.columns else ("spend", "count"),
        )
        rg["CPA"] = rg.apply(lambda r: safe_div(r["spend"], r["purchases"]), axis=1)
        rg["CTR"] = rg.apply(lambda r: safe_div(r["clicks"], r["impressions"], 100), axis=1)
        rg = rg.sort_values("spend", ascending=False)
        fig = px.bar(rg.head(15), x="region", y="spend", color="CPA",
                     color_continuous_scale=["#66BB6A", "#FFCA28", "#EF5350"],
                     text=rg.head(15)["spend"].apply(brl))
        fig.update_layout(**CHART_DEFAULTS, height=400, margin=dict(l=10, r=10, t=10, b=10),
                          xaxis_tickangle=-45)
        st.plotly_chart(fig, use_container_width=True)
        with st.expander(f"📋 Tabela Regional Detalhada ({len(rg)} regiões)"):
            rg_show = rg.rename(columns={
                "region": "Região", "spend": "Spend", "impressions": "Impressões",
                "clicks": "Cliques", "purchases": "Conversões",
            }).copy()
            for c in ["Impressões", "Cliques", "Conversões"]:
                if c in rg_show.columns:
                    rg_show[c] = rg_show[c].apply(fmt_int)
            for c in ["Spend", "CPA"]:
                if c in rg_show.columns:
                    rg_show[c] = rg_show[c].apply(brl)
            if "CTR" in rg_show.columns:
                rg_show["CTR"] = rg_show["CTR"].apply(fmt_pct)
            st.dataframe(rg_show, use_container_width=True, hide_index=True)
    else:
        st.info("Dados regionais não disponíveis.")

    # ══════════════════════════════════════════════════════════════════════
    #  SEÇÃO GA4 (merged into Audiência & Canais)
    # ══════════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown(H("Google Analytics 4", "sh-teal"), unsafe_allow_html=True)

    ga4_traffic = _get_ga4_traffic()
    if ga4_traffic.empty:
        st.info("Sem dados do Google Analytics 4. Verifique se o GA4 está conectado no Windsor.ai.")
    else:
        ga4_sessions = _ga4_col_sum(ga4_traffic, "sessions")
        ga4_users = _ga4_col_sum(ga4_traffic, "users")
        ga4_new_users = _ga4_col_sum(ga4_traffic, "newUsers")
        ga4_pvs = _ga4_col_sum(ga4_traffic, "screenPageViews")
        ga4_bounce = _ga4_weighted_mean(ga4_traffic, "bounceRate")
        ga4_engage = _ga4_weighted_mean(ga4_traffic, "engagementRate")

        st.markdown(kpi_row([
            kpi_card("Sessões", fmt_int(ga4_sessions), icon="🌐"),
            kpi_card("Usuários", fmt_int(ga4_users), icon="👤"),
            kpi_card("Novos Usuários", fmt_int(ga4_new_users), icon="🆕"),
            kpi_card("Pageviews", fmt_int(ga4_pvs), icon="📄"),
            kpi_card("Bounce Rate", fmt_pct(ga4_bounce), icon="↩️"),
            kpi_card("Engagement Rate", fmt_pct(ga4_engage), icon="⚡"),
        ]), unsafe_allow_html=True)

        # GA4 daily trend
        ga4_daily = _get_ga4_daily()
        if not ga4_daily.empty and "date" in ga4_daily.columns:
            gd = ga4_daily.copy()
            gd["_sessions"] = _ga4_col(gd, "sessions")
            gd["_engage"] = _ga4_col(gd, "engagementRate")
            gd_agg = (
                gd.groupby("date", as_index=False)
                .agg(_sessions=("_sessions", "sum"), _engage_w=("_engage", "sum"),
                     _w=("_sessions", "sum"))
            )
            gd_agg["engagement"] = gd_agg.apply(
                lambda r: safe_div(r["_engage_w"], r["_w"]) if r["_w"] else 0, axis=1
            )
            gd_agg = gd_agg.sort_values("date")
            gd_agg["sessions_ma7"] = gd_agg["_sessions"].rolling(7, min_periods=1).mean()

            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=gd_agg["date"], y=gd_agg["_sessions"], name="Sessões",
                line=dict(color="#26A69A", width=1), opacity=0.4,
                fill="tozeroy", fillcolor="rgba(38,166,154,0.07)",
            ))
            fig.add_trace(go.Scatter(
                x=gd_agg["date"], y=gd_agg["sessions_ma7"], name="Sessões MA7",
                line=dict(color="#26A69A", width=3),
            ))
            fig.add_trace(go.Scatter(
                x=gd_agg["date"], y=gd_agg["engagement"], name="Engagement Rate %",
                yaxis="y2", line=dict(color="#42A5F5", width=3),
            ))
            fig.update_layout(
                **CHART_DEFAULTS, height=350,
                margin=dict(l=10, r=10, t=10, b=10),
                yaxis=dict(title="Sessões", showgrid=True, gridcolor="rgba(255,255,255,0.06)"),
                yaxis2=dict(title="Engagement Rate (%)", overlaying="y", side="right", showgrid=False),
                legend=dict(orientation="h", y=-0.15, xanchor="center", x=0.5),
            )
            st.plotly_chart(fig, use_container_width=True)

        # Source/Medium
        with st.expander("📊 Tráfego por Source / Medium"):
            gt = ga4_traffic.copy()
            gt["_sessions"] = _ga4_col(gt, "sessions")
            gt["_users"] = _ga4_col(gt, "users")
            gt["_pvs"] = _ga4_col(gt, "screenPageViews")
            gt["_bounce"] = _ga4_col(gt, "bounceRate")
            gt["_engage"] = _ga4_col(gt, "engagementRate")
            has_medium = "medium" in gt.columns
            group_cols = ["source", "medium"] if has_medium else ["source"]
            src_agg = gt.groupby(group_cols, as_index=False).agg(
                sessions=("_sessions", "sum"), users=("_users", "sum"),
                pageviews=("_pvs", "sum"),
                _bounce_w=("_bounce", lambda x: (x * gt.loc[x.index, "_sessions"]).sum()),
                _engage_w=("_engage", lambda x: (x * gt.loc[x.index, "_sessions"]).sum()),
            )
            src_agg["Bounce Rate"] = src_agg.apply(lambda r: safe_div(r["_bounce_w"], r["sessions"]), axis=1)
            src_agg["Engagement Rate"] = src_agg.apply(lambda r: safe_div(r["_engage_w"], r["sessions"]), axis=1)
            src_agg = src_agg.sort_values("sessions", ascending=False)
            src_show = src_agg.drop(columns=["_bounce_w", "_engage_w"], errors="ignore").copy()
            src_show = src_show.rename(columns={"source": "Source", "medium": "Medium",
                                                  "sessions": "Sessões", "users": "Usuários", "pageviews": "Pageviews"})
            _src_disp = src_show.head(15).copy()
            for c in ["Sessões", "Usuários", "Pageviews"]:
                if c in _src_disp.columns:
                    _src_disp[c] = _src_disp[c].apply(fmt_int)
            for c in ["Bounce Rate", "Engagement Rate"]:
                if c in _src_disp.columns:
                    _src_disp[c] = _src_disp[c].apply(fmt_pct)
            st.dataframe(_src_disp, use_container_width=True, hide_index=True)

        # Devices
        with st.expander("📱 Sessões por Dispositivo"):
            ga4_dev = _get_ga4_device()
            if not ga4_dev.empty:
                gdev = ga4_dev.copy()
                dev_col = "deviceCategory" if "deviceCategory" in gdev.columns else "device_category"
                if dev_col in gdev.columns:
                    gdev["_sessions"] = _ga4_col(gdev, "sessions")
                    dev_agg = gdev.groupby(dev_col, as_index=False).agg(sessions=("_sessions", "sum"))
                    fig = px.pie(dev_agg, values="sessions", names=dev_col, hole=0.4,
                                 color_discrete_sequence=["#26A69A", "#42A5F5", "#FF8C00", "#AB47BC"])
                    fig.update_layout(**CHART_DEFAULTS, height=300, margin=dict(l=10, r=10, t=10, b=10))
                    fig.update_traces(textposition="inside", textinfo="percent+label")
                    st.plotly_chart(fig, use_container_width=True)

    # ══════════════════════════════════════════════════════════════════════
    #  SEÇÃO CROSS-CHANNEL (merged into Audiência & Canais)
    # ══════════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown(H("Cross-Channel — Meta + GA4", "sh-teal"), unsafe_allow_html=True)

    ga4_traffic_cross = _get_ga4_traffic()
    ga4_conv_cross = _get_ga4_conv()
    if ga4_traffic_cross.empty:
        st.info("Sem dados GA4 para cruzamento.")
    else:
        ga4_paid = _is_paid_traffic(ga4_traffic_cross)
        ga4_conv_paid = _is_paid_traffic(ga4_conv_cross) if not ga4_conv_cross.empty else ga4_conv_cross
        paid_sessions = _ga4_col_sum(ga4_paid, "sessions") if not ga4_paid.empty else 0
        paid_conv = _ga4_col_sum(ga4_conv_paid, "conversions") if not ga4_conv_paid.empty else 0
        paid_rev = _ga4_col_sum(ga4_conv_paid, "transactionRevenue") if not ga4_conv_paid.empty else 0

        _passthrough = safe_div(paid_sessions, total_clicks, 100)
        _roas_ga4_x = fmt_dec(safe_div(paid_rev, total_spend), suffix="x")
        cost_per_session = safe_div(total_spend, paid_sessions)

        st.markdown(
            f'<div class="insight-box">'
            f'Taxa de passagem Meta→GA4: <b>{fmt_pct(_passthrough)}</b> · '
            f'ROAS Meta: <b>{fmt_dec(roas, suffix="x")}</b> vs ROAS GA4: <b>{_roas_ga4_x}</b><br>'
            f'<b>{fmt_int(paid_sessions)}</b> sessões pagas · Custo/Sessão: <b>{brl(cost_per_session)}</b>'
            f'</div>', unsafe_allow_html=True,
        )

        st.markdown(kpi_row([
            kpi_card("Sessões Pagas GA4", fmt_int(paid_sessions), icon="🌐"),
            kpi_card("Custo/Sessão", brl(cost_per_session), icon="💵"),
            kpi_card("Conversões GA4", fmt_int(paid_conv), icon="🛒"),
            kpi_card("CPA (GA4)", brl(safe_div(total_spend, paid_conv)), icon="🎯"),
            kpi_card("ROAS (GA4)", fmt_dec(safe_div(paid_rev, total_spend), suffix="x"), icon="📈"),
        ]), unsafe_allow_html=True)

        # Cross-channel campaign comparison
        with st.expander("📋 Comparativo por Campanha — Meta vs GA4"):
            meta_camp = df_camp.groupby("campaign", as_index=False).agg(
                spend=("spend", "sum"), clicks=("clicks", "sum"),
                conv_meta=("actions_purchase", "sum"),
                rev_meta=("action_values_purchase", "sum"),
            )
            meta_camp["roas_meta"] = meta_camp.apply(lambda r: safe_div(r["rev_meta"], r["spend"]), axis=1)
            meta_camp["_norm"] = meta_camp["campaign"].apply(_normalise_campaign_name)

            if not ga4_paid.empty and "campaign" in ga4_paid.columns:
                ga4_camp = ga4_paid.copy()
                ga4_camp["_sessions"] = _ga4_col(ga4_camp, "sessions")
                ga4_camp_agg = ga4_camp.groupby("campaign", as_index=False).agg(
                    sessions_ga4=("_sessions", "sum"))
                if not ga4_conv_paid.empty and "campaign" in ga4_conv_paid.columns:
                    gc_paid = ga4_conv_paid.copy()
                    gc_paid["_conv"] = _ga4_col(gc_paid, "conversions")
                    gc_paid["_rev"] = _ga4_col(gc_paid, "transactionRevenue")
                    gc_agg = gc_paid.groupby("campaign", as_index=False).agg(
                        conv_ga4=("_conv", "sum"), rev_ga4=("_rev", "sum"))
                    ga4_camp_agg = ga4_camp_agg.merge(gc_agg, on="campaign", how="left")
                else:
                    ga4_camp_agg["conv_ga4"] = 0
                    ga4_camp_agg["rev_ga4"] = 0
                ga4_camp_agg = ga4_camp_agg.fillna(0)
                ga4_camp_agg["_norm"] = ga4_camp_agg["campaign"].apply(_normalise_campaign_name)

                merged_rows = []
                for _, mr in meta_camp.iterrows():
                    ga4_row = None
                    exact = ga4_camp_agg[ga4_camp_agg["campaign"] == mr["campaign"]]
                    if not exact.empty:
                        ga4_row = exact.iloc[0]
                    else:
                        norm = ga4_camp_agg[ga4_camp_agg["_norm"] == mr["_norm"]]
                        if not norm.empty and mr["_norm"]:
                            ga4_row = norm.iloc[0]
                    merged_rows.append({
                        "Campanha": mr["campaign"],
                        "Spend": brl(mr["spend"]),
                        "Conv Meta": fmt_int(mr["conv_meta"]),
                        "Conv GA4": fmt_int(ga4_row["conv_ga4"]) if ga4_row is not None else "0",
                        "ROAS Meta": fmt_dec(mr["roas_meta"], suffix="x"),
                        "ROAS GA4": fmt_dec(safe_div(ga4_row["rev_ga4"], mr["spend"]) if ga4_row is not None else 0, suffix="x"),
                    })
                if merged_rows:
                    st.dataframe(pd.DataFrame(merged_rows), use_container_width=True, hide_index=True)

# ─────────────────────────────────────────────────────────────────────────────
#  TAB 2 — DIAGNÓSTICO & AÇÕES (moved to position 2)
# ─────────────────────────────────────────────────────────────────────────────
with tab_diagnostic:

    # ── Quality Rankings ─────────────────────────────────────────────────
    st.markdown(H("Quality Rankings dos Criativos", "sh-red"), unsafe_allow_html=True)
    if not df_ad.empty and "quality_ranking" in df_ad.columns:
        qr = df_ad.groupby("ad_name", as_index=False).agg(
            spend=("spend", "sum"),
            quality=("quality_ranking", "first"),
            engagement_rank=("engagement_rate_ranking", "first"),
            conversion_rank=("conversion_rate_ranking", "first"),
        ).sort_values("spend", ascending=False)
        qr_show = qr.rename(columns={
            "ad_name": "Criativo", "spend": "Spend",
            "quality": "Quality Ranking",
            "engagement_rank": "Engagement Ranking",
            "conversion_rank": "Conversion Ranking",
        }).copy()
        qr_show["Spend"] = qr_show["Spend"].apply(brl)
        st.dataframe(qr_show, use_container_width=True, hide_index=True)
    else:
        st.info("Quality rankings não disponíveis na API.")

    # ── Ad fatigue alerts ────────────────────────────────────────────────
    st.markdown(H("Alertas de Fadiga de Anúncio", "sh-red"), unsafe_allow_html=True)
    if not df_ad.empty and "frequency" in df_ad.columns:
        ad_fatigue = df_ad.groupby("ad_name", as_index=False).agg(
            avg_freq=("frequency", "mean"),
            impressions=("impressions", "sum"),
            clicks=("clicks", "sum"),
            spend=("spend", "sum"),
        )
        ad_fatigue["ctr"] = ad_fatigue.apply(
            lambda r: safe_div(r["clicks"], r["impressions"], 100), axis=1
        )
        high_freq = ad_fatigue[ad_fatigue["avg_freq"] >= 2.5].sort_values("avg_freq", ascending=False)

        if not high_freq.empty:
            for _, row in high_freq.iterrows():
                severity = "alert-box" if row["avg_freq"] >= 5 else "alert-box-warn"
                st.markdown(
                    f'<div class="{severity}">⚠️ <b>{row["ad_name"]}</b> — '
                    f'Frequência média: {fmt_dec(row["avg_freq"], 1)} | '
                    f'CTR: {fmt_pct(row["ctr"])} | '
                    f'Spend: {brl(row["spend"])}</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.success("Nenhum criativo com frequência alta (≥2,5). Sem fadiga detectada.")
    else:
        st.info("Dados de frequência não disponíveis.")

    # ── CPA vs ROAS Quadrant ────────────────────────────────────────────
    st.markdown(H("Quadrante de Eficiência — Campanhas", "sh-red"), unsafe_allow_html=True)
    camp_eff = df_camp.groupby("campaign", as_index=False).agg(
        spend=("spend", "sum"), purchases=("actions_purchase", "sum"),
        revenue=("action_values_purchase", "sum"),
    )
    camp_eff["CPA"] = camp_eff.apply(lambda r: safe_div(r["spend"], r["purchases"]), axis=1)
    camp_eff["ROAS"] = camp_eff.apply(lambda r: safe_div(r["revenue"], r["spend"]), axis=1)
    camp_eff = camp_eff[camp_eff["purchases"] > 0]

    if not camp_eff.empty:
        median_cpa = camp_eff["CPA"].median()
        median_roas = camp_eff["ROAS"].median()

        fig = px.scatter(
            camp_eff, x="CPA", y="ROAS", size="spend",
            hover_name="campaign", color="ROAS",
            color_continuous_scale=["#EF5350", "#FFCA28", "#66BB6A"],
        )
        fig.add_hline(y=median_roas, line_dash="dash", line_color="#666",
                       annotation_text=f"ROAS mediano: {fmt_dec(median_roas)}")
        fig.add_vline(x=median_cpa, line_dash="dash", line_color="#666",
                       annotation_text=f"CPA mediano: {brl(median_cpa)}")
        fig.update_layout(
            **CHART_DEFAULTS, height=450,
            margin=dict(l=10, r=10, t=10, b=10),
            xaxis=dict(title="CPA (R$) — menor é melhor →"),
            yaxis=dict(title="ROAS — maior é melhor ↑"),
        )
        fig.add_annotation(x=0.05, y=0.95, xref="paper", yref="paper",
                           text="✅ ESCALAR", showarrow=False, font=dict(color="#66BB6A", size=14))
        fig.add_annotation(x=0.95, y=0.05, xref="paper", yref="paper",
                           text="🛑 PAUSAR", showarrow=False, font=dict(color="#EF5350", size=14))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Sem conversões suficientes para análise de eficiência.")

    # ── Spend efficiency over time (lazy daily data) ────────────────────
    st.markdown(H("Eficiência do Spend ao Longo do Tempo", "sh-red"), unsafe_allow_html=True)
    eff_src = _get_daily_camp()
    if not eff_src.empty and "date" in eff_src.columns:
        eff_daily = (
            eff_src.groupby("date", as_index=False)
            .agg(spend=("spend", "sum"), purchases=("actions_purchase", "sum"),
                 revenue=("action_values_purchase", "sum"))
            .sort_values("date")
        )
        eff_daily["CPA"] = eff_daily.apply(lambda r: safe_div(r["spend"], r["purchases"]), axis=1)
        eff_daily["ROAS"] = eff_daily.apply(lambda r: safe_div(r["revenue"], r["spend"]), axis=1)
        eff_daily["CPA_ma7"] = eff_daily["CPA"].rolling(7, min_periods=1).mean()
        eff_daily["ROAS_ma7"] = eff_daily["ROAS"].rolling(7, min_periods=1).mean()

        col1, col2 = st.columns(2)
        with col1:
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=eff_daily["date"], y=eff_daily["CPA"],
                                     name="CPA", line=dict(color="#EF5350", width=1), opacity=0.3,
                                     fill="tozeroy", fillcolor="rgba(239,68,68,0.05)"))
            fig.add_trace(go.Scatter(x=eff_daily["date"], y=eff_daily["CPA_ma7"],
                                     name="CPA MA7", line=dict(color="#EF5350", width=3)))
            _add_annotations(fig, eff_daily["date"], eff_daily["CPA_ma7"], fmt_fn=lambda v: brl(v))
            fig.update_layout(**CHART_DEFAULTS, height=300,
                              margin=dict(l=10, r=10, t=30, b=10),
                              title="CPA Diário (MA7)", yaxis=dict(title="CPA (R$)"))
            st.plotly_chart(fig, use_container_width=True)

        with col2:
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=eff_daily["date"], y=eff_daily["ROAS"],
                                     name="ROAS", line=dict(color="#66BB6A", width=1), opacity=0.3,
                                     fill="tozeroy", fillcolor="rgba(34,197,94,0.05)"))
            fig.add_trace(go.Scatter(x=eff_daily["date"], y=eff_daily["ROAS_ma7"],
                                     name="ROAS MA7", line=dict(color="#66BB6A", width=3)))
            _add_annotations(fig, eff_daily["date"], eff_daily["ROAS_ma7"], fmt_fn=lambda v: fmt_dec(v, suffix="x"))
            fig.update_layout(**CHART_DEFAULTS, height=300,
                              margin=dict(l=10, r=10, t=30, b=10),
                              title="ROAS Diário (MA7)", yaxis=dict(title="ROAS"))
            st.plotly_chart(fig, use_container_width=True)

    # ── Custo de Inação (fatigued creatives) ─────────────────────────────
    if not df_ad.empty and "frequency" in df_ad.columns:
        _fatigue_ads = df_ad.groupby("ad_name", as_index=False).agg(
            avg_freq=("frequency", "mean"), spend=("spend", "sum"),
            purchases=("actions_purchase", "sum"),
        )
        _fatigued_ads = _fatigue_ads[_fatigue_ads["avg_freq"] >= FATIGUE_THRESHOLD]
        if not _fatigued_ads.empty:
            _waste_spend = _fatigued_ads["spend"].sum()
            _waste_pct = safe_div(_waste_spend, total_spend, 100)
            st.markdown(H("Custo de Inação — Criativos com Fadiga", "sh-red"), unsafe_allow_html=True)
            st.markdown(
                f'<div class="cost-inaction">'
                f'🔥 <b>{len(_fatigued_ads)} criativos</b> com frequência ≥ {FATIGUE_THRESHOLD} '
                f'estão consumindo <b>{brl(_waste_spend)}</b> ({fmt_pct(_waste_pct)} do orçamento).<br>'
                f'<b>Ação recomendada:</b> Pausar ou substituir estes criativos pode liberar até '
                f'<b>{brl(_waste_spend * 0.3)}</b> para reinvestir em criativos frescos.'
                f'</div>', unsafe_allow_html=True,
            )

    # ── Recomendações Automáticas ────────────────────────────────────────
    st.markdown(H("Recomendações Automáticas", "sh-green"), unsafe_allow_html=True)
    _ca_for_recs = None
    if not df_ad.empty:
        _ca_for_recs = df_ad.groupby("ad_name", as_index=False).agg(
            spend=("spend", "sum"), purchases=("actions_purchase", "sum"))
    _recs = _generate_recommendations(roas, cpa, ctr, avg_freq, target_roas, target_cpa, _ca_for_recs)
    for _title, _desc in _recs:
        st.markdown(
            f'<div class="recommendation-box"><b>{_title}</b><br>{_desc}</div>',
            unsafe_allow_html=True,
        )



#  PDF EXPORT
# ═══════════════════════════════════════════════════════════════════════════════
PDF_CHART_LAYOUT = dict(
    paper_bgcolor="#0E1117",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="Inter, Arial, sans-serif", color="white", size=11),
    margin=dict(l=50, r=30, t=30, b=40),
    xaxis=dict(gridcolor="rgba(255,255,255,0.06)", gridwidth=1),
    yaxis=dict(gridcolor="rgba(255,255,255,0.06)", gridwidth=1),
    hoverlabel=dict(
        bgcolor="#1A1A2E",
        font_color="white",
        font_size=12,
        bordercolor="rgba(255,255,255,0.1)",
    ),
)


def _generate_pdf():
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos
    import requests as _req

    _BG = (14, 17, 23)
    _SF = (26, 26, 46)
    _WH = (250, 250, 250)
    _GR = (170, 170, 170)
    _AC = (255, 140, 0)
    _GN = (34, 197, 94)
    _RD = (239, 68, 68)
    _TL = (14, 165, 233)
    _BLUE = (59, 130, 246)

    # ── Font path resolution ─────────────────────────────────────────
    _fonts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")

    class _ReportPDF(FPDF):
        def header(self):
            self.set_fill_color(*_BG)
            self.rect(0, 0, self.w, self.h, "F")

        def footer(self):
            if self.page_no() == 1:
                return  # Cover page: no footer
            self.set_y(-12)
            # Separator line
            self.set_draw_color(50, 50, 70)
            self.line(10, self.get_y(), self.w - 10, self.get_y())
            self.ln(1.5)
            self.set_font("Inter", "", 6.5)
            self.set_text_color(100, 100, 100)
            # Left: generation date
            self.cell(60, 5, f"Gerado em {date.today().strftime('%d/%m/%Y')}")
            # Center: branding + page
            self.cell(0, 5, f"Meta Dashboard  \u00b7  P\u00e1g. {self.page_no()}/{{nb}}",
                      align="C")

    pdf = _ReportPDF(orientation="L", unit="mm", format="A4")
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=15)

    # ── Register Inter TTF fonts (unicode-safe) ──────────────────────
    pdf.add_font("Inter", "", os.path.join(_fonts_dir, "Inter-Regular.ttf"))
    pdf.add_font("Inter", "B", os.path.join(_fonts_dir, "Inter-Bold.ttf"))
    pdf.add_font("Inter", "I", os.path.join(_fonts_dir, "Inter-Italic.ttf"))

    # ── Helpers ──────────────────────────────────────────────────────

    def _heading(text, sz=16, subtitle=None):
        """Page heading with accent gradient bar at top."""
        y = pdf.get_y()
        # Gradient bar (orange → teal, simulated with 2 rects)
        bar_w = pdf.w - 20
        half = bar_w / 2
        pdf.set_fill_color(*_AC)
        pdf.rect(10, y, half, 3, "F")
        pdf.set_fill_color(*_TL)
        pdf.rect(10 + half, y, half, 3, "F")
        pdf.set_y(y + 5)
        # Title
        pdf.set_text_color(*_WH)
        pdf.set_font("Inter", "B", sz)
        pdf.cell(0, 10, str(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
        if subtitle:
            pdf.set_text_color(*_GR)
            pdf.set_font("Inter", "", 9)
            pdf.cell(0, 6, str(subtitle), new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
        pdf.ln(1)

    def _section(text, color=_AC):
        pdf.ln(2)
        y = pdf.get_y()
        # Background stripe
        pdf.set_fill_color(20, 20, 38)
        pdf.rect(10, y, pdf.w - 20, 9, "F")
        # Left accent bar
        pdf.set_fill_color(*color)
        pdf.rect(10, y, 4, 9, "F")
        pdf.set_xy(18, y + 0.5)
        pdf.set_text_color(*_WH)
        pdf.set_font("Inter", "B", 10)
        pdf.cell(0, 8, str(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(1)

    def _insight(text):
        """Insight box with left accent border."""
        y = pdf.get_y()
        # Background
        pdf.set_fill_color(15, 25, 50)
        lines = str(text).split("\n")
        h = max(12, 5 * len(lines) + 4)
        pdf.rect(10, y, pdf.w - 20, h, "F")
        # Left accent bar (blue)
        pdf.set_fill_color(*_BLUE)
        pdf.rect(10, y, 4, h, "F")
        # Text
        pdf.set_xy(18, y + 2)
        pdf.set_text_color(209, 213, 219)
        pdf.set_font("Inter", "", 7.5)
        for line in lines:
            pdf.cell(pdf.w - 32, 4.5, str(line),
                     new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_x(18)
        pdf.set_y(y + h + 2)
        pdf.set_text_color(*_WH)

    def _kpis(items):
        n = len(items)
        if n == 0:
            return
        cw = (pdf.w - 20) / n
        y0 = pdf.get_y()
        for i, (label, value, delta) in enumerate(items):
            x = 10 + i * cw
            # Card background
            pdf.set_fill_color(*_SF)
            pdf.rect(x, y0, cw - 2, 22, "F")
            # Accent bar on top (orange)
            pdf.set_fill_color(*_AC)
            pdf.rect(x, y0, cw - 2, 2.5, "F")
            # Label (UPPERCASE)
            pdf.set_xy(x + 1, y0 + 4)
            pdf.set_text_color(*_GR)
            pdf.set_font("Inter", "", 5.5)
            pdf.cell(cw - 4, 3.5, str(label).upper(), align="C")
            # Value (bold, large)
            pdf.set_xy(x + 1, y0 + 8.5)
            pdf.set_text_color(*_WH)
            pdf.set_font("Inter", "B", 11)
            pdf.cell(cw - 4, 6, str(value), align="C")
            # Delta
            if delta:
                pdf.set_xy(x + 1, y0 + 16)
                ds = str(delta)
                if ds.startswith("+"):
                    pdf.set_text_color(*_GN)
                elif ds.startswith("-"):
                    pdf.set_text_color(*_RD)
                else:
                    pdf.set_text_color(*_GR)
                pdf.set_font("Inter", "", 6.5)
                pdf.cell(cw - 4, 3.5, ds, align="C")
        pdf.set_y(y0 + 25)
        pdf.set_text_color(*_WH)

    def _chart(fig, w=255, h=350):
        try:
            fig.update_layout(**PDF_CHART_LAYOUT)
            img = fig.to_image(format="png", width=900, height=h, scale=2)
            buf = io.BytesIO(img)
            pdf.image(buf, x=(pdf.w - w) / 2, w=w)
        except Exception:
            pdf.set_font("Inter", "I", 9)
            pdf.set_text_color(*_GR)
            pdf.cell(0, 8, "[Gr\u00e1fico indispon\u00edvel \u2014 instale kaleido]",
                     new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
            pdf.set_text_color(*_WH)

    def _tbl(headers, rows, widths=None):
        n = len(headers)
        if widths is None:
            widths = [(pdf.w - 20) / n] * n

        # Determine numeric-like columns for right-alignment
        _num_cols = set()
        for i, h in enumerate(headers):
            hl = h.lower()
            if hl not in ("campanha", "criativo", "ad_name", "nome"):
                _num_cols.add(i)

        def _tbl_header():
            y = pdf.get_y()
            # Header gradient (2 tones)
            pdf.set_fill_color(30, 30, 55)
            pdf.rect(10, y, pdf.w - 20, 7, "F")
            pdf.set_fill_color(35, 35, 62)
            pdf.rect(10, y, (pdf.w - 20) / 2, 7, "F")
            # Bottom border
            pdf.set_draw_color(60, 60, 90)
            pdf.line(10, y + 7, pdf.w - 10, y + 7)
            pdf.set_xy(10, y)
            pdf.set_text_color(*_WH)
            pdf.set_font("Inter", "B", 7)
            for i, h in enumerate(headers):
                pdf.cell(widths[i], 7, str(h), fill=False, align="C")
            pdf.ln()

        _tbl_header()
        pdf.set_font("Inter", "", 6.5)
        for ri, row in enumerate(rows):
            if pdf.get_y() > pdf.h - 20:
                pdf.add_page()
                _tbl_header()
                pdf.set_font("Inter", "", 6.5)
            # Subtle zebra striping
            if ri % 2 == 0:
                pdf.set_fill_color(18, 18, 32)
            else:
                pdf.set_fill_color(*_BG)
            pdf.set_text_color(220, 220, 220)
            for i, val in enumerate(row):
                align = "R" if i in _num_cols else "L"
                pdf.cell(widths[i], 5.5, str(val)[:40], fill=True, align=align)
            pdf.ln()
        pdf.set_text_color(*_WH)

    def _dl_image(url):
        """Download image from URL, return BytesIO or None."""
        if not url:
            return None
        try:
            r = _req.get(url, timeout=5)
            if r.status_code == 200 and len(r.content) > 200:
                return io.BytesIO(r.content)
        except Exception:
            pass
        return None

    def _fit_img(buf, x, y, max_w, max_h):
        """Place image keeping aspect ratio, centered in bounding box."""
        from PIL import Image as _PILImg
        buf.seek(0)
        try:
            _pil = _PILImg.open(buf)
            iw, ih = _pil.size
        except Exception:
            buf.seek(0)
            pdf.image(buf, x=x, y=y, w=max_w)
            return
        ratio = ih / iw
        w, h = max_w, max_w * ratio
        if h > max_h:
            h = max_h
            w = max_h / ratio
        cx = x + (max_w - w) / 2
        cy = y + (max_h - h) / 2
        buf.seek(0)
        pdf.image(buf, x=cx, y=cy, w=w, h=h)

    def _creative_card(row, rank=None, badge_color=None, badge_text=None):
        """Render a creative card with thumbnail + metrics."""
        y0 = pdf.get_y()
        if y0 > pdf.h - 50:
            pdf.add_page()
            y0 = pdf.get_y()

        img_w, img_h = 35, 35
        thumb_url = _get_thumb(row) if callable(_get_thumb) else None
        img_buf = _dl_image(thumb_url)

        # Card background
        pdf.set_fill_color(*_SF)
        pdf.rect(10, y0, pdf.w - 20, img_h + 2, "F")

        # Thumbnail area
        pdf.set_fill_color(42, 42, 58)
        pdf.rect(11, y0 + 1, img_w, img_h, "F")
        if img_buf:
            try:
                _fit_img(img_buf, 11, y0 + 1, img_w, img_h)
            except Exception:
                pdf.set_xy(11, y0 + 15)
                pdf.set_text_color(*_GR)
                pdf.set_font("Inter", "I", 7)
                pdf.cell(img_w, 4, "Sem preview", align="C")
        else:
            pdf.set_xy(11, y0 + 15)
            pdf.set_text_color(*_GR)
            pdf.set_font("Inter", "I", 7)
            pdf.cell(img_w, 4, "Sem preview", align="C")

        mx = 11 + img_w + 4
        mw = pdf.w - mx - 12

        # Name + badge
        pdf.set_xy(mx, y0 + 1)
        name = str(row.get("ad_name", "-"))[:55]
        rank_str = f"#{rank} \u2014 " if rank else ""
        pdf.set_text_color(*_WH)
        pdf.set_font("Inter", "B", 9)
        pdf.cell(mw - 30, 5, f"{rank_str}{name}")
        if badge_text and badge_color:
            bx = pdf.get_x()
            pdf.set_xy(bx, y0 + 1)
            pdf.set_fill_color(*badge_color)
            pdf.set_text_color(255, 255, 255)
            pdf.set_font("Inter", "B", 6)
            pdf.cell(28, 5, str(badge_text), fill=True, align="C")

        # Headline
        title_val = row.get("title", "") or row.get("name", "") or ""
        if title_val and str(title_val) != "nan":
            pdf.set_xy(mx, y0 + 7)
            pdf.set_text_color(*_GR)
            pdf.set_font("Inter", "I", 6.5)
            pdf.cell(mw, 3.5, f"Headline: {str(title_val)[:80]}")

        # Metrics grid row 1 (4 cols)
        my1 = y0 + 12
        m_cw = mw / 4
        for j, (ml, mv) in enumerate([
            ("SPEND", brl(row.get("spend", 0))),
            ("IMPRESS\u00d5ES", fmt_int(row.get("impressions", 0))),
            ("CLIQUES", fmt_int(row.get("clicks", 0))),
            ("CTR", fmt_pct(row.get("CTR", 0))),
        ]):
            x = mx + j * m_cw
            pdf.set_xy(x, my1)
            pdf.set_text_color(*_GR)
            pdf.set_font("Inter", "", 5)
            pdf.cell(m_cw, 3, ml)
            pdf.set_xy(x, my1 + 3)
            pdf.set_text_color(*_WH)
            pdf.set_font("Inter", "B", 8)
            pdf.cell(m_cw, 5, str(mv))

        # Metrics grid row 2
        my2 = my1 + 10
        for j, (ml, mv) in enumerate([
            ("CONVERS\u00d5ES", fmt_int(row.get("purchases", 0))),
            ("CPA", brl(row.get("CPA", 0))),
            ("ROAS", fmt_dec(row.get("ROAS", 0), suffix="x")),
            ("FREQ.", fmt_dec(row.get("avg_freq", 0), 1)),
        ]):
            x = mx + j * m_cw
            pdf.set_xy(x, my2)
            pdf.set_text_color(*_GR)
            pdf.set_font("Inter", "", 5)
            pdf.cell(m_cw, 3, ml)
            pdf.set_xy(x, my2 + 3)
            pdf.set_text_color(*_WH)
            pdf.set_font("Inter", "B", 8)
            pdf.cell(m_cw, 5, str(mv))

        # Separator
        pdf.set_y(y0 + img_h + 4)
        pdf.set_draw_color(50, 50, 70)
        pdf.line(10, pdf.get_y(), pdf.w - 10, pdf.get_y())
        pdf.ln(3)
        pdf.set_text_color(*_WH)

    # ══════════════════════════════════════════════════════════════════════
    #  COVER PAGE — Capa com branding
    # ══════════════════════════════════════════════════════════════════════
    pdf.add_page()

    # Top gradient bar (orange → teal)
    bar_h = 6
    bar_w = pdf.w
    half_w = bar_w / 2
    pdf.set_fill_color(*_AC)
    pdf.rect(0, 0, half_w, bar_h, "F")
    pdf.set_fill_color(*_TL)
    pdf.rect(half_w, 0, half_w, bar_h, "F")

    # Main title block — centered vertically
    pdf.set_y(55)
    pdf.set_text_color(*_WH)
    pdf.set_font("Inter", "B", 32)
    pdf.cell(0, 18, "META DASHBOARD", align="C",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Inter", "", 14)
    pdf.set_text_color(200, 200, 210)
    pdf.cell(0, 10, "Performance Analytics Report", align="C",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # Divider line
    pdf.ln(6)
    _div_y = pdf.get_y()
    pdf.set_draw_color(60, 60, 90)
    pdf.line(pdf.w / 2 - 40, _div_y, pdf.w / 2 + 40, _div_y)
    pdf.ln(8)

    # Period / Account / Campaign
    pdf.set_font("Inter", "", 11)
    pdf.set_text_color(*_GR)
    pdf.cell(0, 7,
             f"Per\u00edodo: {date_from.strftime('%d/%m/%Y')} \u2014 {date_to.strftime('%d/%m/%Y')}",
             align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 7, f"Conta: {_acct_label}", align="C",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 7, f"Campanha: {_camp_label}", align="C",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # Badge: auto-generated
    pdf.ln(10)
    _badge_w = 72
    _badge_x = (pdf.w - _badge_w) / 2
    _badge_y = pdf.get_y()
    pdf.set_fill_color(25, 25, 45)
    pdf.set_draw_color(60, 60, 90)
    pdf.rect(_badge_x, _badge_y, _badge_w, 8, "DF")
    pdf.set_xy(_badge_x, _badge_y + 0.5)
    pdf.set_font("Inter", "I", 7)
    pdf.set_text_color(150, 150, 170)
    pdf.cell(_badge_w, 7, "Relat\u00f3rio Gerado Automaticamente", align="C")

    # Footer branding on cover
    pdf.set_y(pdf.h - 20)
    pdf.set_font("Inter", "", 7)
    pdf.set_text_color(80, 80, 100)
    pdf.cell(0, 5, "Meta Dashboard \u00b7 Powered by Windsor.ai + Streamlit",
             align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # ══════════════════════════════════════════════════════════════════════
    #  PAGE 2 — Resumo Executivo
    # ══════════════════════════════════════════════════════════════════════
    pdf.add_page()
    _heading("PAINEL DE PERFORMANCE \u2014 META ADS + GA4",
             subtitle=f"{date_from.strftime('%d/%m/%Y')} \u2014 {date_to.strftime('%d/%m/%Y')}"
                      f"  |  Conta: {_acct_label}  |  {_camp_label}")
    pdf.ln(2)

    # Budget Pacing (if configured)
    _n_camps = df_camp["campaign"].nunique() if "campaign" in df_camp.columns else 0
    if monthly_budget > 0:
        _period_days = (date_to - date_from).days + 1
        _ideal_spend = monthly_budget * (_period_days / 30)
        _pacing_pct = safe_div(total_spend, _ideal_spend, 100)
        _pacing_status = "OK" if _pacing_pct <= 110 else ("ATEN\u00c7\u00c3O" if _pacing_pct <= 130 else "EXCEDIDO")
        _insight(
            f"BUDGET PACING: {_pacing_status} ({fmt_pct(_pacing_pct)} do ideal)\n"
            f"Gasto: {brl(total_spend)} de {brl(monthly_budget)} | "
            f"Restante: {brl(monthly_budget - total_spend)}"
        )

    # Insight box
    _insight(
        f"Investimento de {brl(total_spend)} no per\u00edodo com ROAS de "
        f"{fmt_dec(roas, suffix='x')} | CPA m\u00e9dio de {brl(cpa)}\n"
        f"{_n_camps} campanhas ativas | CTR geral de {fmt_pct(ctr)} | "
        f"Frequ\u00eancia m\u00e9dia: {fmt_dec(avg_freq, 1)}"
    )

    _section("KPIs Estrat\u00e9gicos")
    _kpis([
        ("Valor Gasto", brl(total_spend), _delta_str(d_spend)),
        ("ROAS", fmt_dec(roas, suffix="x"), _delta_str(d_roas)),
        ("CPA", brl(cpa), _delta_str(d_cpa)),
        ("Convers\u00f5es", fmt_int(total_purch), _delta_str(d_purch)),
        ("Receita", brl(total_rev), _delta_str(d_rev)),
        ("CTR", fmt_pct(ctr), _delta_str(d_ctr)),
    ])

    _section("KPIs Secund\u00e1rios")
    _kpis([
        ("Impress\u00f5es", fmt_int(total_imp), _delta_str(d_imp)),
        ("Cliques", fmt_int(total_clicks), _delta_str(d_clicks)),
        ("CPC", brl(cpc), _delta_str(d_cpc)),
        ("CPM", brl(cpm), _delta_str(d_cpm)),
        ("Ticket M\u00e9dio", brl(ticket_medio), None),
        ("Frequ\u00eancia", fmt_dec(avg_freq, 1), None),
    ])

    _section("Alcance & Engajamento")
    _kpis([
        ("Alcance", fmt_int(total_reach), _delta_str(d_reach)),
        ("Engajamento", fmt_int(total_engagement), _delta_str(d_eng)),
        ("Custo/Eng.", brl(cost_per_eng), _delta_str(d_cost_eng)),
        ("CPL", brl(cpl), _delta_str(d_cpl)),
    ])

    # Smart Insights in PDF
    _auto_insights_pdf = _generate_smart_insights(
        total_spend, roas, cpa, ctr, avg_freq, total_purch,
        target_roas, target_cpa, monthly_budget, d_roas, d_cpa
    )
    if _auto_insights_pdf:
        _section("Insights Autom\u00e1ticos", _GN)
        _ins_text = "\n".join([i.replace("**", "").replace("*", "") for i in _auto_insights_pdf])
        _insight(_ins_text)

    # ══════════════════════════════════════════════════════════════════════
    #  PAGE 3 — Tend\u00eancia Di\u00e1ria + Top Campanhas
    # ══════════════════════════════════════════════════════════════════════
    pdf.add_page()
    _heading("Tend\u00eancia Di\u00e1ria + Campanhas", 14)

    _section("Tend\u00eancia Di\u00e1ria (com m\u00e9dia m\u00f3vel 7d)")

    _pdf_daily = _get_daily_camp()
    if not _pdf_daily.empty and "date" in _pdf_daily.columns:
        _dd = (
            _pdf_daily.groupby("date", as_index=False)
            .agg(spend=("spend", "sum"), impressions=("impressions", "sum"),
                 clicks=("clicks", "sum"))
            .sort_values("date")
        )
        _dd["ctr"] = _dd.apply(lambda r: safe_div(r["clicks"], r["impressions"], 100), axis=1)
        _dd["spend_ma7"] = _dd["spend"].rolling(7, min_periods=1).mean()
        _dd["ctr_ma7"] = _dd["ctr"].rolling(7, min_periods=1).mean()

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=_dd["date"], y=_dd["spend"], name="Spend",
            line=dict(color="#FF8C00", width=1), opacity=0.4,
            fill="tozeroy", fillcolor="rgba(255,140,0,0.07)",
        ))
        fig.add_trace(go.Scatter(
            x=_dd["date"], y=_dd["spend_ma7"], name="Spend MA7",
            line=dict(color="#FF8C00", width=3),
        ))
        fig.add_trace(go.Scatter(
            x=_dd["date"], y=_dd["ctr_ma7"], name="CTR MA7 (%)",
            yaxis="y2", line=dict(color="#4FC3F7", width=3),
        ))
        fig.update_layout(
            height=350,
            yaxis=dict(title="Spend (R$)", showgrid=True, gridcolor="rgba(255,255,255,0.06)"),
            yaxis2=dict(title="CTR (%)", overlaying="y", side="right", showgrid=False),
            xaxis=dict(showgrid=False),
            legend=dict(orientation="h", y=-0.15, xanchor="center", x=0.5),
        )
        _chart(fig)

    pdf.ln(2)
    _section("Vis\u00e3o Geral por Campanha")
    _pdf_ov = df_camp.groupby("campaign", as_index=False).agg(
        impressions=("impressions", "sum"), clicks=("clicks", "sum"),
        spend=("spend", "sum"), purchases=("actions_purchase", "sum"),
        revenue=("action_values_purchase", "sum"),
    )
    _pdf_ov["CTR"] = _pdf_ov.apply(lambda r: safe_div(r["clicks"], r["impressions"], 100), axis=1)
    _pdf_ov["CPA"] = _pdf_ov.apply(lambda r: safe_div(r["spend"], r["purchases"]), axis=1)
    _pdf_ov["ROAS"] = _pdf_ov.apply(lambda r: safe_div(r["revenue"], r["spend"]), axis=1)
    _pdf_ov = _pdf_ov.sort_values("spend", ascending=False).head(10)

    _camp_rows = []
    for _, r in _pdf_ov.iterrows():
        _camp_rows.append([
            str(r["campaign"])[:30], brl(r["spend"]), fmt_int(r["impressions"]),
            fmt_int(r["clicks"]), fmt_int(r["purchases"]),
            fmt_pct(r["CTR"]), brl(r["CPA"]), fmt_dec(r["ROAS"], suffix="x"),
        ])
    _tbl(
        ["Campanha", "Spend", "Impr.", "Cliques", "Conv.", "CTR", "CPA", "ROAS"],
        _camp_rows,
        [80, 30, 28, 25, 22, 22, 30, 25],
    )

    # ══════════════════════════════════════════════════════════════════════
    #  PAGE 4 — Funil de Convers\u00e3o
    # ══════════════════════════════════════════════════════════════════════
    pdf.add_page()
    _heading("Funil de Convers\u00e3o", 14)

    _cr_total = safe_div(total_purch, total_imp, 100)
    _cr_click = safe_div(total_purch, total_link_clicks if total_link_clicks else total_clicks, 100)
    _insight(
        f"Taxa de convers\u00e3o geral (impress\u00e3o\u2192compra): {fmt_pct(_cr_total)}\n"
        f"Taxa de convers\u00e3o de clique\u2192compra: {fmt_pct(_cr_click)}\n"
        f"{fmt_int(total_purch)} compras de {fmt_int(total_imp)} impress\u00f5es no per\u00edodo"
    )

    _section("Funil Completo de Convers\u00e3o", _GN)
    _pdf_funnel = [
        ("Impress\u00f5es", total_imp),
        ("Cliques no Link", total_link_clicks if total_link_clicks else total_clicks),
        ("Vis. de P\u00e1gina", total_lpv),
        ("Adi\u00e7\u00e3o ao Carrinho", total_atc),
        ("In\u00edcio de Checkout", total_ic),
        ("Compra", total_purch),
    ]
    _f_labels = [f[0] for f in _pdf_funnel]
    _f_values = [f[1] for f in _pdf_funnel]

    _top_val = _f_values[0] if _f_values[0] else 1
    _funnel_text = [
        f"{fl}: {fv:,.0f} ({safe_div(fv, _top_val, 100):.1f}% do topo)"
        for fl, fv in zip(_f_labels, _f_values)
    ]
    fig = go.Figure(go.Funnel(
        y=_f_labels, x=_f_values,
        textinfo="text", text=_funnel_text,
        marker=dict(
            color=["#60A5FA", "#3B82F6", "#2563EB", "#1D4ED8", "#1E40AF", "#1E3A8A"],
            line=dict(width=0),
        ),
        connector=dict(line=dict(color="#1E1E2E", width=0)),
    ))
    fig.update_layout(height=400, showlegend=False)
    _chart(fig, w=230, h=400)

    pdf.ln(2)
    _section("Taxas entre Etapas", _GN)
    _rate_items = []
    for i in range(1, len(_pdf_funnel)):
        _pl, _pv = _pdf_funnel[i - 1]
        _cl, _cv = _pdf_funnel[i]
        _rate = safe_div(_cv, _pv, 100)
        _rate_items.append((f"{_pl} \u2192 {_cl}", f"{_rate:.1f}%", None))
    _kpis(_rate_items)

    # ══════════════════════════════════════════════════════════════════════
    #  PAGE 5+ — Criativos
    # ══════════════════════════════════════════════════════════════════════
    pdf.add_page()
    _heading("Criativos \u2014 Performance", 14)

    if not df_ad.empty:
        _cr_agg = {
            "impressions": ("impressions", "sum"),
            "clicks": ("clicks", "sum"),
            "spend": ("spend", "sum"),
            "reach": ("reach", "sum"),
            "purchases": ("actions_purchase", "sum"),
            "avg_freq": ("frequency", "mean"),
        }
        if "action_values_purchase" in df_ad.columns:
            _cr_agg["revenue"] = ("action_values_purchase", "sum")
        if "actions_post_engagement" in df_ad.columns:
            _cr_agg["engagement"] = ("actions_post_engagement", "sum")
        if "video_views" in df_ad.columns:
            _cr_agg["vv"] = ("video_views", "sum")
        if "video_thruplay_watched" in df_ad.columns:
            _cr_agg["thru"] = ("video_thruplay_watched", "sum")

        _pdf_ca = df_ad.groupby("ad_name", as_index=False).agg(**_cr_agg)

        # Merge asset columns (thumbnail, title, body)
        for asset_col in ["image_url", "thumbnail_url", "promoted_post_full_picture",
                          "desktop_feed_standard_preview_url", "body", "title", "name"]:
            if asset_col in df_ad.columns:
                first_vals = df_ad.dropna(subset=[asset_col]).groupby("ad_name")[asset_col].first()
                _pdf_ca = _pdf_ca.merge(first_vals.rename(asset_col), on="ad_name", how="left")

        _pdf_ca["CTR"] = _pdf_ca.apply(lambda r: safe_div(r["clicks"], r["impressions"], 100), axis=1)
        _pdf_ca["CPA"] = _pdf_ca.apply(lambda r: safe_div(r["spend"], r["purchases"]), axis=1)
        _pdf_ca["ROAS"] = _pdf_ca.apply(lambda r: safe_div(r.get("revenue", 0), r["spend"]), axis=1)
        _pdf_ca["Hook Rate"] = _pdf_ca.apply(lambda r: safe_div(r.get("vv", 0), r["impressions"], 100), axis=1)
        _pdf_ca["Hold Rate"] = _pdf_ca.apply(lambda r: safe_div(r.get("thru", 0), r.get("vv", 1), 100), axis=1)
        _pdf_ca = _pdf_ca.sort_values("spend", ascending=False)

        # Insight box
        _n_cr = len(_pdf_ca)
        _top_name = _pdf_ca.iloc[0]["ad_name"][:50] if not _pdf_ca.empty else "-"
        _with_conv = _pdf_ca[_pdf_ca["purchases"] > 0]
        _best_cpa = brl(_with_conv.nsmallest(1, "CPA")["CPA"].values[0]) if not _with_conv.empty else "-"
        _best_name = _with_conv.nsmallest(1, "CPA")["ad_name"].values[0][:50] if not _with_conv.empty else "-"
        _n_fat = len(_pdf_ca[_pdf_ca["avg_freq"] >= 2.5])
        _fat_txt = f" | {_n_fat} criativos com freq \u2265 2,5" if _n_fat > 0 else ""
        _insight(
            f"{_n_cr} criativos ativos | Top performer: {_best_name} com CPA de {_best_cpa}\n"
            f"Maior spend: {_top_name} ({brl(_pdf_ca.iloc[0]['spend'])}){_fat_txt}"
        )

        # Video KPIs
        _section("Performance de V\u00eddeo", (168, 85, 247))
        _kpis([
            ("Video Views", fmt_int(total_vv), None),
            ("ThruPlay", fmt_int(total_thruplay), None),
            ("Hook Rate", fmt_pct(hook_rate), None),
            ("Hold Rate", fmt_pct(hold_rate), None),
        ])

        # Winner cards
        _winners = _with_conv.nsmallest(3, "CPA")
        if not _winners.empty:
            _section("Top Performers \u2014 Menor CPA", _GN)
            for i, (_, row) in enumerate(_winners.iterrows(), 1):
                _creative_card(row, rank=i, badge_color=_GN, badge_text="WINNER")

        # Loser cards
        _losers = _with_conv.nlargest(3, "CPA")
        if not _losers.empty and len(_with_conv) > 3:
            _section("Underperformers \u2014 Maior CPA", _RD)
            for i, (_, row) in enumerate(_losers.iterrows(), 1):
                _creative_card(row, rank=i, badge_color=_RD, badge_text="UNDERPERFORMER")

        # Fatigue cards
        _fatigued = _pdf_ca[_pdf_ca["avg_freq"] >= 2.5].sort_values("avg_freq", ascending=False).head(3)
        if not _fatigued.empty:
            _section("Criativos com Fadiga (Freq \u2265 2,5)", _RD)
            for _, row in _fatigued.iterrows():
                _creative_card(row, badge_color=(230, 81, 0), badge_text="FADIGA")

        # ── Galeria Completa de Criativos (grid 3 colunas) ──────────────
        _section(f"Galeria Completa de Criativos ({len(_pdf_ca)})", _BLUE)

        _gc_cols = 3
        _gc_cw = (pdf.w - 20 - (_gc_cols - 1) * 4) / _gc_cols
        _gc_h = 62  # height per card

        for gi in range(0, len(_pdf_ca), _gc_cols):
            if pdf.get_y() + _gc_h > pdf.h - 15:
                pdf.add_page()

            y0 = pdf.get_y()
            for gj in range(_gc_cols):
                gidx = gi + gj
                if gidx >= len(_pdf_ca):
                    break
                grow = _pdf_ca.iloc[gidx]
                gx = 10 + gj * (_gc_cw + 4)

                # Card background
                pdf.set_fill_color(*_SF)
                pdf.rect(gx, y0, _gc_cw, _gc_h - 2, "F")
                # Top accent bar per card
                pdf.set_fill_color(*_AC)
                pdf.rect(gx, y0, _gc_cw, 1.5, "F")

                # Thumbnail (top portion)
                _g_thumb_url = _get_thumb(grow) if callable(_get_thumb) else None
                _g_img = _dl_image(_g_thumb_url)
                _g_th = 24  # thumbnail height
                pdf.set_fill_color(42, 42, 58)
                pdf.rect(gx + 1, y0 + 2, _gc_cw - 2, _g_th, "F")
                if _g_img:
                    try:
                        _fit_img(_g_img, gx + 1, y0 + 2,
                                 _gc_cw - 2, _g_th)
                    except Exception:
                        pdf.set_xy(gx + 1, y0 + 12)
                        pdf.set_text_color(*_GR)
                        pdf.set_font("Inter", "I", 6)
                        pdf.cell(_gc_cw - 2, 4, "Sem preview", align="C")
                else:
                    pdf.set_xy(gx + 1, y0 + 12)
                    pdf.set_text_color(*_GR)
                    pdf.set_font("Inter", "I", 6)
                    pdf.cell(_gc_cw - 2, 4, "Sem preview", align="C")

                # Ad name
                _gy = y0 + _g_th + 3
                pdf.set_xy(gx + 2, _gy)
                pdf.set_text_color(*_WH)
                pdf.set_font("Inter", "B", 6.5)
                pdf.cell(_gc_cw - 4, 4,
                         str(grow.get("ad_name", "-"))[:40])

                # Headline
                _g_title = grow.get("title", "") or grow.get("name", "") or ""
                if _g_title and str(_g_title) != "nan":
                    pdf.set_xy(gx + 2, _gy + 4)
                    pdf.set_text_color(*_GR)
                    pdf.set_font("Inter", "I", 5.5)
                    pdf.cell(_gc_cw - 4, 3,
                             str(_g_title)[:50])

                # Metrics line 1: Spend · CTR · CPA
                _gm1y = _gy + 8.5
                pdf.set_xy(gx + 2, _gm1y)
                pdf.set_text_color(200, 200, 200)
                pdf.set_font("Inter", "", 5.5)
                pdf.cell(_gc_cw - 4, 3.5,
                         f"Spend: {brl(grow.get('spend', 0))}  |  "
                         f"CTR: {fmt_pct(grow.get('CTR', 0))}  |  "
                         f"CPA: {brl(grow.get('CPA', 0))}")

                # Metrics line 2: Conv · ROAS
                _g_purch = grow.get("purchases", 0)
                if _g_purch and float(_g_purch) > 0:
                    pdf.set_xy(gx + 2, _gm1y + 4)
                    pdf.cell(_gc_cw - 4, 3.5,
                             f"Conv: {fmt_int(_g_purch)}  |  "
                             f"ROAS: {fmt_dec(grow.get('ROAS', 0), suffix='x')}")

                # Fatigue badge
                _g_freq = float(grow.get("avg_freq", 0) or 0)
                if _g_freq >= 2.5:
                    pdf.set_xy(gx + 2, _gm1y + 8)
                    pdf.set_fill_color(230, 81, 0)
                    pdf.set_text_color(255, 255, 255)
                    pdf.set_font("Inter", "B", 5)
                    pdf.cell(22, 3.5,
                             f"Freq: {_g_freq:.1f}",
                             fill=True, align="C")

            pdf.set_y(y0 + _gc_h)
            pdf.set_text_color(*_WH)

        # ── Tabela Comparativa Completa ────────────────────────────────
        pdf.add_page()
        _section(f"Tabela Comparativa Completa ({len(_pdf_ca)} criativos)", _BLUE)

        _tc_cols_map = {
            "ad_name": "Criativo", "impressions": "Impr.", "clicks": "Cliques",
            "spend": "Spend", "reach": "Alcance", "purchases": "Conv.",
            "revenue": "Receita",
        }
        if "engagement" in _pdf_ca.columns:
            _tc_cols_map["engagement"] = "Engaj."
        if "vv" in _pdf_ca.columns:
            _tc_cols_map["vv"] = "V.Views"
        _tc_cols_map.update({
            "CTR": "CTR", "CPA": "CPA", "ROAS": "ROAS",
            "Hook Rate": "Hook", "Hold Rate": "Hold",
            "avg_freq": "Freq.",
        })
        _tc_available = [c for c in _tc_cols_map if c in _pdf_ca.columns]
        _tc_display = _pdf_ca[_tc_available].copy()
        _tc_display = _tc_display.rename(
            columns={k: v for k, v in _tc_cols_map.items() if k in _tc_available}
        )
        for c in ["Impr.", "Cliques", "Alcance", "Conv.", "Engaj.", "V.Views"]:
            if c in _tc_display.columns:
                _tc_display[c] = _tc_display[c].apply(fmt_int)
        for c in ["Spend", "Receita", "CPA"]:
            if c in _tc_display.columns:
                _tc_display[c] = _tc_display[c].apply(brl)
        for c in ["CTR", "Hook", "Hold"]:
            if c in _tc_display.columns:
                _tc_display[c] = _tc_display[c].apply(fmt_pct)
        if "ROAS" in _tc_display.columns:
            _tc_display["ROAS"] = _tc_display["ROAS"].apply(
                lambda v: fmt_dec(v, suffix="x"))
        if "Freq." in _tc_display.columns:
            _tc_display["Freq."] = _tc_display["Freq."].apply(
                lambda v: fmt_dec(v, 1))

        _tc_headers = list(_tc_display.columns)
        _tc_n = len(_tc_headers)
        # Width allocation: "Criativo" gets more space
        _tc_base = (pdf.w - 20) / max(_tc_n, 1)
        _tc_widths = []
        for h in _tc_headers:
            if h == "Criativo":
                _tc_widths.append(_tc_base * 1.8)
            elif h in ("Spend", "Receita", "CPA"):
                _tc_widths.append(_tc_base * 1.1)
            else:
                _tc_widths.append(_tc_base * 0.8)
        # Normalize to fit page
        _tc_total = sum(_tc_widths)
        _tc_widths = [w * (pdf.w - 20) / _tc_total for w in _tc_widths]

        _tc_rows = []
        for _, r in _tc_display.iterrows():
            _tc_rows.append([str(r[c])[:35] for c in _tc_headers])

        _tbl(_tc_headers, _tc_rows, _tc_widths)

        # Top criativos bar chart
        if pdf.get_y() > pdf.h - 60:
            pdf.add_page()
        _section("Top Criativos por Spend", (168, 85, 247))
        top10_cr = _pdf_ca.nlargest(10, "spend")
        fig = go.Figure()
        fig.add_trace(go.Bar(
            y=top10_cr["ad_name"].apply(lambda n: str(n)[:30]),
            x=top10_cr["spend"], name="Spend",
            orientation="h", marker_color="#FF8C00",
            text=top10_cr["spend"].apply(brl), textposition="auto",
        ))
        fig.add_trace(go.Bar(
            y=top10_cr["ad_name"].apply(lambda n: str(n)[:30]),
            x=top10_cr["purchases"], name="Convers\u00f5es",
            orientation="h", marker_color="#4FC3F7",
            text=top10_cr["purchases"].apply(fmt_int), textposition="auto",
        ))
        fig.update_layout(
            barmode="group", height=400,
            xaxis=dict(showgrid=False),
            yaxis=dict(autorange="reversed"),
            legend=dict(orientation="h", y=-0.1, xanchor="center", x=0.5),
        )
        _chart(fig, h=400)

    else:
        pdf.set_font("Inter", "I", 10)
        pdf.set_text_color(*_GR)
        pdf.cell(0, 10, "Sem dados de criativos para os filtros selecionados.",
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")

    # ══════════════════════════════════════════════════════════════════════
    #  PAGE — GA4 (se dispon\u00edvel)
    # ══════════════════════════════════════════════════════════════════════
    _pdf_ga4 = _get_ga4_traffic()
    if not _pdf_ga4.empty:
        pdf.add_page()
        _heading("Google Analytics 4", 14)

        _g_sess = _ga4_col_sum(_pdf_ga4, "sessions")
        _g_users = _ga4_col_sum(_pdf_ga4, "users")
        _g_new = _ga4_col_sum(_pdf_ga4, "newUsers")
        _g_pvs = _ga4_col_sum(_pdf_ga4, "screenPageViews")
        _g_bounce = _ga4_weighted_mean(_pdf_ga4, "bounceRate")
        _g_engage = _ga4_weighted_mean(_pdf_ga4, "engagementRate")
        _ga4_nsrc = _pdf_ga4["source"].nunique() if "source" in _pdf_ga4.columns else 0

        _insight(
            f"{fmt_int(_g_sess)} sess\u00f5es de {_ga4_nsrc} fontes | "
            f"Bounce Rate m\u00e9dio: {fmt_pct(_g_bounce)}"
        )

        _section("KPIs de Tr\u00e1fego \u2014 Google Analytics 4", _TL)
        _kpis([
            ("Sess\u00f5es", fmt_int(_g_sess), None),
            ("Usu\u00e1rios", fmt_int(_g_users), None),
            ("Novos Usu\u00e1rios", fmt_int(_g_new), None),
            ("Pageviews", fmt_int(_g_pvs), None),
            ("Bounce Rate", fmt_pct(_g_bounce), None),
            ("Engagement Rate", fmt_pct(_g_engage), None),
        ])

        # GA4 daily trend
        _pdf_ga4d = _get_ga4_daily()
        if not _pdf_ga4d.empty and "date" in _pdf_ga4d.columns:
            _section("Tend\u00eancia Di\u00e1ria \u2014 Sess\u00f5es & Engagement Rate", _TL)
            _gd = _pdf_ga4d.copy()
            _gd["_sessions"] = _ga4_col(_gd, "sessions")
            _gd["_engage"] = _ga4_col(_gd, "engagementRate")
            _gda = (
                _gd.groupby("date", as_index=False)
                .agg(_sessions=("_sessions", "sum"),
                     _engage_w=("_engage", "sum"),
                     _w=("_sessions", "sum"))
            )
            _gda["engagement"] = _gda.apply(
                lambda r: safe_div(r["_engage_w"], r["_w"]) if r["_w"] else 0, axis=1
            )
            _gda = _gda.sort_values("date")
            _gda["sessions_ma7"] = _gda["_sessions"].rolling(7, min_periods=1).mean()

            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=_gda["date"], y=_gda["_sessions"], name="Sess\u00f5es",
                line=dict(color="#26A69A", width=1), opacity=0.4,
                fill="tozeroy", fillcolor="rgba(38,166,154,0.07)",
            ))
            fig.add_trace(go.Scatter(
                x=_gda["date"], y=_gda["sessions_ma7"], name="Sess\u00f5es MA7",
                line=dict(color="#26A69A", width=3),
            ))
            fig.add_trace(go.Scatter(
                x=_gda["date"], y=_gda["engagement"], name="Engagement Rate %",
                yaxis="y2", line=dict(color="#42A5F5", width=3),
            ))
            fig.update_layout(
                height=350,
                yaxis=dict(title="Sess\u00f5es", showgrid=True, gridcolor="rgba(255,255,255,0.06)"),
                yaxis2=dict(title="Engagement Rate (%)", overlaying="y",
                            side="right", showgrid=False),
                xaxis=dict(showgrid=False),
                legend=dict(orientation="h", y=-0.15, xanchor="center", x=0.5),
            )
            _chart(fig)

    # ══════════════════════════════════════════════════════════════════════
    #  PAGE — Cross-Channel (se dispon\u00edvel)
    # ══════════════════════════════════════════════════════════════════════
    if not _pdf_ga4.empty:
        _pdf_paid = _is_paid_traffic(_pdf_ga4)
        _pdf_ga4_conv = _get_ga4_conv()
        _pdf_conv_paid = (
            _is_paid_traffic(_pdf_ga4_conv)
            if not _pdf_ga4_conv.empty else _pdf_ga4_conv
        )

        _p_sess = _ga4_col_sum(_pdf_paid, "sessions") if not _pdf_paid.empty else 0
        _p_conv = _ga4_col_sum(_pdf_conv_paid, "conversions") if not _pdf_conv_paid.empty else 0
        _p_rev = _ga4_col_sum(_pdf_conv_paid, "transactionRevenue") if not _pdf_conv_paid.empty else 0

        if _p_sess > 0 or _p_conv > 0:
            pdf.add_page()
            _heading("Cross-Channel \u2014 Meta + GA4", 14)

            _x_cps = safe_div(total_spend, _p_sess)
            _x_cpa = safe_div(total_spend, _p_conv)
            _x_roas = safe_div(_p_rev, total_spend)
            _x_pass = safe_div(_p_sess, total_clicks, 100)

            _insight(
                f"Taxa de passagem Meta\u2192GA4: {fmt_pct(_x_pass)} | "
                f"ROAS Meta: {fmt_dec(roas, suffix='x')} vs ROAS GA4: {fmt_dec(_x_roas, suffix='x')}\n"
                f"{fmt_int(_p_sess)} sess\u00f5es pagas | {fmt_int(_p_conv)} convers\u00f5es GA4 | "
                f"Receita GA4: {brl(_p_rev)}"
            )

            # Cross funnel
            _section("Funil Completo \u2014 Meta Ads \u2192 Google Analytics 4", _TL)
            _cross_f = [
                ("Impress\u00f5es (Meta)", total_imp),
                ("Cliques (Meta)", total_clicks),
                ("Sess\u00f5es (GA4)", _p_sess),
                ("Convers\u00f5es (GA4)", _p_conv),
                ("Receita (GA4)", _p_rev),
            ]
            _cf_labels = [f[0] for f in _cross_f]
            _cf_values = [f[1] for f in _cross_f]
            colors_c = ["#FF8C00", "#FF6B00", "#26A69A", "#00897B", "#004D40"]
            fig = go.Figure(go.Funnel(
                y=_cf_labels, x=_cf_values,
                textinfo="value+label",
                texttemplate="<b>%{label}</b><br>%{value:,.0f}",
                marker=dict(color=colors_c, line=dict(width=0)),
                connector=dict(line=dict(color="#1E1E2E", width=0)),
            ))
            fig.update_layout(height=350, showlegend=False)
            _chart(fig, w=230, h=350)

            _section("KPIs Cruzadas \u2014 Meta Ads + GA4", _TL)
            _kpis([
                ("Investimento Meta", brl(total_spend), None),
                ("Sess\u00f5es GA4 (paid)", fmt_int(_p_sess), None),
                ("Custo/Sess\u00e3o", brl(_x_cps), None),
                ("Convers\u00f5es GA4", fmt_int(_p_conv), None),
                ("CPA (GA4)", brl(_x_cpa), None),
                ("ROAS (GA4)", fmt_dec(_x_roas, suffix="x"), None),
            ])

    # ══════════════════════════════════════════════════════════════════════
    #  PAGE — Recomenda\u00e7\u00f5es
    # ══════════════════════════════════════════════════════════════════════
    pdf.add_page()
    _heading("Diagn\u00f3stico & Recomenda\u00e7\u00f5es", 14)

    _ca_for_recs_pdf = None
    if not df_ad.empty:
        _ca_for_recs_pdf = df_ad.groupby("ad_name", as_index=False).agg(
            spend=("spend", "sum"), purchases=("actions_purchase", "sum"))
    _recs_pdf = _generate_recommendations(roas, cpa, ctr, avg_freq, target_roas, target_cpa, _ca_for_recs_pdf)

    for _rtitle, _rdesc in _recs_pdf:
        _section(str(_rtitle), _GN)
        pdf.set_text_color(*_GR)
        pdf.set_font("Inter", "", 8)
        pdf.multi_cell(pdf.w - 30, 5, str(_rdesc))
        pdf.ln(2)

    # Cost of inaction
    if not df_ad.empty and "frequency" in df_ad.columns:
        _fat_ads_pdf = df_ad.groupby("ad_name", as_index=False).agg(
            avg_freq=("frequency", "mean"), spend=("spend", "sum"))
        _fat_pdf = _fat_ads_pdf[_fat_ads_pdf["avg_freq"] >= FATIGUE_THRESHOLD]
        if not _fat_pdf.empty:
            _waste = _fat_pdf["spend"].sum()
            _section("Custo de Ina\u00e7\u00e3o \u2014 Criativos com Fadiga", _RD)
            _insight(
                f"{len(_fat_pdf)} criativos com freq \u2265 {FATIGUE_THRESHOLD} "
                f"consumindo {brl(_waste)} ({fmt_pct(safe_div(_waste, total_spend, 100))} do or\u00e7amento)\n"
                f"Pausar/substituir pode liberar at\u00e9 {brl(_waste * 0.3)} para reinvestir"
            )

    return bytes(pdf.output())


# ── Sidebar: PDF button ──────────────────────────────────────────────────────
with st.sidebar:
    st.markdown('<div style="border-top:1px solid rgba(255,255,255,0.06);margin:8px 0 12px"></div>', unsafe_allow_html=True)
    st.markdown('<p style="font-size:.85rem;font-weight:700;color:#FAFAFA;margin:0 0 8px;letter-spacing:-0.01em">📄 Exportar</p>', unsafe_allow_html=True)
    if st.button("Gerar Relatorio PDF", use_container_width=True):
        with st.spinner("Gerando relatorio PDF..."):
            try:
                st.session_state["_pdf_bytes"] = _generate_pdf()
                st.session_state["_pdf_name"] = f"relatorio_{date_from}_{date_to}.pdf"
            except Exception as e:
                st.error(f"Erro ao gerar PDF: {e}")
                st.session_state["_pdf_bytes"] = None

    if st.session_state.get("_pdf_bytes"):
        st.download_button(
            "Baixar PDF",
            st.session_state["_pdf_bytes"],
            st.session_state.get("_pdf_name", "relatorio.pdf"),
            "application/pdf",
            key="dl_pdf",
        )
