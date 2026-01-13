# ============================================================================
# 1. IMPORTS
# ============================================================================
import streamlit as st
import pdfplumber
import pandas as pd
import re
import os
from pathlib import Path
from typing import List, Dict, Tuple, Any

# Bandome importuoti python-docx
try:
    from docx import Document
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False

# ============================================================================
# 2. CONSTANTS
# ============================================================================
DATA_FOLDER = "data"

# ============================================================================
# 3. GLOBAL FUNCTION DEFINITIONS
# ============================================================================

def sanitize_code(code: str) -> str:
    """Išvalo kodą nuo taškų ir tarpų."""
    return code.replace(".", "").replace(" ", "").replace("\u00A0", "")

def extract_codes_from_text(raw_text: str) -> List[str]:
    """
    PAGRINDINĖ FUNKCIJA: Ištraukia kodus iš teksto.
    """
    if not raw_text:
        return []

    text = str(raw_text)

    # 1. Hard Separators -> Pipe |
    text = re.sub(r'[;,\/\\\n\r]', '|', text)

    # 2. Remove dots
    text = text.replace('.', '')

    # 3. Split by Hard Separators
    raw_blocks = text.split('|')

    final_codes = []

    for block in raw_blocks:
        parts = block.split()
        if not parts:
            continue

        current_code = parts[0]

        for next_part in parts[1:]:
            # HEURISTIC: Jei sujungus ilgis <= 10, klijuojame (pvz. 84 + 71)
            if len(current_code) + len(next_part) <= 10:
                current_code += next_part
            else:
                if len(current_code) >= 4:
                    final_codes.append(current_code)
                current_code = next_part

        if len(current_code) >= 4:
            final_codes.append(current_code)

    return sorted(list(set([c for c in final_codes if c.isdigit() and 4 <= len(c) <= 10])))

def categorize_file(filename: str) -> str:
    """Nustato sankcijos tipą pagal failo pavadinimą."""
    filename_lower = filename.lower()

    if "(tru)" in filename_lower: return "RU transit sanctions"
    elif "(lv_ru)" in filename_lower: return "EU sanctions for BY"
    elif "(du)" in filename_lower: return "Dvejopo naudojimo prekės (Dual Use)"
    elif "(glonass)" in filename_lower: return "Glonass navigation seal requirement"
    elif "(7a)" in filename_lower: return "VII Appendix A, possible transit restriction"
    elif "(lv)" in filename_lower or filename_lower.startswith("(lv)"): return "Lithuanian National Sanctions"
    elif filename_lower.startswith("(sa)"): return "EU Sanctions"
    elif filename_lower.startswith("(tr)"): return "Transit Restrictions"
    else: return "Uncategorized Restriction"

def is_valid_code_cell(text: str) -> bool:
    """
    GRIEŽTAS TRIUKŠMO FILTRAS.
    """
    if not text: return False
    # 1. Ilgio patikra
    if len(text) > 25: return False
    # 2. Valymas analizei
    clean = re.sub(r'(?i)\bex\b', '', text)
    clean = re.sub(r'[ \.\,\n\r\t\u00A0]', '', clean)
    # 3. Griežtas skaitmenų testas
    if not clean.isdigit(): return False
    if not clean: return False
    return True

# --- SMART TABLE PARSER ---

