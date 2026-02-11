import os
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

from windsor_api import WindsorClient

load_dotenv()

st.set_page_config(
    page_title="Relatório Meta Ads — Análise Profunda",
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


PLOTLY_TRANSPARENT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color="white"),
)

# ═══════════════════════════════════════════════════════════════════════════════
#  CSS
# ═══════════════════════════════════════════════════════════════════════════════
st.markdown("""
<style>
    .sh{background:linear-gradient(135deg,#FF6B00,#FF8C00);color:#fff;padding:10px 20px;
        border-radius:8px;font-size:1.05rem;font-weight:700;margin:24px 0 14px;text-align:center}
    .sh-blue{background:linear-gradient(135deg,#0288D1,#4FC3F7);color:#fff;padding:10px 20px;
        border-radius:8px;font-size:1.05rem;font-weight:700;margin:24px 0 14px;text-align:center}
    .sh-green{background:linear-gradient(135deg,#2E7D32,#66BB6A);color:#fff;padding:10px 20px;
        border-radius:8px;font-size:1.05rem;font-weight:700;margin:24px 0 14px;text-align:center}
    .sh-purple{background:linear-gradient(135deg,#6A1B9A,#AB47BC);color:#fff;padding:10px 20px;
        border-radius:8px;font-size:1.05rem;font-weight:700;margin:24px 0 14px;text-align:center}
    .sh-red{background:linear-gradient(135deg,#C62828,#EF5350);color:#fff;padding:10px 20px;
        border-radius:8px;font-size:1.05rem;font-weight:700;margin:24px 0 14px;text-align:center}
    .main-title{background:#1E1E2E;border:1px solid #333;border-radius:8px;
        padding:14px 20px;text-align:center;margin-bottom:20px}
    .main-title h1{font-size:1.45rem;font-weight:700;color:#FAFAFA;margin:0}
    .main-title .ic{color:#0095F6;margin-right:8px}
    [data-testid="stMetricValue"]{font-size:1.8rem!important;font-weight:700!important}
    [data-testid="stMetricLabel"]{font-size:.82rem!important;color:#AAA!important}
    .block-container{padding-top:1.2rem}
    .alert-box{background:#C62828;color:#fff;padding:12px 16px;border-radius:8px;
        margin:8px 0;font-weight:600}
    .alert-box-warn{background:#E65100;color:#fff;padding:12px 16px;border-radius:8px;
        margin:8px 0;font-weight:600}
</style>
""", unsafe_allow_html=True)

H = lambda text, cls="sh": f'<div class="{cls}">{text}</div>'

st.markdown(
    '<div class="main-title"><h1><span class="ic">&#9679;</span>'
    'RELATÓRIO DE CAMPANHAS DE ANÚNCIOS - META</h1></div>',
    unsafe_allow_html=True,
)

# ═══════════════════════════════════════════════════════════════════════════════
#  SIDEBAR — FILTERS
# ═══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.header("Configurações")
    api_key = st.text_input(
        "Windsor.ai API Key",
        value=os.getenv("WINDSOR_API_KEY", ""),
        type="password",
    )
    date_from = st.date_input("Data inicial", value=date.today() - timedelta(days=30))
    date_to = st.date_input("Data final", value=date.today())

if not api_key:
    st.info("Insira sua API Key na barra lateral para começar.")
    st.stop()


@st.cache_data(ttl=600, show_spinner="Carregando contas…")
def load_accounts(key, dfrom, dto):
    return WindsorClient(key).get_accounts(dfrom, dto, progress_cb=None)


try:
    accounts = load_accounts(api_key, str(date_from), str(date_to))
except Exception as exc:
    st.error(f"Erro ao buscar contas: {exc}")
    st.stop()

with st.sidebar:
    sel_account = st.selectbox("Conta de Anúncios", ["Todas as contas"] + accounts)
    st.markdown("---")
    obj_mode = st.radio(
        "Tipo de Campanha",
        ["Todas", "Conversão (Vendas)", "Topo de Funil (Alcance/Engajamento)"],
        help="Filtra campanhas pelo objetivo e adapta métricas.",
    )
    st.markdown("---")
    fetch = st.button("🔄 Atualizar dados", use_container_width=True)

acct = None if sel_account == "Todas as contas" else sel_account


# ═══════════════════════════════════════════════════════════════════════════════
#  DATA LOADING  — core data uses monthly aggregation (fast single requests)
#  Secondary tabs (demo/placement/region) load lazily on demand.
# ═══════════════════════════════════════════════════════════════════════════════
_cache_key = f"{api_key}|{date_from}|{date_to}|{acct}"
need_load = fetch or st.session_state.get("_cache_key") != _cache_key

