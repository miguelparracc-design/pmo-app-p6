import streamlit as st
import pandas as pd
import plotly.express as px

st.set_page_config(
    page_title="PMO Control - Miguel Parra",
    page_icon="Logo.png",
    layout="wide"
)

from PIL import Image

logo = Image.open("Logo.png")

col1, col2 = st.columns([1,4])
with col1:
    st.image(logo, width=90)
with col2:
    st.markdown("## PMO Control")
    st.caption("Miguel Parra · Versión de prueba")

# =========================
# CONFIG / HOMOLOGACIÓN
# =========================
DISC_MAP = {
    "ING": "Ingeniería",
    "ADQ": "Adquisiciones",
    "SUB_MT": "Subcontratos MT",
    "IFF": "Instalaciones de Faena",
    "MT": "Movimiento de Tierra",
    "00 CC": "Obras Civiles",
    "EST": "Estructuras",
    "MEC": "Mecánico",
    "CAÑE": "Piping",
    "ELE&INT": "Eléctrico e Instrumentación",
    "COM": "Precomisionamiento",
    "PER": "Permisos",
}

AREA_MAP = {
    "IIF": "IIF",
    "ETAI": "Etapa I",
    "ETAI I": "Etapa II",
    "CHANC": "Chancador Primario Esperanza Sur",
    "TRANS": "Transporte de Mineral",
    "SUM": "Suministros",
    "PERM": "Gestión Permiso",
    "Servi": "Infraestructura y Servicios",
    "LOOP": "Sistema Loop Eléctrico Mina",
    "Camin": "Caminos Interiores",
    "G.SUB": "Gestión Subcontratos",
}

# ✅ Tu selección WBS (solo estos aparecen en el filtro)
WBS_SELECTION = [
    "406.AM.EPC.A.73-2",
    "406.AM.EPC.A.73-2.4",
    "406.AM.EPC.A.73-2.2",
    "406.AM.EPC.A.73-2.10",
    "406.AM.EPC.A.73-2.8",
    "406.AM.EPC.A.73-2.3",
    "406.AM.EPC.A.73-2.6",
    "406.AM.EPC.A.73-2.9",
    "406.AM.EPC.A.73-2.5",   # Construcción
    "406.AM.EPC.A.73-2.13",
    "406.AM.EPC.A.73-2.7",
]
CONSTRUCTION_WBS_NAME = "Construcción"

# =========================
# HELPERS
# =========================
def to_dt(x):
    return pd.to_datetime(x, errors="coerce")

def safe_num(x):
    return pd.to_numeric(x, errors="coerce")

def safe_bool(x):
    if isinstance(x, bool):
        return x
    if pd.isna(x):
        return False
    s = str(x).strip().lower()
    return s in ["true", "t", "y", "yes", "1"]

def code_suffix(x):
    if pd.isna(x):
        return None
    s = str(x).strip()
    if "_" in s:
        return s.split("_", 1)[1].strip()
    return s

def map_or_raw(code, mapping):
    if code is None:
        return None
    return mapping.get(code, code)

def semaforo_spi(x):
    if x is None or pd.isna(x): return ("⚪", "N/D")
    if x >= 1.00: return ("🟢", "En control")
    if x >= 0.95: return ("🟡", "Atención")
    return ("🔴", "At Risk")

def spread_units_daily(df_in, qty_col):
    rows = df_in.dropna(subset=["ASG_START", "ASG_FINISH", qty_col]).copy()
    rows = rows[rows["ASG_FINISH"] >= rows["ASG_START"]]
    if rows.empty:
        return pd.Series(dtype=float)

    daily = {}
    for _, r in rows.iterrows():
        start = r["ASG_START"].normalize()
        finish = r["ASG_FINISH"].normalize()
        qty = float(r[qty_col]) if pd.notna(r[qty_col]) else 0.0
        days = (finish - start).days + 1
        if days <= 0 or qty == 0:
            continue
        per_day = qty / days
        d = start
        for _i in range(days):
            daily[d] = daily.get(d, 0.0) + per_day
            d += pd.Timedelta(days=1)

    return pd.Series(daily).sort_index()

def spread_units_cum(df_in, qty_col):
    s = spread_units_daily(df_in, qty_col)
    return s.cumsum() if not s.empty else s

def staffing_divisor(mode):
    return 360.0 if mode == "Mensual" else 77.0

