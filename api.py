from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import csv

# Initialize the API
app = FastAPI(title="The Vault API")

# S.L.I.P. Framework: CORS allows any Shopify site to securely talk to your API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_FILE = "vault_tshirt_database.csv"

def load_database():
    data = []
    try:
        with open(DATABASE_FILE, mode='r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            for row in reader:
                data.append(row)
        return data
    except FileNotFoundError:
        return []

def safe_float(value):
    try:
        return float(value)
    except (ValueError, TypeError):
        return None

# The Web Endpoint
@app.get("/get_fit")
def check_fit(ref_brand: str, ref_size: str, target_brand: str):
    db = load_database()
    if not db:
        raise HTTPException(status_code=500, detail="Database offline")

    # 1. FIND THE ANCHOR
    ref_data = next((row for row in db if row['Brand'].lower() == ref_brand.lower() and row['Size Label'].upper() == ref_size.upper()), None)
    
    if not ref_data:
        raise HTTPException(status_code=404, detail="Anchor size not found")

    min_chest = safe_float(ref_data['Chest Min (Inches)'])
    max_chest = safe_float(ref_data['Chest Max (Inches)'])
    ref_true_inches = (min_chest + max_chest) / 2
    ref_fit_type = ref_data['Fit Type'].strip().lower()

   # 2. FIND THE TARGET
    best_match = None
    smallest_diff = 999

    for row in db:
        if row['Brand'].lower() == target_brand.lower():
            t_min = safe_float(row['Chest Min (Inches)'])
            t_max = safe_float(row['Chest Max (Inches)'])
            if t_min is None or t_max is None: continue
            
            target_true_inches = (t_min + t_max) / 2
            
            # Find the actual difference (Target minus Anchor)
            raw_diff = target_true_inches - ref_true_inches
            
            # THE TIGHTNESS PENALTY 
            # If the target shirt is smaller than the anchor, multiply the difference by 2.5
            # This heavily biases the algorithm to recommend the slightly larger size.
            if raw_diff < 0:
                weighted_diff = abs(raw_diff) * 2.5 
            else:
                weighted_diff = raw_diff

            if weighted_diff < smallest_diff:
                smallest_diff = weighted_diff
                best_match = row

    if not best_match:
        raise HTTPException(status_code=404, detail="Target brand not found")

    # 3. FIT VIBE LOGIC
    target_fit_type = best_match['Fit Type'].strip().lower()
    warning_message = "Perfect Match: Size and intended style align beautifully."

    if ref_fit_type in ["regular", "slim"] and target_fit_type in ["oversized", "boxy", "loose"]:
        warning_message = f"VIBE SHIFT: Matches chest width, but {target_brand.capitalize()} designed this to be baggy. It will feel looser than your {ref_brand.capitalize()}."
    elif ref_fit_type in ["oversized", "boxy", "loose"] and target_fit_type in ["slim", "regular"]:
        warning_message = f"VIBE SHIFT: Matches measurements, but is a slimmer cut. It will hug your body tighter than your {ref_brand.capitalize()}."

    # 4. SEND RESPONSE TO WIDGET
    return {
        "status": "success",
        "target_brand": target_brand.capitalize(),
        "recommended_size": best_match['Size Label'].upper(),
        "warning": warning_message

    }