def identify_table_columns_universal(rows: List[Any], is_docx: bool = False) -> Tuple[int, int, int]:
    """
    Nustato Kodo ir Aprašymo stulpelius.
    """
    code_idx = -1
    desc_idx = -1
    start_row = 0

    def get_cell_text(row, idx):
        if is_docx:
            if idx < len(row.cells): return row.cells[idx].text.strip()
        else:
            if idx < len(row) and row[idx]: return str(row[idx]).strip()
        return ""

    def get_row_len(row):
        return len(row.cells) if is_docx else len(row)

    # 1. ANTRAŠČIŲ PAIEŠKA
    for r_idx, row in enumerate(rows[:6]):
        row_len = get_row_len(row)
        for c_idx in range(row_len):
            txt = get_cell_text(row, c_idx).lower()
            if "kodas" in txt or "code" in txt or "kn" in txt or "cn" in txt:
                code_idx = c_idx
            if "aprašymas" in txt or "description" in txt or "prekės" in txt:
                desc_idx = c_idx

        if code_idx != -1:
            start_row = r_idx + 1
            if desc_idx == -1 and row_len > code_idx + 1:
                desc_idx = code_idx + 1
            return code_idx, desc_idx, start_row

    # 2. TURINIO ANALIZĖ
    best_code_score = -999
    best_code_col = 0
    max_cols_check = 0
    if rows: max_cols_check = get_row_len(rows[0])

    for c in range(min(5, max_cols_check)):
        score = 0
        for row in rows[:15]:
            if get_row_len(row) > c:
                txt = get_cell_text(row, c)
                if is_valid_code_cell(txt):
                    score += 10
                elif len(txt) > 30:
                    score -= 50
                elif re.match(r'^\d{1,2}\.?$', txt.strip()):
                    score -= 5

        if score > best_code_score:
            best_code_score = score
            best_code_col = c

    if desc_idx == -1: desc_idx = best_code_col + 1
    return best_code_col, desc_idx, 0

# --- DATA LOADERS ---

@st.cache_data
def load_docx_data() -> Tuple[List[Dict], int]:
    data_folder = Path(DATA_FOLDER)
    if not data_folder.exists() or not DOCX_AVAILABLE:
        return [], 0
    all_codes = []
    docx_files = list(data_folder.glob("*.docx"))

    for docx_file in docx_files:
        try:
            if "(master)" in docx_file.name.lower(): continue
            category = categorize_file(docx_file.name)
            source = docx_file.name
            doc = Document(docx_file)

            for table in doc.tables:
                code_col_idx, desc_col_idx, start_row = identify_table_columns_universal(table.rows, is_docx=True)

                for row in table.rows[start_row:]:
                    if len(row.cells) <= code_col_idx: continue

                    raw_text = row.cells[code_col_idx].text.strip()
                    if not is_valid_code_cell(raw_text): continue

                    found_codes = extract_codes_from_text(raw_text)
                    for valid_code in found_codes:
                        extra_info = ""
                        if len(row.cells) > desc_col_idx and desc_col_idx != -1:
                            extra_info = row.cells[desc_col_idx].text.strip().replace('\n', ' ')
                        all_codes.append({
                            "code": valid_code, "category": category, "source": source,
                            "extra_info": extra_info, "context": f"{valid_code} | {extra_info}"
                        })
        except Exception: continue
    return all_codes, len(docx_files)