def round_staffing(x):
    if pd.isna(x):
        return 0
    return int(round(float(x), 0))

def find_col(df, candidates):
    cols = {str(c).strip().lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in cols:
            return cols[cand.lower()]
    for lc, orig in cols.items():
        for cand in candidates:
            if cand.lower() in lc:
                return orig
    return None

def clamp_pct(x):
    if pd.isna(x):
        return 0.0
    return max(0.0, min(100.0, float(x)))

def is_milestone(task_type_value, row):
    # Si existe task type, úsalo
    if task_type_value is not None and pd.notna(task_type_value):
        return "milestone" in str(task_type_value).lower()

    # Heurística si no existe task type: baseline/target de un día y/o rem duration = 0
    ts, te = row.get("PLAN_START"), row.get("PLAN_FINISH")
    bs, be = row.get("BL_START"), row.get("BL_FINISH")
    rem = row.get("REMAIN_HR")
    same_target = pd.notna(ts) and pd.notna(te) and ts.normalize() == te.normalize()
    same_base = pd.notna(bs) and pd.notna(be) and bs.normalize() == be.normalize()
    rem0 = pd.notna(rem) and float(rem) == 0.0
    return rem0 and (same_target or same_base)

# =========================
# UI
# =========================
st.markdown("## PMO Dashboard — Primavera P6 (HH)")
project_name = st.text_input("Nombre del Proyecto", value="Proyecto - Control PMO (HH)")

uploaded = st.file_uploader("Sube tu export de P6 (XLSX)", type=["xlsx"])
if uploaded is None:
    st.stop()

xls = pd.ExcelFile(uploaded)
sheets = [s.upper() for s in xls.sheet_names]
if "TASK" not in sheets or "TASKRSRC" not in sheets:
    st.error(f"Faltan hojas TASK o TASKRSRC. Detecté: {xls.sheet_names}")
    st.stop()

task = pd.read_excel(uploaded, sheet_name=xls.sheet_names[sheets.index("TASK")])
rsrc = pd.read_excel(uploaded, sheet_name=xls.sheet_names[sheets.index("TASKRSRC")])

# =========================
# Detect TASK columns (flexible)
# =========================
COL_TASK_ID = find_col(task, ["task_code", "activity id", "activity_id"])
COL_TASK_NAME = find_col(task, ["task_name", "activity name", "activity_name"])
COL_STATUS = find_col(task, ["status_code", "status"])
COL_CRIT = find_col(task, ["critical_flag", "critical"])
COL_FLOAT = find_col(task, ["total_float_hr_cnt", "total float", "float"])
COL_REMAIN = find_col(task, ["remain_drtn_hr_cnt", "remaining duration", "remain"])

COL_PLAN_START = find_col(task, ["target_start_date", "planned start", "plan start"])
COL_PLAN_FINISH = find_col(task, ["target_end_date", "planned finish", "plan finish"])
COL_ACT_START = find_col(task, ["act_start_date", "actual start", "act start"])
COL_ACT_FINISH = find_col(task, ["act_end_date", "actual finish", "act finish"])
COL_BL_START = find_col(task, ["primary_base_start_date", "baseline start", "bl start"])
COL_BL_FINISH = find_col(task, ["primary_base_end_date", "baseline finish", "bl finish"])

COL_WBS_ID = find_col(task, ["wbs_id", "wbs id"])
COL_WBS_CODE = find_col(task, ["wbs_code", "wbs code"])
COL_WBS_NAME = find_col(task, ["wbs_name", "wbs name"])

COL_TASK_TYPE = find_col(task, ["task_type", "task type", "Task_type", "Task Type", "activity type", "type"])

COL_DISC_RAW = find_col(task, ["actv_code__disciplinas", "disciplinas"])
COL_AREA_RAW = find_col(task, ["actv_code__areas", "areas"])

need = [COL_TASK_ID, COL_PLAN_FINISH, COL_WBS_ID]
if any(c is None for c in need):
    st.error("Tu export no trae columnas mínimas (task_code / target_end_date / wbs_id). Revisa el template.")
    st.stop()

# =========================
# Normalize TASK
# =========================
task = task.copy()
task["ACT_ID"] = task[COL_TASK_ID]
task["ACT_NAME"] = task[COL_TASK_NAME] if COL_TASK_NAME else None
task["STATUS"] = task[COL_STATUS] if COL_STATUS else None
task["CRITICAL_FLAG"] = task[COL_CRIT].apply(safe_bool) if COL_CRIT else False
task["FLOAT_HR"] = safe_num(task[COL_FLOAT]) if COL_FLOAT else pd.Series([pd.NA]*len(task))
task["REMAIN_HR"] = safe_num(task[COL_REMAIN]) if COL_REMAIN else pd.Series([pd.NA]*len(task))

task["PLAN_START"] = to_dt(task[COL_PLAN_START]) if COL_PLAN_START else pd.NaT
task["PLAN_FINISH"] = to_dt(task[COL_PLAN_FINISH])
task["ACT_START"] = to_dt(task[COL_ACT_START]) if COL_ACT_START else pd.NaT
task["ACT_FINISH"] = to_dt(task[COL_ACT_FINISH]) if COL_ACT_FINISH else pd.NaT
task["BL_START"] = to_dt(task[COL_BL_START]) if COL_BL_START else pd.NaT
task["BL_FINISH"] = to_dt(task[COL_BL_FINISH]) if COL_BL_FINISH else pd.NaT

task["WBS_ID"] = task[COL_WBS_ID].astype(str)

# Si ya agregaste WBS Code/Name, los usamos; si no, fallback a WBS_ID
task["WBS_CODE"] = task[COL_WBS_CODE].astype(str) if COL_WBS_CODE else task["WBS_ID"]
task["WBS_NAME"] = task[COL_WBS_NAME].astype(str) if COL_WBS_NAME else task["WBS_ID"]
task["WBS_LABEL"] = task["WBS_CODE"] + " | " + task["WBS_NAME"]

# Disciplina / Área
if COL_DISC_RAW:
    task["DISC_CODE"] = task[COL_DISC_RAW].apply(code_suffix)
    task["DISCIPLINA"] = task["DISC_CODE"].apply(lambda x: map_or_raw(x, DISC_MAP))
else:
    task["DISCIPLINA"] = None

if COL_AREA_RAW:
    task["AREA_CODE"] = task[COL_AREA_RAW].apply(code_suffix)
    task["AREA"] = task["AREA_CODE"].apply(lambda x: map_or_raw(x, AREA_MAP))
else:
    task["AREA"] = None

task["TASK_TYPE"] = task[COL_TASK_TYPE] if COL_TASK_TYPE else None

# ✅ CRÍTICAS según P6/DCMA:
# - critical_flag OR total float <= 0
task["IS_CRITICAL"] = task["CRITICAL_FLAG"] | (task["FLOAT_HR"].fillna(999999) <= 0)

# =========================
# Normalize TASKRSRC
# =========================
COL_RSRC_TASK_ID = find_col(rsrc, ["task_id", "activity id"])
COL_ASG_START = find_col(rsrc, ["start_date", "start date"])
COL_ASG_FINISH = find_col(rsrc, ["end_date", "finish date", "end date"])
COL_HH_PLAN = find_col(rsrc, ["target_qty", "target qty"])
COL_HH_ACT = find_col(rsrc, ["act_qty", "actual qty", "act qty"])
COL_EFF = find_col(rsrc, ["effort_complete_pct", "effort % complete", "units % complete"])

need_r = [COL_RSRC_TASK_ID, COL_ASG_START, COL_ASG_FINISH]
if any(c is None for c in need_r):
    st.error("TASKRSRC no trae columnas mínimas (task_id / start_date / end_date).")
    st.stop()

rsrc = rsrc.copy()
rsrc["task_id"] = rsrc[COL_RSRC_TASK_ID]
rsrc["ASG_START"] = to_dt(rsrc[COL_ASG_START])
rsrc["ASG_FINISH"] = to_dt(rsrc[COL_ASG_FINISH])
rsrc["HH_PLAN"] = safe_num(rsrc[COL_HH_PLAN]) if COL_HH_PLAN else 0.0
rsrc["HH_ACT"] = safe_num(rsrc[COL_HH_ACT]) if COL_HH_ACT else 0.0
rsrc["PCT_EFF"] = safe_num(rsrc[COL_EFF]) if COL_EFF else 0.0
rsrc["HH_EARN"] = rsrc["HH_PLAN"] * (rsrc["PCT_EFF"] / 100.0)

# =========================
# MERGE
# =========================
df = rsrc.merge(
    task[[
        "ACT_ID","ACT_NAME","STATUS",
        "PLAN_START","PLAN_FINISH","ACT_FINISH","BL_FINISH","BL_START",
        "DISCIPLINA","AREA",
        "WBS_ID","WBS_CODE","WBS_NAME","WBS_LABEL",
        "IS_CRITICAL","FLOAT_HR","REMAIN_HR","TASK_TYPE"
    ]],
    left_on="task_id",
    right_on="ACT_ID",
    how="left"
)

# =========================
# DATA DATE (y project finish forzado)
# =========================
proj_finish = task["PLAN_FINISH"].max()
top_left, top_right = st.columns([3, 1])
with top_right:
    data_date = st.date_input("DATA DATE", value=(proj_finish.date() if pd.notna(proj_finish) else None))
data_date = pd.to_datetime(data_date)
dd = data_date.normalize()

# EV/AC a la fecha: cortamos asignaciones a dd
df_exec = df.copy()
df_exec["ASG_START"] = df_exec["ASG_START"].clip(upper=data_date)
df_exec["ASG_FINISH"] = df_exec["ASG_FINISH"].clip(upper=data_date)

# =========================
# FILTERS
# =========================
st.sidebar.header("Filtros")

disc_opts = sorted([x for x in df["DISCIPLINA"].dropna().unique()])
area_opts = sorted([x for x in df["AREA"].dropna().unique()])
status_opts = sorted([x for x in df["STATUS"].dropna().unique()])

# WBS: solo tu selección (por WBS_CODE)
wbs_rows = task[["WBS_CODE","WBS_NAME"]].dropna().drop_duplicates()
wbs_rows = wbs_rows[wbs_rows["WBS_CODE"].astype(str).isin(WBS_SELECTION)]
wbs_rows["WBS_LABEL"] = wbs_rows["WBS_CODE"].astype(str) + " | " + wbs_rows["WBS_NAME"].astype(str)
wbs_labels = sorted(wbs_rows["WBS_LABEL"].unique())

f_disc = st.sidebar.multiselect("Disciplina", disc_opts)
f_area = st.sidebar.multiselect("Área", area_opts)
f_status = st.sidebar.multiselect("Status", status_opts)
f_wbs = st.sidebar.multiselect("WBS (solo selección)", wbs_labels)

st.sidebar.subheader("Dotación")
granularity = st.sidebar.radio("Periodicidad", ["Semanal", "Mensual"], index=0)
week_mode = st.sidebar.selectbox("Cierre semana", ["Sab-Vie", "Lun-Dom"], index=0)

min_date = df["ASG_START"].min()
max_date = df["ASG_FINISH"].max()
date_range = None
if pd.notna(min_date) and pd.notna(max_date):
    date_range = st.sidebar.date_input("Rango de fechas", value=(min_date.date(), max_date.date()))

def apply_filters(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    if f_disc:
        out = out[out["DISCIPLINA"].isin(f_disc)]
    if f_area:
        out = out[out["AREA"].isin(f_area)]
    if f_status:
        out = out[out["STATUS"].isin(f_status)]
    if f_wbs:
        keep_codes = [x.split(" | ")[0].strip() for x in f_wbs]
        out = out[out["WBS_CODE"].astype(str).isin(keep_codes)]
    if date_range and isinstance(date_range, tuple) and len(date_range) == 2:
        d0 = pd.to_datetime(date_range[0]).normalize()
        d1 = pd.to_datetime(date_range[1]).normalize()
        out = out[(out["ASG_FINISH"].fillna(out["ASG_START"]) >= d0) & (out["ASG_START"].fillna(out["ASG_FINISH"]) <= d1)]
    return out

dff = apply_filters(df)
dff_exec = apply_filters(df_exec)

# =========================
# KPI (HH)
# =========================
PV_total = dff["HH_PLAN"].sum(skipna=True)
EV_total = dff["HH_EARN"].sum(skipna=True)
AC_total = dff["HH_ACT"].sum(skipna=True)

pv_curve = spread_units_cum(dff, "HH_PLAN")
ev_curve = spread_units_cum(dff_exec, "HH_EARN")

pv_at = float(pv_curve[pv_curve.index <= dd].iloc[-1]) if not pv_curve.empty and (pv_curve.index <= dd).any() else 0.0
ev_at = float(ev_curve[ev_curve.index <= dd].iloc[-1]) if not ev_curve.empty and (ev_curve.index <= dd).any() else 0.0

SPI = (ev_at / pv_at) if pv_at != 0 else None
IPC_HH = (EV_total / AC_total) if pd.notna(AC_total) and AC_total != 0 else None

avance_plan_pct = (pv_at / PV_total * 100) if PV_total else None
avance_real_pct = (EV_total / PV_total * 100) if PV_total else None

SV = ev_at - pv_at
CV = EV_total - AC_total

spi_icon, spi_txt = semaforo_spi(SPI)

# =========================
# KPI CARDS
# =========================
st.markdown(f"**{project_name}**")
k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("SPI", f"{SPI:.2f}" if SPI is not None else "N/D")
k2.metric("IPC (HH)", f"{IPC_HH:.2f}" if IPC_HH is not None else "N/D")
k3.metric("AVANCE REAL (EV/PV)", f"{avance_real_pct:.1f}%" if avance_real_pct is not None else "N/D")
k4.metric("AVANCE PLAN (PV/PV)", f"{avance_plan_pct:.1f}%" if avance_plan_pct is not None else "N/D")
k5.metric("CV (HH)", f"{CV:,.0f}" if pd.notna(CV) else "N/D")
k6.metric("SV (HH)", f"{SV:,.0f}" if pd.notna(SV) else "N/D")

with top_left:
    st.write("")
    st.write(f"**Estado:** {spi_icon} **{spi_txt}**")

st.divider()

# =========================
# ROW 2: CURVA S (hasta fin proyecto) + AVANCE DISCIPLINA (cap 100%)
# =========================
left, right = st.columns([3, 2])

with left:
    st.subheader("Curva S (HH) — Plan vs Real + Forecast (hasta fin proyecto)")

    # Forzar rango hasta término final del proyecto (max plan_finish)
    proj_finish = task["PLAN_FINISH"].max()
    if pd.isna(proj_finish):
        proj_finish = dd

    start_axis = min(pv_curve.index.min() if not pv_curve.empty else dd, dd)
    end_axis = proj_finish.normalize()

    # Eje diario completo
    axis = pd.date_range(start=start_axis, end=end_axis, freq="D")

    pv_full = pv_curve.reindex(axis).ffill().fillna(0)

    # Real EV cortado en dd
    ev_real = ev_curve.reindex(axis)  # sin ffill
    ev_real = ev_real.where(ev_real.index <= dd)

    # forecast desde dd
    ev_at_dd = float(ev_curve[ev_curve.index <= dd].iloc[-1]) if not ev_curve.empty and (ev_curve.index <= dd).any() else 0.0
    pv_at_dd = float(pv_full[pv_full.index <= dd].iloc[-1]) if (pv_full.index <= dd).any() else 0.0
    forecast = (ev_at_dd + (pv_full - pv_at_dd)).where(pv_full.index >= dd)

    curve = pd.DataFrame({
        "date": axis,
        "Plan (PV)": pv_full.values,
        "Real (EV)": ev_real.values,
        "Forecast (EV)": forecast.values,
    })

    plot_df = curve.melt(id_vars="date", var_name="Serie", value_name="HH acumuladas").dropna()
    plot_df["TipoLinea"] = plot_df["Serie"].apply(lambda s: "dash" if "Forecast" in s else "solid")

    fig = px.line(plot_df, x="date", y="HH acumuladas", color="Serie", line_dash="TipoLinea")
    fig.update_layout(
        height=420,
        xaxis_title="Fecha",
        yaxis_title="HH acumuladas",
        legend_title_text="",
        margin=dict(l=20, r=20, t=40, b=20),
    )
    st.plotly_chart(fig, use_container_width=True)

with right:
    st.subheader("Avance por Disciplina — Plan a la fecha vs Real a la fecha (máx 100%)")

    # Top disciplinas por PV total
    disc_pv = dff.groupby("DISCIPLINA", dropna=False).agg(PV=("HH_PLAN","sum")).reset_index()
    disc_top = disc_pv.sort_values("PV", ascending=False).head(12)["DISCIPLINA"].tolist()

    rows = []
    for disc in disc_top:
        sub = dff[dff["DISCIPLINA"] == disc]
        sub_exec = dff_exec[dff_exec["DISCIPLINA"] == disc]

        pv_total_disc = sub["HH_PLAN"].sum(skipna=True)

        # PLAN a la fecha (PV acumulado)
        pv_disc_curve = spread_units_cum(sub, "HH_PLAN")
        pv_disc_at = float(pv_disc_curve[pv_disc_curve.index <= dd].iloc[-1]) if not pv_disc_curve.empty and (pv_disc_curve.index <= dd).any() else 0.0

        # REAL a la fecha (AC acumulado) -> como pediste: HH planificadas vs HH reales
        ac_disc_curve = spread_units_cum(sub_exec, "HH_ACT")
        ac_disc_at = float(ac_disc_curve[ac_disc_curve.index <= dd].iloc[-1]) if not ac_disc_curve.empty and (ac_disc_curve.index <= dd).any() else 0.0

        plan_pct = clamp_pct((pv_disc_at / pv_total_disc * 100) if pv_total_disc else 0.0)
        real_pct = clamp_pct((ac_disc_at / pv_total_disc * 100) if pv_total_disc else 0.0)

        rows.append({"Disciplina": disc, "Plan % a la fecha": plan_pct, "Real % a la fecha": real_pct})

    disc_df = pd.DataFrame(rows).sort_values("Plan % a la fecha", ascending=False)
    disc_long = disc_df.melt(id_vars="Disciplina",
                             value_vars=["Plan % a la fecha", "Real % a la fecha"],
                             var_name="Serie", value_name="%")

    fig2 = px.bar(disc_long, x="%", y="Disciplina", color="Serie", orientation="h", range_x=[0, 100])
    fig2.update_layout(height=420, margin=dict(l=20, r=20, t=40, b=20))
    st.plotly_chart(fig2, use_container_width=True)

st.divider()

# =========================
# ROW 3: DOTACIÓN + CRÍTICAS + HITOS CONSTRUCCIÓN
# =========================
left2, right2 = st.columns([2, 3])

with left2:
    st.subheader("Histograma Dotación — Plan vs Real (cierre semana visible)")

    daily_plan = spread_units_daily(dff, "HH_PLAN")
    daily_real = spread_units_daily(dff, "HH_ACT")

    if daily_plan.empty and daily_real.empty:
        st.info("No hay HH suficientes para dotación con los filtros actuales.")
    else:
        if date_range and isinstance(date_range, tuple) and len(date_range) == 2:
            d0 = pd.to_datetime(date_range[0]).normalize()
            d1 = pd.to_datetime(date_range[1]).normalize()
            daily_plan = daily_plan[(daily_plan.index >= d0) & (daily_plan.index <= d1)] if not daily_plan.empty else daily_plan
            daily_real = daily_real[(daily_real.index >= d0) & (daily_real.index <= d1)] if not daily_real.empty else daily_real

        if granularity == "Mensual":
            per_plan = daily_plan.resample("MS").sum() if not daily_plan.empty else pd.Series(dtype=float)
            per_real = daily_real.resample("MS").sum() if not daily_real.empty else pd.Series(dtype=float)
            divisor = staffing_divisor("Mensual")
            title = "Dotación mensual (HH/360)"
        else:
            rule = "W-FRI" if week_mode == "Sab-Vie" else "W-SUN"
            per_plan = daily_plan.resample(rule).sum() if not daily_plan.empty else pd.Series(dtype=float)
            per_real = daily_real.resample(rule).sum() if not daily_real.empty else pd.Series(dtype=float)
            divisor = staffing_divisor("Semanal")
            title = f"Dotación semanal (HH/77) — Week ending {'FRI' if rule=='W-FRI' else 'SUN'}"

        idx = pd.Index(sorted(set(per_plan.index).union(set(per_real.index))))
        per_plan = per_plan.reindex(idx).fillna(0)
        per_real = per_real.reindex(idx).fillna(0)

        dot_plan = (per_plan / divisor).apply(round_staffing)
        dot_real = (per_real / divisor).apply(round_staffing)

        out = pd.DataFrame({
            "Periodo (cierre)": idx,
            "Dotación Planificada": dot_plan.values,
            "Dotación Real": dot_real.values,
        }).sort_values("Periodo (cierre)")

        out_long = out.melt(id_vars="Periodo (cierre)",
                            value_vars=["Dotación Planificada", "Dotación Real"],
                            var_name="Serie", value_name="Dotación")

        fig3 = px.bar(out_long, x="Periodo (cierre)", y="Dotación", color="Serie",
                      barmode="group", title=title, text="Dotación")
        fig3.update_traces(texttemplate="%{text}", textposition="outside", cliponaxis=False)
        fig3.update_layout(
            height=420,
            xaxis_title="",
            yaxis_title="Dotación (personas equivalentes)",
            margin=dict(l=20, r=20, t=60, b=40),
        )
        # ticks semanales (para que se note el cierre)
        if granularity == "Semanal":
            fig3.update_xaxes(dtick=7*24*60*60*1000, tickformat="%d-%b-%y", tickangle=-30)
        else:
            fig3.update_xaxes(tickformat="%b-%y", tickangle=-30)

        st.plotly_chart(fig3, use_container_width=True)

with right2:
    st.subheader("Críticas (P6/DCMA) + Hitos Construcción")

    # --------- CRÍTICAS ----------
    st.markdown("### Actividades Críticas (por Disciplina)")
    tcrit = task.copy()

    # aplicar mismos filtros a TASK
    if f_disc:
        tcrit = tcrit[tcrit["DISCIPLINA"].isin(f_disc)]
    if f_area:
        tcrit = tcrit[tcrit["AREA"].isin(f_area)]
    if f_status:
        tcrit = tcrit[tcrit["STATUS"].isin(f_status)]
    if f_wbs:
        keep_codes = [x.split(" | ")[0].strip() for x in f_wbs]
        tcrit = tcrit[tcrit["WBS_CODE"].astype(str).isin(keep_codes)]

    tcrit = tcrit[tcrit["IS_CRITICAL"] == True].copy()

    if tcrit.empty:
        st.warning("No aparecen críticas con criterio (critical_flag OR total_float<=0). Revisa si total_float viene vacío.")
    else:
        resumen = tcrit.groupby("DISCIPLINA", dropna=False).agg(
            Criticas=("ACT_ID", "count"),
            FloatMin=("FLOAT_HR", "min"),
        ).reset_index().sort_values("Criticas", ascending=False)
        st.dataframe(resumen, use_container_width=True)

        cols_show = ["ACT_ID","ACT_NAME","DISCIPLINA","AREA","WBS_CODE","WBS_NAME","STATUS","PLAN_FINISH","ACT_FINISH","FLOAT_HR"]
        cols_show = [c for c in cols_show if c in tcrit.columns]
        st.dataframe(
            tcrit[cols_show].sort_values(["DISCIPLINA","FLOAT_HR","PLAN_FINISH"], na_position="last").head(400),
            use_container_width=True
        )

    st.markdown("---")

    # --------- HITOS CONSTRUCCIÓN ----------
    st.markdown("### Hitos — Solo Construcción")
    tm = task.copy()

    # Solo WBS Name = Construcción
    tm = tm[tm["WBS_NAME"].astype(str).str.strip().str.lower() == CONSTRUCTION_WBS_NAME.lower()].copy()

    # Milestones
    tm["IS_MILESTONE"] = tm.apply(lambda r: is_milestone(r.get("TASK_TYPE"), r), axis=1)
    tm = tm[tm["IS_MILESTONE"] == True].copy()

    if tm.empty:
        st.warning("No detecté hitos. Si exportaste 'Task Type/Activity Type', debería poblarse (Milestone).")
    else:
        tm["BASELINE"] = tm["BL_FINISH"]
        tm["FORECAST"] = tm["PLAN_FINISH"]
        tm["VAR_DAYS"] = (tm["FORECAST"] - tm["BASELINE"]).dt.days

        def estado(var):
            if pd.isna(var): return "N/D"
            if var > 0: return "Atrasado"
            if var < 0: return "Adelantado"
            return "En fecha"

        tm["ESTADO"] = tm["VAR_DAYS"].apply(estado)
        show = tm[["ACT_ID","ACT_NAME","BASELINE","FORECAST","VAR_DAYS","ESTADO"]].sort_values("FORECAST")
        st.dataframe(show.head(300), use_container_width=True)

st.divider()

with st.expander("🔎 Vista previa merge filtrado"):

    st.dataframe(dff.head(200), use_container_width=True)