if need_load:
    try:
        c = WindsorClient(api_key)
        dfrom, dto = str(date_from), str(date_to)

        progress = st.progress(0, text="Carregando dados principais…")

        progress.progress(0.15, text="Carregando campanhas (agregado mensal)…")
        camp = c.get_campaign_data(dfrom, dto, acct)

        progress.progress(0.40, text="Carregando conjuntos de anúncios…")
        adset = c.get_adset_data(dfrom, dto, acct)

        progress.progress(0.65, text="Carregando anúncios / criativos…")
        ad = c.get_ad_data(dfrom, dto, acct)

        progress.progress(1.0, text="Dados carregados!")
        progress.empty()

        st.session_state.update(
            camp=camp, adset=adset, ad=ad,
            _cache_key=_cache_key,
            # Clear lazy caches so they reload on next access
            _demo=None, _placement=None, _region=None,
            _daily_camp=None, _daily_ad=None,
        )
    except Exception as exc:
        st.error(f"Erro ao buscar dados: {exc}")
        st.stop()

if "_cache_key" not in st.session_state:
    st.info("Clique em 'Atualizar dados' para carregar.")
    st.stop()


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

# ── Campaign filter ──────────────────────────────────────────────────────────
campaigns = (
    ["Todas"] + sorted(df_camp["campaign"].dropna().unique().tolist())
    if not df_camp.empty and "campaign" in df_camp.columns else ["Todas"]
)
with st.sidebar:
    sel_campaign = st.selectbox("Campanha", campaigns)

if sel_campaign != "Todas":
    df_camp = df_camp[df_camp["campaign"] == sel_campaign] if not df_camp.empty else df_camp
    df_adset = df_adset[df_adset["campaign"] == sel_campaign] if not df_adset.empty else df_adset
    df_ad = df_ad[df_ad["campaign"] == sel_campaign] if not df_ad.empty else df_ad

# ── Keyword search filter ────────────────────────────────────────────────────
with st.sidebar:
    keyword = st.text_input(
        "Buscar por palavra-chave",
        placeholder="Ex: remarketing, vídeo, promo…",
        help="Filtra por nome de Campanha, Conjunto de Anúncios ou Criativo.",
    )

matched_campaigns = set()
if keyword:
    kw = keyword.strip().lower()

    def _kw_match(df, cols):
        """Return rows where any of `cols` contains the keyword (case-insensitive)."""
        mask = pd.Series(False, index=df.index)
        for c in cols:
            if c in df.columns:
                mask = mask | df[c].astype(str).str.lower().str.contains(kw, na=False)
        return df[mask]

    # Find matching campaign names across all levels
    matched_campaigns = set()
    for _df, _cols in [
        (df_camp, ["campaign"]),
        (df_adset, ["campaign", "adset_name"]),
        (df_ad, ["campaign", "adset_name", "ad_name"]),
    ]:
        if not _df.empty:
            hits = _kw_match(_df, _cols)
            if "campaign" in hits.columns:
                matched_campaigns.update(hits["campaign"].dropna().unique())

    # Filter: keep full campaign if keyword matches at ANY level
    if matched_campaigns:
        df_camp = df_camp[df_camp["campaign"].isin(matched_campaigns)] if not df_camp.empty else df_camp
        df_adset = df_adset[df_adset["campaign"].isin(matched_campaigns)] if not df_adset.empty else df_adset
        df_ad = df_ad[df_ad["campaign"].isin(matched_campaigns)] if not df_ad.empty else df_ad
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
    """Apply objective, campaign, and keyword filters to a lazy-loaded df."""
    df = _classify(df)
    if obj_mode != "Todas":
        df = df[df["_cat"] == obj_mode]
    if sel_campaign != "Todas" and "campaign" in df.columns:
        df = df[df["campaign"] == sel_campaign]
    if keyword and "campaign" in df.columns:
        df = df[df["campaign"].isin(matched_campaigns)] if matched_campaigns else df.iloc[0:0]
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
    if keyword and "ad_name" in df.columns and matched_campaigns:
        # For daily ad, filter by ad names that belong to matched campaigns
        matched_ads = df_ad["ad_name"].unique() if not df_ad.empty else []
        df = df[df["ad_name"].isin(matched_ads)] if len(matched_ads) > 0 else df
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
#  TABS
# ═══════════════════════════════════════════════════════════════════════════════
tab_overview, tab_funnel, tab_creative, tab_audience, tab_diagnostic = st.tabs([
    "📊 Visão Geral",
    "🔻 Funil de Conversão",
    "🎨 Criativos",
    "👥 Audiência & Placement",
    "🩺 Diagnóstico & Otimização",
])