@st.cache_data
def load_excel_csv_data(data_folder_str: str) -> List[Dict]:
    all_codes = []
    data_folder = Path(data_folder_str)
    all_files = list(data_folder.glob("*.xlsx")) + list(data_folder.glob("*.csv"))

    for file_path in all_files:
        try:
            if "(master)" in file_path.name.lower(): continue

            is_du_file = "(du)" in file_path.name.lower()
            is_sa_file = "(sa)" in file_path.name.lower()

            # PATAISYMAS ČIA: Nustatome kategoriją pagal failo pavadinimą
            file_category = categorize_file(file_path.name)

            df = None
            try:
                if is_sa_file:
                    if file_path.suffix.lower() == '.xlsx':
                        df = pd.read_excel(file_path, engine='openpyxl', header=None, dtype=str)
                    else:
                        df = pd.read_csv(file_path, header=None, dtype=str, sep=None, engine='python')
                else:
                    if file_path.suffix.lower() == '.xlsx':
                        df = pd.read_excel(file_path, engine='openpyxl', dtype=str)
                    else:
                        df = pd.read_csv(file_path, dtype=str, sep=None, engine='python')
            except Exception: continue

            if df is None or df.empty: continue

            header_idx = -1
            if is_sa_file or is_du_file:
                for r in range(min(15, len(df))):
                    row_vals = df.iloc[r].fillna('').astype(str).str.lower().tolist()
                    if any(k in s for s in row_vals for k in ['kn kodas', 'cn code', 'kodas', 'cn code', 'kn code']):
                        header_idx = r
                        break
            if header_idx != -1:
                df.columns = df.iloc[header_idx].fillna('').astype(str)
                df = df.iloc[header_idx+1:].reset_index(drop=True)
            else:
                df.columns = df.columns.astype(str)

            code_col = None
            for col in df.columns:
                c = col.lower().strip()
                if 'kn kodas' in c or 'cn code' in c or ('kodas' in c and 'code' in c):
                    code_col = col
                    break
            if code_col is None:
                for col in df.columns[:5]:
                    sample = df[col].astype(str).str.replace(' ', '').head(10)
                    if sample.str.contains(r'^\d{4,}$', regex=True).any():
                        code_col = col
                        break
            if code_col is None: continue

            desc_col = None
            for col in df.columns:
                c = col.lower().strip()
                if 'aprašymas' in c or 'description' in c:
                    desc_col = col
                    break

            for _, row in df.iterrows():
                val = row[code_col]
                if pd.isna(val): continue
                raw_text = str(val).strip()
                if not is_valid_code_cell(raw_text): continue

                clean = re.sub(r'^\s*ex\s*', '', raw_text, flags=re.IGNORECASE)
                clean = clean.replace('.', '').replace(' ', '').replace('\u00A0', '')
                if not clean.isdigit(): continue
                if len(clean) == 8 and clean[4:6] == '00': clean = clean[:4]
                if len(clean) not in [2, 4, 6, 8, 10]: continue

                extra = ""
                if desc_col and not pd.isna(row[desc_col]):
                    extra = str(row[desc_col]).strip().replace('\n', ' ')

                all_codes.append({
                    "code": clean,
                    "category": file_category, # Naudojame teisingą kategoriją
                    "source": file_path.name,
                    "extra_info": extra,
                    "context": f"{clean} | {extra}"
                })
        except Exception: continue
    return all_codes

@st.cache_data
def load_pdf_data() -> Tuple[List[Dict], int]:
    data_folder = Path(DATA_FOLDER)
    if not data_folder.exists(): return [], 0
    all_codes = []
    pdf_files = list(data_folder.glob("*.pdf"))

    for pdf_file in pdf_files:
        try:
            if "(master)" in pdf_file.name.lower(): continue
            category = categorize_file(pdf_file.name)
            source = pdf_file.name

            with pdfplumber.open(pdf_file) as pdf:
                for page in pdf.pages:
                    tables = page.extract_tables()
                    extracted_from_table = False

                    if tables:
                        for table in tables:
                            code_idx, desc_idx, start_row = identify_table_columns_universal(table, is_docx=False)

                            for row in table[start_row:]:
                                if not row or len(row) <= code_idx: continue
                                raw_code = str(row[code_idx]).strip() if row[code_idx] else ""
                                if not is_valid_code_cell(raw_code): continue

                                found_codes = extract_codes_from_text(raw_code)
                                for code in found_codes:
                                    desc = ""
                                    if desc_idx != -1 and len(row) > desc_idx and row[desc_idx]:
                                        desc = str(row[desc_idx]).replace('\n', ' ')

                                    all_codes.append({
                                        "code": code, "category": category, "source": source,
                                        "extra_info": desc, "context": f"{code} | {desc}"
                                    })
                                    extracted_from_table = True

                    if not extracted_from_table and not tables:
                        text = page.extract_text()
                        if text:
                            candidates = extract_codes_from_text(text)
                            for c in candidates:
                                all_codes.append({
                                    "code": c, "category": category, "source": source,
                                    "extra_info": "Extracted from text", "context": c
                                })
        except Exception: continue
    return all_codes, len(pdf_files)

