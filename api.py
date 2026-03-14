from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import csv

app = FastAPI(title="The Vault API - v3 (Tri-Factor Matching)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def safe_float(value):
    try:
        return float(value)
    except (ValueError, TypeError, AttributeError):
        return None

@app.get("/get_fit")
def check_fit(ref_brand: str, ref_size: str, ref_fit: str, target_brand: str):
    db = []
    try:
        with open("vault_tshirt_database.csv", mode='r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            for row in reader:
                db.append(row)
    except FileNotFoundError:
        pass

    if not db:
        raise HTTPException(status_code=500, detail="Database offline or missing.")

    # 1. FIND THE ANCHOR (NOW REQUIRES BRAND + SIZE + FIT)
    ref_data = None
    for row in db:
        brand_match = row.get('Brand', '').strip().lower() == ref_brand.lower()
        size_match = row.get('Size Label', '').strip().upper() == ref_size.upper()
        # New Match Logic for Fit Type
        fit_match = row.get('Fit Type', '').strip().lower() == ref_fit.lower()
        
        if brand_match and size_match and fit_match:
            ref_data = row
            break
            
    if not ref_data:
        raise HTTPException(status_code=404, detail=f"We don't have the data for {ref_brand.title()} {ref_fit.title()} {ref_size.upper()} yet.")

    min_chest = safe_float(ref_data.get('Chest Min (Inches)'))
    max_chest = safe_float(ref_data.get('Chest Max (Inches)'))
    if min_chest is None or max_chest is None:
        raise HTTPException(status_code=400, detail="Anchor chest data missing in CSV.")
        
    ref_true_inches = (min_chest + max_chest) / 2
    ref_fit_type = ref_data.get('Fit Type', '').strip().lower()
    ref_shoulder = safe_float(ref_data.get('Shoulder (Inches)'))

    # 2. FIND THE TARGET
    best_match = None
    smallest_penalty_score = 9999

    for row in db:
        if row.get('Brand', '').strip().lower() == target_brand.lower():
            t_min = safe_float(row.get('Chest Min (Inches)'))
            t_max = safe_float(row.get('Chest Max (Inches)'))
            if t_min is None or t_max is None: 
                continue
            
            target_true_inches = (t_min + t_max) / 2
            target_shoulder = safe_float(row.get('Shoulder (Inches)'))
            
            chest_diff = target_true_inches - ref_true_inches
            
            if chest_diff < 0:
                chest_score = abs(chest_diff) * 3.0 
            else:
                chest_score = chest_diff

            shoulder_score = 0
            if ref_shoulder is not None and target_shoulder is not None:
                shoulder_diff = target_shoulder - ref_shoulder
                if shoulder_diff < -0.5: 
                    shoulder_score = abs(shoulder_diff) * 2.0
                else:
                    shoulder_score = abs(shoulder_diff) * 0.5 
            
            total_score = chest_score + shoulder_score

            if total_score < smallest_penalty_score:
                smallest_penalty_score = total_score
                best_match = row

    if not best_match:
        raise HTTPException(status_code=404, detail="Target brand not found in database.")

    # 3. THE DEALBREAKER CLAUSE
    winner_t_min = safe_float(best_match.get('Chest Min (Inches)'))
    winner_t_max = safe_float(best_match.get('Chest Max (Inches)'))
    winner_target_inches = (winner_t_min + winner_t_max) / 2
    
    final_chest_diff = winner_target_inches - ref_true_inches
    
    if final_chest_diff < -1.5:
        raise HTTPException(
            status_code=406, 
            detail=f"{target_brand.title()} does not manufacture a size large enough to match your {ref_brand.title()} {ref_size}."
        )

    # 4. THE VIBE WARNING
    target_fit_type = best_match.get('Fit Type', '').strip().lower()
    warning_message = "Perfect Match: Chest and intended style align beautifully."

    t_shoulder_val = safe_float(best_match.get('Shoulder (Inches)'))
    
    if ref_shoulder and t_shoulder_val:
        s_diff = t_shoulder_val - ref_shoulder
        if s_diff > 2.0:
            warning_message = f"VIBE SHIFT: Matches your chest width, but has a heavily dropped shoulder ({t_shoulder_val}\" vs your usual {ref_shoulder}\"). It will look much baggier."
        elif s_diff < -1.0:
            warning_message = f"VIBE SHIFT: Matches chest, but the shoulders run quite narrow. Might feel restrictive."
        elif ref_fit_type in ["regular", "slim"] and target_fit_type in ["oversized", "boxy", "loose", "relaxed"]:
            warning_message = f"VIBE SHIFT: Matches chest, but {target_brand.title()} designed this to be intentionally loose/boxy."
    elif ref_fit_type in ["regular", "slim"] and target_fit_type in ["oversized", "boxy", "loose", "relaxed"]:
        warning_message = f"VIBE SHIFT: Matches chest width, but {target_brand.title()} designed this to be baggy."
    elif ref_fit_type in ["oversized", "boxy", "loose", "relaxed"] and target_fit_type in ["slim", "regular"]:
        warning_message = f"VIBE SHIFT: Matches measurements, but is a slimmer cut. It will hug your body tighter."

    return {
        "status": "success",
        "target_brand": target_brand.title(),
        "recommended_size": best_match.get('Size Label', '').strip().upper(),
        "warning": warning_message
    }