# ─────────────────────────────────────────────────────────────────────────────
#  TAB 1 — VISÃO GERAL
# ─────────────────────────────────────────────────────────────────────────────
with tab_overview:

    if is_conv or obj_mode == "Todas":
        st.markdown(H("Taxa de Cliques e Impressões"), unsafe_allow_html=True)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Impressões", fmt_int(total_imp))
        c2.metric("Cliques", fmt_int(total_clicks))
        c3.metric("CTR", fmt_pct(ctr))
        c4.metric("CPC", brl(cpc))

        st.markdown(H("Investimentos & Conversão"), unsafe_allow_html=True)
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("Valor Gasto", brl(total_spend))
        c2.metric("Conversões", fmt_int(total_purch))
        c3.metric("CPA", brl(cpa))
        c4.metric("Receita", brl(total_rev))
        c5.metric("ROAS", fmt_dec(roas, suffix="x"))
        c6.metric("Ticket Médio", brl(ticket_medio))

    if is_tofu:
        st.markdown(H("Alcance e Impressões", "sh-blue"), unsafe_allow_html=True)
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Impressões", fmt_int(total_imp))
        c2.metric("Alcance", fmt_int(total_reach))
        c3.metric("Frequência", fmt_dec(avg_freq))
        c4.metric("CPM", brl(cpm))
        c5.metric("CPR (custo/1k alcance)", brl(cpr))

        st.markdown(H("Engajamento e Tráfego", "sh-blue"), unsafe_allow_html=True)
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Cliques", fmt_int(total_clicks))
        c2.metric("CTR", fmt_pct(ctr))
        c3.metric("CPC", brl(cpc))
        c4.metric("Engajamento Total", fmt_int(total_engagement))
        c5.metric("Custo/Engajamento", brl(cost_per_eng))

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
        ))
        fig.add_trace(go.Scatter(
            x=daily["date"], y=daily["spend_ma7"], name="Spend MA7",
            line=dict(color="#FF8C00", width=3),
        ))
        fig.add_trace(go.Scatter(
            x=daily["date"], y=daily["ctr_ma7"], name="CTR MA7 (%)",
            yaxis="y2", line=dict(color="#4FC3F7", width=3),
        ))
        fig.update_layout(
            **PLOTLY_TRANSPARENT, height=350,
            margin=dict(l=10, r=10, t=10, b=10),
            yaxis=dict(title="Spend (R$)", showgrid=True, gridcolor="#333"),
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
    ov = ov.rename(columns={
        "campaign": "Campanha", "impressions": "Impressões", "clicks": "Cliques",
        "spend": "Valor Gasto", "reach": "Alcance", "purchases": "Conversões",
        "revenue": "Receita", "engagement": "Engajamento",
    })
    for c in ["Impressões", "Cliques", "Alcance", "Conversões", "Engajamento"]:
        if c in ov.columns:
            ov[c] = ov[c].apply(fmt_int)
    for c in ["Valor Gasto", "Receita", "CPA", "CPM"]:
        if c in ov.columns:
            ov[c] = ov[c].apply(brl)
    if "CTR" in ov.columns:
        ov["CTR"] = ov["CTR"].apply(fmt_pct)
    if "ROAS" in ov.columns:
        ov["ROAS"] = ov["ROAS"].apply(lambda v: fmt_dec(v, suffix="x"))
    st.dataframe(ov, use_container_width=True, hide_index=True)

    # ── Pie meses + Desempenho mensal (uses monthly-aggregated camp data) ─
    col_pie, col_monthly = st.columns([2, 3])
    with col_pie:
        st.markdown(H("Meses com Maior Investimento"), unsafe_allow_html=True)
        if "date" in df_camp.columns:
            ds = df_camp.groupby("date", as_index=False).agg(spend=("spend", "sum")).sort_values("spend", ascending=False)
            ds["label"] = ds["date"].dt.strftime("%m/%Y")
            top = ds.head(9)
            rest = ds.iloc[9:]
            if not rest.empty:
                top = pd.concat([top, pd.DataFrame([{"label": "Outros", "spend": rest["spend"].sum()}])], ignore_index=True)
            fig = px.pie(top, values="spend", names="label", hole=0.35, color_discrete_sequence=px.colors.qualitative.Dark24)
            fig.update_layout(**PLOTLY_TRANSPARENT, height=350, margin=dict(l=10, r=10, t=10, b=10))
            fig.update_traces(textposition="inside", textinfo="percent")
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

    st.markdown(H("Funil Completo de Conversão", "sh-green"), unsafe_allow_html=True)

    funnel_data = [
        ("Impressões", total_imp),
        ("Cliques no Link", total_link_clicks if total_link_clicks else total_clicks),
        ("Visualização de Página", total_lpv),
        ("Add to Cart", total_atc),
        ("Initiate Checkout", total_ic),
        ("Compra", total_purch),
    ]
    funnel_labels = [f[0] for f in funnel_data]
    funnel_values = [f[1] for f in funnel_data]

    col_f, col_rates = st.columns([3, 2])

    with col_f:
        colors = ["#4FC3F7", "#29B6F6", "#0288D1", "#0277BD", "#01579B", "#002F6C"]
        fig = go.Figure(go.Funnel(
            y=funnel_labels, x=funnel_values,
            textinfo="value+label",
            texttemplate="<b>%{label}</b><br>%{value:,.0f}",
            marker=dict(color=colors, line=dict(width=0)),
            connector=dict(line=dict(color="#1E1E2E", width=0)),
        ))
        fig.update_layout(**PLOTLY_TRANSPARENT, height=450,
                          margin=dict(l=20, r=20, t=10, b=10), showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

    with col_rates:
        st.markdown(H("Taxas de Conversão entre Etapas", "sh-green"), unsafe_allow_html=True)
        for i in range(1, len(funnel_data)):
            prev_label, prev_val = funnel_data[i - 1]
            curr_label, curr_val = funnel_data[i]
            rate = safe_div(curr_val, prev_val, 100)
            drop = 100 - rate
            st.metric(
                f"{prev_label} → {curr_label}",
                f"{rate:.1f}%",
                delta=f"-{drop:.1f}% drop" if drop > 0 else "0%",
                delta_color="inverse",
            )

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
            "link_clicks": "Cliques Link", "lpv": "LPV", "atc": "Add to Cart",
            "purchases": "Compras", "spend": "Spend",
        })
        for c in ["Impressões", "Cliques Link", "LPV", "Add to Cart", "Compras"]:
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
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Spend", brl(row.get('spend', 0)))
        m2.metric("Impressões", fmt_int(row.get('impressions', 0)))
        m3.metric("Cliques", fmt_int(row.get('clicks', 0)))
        m4.metric("CTR", fmt_pct(row.get('CTR', 0)))

        m5, m6, m7, m8 = st.columns(4)
        m5.metric("Conversões", fmt_int(row.get('purchases', 0)))
        m6.metric("CPA", brl(row.get('CPA', 0)))
        m7.metric("ROAS", fmt_dec(row.get('ROAS', 0), suffix="x"))
        m8.metric("Engajamento", fmt_int(row.get('engagement', 0)))

        if row.get("Hook Rate", 0) > 0 or row.get("Hold Rate", 0) > 0:
            m9, m10, m11, m12 = st.columns(4)
            m9.metric("Hook Rate", fmt_pct(row.get('Hook Rate', 0)))
            m10.metric("Hold Rate", fmt_pct(row.get('Hold Rate', 0)))
            m11.metric("Video Views", fmt_int(row.get('vv', 0)))
            m12.metric("Frequência", fmt_dec(row.get('avg_freq', 0), 1))

    st.markdown("---")