@st.cache_data
def load_taric_master_codes(data_folder_str: str) -> set:
    valid_taric_codes = set()
    data_folder = Path(data_folder_str)
    all_files = list(data_folder.glob("*.xlsx")) + list(data_folder.glob("*.csv"))

    for file_path in all_files:
        if "(master)" not in file_path.name.lower(): continue
        try:
            if file_path.suffix.lower() == '.xlsx':
                df = pd.read_excel(file_path, engine='openpyxl', dtype=str)
            else:
                df = pd.read_csv(file_path, dtype=str)
            if df.empty: continue
            code_col = None
            for col in df.columns:
                c = str(col).lower().strip()
                if 'cn2025' in c or 'cn code' in c or c == 'code' or c == 'kodas':
                    code_col = col
                    break
            if code_col is None: continue
            for _, row in df.iterrows():
                val = row[code_col]
                if pd.isna(val): continue
                clean = sanitize_code(str(val))
                if clean.isdigit():
                    if len(clean) == 8: valid_taric_codes.add(clean)
                    elif len(clean) == 10:
                        valid_taric_codes.add(clean)
                        valid_taric_codes.add(clean[:8])
                    elif len(clean) in [4, 6]: valid_taric_codes.add(clean)
        except Exception: continue
    return valid_taric_codes

def validate_taric_code(code: str, valid_taric_codes: set) -> bool:
    if not valid_taric_codes: return True
    clean = sanitize_code(code)
    if not clean.isdigit(): return False
    if len(clean) >= 8: return clean[:8] in valid_taric_codes
    elif len(clean) in [4, 6]: return clean in valid_taric_codes
    return False

@st.cache_data
def load_all_data() -> Tuple[List[Dict], int]:
    data_folder = Path(DATA_FOLDER)
    if not data_folder.exists(): return [], 0
    all_codes = []
    pdf_codes, pdf_count = load_pdf_data()
    all_codes.extend(pdf_codes)
    spreadsheet_codes = load_excel_csv_data(str(data_folder))
    all_codes.extend(spreadsheet_codes)
    docx_codes, docx_count = load_docx_data()
    all_codes.extend(docx_codes)
    total_count = pdf_count + len(list(data_folder.glob("*.xlsx"))) + len(list(data_folder.glob("*.csv"))) + docx_count
    return all_codes, total_count

# --- MATCHING LOGIC ---

def is_du_match(user_input: str, db_code: str) -> bool:
    u = str(user_input).strip().replace(' ', '').replace('.', '')
    d = str(db_code).strip().replace(' ', '').replace('.', '')
    if not u or not d: return False
    def get_sig(c): return c[:4] if len(c.rstrip('0')) < 4 and len(c) >= 4 else c.rstrip('0')
    u_sig, d_sig = get_sig(u), get_sig(d)
    return u_sig.startswith(d_sig) or d_sig.startswith(u_sig)

def is_lv_match(user_input: str, db_code: str) -> Tuple[bool, str]:
    u = str(user_input).strip()
    d = str(db_code).strip()
    if u == d: return (True, "")
    if len(d) >= 2 and u.startswith(d): return (True, d)
    d_stripped = d.rstrip('0')
    if len(d_stripped) >= 2 and u.startswith(d_stripped): return (True, d_stripped)
    return (False, "")

def is_hierarchical_match(user_input: str, db_code: str) -> Tuple[bool, str]:
    u = str(user_input).strip()
    d = str(db_code).strip()
    if u == d: return (True, "")
    if len(d) == 4: parent = d
    elif len(d) > 4:
        parent = d.rstrip('0')
        if len(parent) < 4: parent = d[:4]
    else: parent = d
    if len(parent) >= 2 and u.startswith(parent): return (True, parent)
    if d.startswith(u): return (True, u)
    return (False, "")

