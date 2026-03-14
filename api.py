from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import csv

app = FastAPI(title="The Vault API - v2 (Two-Factor Sizing)")

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
    except (ValueError, TypeError, AttributeError):
        return None

@app.get("/get_fit")
def check_fit(ref_brand: str, ref_size: str, target_brand: str):
    db = load_database()
    if not db:
        raise HTTPException(status_code=500, detail="Database offline")

    # 1. FIND THE ANCHOR
    ref_data = next((row for row in db if row['Brand'].lower() == ref_brand.lower() and row['Size Label'].upper() == ref_size.upper()), None)
    
    if not ref_data:
        raise HTTPException(status_code=404, detail="Anchor size not found")

    min_chest = safe_float(ref_data.get('Chest Min (Inches)'))
    max_chest = safe_float(ref_data.get('Chest Max (Inches)'))
    if min_chest is None or max_chest is None:
        raise HTTPException(status_code=400, detail="Anchor chest data missing")
        
    ref_true_inches = (min_chest + max_chest) / 2
    ref_fit_type = ref_data.get('Fit Type', '').strip().lower()
    ref_shoulder = safe_float(ref_data.get('Shoulder (Inches)'))

    # 2. FIND THE TARGET (TWO-FACTOR MATCH)
    best_match = None
    smallest_penalty_score = 9999

    for row in db:
        if row['Brand'].lower() == target_brand.lower():
            t_min = safe_float(row.get('Chest Min (Inches)'))
            t_max = safe_float(row.get('Chest Max (Inches)'))
            if t_min is None or t_max is None: continue
            
            target_true_inches = (t_min + t_max) / 2
            target_shoulder = safe_float(row.get('Shoulder (Inches)'))
            
            # FACTOR 1: CHEST DIFFERENCE
            chest_diff = target_true_inches - ref_true_inches
            
            # THE TIGHTNESS PENALTY (Chest)
            if chest_diff < 0:
                chest_score = abs(chest_diff) * 3.0 # Heavy penalty for tight chest
            else:
                chest_score = chest_diff

            # FACTOR 2: SHOULDER DIFFERENCE (If both exist)
            shoulder_score = 0
            if ref_shoulder is not None and target_shoulder is not None:
                shoulder_diff = target_shoulder - ref_shoulder
                if shoulder_diff < -0.5: 
                    shoulder_score = abs(shoulder_diff) * 2.0
                else:
                    shoulder_score = abs(shoulder_diff) * 0.5 
            
            # TOTAL PENALTY SCORE
            total_score = chest_score + shoulder_score

            if total_score < smallest_penalty_score:
                smallest_penalty_score = total_score
                best_match = row

    if not best_match:
        raise HTTPException(status_code=404, detail="Target brand not found")

    # ==========================================
    # 🛑 THE DEALBREAKER CLAUSE
    # ==========================================
    winner_t_min = safe_float(best_match.get('Chest Min (Inches)'))
    winner_t_max = safe_float(best_match.get('Chest Max (Inches)'))
    winner_target_inches = (winner_t_min + winner_t_max) / 2
    
    final_chest_diff = winner_target_inches - ref_true_inches
    
    if final_chest_diff < -1.5:
        raise HTTPException(
            status_code=406, 
            detail=f"{target_brand.title()} does not manufacture a size large enough to match your {ref_brand.title()} {ref_size}."
        )
    # ==========================================

    # 3. FIT VIBE LOGIC (Upgraded with Shoulder Context)
    target_fit_type = best_match.get('Fit Type', '').strip().lower()
    warning_message = "Perfect Match: Chest and intended style align beautifully."

    t_shoulder_val = safe_float(best_match.get('Shoulder (Inches)'))
    
    if ref_shoulder and t_shoulder_val:
        s_diff = t_shoulder_val - ref_shoulder
        if s_diff > 2.0:
            warning_message = f"VIBE SHIFT: Matches your chest width, but has a heavily dropped shoulder ({t_shoulder_val}\" vs your usual {ref_shoulder}\"). It will look much baggier."
        elif s_diff < -1.0:
            warning_message = f"VIBE SHIFT: Matches chest, but the shoulders run quite narrow. Might feel slightly restrictive."
        elif ref_fit_type in ["regular", "slim"] and target_fit_type in ["oversized", "boxy", "loose"]:
            warning_message = f"VIBE SHIFT: Matches chest, but {target_brand.title()} designed this to be intentionally loose/boxy."
    elif ref_fit_type in ["regular", "slim"] and target_fit_type in ["oversized", "boxy", "loose"]:
        warning_message = f"VIBE SHIFT: Matches chest width, but {target_brand.title()} designed this to be baggy. It will feel looser than your {ref_brand.title()}."
    elif ref_fit_type in ["oversized", "boxy", "loose"] and target_fit_type in ["slim", "regular"]:
        warning_message = f"VIBE SHIFT: Matches measurements, but is a slimmer cut. It will hug your body tighter than your {ref_brand.title()}."

    # 4. SEND RESPONSE TO WIDGET
    return {
        "status": "success",
        "target_brand": target_brand.title(),
        "recommended_size": best_match['Size Label'].upper(),
        "warning": warning_message
    }