with tab_creative:

    if df_ad.empty:
        st.warning("Sem dados de criativos.")
    else:
        # ── Build creative aggregate with asset info ─────────────────────
        # Keep first asset URL and text per ad_name
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

        # ── KPIs de Vídeo ────────────────────────────────────────────────
        st.markdown(H("Performance de Vídeo", "sh-purple"), unsafe_allow_html=True)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Video Views", fmt_int(total_vv))
        c2.metric("ThruPlay", fmt_int(total_thruplay))
        c3.metric("Hook Rate (views/imp)", fmt_pct(hook_rate))
        c4.metric("Hold Rate (thru/views)", fmt_pct(hold_rate))

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
        fatigued = ca[ca["avg_freq"] >= 3].sort_values("avg_freq", ascending=False).head(3)
        if not fatigued.empty:
            st.markdown(H("🔥 Criativos com Fadiga (Frequência ≥ 3)", "sh-red"), unsafe_allow_html=True)
            for _, row in fatigued.iterrows():
                _render_creative_card(row, badge="#E65100")

        # ── Full gallery ─────────────────────────────────────────────────
        st.markdown(H("Galeria Completa de Criativos", "sh-purple"), unsafe_allow_html=True)

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
                    if row.get("avg_freq", 0) >= 3:
                        st.markdown(
                            f'<span style="background:#E65100;color:#fff;padding:2px 8px;'
                            f'border-radius:10px;font-size:.75rem">Freq: {row["avg_freq"]:.1f}</span>',
                            unsafe_allow_html=True,
                        )

        # ── Comparison table ─────────────────────────────────────────────
        st.markdown(H("Tabela Comparativa Completa", "sh-purple"), unsafe_allow_html=True)
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
                **PLOTLY_TRANSPARENT, barmode="group", height=400,
                margin=dict(l=10, r=10, t=10, b=10),
                xaxis=dict(type="log", showgrid=False),
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
                    **PLOTLY_TRANSPARENT, height=400,
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
                    fig.update_layout(**PLOTLY_TRANSPARENT, height=350,
                                      margin=dict(l=10, r=10, t=40, b=10))
                    st.plotly_chart(fig, use_container_width=True)
                with col_freq:
                    fig = px.line(fat_daily, x="date", y="frequency", color="ad_name",
                                  title="Frequência por Criativo ao Longo do Tempo")
                    fig.update_layout(**PLOTLY_TRANSPARENT, height=350,
                                      margin=dict(l=10, r=10, t=40, b=10))
                    st.plotly_chart(fig, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
#  TAB 4 — AUDIÊNCIA & PLACEMENT
# ─────────────────────────────────────────────────────────────────────────────
with tab_audience:

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
            fig.update_layout(**PLOTLY_TRANSPARENT, height=350, margin=dict(l=10, r=10, t=10, b=10),
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
            fig.update_layout(**PLOTLY_TRANSPARENT, height=350, margin=dict(l=10, r=10, t=10, b=10),
                              showlegend=False)
            st.plotly_chart(fig, use_container_width=True)

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
            fig.update_layout(**PLOTLY_TRANSPARENT, height=350, margin=dict(l=10, r=10, t=10, b=10),
                              xaxis_tickangle=-45, showlegend=False)
            st.plotly_chart(fig, use_container_width=True)

        with col_pl2:
            fig = px.bar(pl.nlargest(10, "spend"), x="placement", y="CPA",
                         color="CPA", color_continuous_scale=["#66BB6A", "#EF5350"],
                         text=pl.nlargest(10, "spend")["CPA"].apply(brl))
            fig.update_layout(**PLOTLY_TRANSPARENT, height=350, margin=dict(l=10, r=10, t=10, b=10),
                              xaxis_tickangle=-45, showlegend=False)
            st.plotly_chart(fig, use_container_width=True)

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
        fig.update_layout(**PLOTLY_TRANSPARENT, height=400, margin=dict(l=10, r=10, t=10, b=10),
                          xaxis_tickangle=-45)
        st.plotly_chart(fig, use_container_width=True)
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


# ─────────────────────────────────────────────────────────────────────────────
#  TAB 5 — DIAGNÓSTICO & OTIMIZAÇÃO
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
        high_freq = ad_fatigue[ad_fatigue["avg_freq"] >= 3].sort_values("avg_freq", ascending=False)

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
            st.success("Nenhum criativo com frequência alta (>3). Sem fadiga detectada.")
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
            **PLOTLY_TRANSPARENT, height=450,
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
                                     name="CPA", line=dict(color="#EF5350", width=1), opacity=0.3))
            fig.add_trace(go.Scatter(x=eff_daily["date"], y=eff_daily["CPA_ma7"],
                                     name="CPA MA7", line=dict(color="#EF5350", width=3)))
            fig.update_layout(**PLOTLY_TRANSPARENT, height=300,
                              margin=dict(l=10, r=10, t=30, b=10),
                              title="CPA Diário (MA7)", yaxis=dict(title="CPA (R$)"))
            st.plotly_chart(fig, use_container_width=True)

        with col2:
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=eff_daily["date"], y=eff_daily["ROAS"],
                                     name="ROAS", line=dict(color="#66BB6A", width=1), opacity=0.3))
            fig.add_trace(go.Scatter(x=eff_daily["date"], y=eff_daily["ROAS_ma7"],
                                     name="ROAS MA7", line=dict(color="#66BB6A", width=3)))
            fig.update_layout(**PLOTLY_TRANSPARENT, height=300,
                              margin=dict(l=10, r=10, t=30, b=10),
                              title="ROAS Diário (MA7)", yaxis=dict(title="ROAS"))
            st.plotly_chart(fig, use_container_width=True)