def search_codes(user_input: str, all_codes: List[Dict]) -> List[Dict]:
    if not user_input: return []
    matches = []
    for entry in all_codes:
        cat = entry.get("category", "").lower()
        src = entry.get("source", "").lower()
        if "dual use" in cat or "dvejopo naudojimo" in cat or "(du)" in src:
            if is_du_match(user_input, entry["code"]):
                m = entry.copy()
                m["matched_parent_code"] = ""
                matches.append(m)
        elif "(lv)" in src or "lithuanian" in cat:
            is_m, parent = is_lv_match(user_input, entry["code"])
            if is_m:
                m = entry.copy()
                m["matched_parent_code"] = parent
                matches.append(m)
        else:
            is_m, parent = is_hierarchical_match(user_input, entry["code"])
            if is_m:
                m = entry.copy()
                m["matched_parent_code"] = parent
                matches.append(m)
    return matches

def extract_tags_from_matches(matches: List[Dict]) -> str:
    tags = set()
    for m in matches:
        src = m.get("source", "").lower()
        cat = m.get("category", "").lower()
        if "(du)" in src or "dual use" in cat: tags.add("DU")
        elif "(tru)" in src: tags.add("TRU")
        elif "(lv_ru)" in src: tags.add("LV_RU")
        elif "(tr)" in src: tags.add("TR")
        elif "(sa)" in src: tags.add("SA")
        elif "(lv)" in src: tags.add("LV")
        elif "(7a)" in src: tags.add("7A")
        elif "(glonass)" in src: tags.add("GLONASS")
        else: tags.add("OTHER")
    return "; ".join(sorted(list(tags))) if tags else "OTHER"

def extract_codes_smart(uploaded_file) -> List[str]:
    df = None
    keywords = ['code', 'kod', 'cn', 'tn ved', 'тн вэд', 'hs', 'taric']
    try:
        uploaded_file.seek(0)
        if uploaded_file.name.endswith('.csv'):
            df = pd.read_csv(uploaded_file, header=None, dtype=str)
        else:
            try: df = pd.read_excel(uploaded_file, header=None, dtype=str, engine='openpyxl')
            except:
                uploaded_file.seek(0)
                df = pd.read_csv(uploaded_file, header=None, dtype=str)
    except: return []
    if df is None or df.empty: return []
    header_idx, col_idx = -1, -1
    for r in range(min(20, len(df))):
        row_vals = df.iloc[r].astype(str).str.lower().tolist()
        for c, val in enumerate(row_vals):
            if any(k in val for k in keywords):
                header_idx, col_idx = r, c
                break
        if header_idx != -1: break
    codes = []
    if header_idx != -1 and col_idx != -1:
        raw = df.iloc[header_idx+1:, col_idx].tolist()
        for c in raw:
            clean = str(c).replace(' ', '').replace('.', '').strip()
            if re.match(r'^\d{4,10}$', clean): codes.append(clean)
    return list(set(codes))

# ============================================================================
# 4. UI SETUP
# ============================================================================
st.set_page_config(page_title="Sanctions Checker", page_icon="🇪🇺", layout="wide")

st.markdown("""
    <div style='text-align: center;'>
        <h1 style='color: #0056b3; margin-bottom: 0;'>HS Code Checker (EU-CIS)</h1>
        <h3 style='color: #666; margin-top: 0;'>Sankcijų ir Prekių Kodų Patikra</h3>
        <hr style='border: none; height: 2px; background-color: #0056b3; width: 50%; margin: auto;'>
    </div></br>
""", unsafe_allow_html=True)

