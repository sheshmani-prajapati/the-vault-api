from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import csv
import os
import traceback

app = FastAPI(title="The Vault API - v3.3 (Diagnostic Shield)")

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

def safe_str(value):
    if value is None:
        return ""
    return str(value).strip()

def normalize_match(value):
    if not value:
        return ""
    val = str(value).lower().replace("'", "").strip()
    if val.endswith(" fit"):
        val = val[:-4].strip()
    return val

@app.get("/get_fit")
def check_fit(ref_brand: str, ref_size: str, ref_fit: str, target_brand: str, target_fit: str = "Regular Fit"):
    try:
        db = []
        
        # 1. SMART FILE LOCATOR (Fixes Linux Case-Sensitivity)
        file_path = "vault_tshirt_database.csv"
        if not os.path.exists(file_path):
            for f in os.listdir("."):
                if f.lower() == "vault_tshirt_database.csv":
                    file_path = f
                    break

        if not os.path.exists(file_path):
            raise ValueError(f"Could not find the database file on Render.")

        # 2. SAFE FILE READER (Fixes Encoding Crashes)
        try:
            with open(file_path, mode='r', encoding='utf-8-sig') as file:
                reader = csv.DictReader(file)
                for row in reader:
                    db.append(row)
        except UnicodeDecodeError:
            with open(file_path, mode='r', encoding='latin1') as file:
                reader = csv.DictReader(file)
                for row in reader:
                    db.append(row)

        if not db:
            raise ValueError("The CSV file was found, but it appears to be empty.")

        norm_ref_brand = normalize_match(ref_brand)
        norm_ref_fit = normalize_match(ref_fit)
        norm_target_brand = normalize_match(target_brand)
        norm_target_fit = normalize_match(target_fit)
        norm_ref_size = safe_str(ref_size).upper()

        # FIND ANCHOR
        ref_data = None
        exact_fit_found = True
        
        for row in db:
            if (normalize_match(row.get('Brand')) == norm_ref_brand and 
                safe_str(row.get('Size Label')).upper() == norm_ref_size and 
                normalize_match(row.get('Fit Type')) == norm_ref_fit):
                ref_data = row
                break
                
        if not ref_data:
            for row in db:
                if (normalize_match(row.get('Brand')) == norm_ref_brand and 
                    safe_str(row.get('Size Label')).upper() == norm_ref_size):
                    ref_data = row
                    exact_fit_found = False
                    break
                    
        if not ref_data:
            raise ValueError(f"We don't have the exact data for {ref_brand.title()} {ref_size.upper()} yet.")

        min_chest = safe_float(ref_data.get('Chest Min (Inches)'))
        max_chest = safe_float(ref_data.get('Chest Max (Inches)'))
        if min_chest is None or max_chest is None:
            raise ValueError("Anchor chest data missing in CSV. We need numbers to do the math!")
            
        ref_true_inches = (min_chest + max_chest) / 2
        ref_fit_type = safe_str(ref_data.get('Fit Type')).lower()
        ref_shoulder = safe_float(ref_data.get('Shoulder (Inches)'))
        
        # FIND TARGET
        best_match = None
        smallest_penalty_score = 9999

        for row in db:
            t_brand_match = normalize_match(row.get('Brand')) == norm_target_brand
            t_fit_match = normalize_match(row.get('Fit Type')) == norm_target_fit

            if t_brand_match and t_fit_match:
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
            raise ValueError(f"Could not calculate sizes for {target_brand.title()} {target_fit.title()}.")

        # DEALBREAKER
        winner_t_min = safe_float(best_match.get('Chest Min (Inches)'))
        winner_t_max = safe_float(best_match.get('Chest Max (Inches)'))
        winner_target_inches = (winner_t_min + winner_t_max) / 2
        
        final_chest_diff = winner_target_inches - ref_true_inches
        
        if final_chest_diff < -1.5:
            raise ValueError(f"{target_brand.title()} does not manufacture a size large enough to match your {ref_brand.title()} {ref_size}.")

        # WARNINGS
        target_fit_type = safe_str(best_match.get('Fit Type')).lower()
        warning_message = "✨ Perfect Fit: The chest and style match your usual size beautifully."

        t_shoulder_val = safe_float(best_match.get('Shoulder (Inches)'))
        
        if ref_shoulder and t_shoulder_val:
            s_diff = t_shoulder_val - ref_shoulder
            if s_diff > 2.0:
                warning_message = f"👕 Style Note: This fits your chest, but features a trendy drop-shoulder. It will look intentionally baggier than your {ref_brand.title()} tee."
            elif s_diff < -1.0:
                warning_message = f"⚠️ Style Note: This fits your chest, but the shoulders are cut narrower. It might feel slightly snug."
            elif ref_fit_type in ["regular", "slim"] and target_fit_type in ["oversized", "boxy", "loose", "relaxed"]:
                warning_message = f"👕 Style Note: This fits your chest, but {target_brand.title()} cuts this specific shirt to be intentionally oversized."
        elif ref_fit_type in ["regular", "slim"] and target_fit_type in ["oversized", "boxy", "loose", "relaxed"]:
            warning_message = f"👕 Style Note: This fits your chest, but {target_brand.title()} designed this to be a baggy fit."
        elif ref_fit_type in ["oversized", "boxy", "loose", "relaxed"] and target_fit_type in ["slim", "regular"]:
            warning_message = f"⚠️ Style Note: This matches your measurements, but it's a slimmer cut. It will hug your body tighter than you are used to."

        if not exact_fit_found:
            fallback_label = safe_str(ref_data.get('Fit Type')).title()
            warning_message = f"💡 We based this on your {ref_brand.title()} ({fallback_label}) size. " + warning_message

        return {
            "status": "success",
            "target_brand": target_brand.title(),
            "recommended_size": safe_str(best_match.get('Size Label')).upper(),
            "warning": warning_message
        }

    except Exception as e:
        # This catches ANY Python crash and sends it directly to your widget safely
        print("CRASH LOG:", traceback.format_exc())
        raise HTTPException(status_code=400, detail=f"Engine Error: {str(e)}")


@app.get("/meta")
def get_metadata():
    brand_data = {}
    try:
        file_path = "vault_tshirt_database.csv"
        if not os.path.exists(file_path):
            for f in os.listdir("."):
                if f.lower() == "vault_tshirt_database.csv":
                    file_path = f
                    break
                    
        with open(file_path, mode='r', encoding='utf-8-sig') as file:
            reader = csv.DictReader(file)
            for row in reader:
                brand = safe_str(row.get('Brand')).title()
                fit = safe_str(row.get('Fit Type')).title()
                size = safe_str(row.get('Size Label')).upper()
                
                if not brand or not fit or not size:
                    continue
                    
                if brand not in brand_data:
                    brand_data[brand] = {}
                if fit not in brand_data[brand]:
                    brand_data[brand][fit] = []
                if size not in brand_data[brand][fit]:
                    brand_data[brand][fit].append(size)
    except Exception:
        pass
        
    return brand_data