# ============================================================================
# 5. MAIN EXECUTION
# ============================================================================
def main():
    if not DOCX_AVAILABLE:
        st.error("⚠️ Trūksta bibliotekos: `python-docx`. Įdiekite ją komanda: `pip install python-docx`")

    with st.spinner("Loading files..."):
        all_codes, file_count = load_all_data()
        valid_taric_codes = load_taric_master_codes(DATA_FOLDER)

    # --- SIDEBAR ---
    with st.sidebar:
        st.header("Duomenų šaltiniai")
        data_path = Path(DATA_FOLDER)
        files = []
        if data_path.exists():
            files = sorted([f for f in data_path.glob('*') if f.suffix.lower() in ['.xlsx', '.xls', '.csv', '.pdf', '.docx']])

        with st.expander(f"📚 Aktyvūs failai ({len(files)})", expanded=True):
            if not files:
                st.warning("Data aplankas tuščias.")
            else:
                for f in files:
                    fname = f.name.lower()
                    if "(du)" in fname: icon = "🟠"
                    elif "(sa)" in fname: icon = "🔴"
                    elif "(ru)" in fname or "transit" in fname or "(tr)" in fname: icon = "🚫"
                    elif "(glonass)" in fname: icon = "🛰️"
                    elif "(lv)" in fname: icon = "🇱🇹"
                    else: icon = "⚪"
                    ftype = f.suffix.upper().replace('.', '')
                    st.write(f"{icon} `[{ftype}]` {f.name}")

        with st.expander("📊 Statistika", expanded=False):
            st.metric("Total Files", len(files))
            st.metric("Indexed Codes", len(all_codes))

        with st.expander("🛠️ Debugger", expanded=False):
            debug_text = st.text_input("Testuoti tekstą:", key="debug")
            if debug_text:
                res = extract_codes_from_text(debug_text)
                st.write(res)

    # --- MAIN CONTENT ---
    extracted_codes = []

    st.subheader("✍️ Rankinė kodų paieška")
    user_input = st.text_area("📋 Įklijuokite kodus:", height=150)
    if user_input:
        extracted_codes.extend(extract_codes_from_text(user_input))

    st.markdown("---")

    st.subheader("📂 Arba įkelkite failą")
    uploaded_file = st.file_uploader("Tempkite failą čia (Excel/CSV)", type=['xlsx', 'xls', 'csv'])
    if uploaded_file:
        with st.spinner("Apdorojama..."):
            file_codes = extract_codes_smart(uploaded_file)
            extracted_codes.extend(file_codes)
            if file_codes:
                st.success(f"Rasta kodų: {len(file_codes)}")

    if st.button("Tikrinti", type="primary", use_container_width=True):
        extracted_codes = list(set(extracted_codes))

        if not extracted_codes:
            st.warning("Nėra kodų patikrai.")
            return

        code_list_str = ", ".join(extracted_codes)
        st.info(f"📊 Tikrinami {len(extracted_codes)} kodai: {code_list_str}")

        invalid_codes = []
        sanctioned_items = []
        safe_items = []

        for code in extracted_codes:
            if not validate_taric_code(code, valid_taric_codes):
                invalid_codes.append(code)
                continue

            matches = search_codes(code, all_codes)
            if matches:
                sanctioned_items.append({"code": code, "matches": matches})
            else:
                safe_items.append(code)

        if invalid_codes:
            st.subheader(f"⚠️ Neatpažinti TARIC kodai ({len(invalid_codes)})")
            for c in invalid_codes: st.error(f"{c} - Nėra TARIC bazėje")

        if sanctioned_items:
            st.subheader(f"🔴 Rastos sankcijos ({len(sanctioned_items)})")
            for item in sanctioned_items:
                code = item["code"]
                tags = extract_tags_from_matches(item["matches"])
                with st.expander(f"🔴 {code} — {tags}"):
                    df = pd.DataFrame(item["matches"])
                    st.dataframe(df[["category", "source", "extra_info"]], hide_index=True)

        if safe_items:
            st.subheader(f"✅ Švarūs kodai ({len(safe_items)})")
            st.success(", ".join(safe_items))

if __name__ == "__main__":
    main()
