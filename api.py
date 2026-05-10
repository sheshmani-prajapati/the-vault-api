from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import json
from pydantic import BaseModel
from typing import Optional
from supabase import create_client, Client
import csv
import os
import re
import traceback

app = FastAPI(title="The Vault API - v3.5 (With Persistent Supabase)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- SUPABASE DATABASE SETUP ---
# We use environment variables so your API keys aren't exposed in Github
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client | None = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("✅ Supabase successfully connected.")
    except Exception as e:
        print("❌ Failed to initialize Supabase:", str(e))
else:
    print("⚠️ Warning: Supabase keys not found in environment variables.")

# --- TRACKING DATA MODEL ---
class TrackingPayload(BaseModel):
    event: str
    product_id: Optional[str] = None
    brand: Optional[str] = None
    result: Optional[str] = None
    uuid: Optional[str] = "anonymous" # Added the UUID from the Chrome Extension
    price: Optional[float] = 0.0

# --- NEW TRACKING ENDPOINT ---
@app.post("/track_event")
async def track_event(payload: TrackingPayload):
    if not supabase:
        print("Warning: Event received but Supabase is not connected.")
        return {"status": "error", "message": "Database not configured"}
        
    try:
        # 1. Upsert the User (Creates them if they are new, ignores if they exist)
        supabase.table("vault_users").upsert({
            "uuid": payload.uuid
        }).execute()

        # 2. Log the exact action they took to the tracking table
        supabase.table("tracking_events").insert({
            "user_uuid": payload.uuid,
            "brand_name": payload.brand or "Unknown Brand",
            "event_type": payload.event,
            "product_id": payload.product_id or "N/A",
            "result_text": payload.result or "N/A",
            "product_price": payload.price
        }).execute()
        
        return {"status": "success", "recorded_event": payload.event}
        
    except Exception as e:
        # We catch the error silently so it NEVER crashes the Shopify cart
        print(f"Vault Tracking Error: {str(e)}")
        return {"status": "error", "message": "Failed to log event"}

# --- HELPER FUNCTIONS ---
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

# --- EXISTING ENDPOINTS ---
@app.get("/get_fit")
def check_fit(ref_brand: str, ref_size: str, ref_fit: str, target_brand: str, target_fit: str = "Regular Fit"):
    try:
        db = []
        
        # 1. SMART FILE LOCATOR
        file_path = "vault_tshirt_database.csv"
        if not os.path.exists(file_path):
            for f in os.listdir("."):
                if f.lower() == "vault_tshirt_database.csv":
                    file_path = f
                    break

        if not os.path.exists(file_path):
            raise ValueError(f"Could not find the database file on Render.")

        # 2. SAFE FILE READER
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

        # --- NEW: THE COMMUNITY FLYWHEEL (CLOSED LOOP) ---
        community_offset_inches = 0.0
        community_insight = ""
        
        if supabase:
            try:
                # Fetch verified feedback for this specific brand
                res = supabase.table("tracking_events").select("result_text").eq("event_type", "verified_fit_feedback").ilike("brand_name", target_brand).execute()
                
                tight_count = sum(1 for row in res.data if "Too Tight" in row.get("result_text", ""))
                loose_count = sum(1 for row in res.data if "Too Loose" in row.get("result_text", ""))
                        
                # For every net "Too Tight" vote, shrink the brand's chest size by 0.1 inches.
                raw_offset = (tight_count - loose_count) * 0.10  
                
                # Cap the offset to prevent breaking the core engine
                community_offset_inches = max(-1.5, min(1.5, raw_offset))
                
                if community_offset_inches >= 0.4:
                    community_insight = " 🔄 Community Data: Buyers report this brand runs tight. We mathematically sized you up."
                elif community_offset_inches <= -0.4:
                    community_insight = " 🔄 Community Data: Buyers report this brand runs large. We mathematically sized you down."
            except Exception as e:
                print(f"Flywheel Error: {e}")
        # --------------------------------------------------

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
                
                # --- FLYWHEEL APPLIED HERE ---
                # Subtracting the offset makes the target shirt mathematically smaller (or larger) 
                # based entirely on your Supabase feedback.
                target_true_inches = ((t_min + t_max) / 2) - community_offset_inches
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
        winner_target_inches = ((winner_t_min + winner_t_max) / 2) - community_offset_inches
        
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
            
        # Append the Community Insight if the flywheel triggered a massive change
        if community_insight:
            warning_message += community_insight

        return {
            "status": "success",
            "target_brand": target_brand.title(),
            "recommended_size": safe_str(best_match.get('Size Label')).upper(),
            "warning": warning_message
        }

    except Exception as e:
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
    
# --- B2B ENTERPRISE DASHBOARD ---
@app.get("/dashboard", response_class=HTMLResponse)
async def b2b_dashboard(brand: str = "Snitch"):
    if not supabase:
        return "<h1>Dashboard Offline: Supabase not connected.</h1>"

    try:
        # 1. Fetch records to do smart filtering in Python
        res = supabase.table("tracking_events").select("*").limit(10000).execute()
        
        # 2. Smart String Matching (Merges "Bear House" and "THEBEARHOUSE" together)
        events = []
        target_clean = brand.replace(" ", "").lower()
        
        for e in res.data:
            db_brand = e.get("brand_name", "unknown").replace(" ", "").lower()
            # If the database says 'bearhouse' and the URL says 'thebearhouse', it matches!
            if db_brand in target_clean or target_clean in db_brand:
                events.append(e)

        # 3. Data Structures

        # 2. Data Structures
        funnel = {"widget_opened": 0, "size_calculated": 0, "add_to_cart_intent": 0, "purchase_completed": 0}
        feedback = {"Perfect": 0, "Too Tight": 0, "Too Loose": 0, "Returned": 0}
        brand_counts = {}
        
        # Perception Gap Trackers
        standard_sizes = ["S", "M", "L", "XL", "XXL"]
        guesses_tally = {s: 0 for s in standard_sizes}
        recs_tally = {s: 0 for s in standard_sizes}
        gap_matches = 0
        gap_corrections = 0
        
        # SKU Tracker
        sku_counts = {}

        # 3. Crunch the Real Data
        for e in events:
            evt = e.get("event_type")
            res_text = e.get("result_text", "")
            product_url = e.get("product_id", "")
            
            # Funnel aggregation
            if evt in funnel:
                funnel[evt] += 1
            
            # Feedback aggregation
            if evt == "verified_fit_feedback":
                clean_feedback = res_text.replace("User reported: ", "").strip()
                if clean_feedback in feedback:
                    feedback[clean_feedback] += 1
                else:
                    feedback[clean_feedback] = 1

            # Sizing, Perception Gap & Competitive Intel
            if evt == "size_calculated":
                # Regex to extract: Rec: XL (from Van Heusen) | Guessed: L
                rec_match = re.search(r'Rec:\s*([A-Za-z0-9]+)\s*\(from\s+(.+?)\)', res_text)
                guess_match = re.search(r'Guessed:\s*([A-Za-z0-9]+)', res_text)
                
                if rec_match:
                    vault_rec = rec_match.group(1).upper()
                    ref_b = rec_match.group(2).upper()
                    brand_counts[ref_b] = brand_counts.get(ref_b, 0) + 1
                    
                    if vault_rec in recs_tally:
                        recs_tally[vault_rec] += 1
                        
                    user_guess = guess_match.group(1).upper() if guess_match else "NONE"
                    
                    if user_guess in guesses_tally:
                        guesses_tally[user_guess] += 1
                        
                    # Calculate the Gap!
                    if user_guess != "NONE":
                        if user_guess == vault_rec:
                            gap_matches += 1
                        else:
                            gap_corrections += 1

            # SKU Extraction
            if evt in ["size_calculated", "add_to_cart_intent"] and "/" in product_url:
                clean_sku = product_url.split("/")[-1].split("?")[0].replace("-", " ").title()
                if len(clean_sku) > 3:
                    sku_counts[clean_sku] = sku_counts.get(clean_sku, 0) + 1

        # 4. Format Reference Brands
        total_refs = sum(brand_counts.values())
        reference_brands = []
        if total_refs > 0:
            for b, count in sorted(brand_counts.items(), key=lambda x: x[1], reverse=True)[:5]:
                reference_brands.append({"brand": b, "percentage": round((count/total_refs)*100, 1), "count": count})

        # 5. Format SKU Risk for the Table
        sku_risk_data = []
        for sku, count in sorted(sku_counts.items(), key=lambda x: x[1], reverse=True)[:6]:
            # Assigning a dynamic risk label based on high interactions
            risk_level = "critical" if count > 10 else ("high" if count > 5 else "medium")
            mismatch_pct = min(100, 40 + (count * 2)) # Mock formula for visual demo
            sku_risk_data.append({"name": sku, "category": "Apparel", "mismatch": mismatch_pct, "risk": risk_level})

        # 6. KPI & Perception Calculations
        vault_opens = funnel["widget_opened"]
        purchases = funnel["purchase_completed"]
        cvr = round((purchases / vault_opens) * 100, 1) if vault_opens > 0 else 0.0
        rto_saved = purchases - feedback.get("Returned", 0)
        
        total_guesses = gap_matches + gap_corrections
        wrong_pct = round((gap_corrections / total_guesses) * 100, 1) if total_guesses > 0 else 0
        perception_insight = f"{wrong_pct}% of your customers are guessing the wrong size before using The Vault." if wrong_pct > 0 else "Collecting initial perception data..."

        # 7. Construct the JSON Payload matching Claude's exact structure
        dashboard_data = {
            "brand_name": brand.upper(),
            "kpis": {
                "vault_opens": vault_opens,
                "add_to_bags": funnel["add_to_cart_intent"],
                "purchases": purchases,
                "cvr": cvr,
                "rto_saved_count": rto_saved
            },
            "funnel": {
                "labels": ["Opened Vault", "Calculated Size", "Added to Bag", "Purchased"],
                "data": [funnel["widget_opened"], funnel["size_calculated"], funnel["add_to_cart_intent"], purchases]
            },
            "perception_gap": {
                "insight": perception_insight,
                "labels": standard_sizes,
                "user_guesses": [guesses_tally[s] for s in standard_sizes],
                "vault_recommendations": [recs_tally[s] for s in standard_sizes]
            },
            "reference_brands": reference_brands,
            "fit_feedback": {
                "Perfect": feedback.get("Perfect", 0),
                "Too Tight": feedback.get("Too Tight", 0),
                "Too Loose": feedback.get("Too Loose", 0),
                "Returned": feedback.get("Returned", 0)
            },
            "sku_risk": sku_risk_data if len(sku_risk_data) > 0 else [
                {"name": "Awaiting Data", "category": "---", "mismatch": 0, "risk": "low"}
            ],
            
            # --- MOCK DATA (Leaving Claude's predictive AI features populated so the UI remains beautiful) ---
            "sparklines": {
                "vault_opens": [8900,9400,9100,10200,9800,10900,10400,11200,10800,11600,11900,12000,12200,12350,12450],
                "size_calc":   [6800,7100,7000,7600,7400,8000,7800,8500,8200,8800,9000,9200,9400,9600,9800],
                "atb":         [2800,3000,2900,3100,3000,3300,3200,3500,3400,3700,3900,3900,4000,4050,4120],
                "cvr":         [6.2,6.4,6.1,6.8,6.5,7.0,6.9,7.4,7.2,7.7,7.9,8.0,8.1,8.2,8.4],
                "rto":         [500,540,520,580,560,620,610,680,650,710,750,800,860,920,980]
            },
            "confidence_trend": {
                "days": ["May 1","May 4","May 7","May 10","May 13","May 16","May 19","May 22","May 25","May 28","May 30"],
                "pre_vault":  [48,50,49,52,51,53,52,54,53,55,52],
                "post_vault": [78,80,82,83,85,84,86,86,87,88,87]
            },
            "hour_data": [120,85,60,45,38,52,180,620,890,1100,980,1050,820,740,680,720,900,1300,1450,1380,1100,820,540,280],
            "cohort": {
                "labels": ["Week 1","Week 2","Week 3","Week 4","Week 5","Week 6","Week 7","Week 8"],
                "vault_users":    [100,88,80,76,74,72,70,68],
                "no_vault_users": [100,72,58,48,40,35,31,28]
            },
            "geo": [
                {"city":"Mumbai",    "anchor":"M", "vault_rec":"L",  "shift":+1, "users":2840},
                {"city":"Delhi",     "anchor":"M", "vault_rec":"L",  "shift":+1, "users":2210},
                {"city":"Bangalore", "anchor":"L", "vault_rec":"L",  "shift": 0, "users":1890},
                {"city":"Hyderabad", "anchor":"M", "vault_rec":"M",  "shift": 0, "users":1340},
                {"city":"Pune",      "anchor":"S", "vault_rec":"M",  "shift":+1, "users": 980},
                {"city":"Chennai",   "anchor":"L", "vault_rec":"XL", "shift":+1, "users": 740},
            ]
        }

        # 8. Load the HTML from a raw string (using exactly what Claude provided)
        html_template = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>The Vault · Intelligence Console · SNITCH</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=IBM+Plex+Mono:wght@300;400;500;600;700&family=Manrope:wght@300;400;500;600;700&display=swap" rel="stylesheet"/>
<style>
:root{
  --bg:       #050812;
  --bg-s:     #080d1a;
  --bg-card:  #0a1020;
  --bg-card2: #0d1428;
  --b:        rgba(79,107,255,0.12);
  --b2:       rgba(79,107,255,0.22);
  --teal:     #00d4aa;
  --teal2:    #00a884;
  --blue:     #4f6bff;
  --amber:    #ffb347;
  --rose:     #ff5e7d;
  --violet:   #b57bff;
  --sky:      #38bdf8;
  --t1:       #dde4f0;
  --t2:       #7a8aaa;
  --t3:       #3a4660;
}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{
  background:var(--bg);
  color:var(--t1);
  font-family:'Manrope',sans-serif;
  font-weight:400;
  overflow-x:hidden;
  min-height:100vh;
}
body::before{
  content:''; position:fixed;inset:0;
  background:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='400' height='400'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='400' height='400' filter='url(%23n)' opacity='0.025'/%3E%3C/svg%3E");
  pointer-events:none;z-index:0;opacity:0.4;
}
body::after{
  content:''; position:fixed;inset:0;
  background-image:linear-gradient(rgba(79,107,255,0.03) 1px,transparent 1px),linear-gradient(90deg,rgba(79,107,255,0.03) 1px,transparent 1px);
  background-size:48px 48px; pointer-events:none;z-index:0;
}
.g1,.g2,.g3,.g4{position:fixed;border-radius:50%;filter:blur(130px);pointer-events:none;z-index:0;}
.g1{width:700px;height:700px;background:#0038ff;opacity:.04;top:-250px;left:-150px;}
.g2{width:500px;height:500px;background:#00d4aa;opacity:.05;bottom:-200px;right:-100px;}
.g3{width:350px;height:350px;background:#b57bff;opacity:.04;top:50%;left:45%;}
.g4{width:250px;height:250px;background:#ff5e7d;opacity:.03;top:30%;right:10%;}
.z1{position:relative;z-index:1}
.font-display{font-family:'Bebas Neue',sans-serif;letter-spacing:.05em}
.font-mono{font-family:'IBM Plex Mono',monospace}
.card{
  background:var(--bg-card); border:1px solid var(--b); border-radius:14px; position:relative; overflow:hidden; transition:border-color .25s,transform .2s;
}
.card:hover{border-color:var(--b2)}
.card::before{
  content:'';position:absolute;top:0;left:0;right:0;height:1px;
  background:linear-gradient(90deg,transparent,rgba(79,107,255,.3),transparent);
}
.card-teal::after{content:'';position:absolute;top:0;left:0;width:3px;height:100%;background:linear-gradient(180deg,var(--teal),transparent)}
.card-blue::after{content:'';position:absolute;top:0;left:0;width:3px;height:100%;background:linear-gradient(180deg,var(--blue),transparent)}
.card-amber::after{content:'';position:absolute;top:0;left:0;width:3px;height:100%;background:linear-gradient(180deg,var(--amber),transparent)}
.card-rose::after{content:'';position:absolute;top:0;left:0;width:3px;height:100%;background:linear-gradient(180deg,var(--rose),transparent)}
.card-violet::after{content:'';position:absolute;top:0;left:0;width:3px;height:100%;background:linear-gradient(180deg,var(--violet),transparent)}
.kpi-num{ font-family:'Bebas Neue',sans-serif; font-size:clamp(2.2rem,4vw,3rem); line-height:1;letter-spacing:.04em; }
.lbl{ font-family:'IBM Plex Mono',monospace; font-size:.6rem;font-weight:600; letter-spacing:.18em;text-transform:uppercase; color:var(--t3); }
.badge{ display:inline-flex;align-items:center;gap:3px; padding:2px 9px;border-radius:99px; font-family:'IBM Plex Mono',monospace; font-size:.62rem;font-weight:600;letter-spacing:.04em; }
.badge-g{background:rgba(0,212,170,.1);color:var(--teal);border:1px solid rgba(0,212,170,.2)}
.badge-r{background:rgba(255,94,125,.1);color:var(--rose);border:1px solid rgba(255,94,125,.2)}
.badge-a{background:rgba(255,179,71,.1);color:var(--amber);border:1px solid rgba(255,179,71,.2)}
.badge-v{background:rgba(181,123,255,.1);color:var(--violet);border:1px solid rgba(181,123,255,.2)}
.f-track{height:44px;background:rgba(255,255,255,.03);border-radius:10px;overflow:hidden;position:relative}
.f-fill{height:100%;border-radius:10px;transition:width 1.6s cubic-bezier(.16,1,.3,1)}
.heat-cell{ aspect-ratio:1;border-radius:5px; display:flex;align-items:center;justify-content:center; font-family:'IBM Plex Mono',monospace;font-size:.6rem;font-weight:600; transition:transform .2s;cursor:default; }
.heat-cell:hover{transform:scale(1.12)}
.ref-row{border-radius:8px;padding:8px 10px;transition:background .2s;cursor:default}
.ref-row:hover{background:rgba(255,255,255,.025)}
.ref-bar-t{height:4px;background:rgba(255,255,255,.06);border-radius:99px;overflow:hidden;margin-top:6px}
.ref-bar-f{height:100%;border-radius:99px;transition:width 1.4s cubic-bezier(.16,1,.3,1)}
.stat-pill{ background:rgba(255,255,255,.04);border:1px solid var(--b); border-radius:8px;padding:10px 14px; }
.hr{border:none;border-top:1px solid var(--b);margin:0}
@keyframes fadeUp{from{opacity:0;transform:translateY(20px)}to{opacity:1;transform:translateY(0)}}
@keyframes fadeIn{from{opacity:0}to{opacity:1}}
@keyframes pulse2{0%,100%{opacity:1;box-shadow:0 0 0 0 rgba(0,212,170,.5)}50%{opacity:.8;box-shadow:0 0 0 6px rgba(0,212,170,0)}}
@keyframes ticker{from{transform:translateX(0)}to{transform:translateX(-50%)}}
.fu{animation:fadeUp .55s ease both} .fi{animation:fadeIn .5s ease both}
.d1{animation-delay:.06s} .d2{animation-delay:.12s} .d3{animation-delay:.18s} .d4{animation-delay:.24s} .d5{animation-delay:.30s} .d6{animation-delay:.36s} .d7{animation-delay:.42s}
.live-dot{width:7px;height:7px;border-radius:50%;background:var(--teal);animation:pulse2 2.2s infinite}
.ticker-outer{overflow:hidden;background:rgba(0,212,170,.05);border-top:1px solid rgba(0,212,170,.1);border-bottom:1px solid rgba(0,212,170,.1)}
.ticker-inner{display:flex;white-space:nowrap;animation:ticker 28s linear infinite}
.ticker-inner:hover{animation-play-state:paused}
.ticker-item{padding:7px 32px;font-family:'IBM Plex Mono',monospace;font-size:.65rem;color:var(--teal);letter-spacing:.05em}
.ticker-sep{color:var(--t3);padding:0 4px}
.sku-row{border-radius:7px;padding:9px 12px;transition:background .2s}
.sku-row:hover{background:rgba(255,255,255,.03)}
.risk-bar{height:3px;border-radius:99px;transition:width 1.4s cubic-bezier(.16,1,.3,1)}
::-webkit-scrollbar{width:4px}
::-webkit-scrollbar-track{background:var(--bg)}
::-webkit-scrollbar-thumb{background:rgba(79,107,255,.3);border-radius:99px}
</style>
</head>
<body>

<div class="g1"></div><div class="g2"></div><div class="g3"></div><div class="g4"></div>

<header class="z1 border-b fu" style="border-color:var(--b)">
  <div class="max-w-screen-2xl mx-auto px-6 py-4 flex items-center justify-between">
    <div class="flex items-center gap-4">
      <div class="relative">
        <div class="w-10 h-10 rounded-xl flex items-center justify-center text-white font-bold text-sm" style="background:linear-gradient(135deg,#4f6bff,#00d4aa)">V</div>
        <div class="absolute -bottom-1 -right-1 w-3 h-3 rounded-full live-dot"></div>
      </div>
      <div>
        <div class="flex items-center gap-2">
          <span class="font-display text-white tracking-widest text-lg">THE VAULT</span>
          <span class="badge badge-v">INTELLIGENCE CONSOLE</span>
        </div>
        <div class="lbl mt-0.5">Partner: <span style="color:var(--teal)" id="hdr-brand">SNITCH</span> · Updated just now</div>
      </div>
    </div>
    <div class="hidden md:flex items-center gap-2">
      <div class="flex items-center gap-2 px-3 py-1.5 rounded-lg" style="background:rgba(0,212,170,.08);border:1px solid rgba(0,212,170,.15)">
        <div class="live-dot"></div>
        <span class="font-mono text-xs" style="color:var(--teal)">LIVE</span>
      </div>
    </div>
  </div>
</header>

<div class="ticker-outer z1 fu d1"><div class="ticker-inner" id="ticker-content"></div></div>

<main class="z1 max-w-screen-2xl mx-auto px-6 py-6 space-y-5 pb-16">
  <div class="flex items-center gap-3 fu d1">
    <div class="lbl">Executive KPIs</div><div class="flex-1 hr"></div><div class="lbl" id="period-label">Live Data Stream</div>
  </div>

  <div class="grid grid-cols-2 lg:grid-cols-5 gap-3 fu d2">
    <div class="card card-blue p-5">
      <div class="lbl mb-3">Vault Opens</div>
      <div class="kpi-num text-white"><span class="cnt" data-target="__VO__" data-fmt="comma">0</span></div>
      <canvas id="sp1" height="36" class="mt-3"></canvas>
    </div>
    <div class="card card-teal p-5">
      <div class="lbl mb-3">Size Calculated</div>
      <div class="kpi-num" style="color:var(--teal)"><span class="cnt" data-target="__SC__" data-fmt="comma">0</span></div>
      <canvas id="sp2" height="36" class="mt-3"></canvas>
    </div>
    <div class="card card-amber p-5">
      <div class="lbl mb-3">Add-to-Bag Intent</div>
      <div class="kpi-num" style="color:var(--amber)"><span class="cnt" data-target="__ATB__" data-fmt="comma">0</span></div>
      <canvas id="sp3" height="36" class="mt-3"></canvas>
    </div>
    <div class="card card-violet p-5">
      <div class="lbl mb-3">Vault CVR</div>
      <div class="kpi-num" style="color:var(--violet)"><span class="cnt" data-target="__CVR__" data-fmt="float">0</span><span class="font-display text-2xl">%</span></div>
      <canvas id="sp4" height="36" class="mt-3"></canvas>
    </div>
    <div class="card card-rose p-5">
      <div class="lbl mb-3">RTO Intercepted</div>
      <div class="kpi-num" style="color:var(--rose)"><span class="cnt" data-target="__RTO__" data-fmt="comma">0</span></div>
      <canvas id="sp5" height="36" class="mt-3"></canvas>
    </div>
  </div>

  <div class="grid grid-cols-1 lg:grid-cols-5 gap-4">
    <div class="card card-blue p-6 lg:col-span-3 fu d4">
      <div class="flex items-start justify-between mb-5">
        <div>
          <div class="lbl mb-1">Intent Funnel</div>
          <div class="font-display text-white text-xl tracking-wide">Where Shoppers Fall Off</div>
        </div>
        <div class="text-right">
          <div class="lbl mb-1">End-to-End CVR</div>
          <div class="font-display text-2xl" style="color:var(--blue)" id="e2e-cvr">—</div>
        </div>
      </div>
      <div id="funnel-wrap" class="space-y-3"></div>
      <div class="mt-5 rounded-xl p-4 flex gap-3" style="background:rgba(79,107,255,.07);border:1px solid rgba(79,107,255,.15)">
        <p class="text-xs leading-relaxed" style="color:var(--t2)" id="funnel-insight-txt">Computing…</p>
      </div>
    </div>
    <div class="card card-amber p-6 lg:col-span-2 fu d5">
      <div class="lbl mb-1">Perception Gap Analysis</div>
      <div class="font-display text-white text-xl tracking-wide mb-1">What They Think vs. Reality</div>
      <div class="text-xs mb-4" style="color:var(--t2)" id="perception-insight-lbl">—</div>
      <canvas id="perceptionChart" height="220"></canvas>
      <div class="mt-4 flex gap-3">
        <div class="flex items-center gap-2"><div class="w-2.5 h-2.5 rounded-sm" style="background:rgba(79,107,255,.8)"></div><span class="text-xs font-mono" style="color:var(--t2)">Customer Guess</span></div>
        <div class="flex items-center gap-2"><div class="w-2.5 h-2.5 rounded-sm" style="background:rgba(0,212,170,.8)"></div><span class="text-xs font-mono" style="color:var(--t2)">Vault Rec.</span></div>
      </div>
    </div>
  </div>

  <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
    <div class="card card-teal p-6 fu d4">
      <div class="lbl mb-1">Competitive Intelligence</div>
      <div class="font-display text-white text-xl tracking-wide mb-1">Size Anchor Brands</div>
      <div class="text-xs mb-5" style="color:var(--t2)">The brands your customers trust as their mental size reference</div>
      <div id="ref-brands-wrap" class="space-y-3"></div>
    </div>
    <div class="card card-rose p-6 fu d5">
      <div class="lbl mb-1">Post-Purchase RTO Predictor</div>
      <div class="font-display text-white text-xl tracking-wide mb-4">Fit Feedback Loop</div>
      <div class="relative flex items-center justify-center">
        <canvas id="fitDoughnut" height="200"></canvas>
        <div class="absolute text-center pointer-events-none">
          <div class="font-display text-3xl" style="color:var(--teal)"><span id="fit-perfect-pct">0</span>%</div>
          <div class="lbl">Perfect Fit</div>
        </div>
      </div>
      <div id="fit-legend" class="mt-4 space-y-2"></div>
    </div>
    <div class="card card-violet p-6 fu d6">
      <div class="lbl mb-1">AI-Computed · Imagined Insight</div>
      <div class="font-display text-white text-xl tracking-wide mb-1">Size Confidence Trend</div>
      <div class="text-xs mb-4" style="color:var(--t2)">Daily score: how certain customers are about their size before & after Vault</div>
      <canvas id="confTrend" height="190"></canvas>
    </div>
  </div>

  <div class="flex items-center gap-3 fu d5 mt-2">
    <div class="lbl">AI-Imagined Intelligence Layers</div>
    <div class="flex-1 hr"></div>
    <div class="badge badge-v">PREDICTIVE · COMPUTED</div>
  </div>

  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
    <div class="card card-violet p-6 fu d6">
      <div class="lbl mb-1">Engagement Intelligence</div>
      <div class="font-display text-white text-xl tracking-wide mb-1">Peak Intent Windows</div>
      <div class="text-xs mb-5" style="color:var(--t2)">Vault opens by hour — plan your sale drops around these peaks</div>
      <div id="heatmap-grid" class="grid gap-1.5" style="grid-template-columns:repeat(12,1fr)"></div>
      <div class="mt-4"><canvas id="hourChart" height="120"></canvas></div>
    </div>
    <div class="card card-rose p-6 fu d7">
      <div class="flex items-start justify-between mb-5">
        <div>
          <div class="lbl mb-1">SKU Intelligence</div>
          <div class="font-display text-white text-xl tracking-wide">Size Risk by Category</div>
        </div>
        <div class="badge badge-r">LIVE DATA</div>
      </div>
      <div class="space-y-1" id="sku-table"></div>
    </div>
  </div>
</main>

<script>
const D = __INJECT_DATA__;

Chart.defaults.color = '#3a4660';
Chart.defaults.borderColor = 'rgba(79,107,255,0.1)';
Chart.defaults.font.family = "'IBM Plex Mono',monospace";
Chart.defaults.font.size = 10;
const C = { teal:'#00d4aa', teal2:'#00a884', blue:'#4f6bff', amber:'#ffb347', rose:'#ff5e7d', violet:'#b57bff', sky:'#38bdf8', t2:'#7a8aaa', t3:'#3a4660' };

function animCount(el, target, fmt='comma', dur=1300){
  if(!el) return;
  const s = performance.now();
  (function step(now){
    const p = Math.min((now-s)/dur, 1);
    const v = (1-Math.pow(1-p,3))*target;
    if(fmt==='comma') el.textContent = Math.round(v).toLocaleString('en-IN');
    else if(fmt==='float') el.textContent = v.toFixed(1);
    else el.textContent = Math.round(v);
    if(p<1) requestAnimationFrame(step);
  })(s);
}

function sparkline(id, data, color){
  const ctx = document.getElementById(id);
  if(!ctx) return;
  new Chart(ctx,{
    type:'line', data:{ labels:data.map((_,i)=>i), datasets:[{data, borderColor:color, borderWidth:1.5, pointRadius:0, fill:true,
      backgroundColor:ctx2=>{const g=ctx2.chart.ctx.createLinearGradient(0,0,0,40);g.addColorStop(0,color+'44');g.addColorStop(1,color+'00');return g}, tension:0.4}] },
    options:{ responsive:true, animation:{duration:1200}, plugins:{legend:{display:false},tooltip:{enabled:false}}, scales:{x:{display:false},y:{display:false}} }
  });
}

function initTicker(){
  const items = [`VAULT OPENS: ${D.kpis.vault_opens}`, `RTO SAVED: ${D.kpis.rto_saved_count}`, `CVR: ${D.kpis.cvr}%`];
  const tpl = items.map(t=>`<span class="ticker-item">${t}<span class="ticker-sep">·</span></span>`).join('');
  document.getElementById('ticker-content').innerHTML = tpl+tpl+tpl+tpl;
}

function initKPIs(){
  document.getElementById('hdr-brand').textContent = D.brand_name;
  document.querySelectorAll('.cnt').forEach(el=>{
    const t = parseFloat(el.dataset.target);
    const f = el.dataset.fmt;
    animCount(el, t, f);
  });
  sparkline('sp1', D.sparklines.vault_opens, C.blue);
  sparkline('sp2', D.sparklines.size_calc, C.teal);
  sparkline('sp3', D.sparklines.atb, C.amber);
  sparkline('sp4', D.sparklines.cvr, C.violet);
  sparkline('sp5', D.sparklines.rto, C.rose);
}

function initFunnel(){
  const f = D.funnel;
  const max = Math.max(1, f.data[0]);
  const colors = [C.blue, C.teal, C.amber, C.violet];
  const icons = ['◎','◈','◆','★'];
  const wrap = document.getElementById('funnel-wrap');
  wrap.innerHTML='';
  f.labels.forEach((lbl,i)=>{
    const val = f.data[i];
    const w = (val/max*100).toFixed(1);
    const drop = (i>0 && f.data[i-1]>0) ? ((1-val/f.data[i-1])*100).toFixed(1) : null;
    const div=document.createElement('div');
    div.innerHTML=`
      <div class="flex items-center gap-3 mb-1.5">
        <span class="font-mono text-xs" style="color:${colors[i]}">${icons[i]}</span>
        <span class="flex-1 text-sm" style="color:var(--t2)">${lbl}</span>
        <span class="font-mono font-semibold text-sm" style="color:${colors[i]}">${val.toLocaleString('en-IN')}</span>
        ${drop?`<span class="font-mono text-xs w-16 text-right" style="color:var(--rose)">−${drop}%</span>`:`<span class="w-16"></span>`}
      </div>
      <div class="f-track"><div class="f-fill" data-w="${w}" style="width:0%;background:linear-gradient(90deg,${colors[i]}cc,${colors[i]}44)"></div></div>
    `;
    wrap.appendChild(div);
  });
  setTimeout(()=>document.querySelectorAll('.f-fill').forEach(el=>{el.style.width=el.dataset.w+'%'}), 200);
  const eff = (D.funnel.data[3]/max*100).toFixed(1);
  document.getElementById('e2e-cvr').textContent = eff+'%';
  document.getElementById('funnel-insight-txt').textContent = `End-to-End Vault Conversion is ${eff}%. Live data pipeline active.`;
}

function initPerception(){
  document.getElementById('perception-insight-lbl').textContent = D.perception_gap.insight;
  new Chart(document.getElementById('perceptionChart'),{
    type:'bar',
    data:{ labels: D.perception_gap.labels, datasets:[
        { label:'Customer Guess', data:D.perception_gap.user_guesses, backgroundColor: C.blue+'55', borderColor:C.blue, borderWidth:1.5, borderRadius:5 },
        { label:'Vault Rec.', data:D.perception_gap.vault_recommendations, backgroundColor: C.teal+'55', borderColor:C.teal, borderWidth:1.5, borderRadius:5 }
      ]},
    options:{ responsive:true, animation:{duration:1300}, plugins:{ legend:{display:false}, tooltip:{ backgroundColor:'#0a1020', borderColor:C.blue+'44', borderWidth:1, padding:10 } }, scales:{ x:{grid:{display:false}, ticks:{color:C.t3}}, y:{grid:{color:'rgba(79,107,255,0.06)'}, ticks:{color:C.t3}} } }
  });
}

function initRefBrands(){
  const brands = D.reference_brands;
  const wrap = document.getElementById('ref-brands-wrap');
  const bColors = [C.teal,C.blue,C.amber,C.violet,C.rose];
  if(brands.length===0) wrap.innerHTML = '<div class="text-sm" style="color:var(--t2)">Awaiting data...</div>';
  brands.forEach((b,i)=>{
    const c = bColors[i%bColors.length];
    const row=document.createElement('div');
    row.className='ref-row';
    row.innerHTML=`
      <div class="flex items-center justify-between mb-1.5">
        <div class="flex items-center gap-2"><div class="w-2 h-2 rounded-full" style="background:${c}"></div><span class="text-sm font-semibold text-white">${b.brand}</span></div>
        <div class="flex items-center gap-3"><span class="font-mono text-xs" style="color:var(--t2)">${b.percentage}%</span></div>
      </div>
      <div class="ref-bar-t"><div class="ref-bar-f" style="width:0%;background:${c}88" data-w="${b.percentage}"></div></div>
    `;
    wrap.appendChild(row);
  });
  setTimeout(()=>document.querySelectorAll('.ref-bar-f').forEach(el=>{el.style.width=el.dataset.w+'%'}),300);
}

function initFitDoughnut(){
  const fb = D.fit_feedback;
  const labels = Object.keys(fb);
  const values = Object.values(fb);
  const total = values.reduce((a,b)=>a+b, 0);
  const perfPct = total>0 ? (fb.Perfect/total*100) : 0;
  const colors = [C.teal, C.rose, C.amber, '#475569'];
  new Chart(document.getElementById('fitDoughnut'),{
    type:'doughnut',
    data:{ labels, datasets:[{ data:values.length?values:[1], backgroundColor:colors.map(c=>c+'bb'), borderColor:colors, borderWidth:1.5, hoverOffset:8 }] },
    options:{ cutout:'70%', animation:{duration:1200}, plugins:{ legend:{display:false}, tooltip:{ backgroundColor:'#0a1020', borderColor:C.rose+'44', borderWidth:1, padding:10 } } }
  });
  animCount(document.getElementById('fit-perfect-pct'), perfPct, 'float');
  const leg = document.getElementById('fit-legend');
  labels.forEach((lbl,i)=>{
    const v = total>0 ? ((values[i]/total)*100).toFixed(1) : 0;
    const row=document.createElement('div');
    row.className='flex items-center justify-between';
    row.innerHTML=`<div class="flex items-center gap-2"><div class="w-2 h-2 rounded-full" style="background:${colors[i]}"></div><span class="text-xs" style="color:var(--t2)">${lbl}</span></div><span class="font-mono font-bold text-xs text-white">${v}%</span>`;
    leg.appendChild(row);
  });
}

function initConfTrend(){
  const ct = D.confidence_trend;
  new Chart(document.getElementById('confTrend'),{
    type:'line',
    data:{ labels: ct.days, datasets:[
        { label:'Pre-Vault', data:ct.pre_vault, borderColor:C.rose, borderWidth:1.5, pointRadius:0, fill:false, tension:0.4, borderDash:[4,3] },
        { label:'Post-Vault', data:ct.post_vault, borderColor:C.teal, borderWidth:2, pointRadius:3, pointBackgroundColor:C.teal, fill:true, backgroundColor:ctx=>{const g=ctx.chart.ctx.createLinearGradient(0,0,0,200);g.addColorStop(0,C.teal+'33');g.addColorStop(1,C.teal+'00');return g}, tension:0.4 }
      ]},
    options:{ responsive:true, animation:{duration:1400}, plugins:{ legend:{display:true, position:'top', align:'end', labels:{boxWidth:8,boxHeight:8,padding:12,color:C.t2} }, tooltip:{ backgroundColor:'#0a1020', borderColor:C.violet+'44', borderWidth:1, padding:10 } }, scales:{ x:{grid:{display:false}, ticks:{color:C.t3, maxRotation:0}}, y:{grid:{color:'rgba(79,107,255,0.06)'}, ticks:{color:C.t3}, min:30, max:100} } }
  });
}

function initHeatmap(){
  const h = D.hour_data;
  const max = Math.max(...h);
  const grid = document.getElementById('heatmap-grid');
  grid.innerHTML='';
  for(let i=0;i<24;i+=2){
    const v=h[i]; const pct=v/max;
    const cell=document.createElement('div');
    cell.className='heat-cell'; cell.style.background=`rgba(181,123,255,${0.08+pct*0.92})`; cell.style.color=pct>0.5?'white':'var(--t3)'; cell.textContent=i<12 ? (i===0?'12':i)+'a' : (i===12?'12':i-12)+'p';
    grid.appendChild(cell);
  }
  new Chart(document.getElementById('hourChart'),{
    type:'bar', data:{ labels: Array.from({length:24},(_,i)=>i), datasets:[{ data:h, backgroundColor:h.map(v=>v/max>0.7?C.violet:C.violet+'44'), borderRadius:3 }] },
    options:{ responsive:true, animation:{duration:1400}, plugins:{ legend:{display:false}}, scales:{ x:{display:false}, y:{display:false} } }
  });
}

function initSKURisk(){
  const skus = D.sku_risk;
  const table = document.getElementById('sku-table');
  const riskMap = { critical:{color:C.rose,label:'CRITICAL'}, high:{color:C.amber,label:'HIGH'}, medium:{color:C.blue,label:'MEDIUM'}, low:{color:C.teal,label:'LOW'} };
  if(skus.length===0) table.innerHTML = '<div class="text-sm" style="color:var(--t2)">Awaiting data...</div>';
  skus.forEach(s=>{
    const r=riskMap[s.risk];
    const row=document.createElement('div');
    row.className='sku-row';
    row.innerHTML=`
      <div class="flex items-center justify-between mb-1">
        <div><span class="text-sm font-semibold text-white">${s.name}</span></div>
        <div class="flex items-center gap-2"><span class="font-mono font-bold text-sm" style="color:${r.color}">${s.mismatch}%</span><span class="badge" style="background:${r.color}18;color:${r.color};border:1px solid ${r.color}33;font-size:.55rem">${r.label}</span></div>
      </div>
      <div class="h-0.5 rounded-full overflow-hidden" style="background:rgba(255,255,255,.05)"><div class="risk-bar" style="width:0%;background:${r.color}" data-w="${s.mismatch}"></div></div>
    `;
    table.appendChild(row);
  });
  setTimeout(()=>document.querySelectorAll('.risk-bar').forEach(el=>{el.style.width=el.dataset.w+'%'}),400);
}

document.addEventListener('DOMContentLoaded',()=>{
  // Replace the placeholder target values with real data before initializing animations
  document.querySelector('[data-target="__VO__"]').dataset.target = D.kpis.vault_opens;
  document.querySelector('[data-target="__SC__"]').dataset.target = D.funnel.data[1];
  document.querySelector('[data-target="__ATB__"]').dataset.target = D.kpis.add_to_bags;
  document.querySelector('[data-target="__CVR__"]').dataset.target = D.kpis.cvr;
  document.querySelector('[data-target="__RTO__"]').dataset.target = D.kpis.rto_saved_count;

  initTicker(); initKPIs(); initFunnel(); initPerception(); initRefBrands(); initFitDoughnut(); initConfTrend(); initHeatmap(); initSKURisk();
});
</script>
</body>
</html>
        """
        
        # 9. Safely inject the JSON string into the HTML
        final_html = html_template.replace("__INJECT_DATA__", json.dumps(dashboard_data))
        
        return HTMLResponse(content=final_html, status_code=200)

    except Exception as e:
        return f"<h1 style='color:white; background:black; padding:20px; font-family:monospace;'>Python Error: {str(e)}</h1>"

# --- GOD MODE MASTER DASHBOARD (INTERNAL ONLY) ---
@app.get("/admin-god-mode", response_class=HTMLResponse)
async def admin_god_mode():
    if not supabase:
        return "<h1>System Offline: Supabase not connected.</h1>"

    try:
        # 1. Fetch EVERYTHING (No brand filtering)
        res = supabase.table("tracking_events").select("*").limit(50000).execute()
        events = res.data

        # 2. Master Aggregators
        global_funnel = {"widget_opened": 0, "size_calculated": 0, "add_to_cart_intent": 0, "purchase_completed": 0}
        global_feedback = {"Perfect": 0, "Too Tight": 0, "Too Loose": 0, "Returned": 0}
        brand_stats = {}
        anchor_counts = {}
        gmv_total = 0.0
        
        # Perception & Cascade Trackers
        standard_sizes = ["XS", "S", "M", "L", "XL", "XXL"]
        anchored_sizes = {s: 0 for s in standard_sizes}
        corrected_sizes = {s: 0 for s in standard_sizes}
        gap_matches = 0
        gap_corrections = 0

        # 3. Process the Ecosystem Data
        for e in events:
            evt = e.get("event_type")
            res_text = e.get("result_text", "")
            brand = e.get("brand_name", "Unknown").upper()
            price = safe_float(e.get("product_price", 0)) or 0.0

            # Initialize brand in leaderboard tracker
            if brand not in brand_stats:
                brand_stats[brand] = {"opens": 0, "purchases": 0, "cvr": 0.0}

            # Funnel & GMV
            if evt in global_funnel:
                global_funnel[evt] += 1
            if evt == "widget_opened":
                brand_stats[brand]["opens"] += 1
            if evt == "purchase_completed":
                brand_stats[brand]["purchases"] += 1
                gmv_total += price

            # Global Feedback
            if evt == "verified_fit_feedback":
                clean_feedback = res_text.replace("User reported: ", "").strip()
                if clean_feedback in global_feedback:
                    global_feedback[clean_feedback] += 1
                else:
                    global_feedback[clean_feedback] = 1

            # Global Algorithm & Perception
            if evt == "size_calculated":
                rec_match = re.search(r'Rec:\s*([A-Za-z0-9]+)\s*\(from\s+(.+?)\)', res_text)
                guess_match = re.search(r'Guessed:\s*([A-Za-z0-9]+)', res_text)
                
                if rec_match:
                    vault_rec = rec_match.group(1).upper()
                    ref_b = rec_match.group(2).upper()
                    anchor_counts[ref_b] = anchor_counts.get(ref_b, 0) + 1
                    
                    if vault_rec in corrected_sizes:
                        corrected_sizes[vault_rec] += 1
                        
                    user_guess = guess_match.group(1).upper() if guess_match else "NONE"
                    if user_guess in anchored_sizes:
                        anchored_sizes[user_guess] += 1
                        
                    if user_guess != "NONE":
                        if user_guess == vault_rec:
                            gap_matches += 1
                        else:
                            gap_corrections += 1

        # 4. Compute Master KPIs
        total_opens = global_funnel["widget_opened"]
        total_purchases = global_funnel["purchase_completed"]
        global_cvr = round((total_purchases / total_opens) * 100, 1) if total_opens > 0 else 0.0
        rto_saved = total_purchases - global_feedback.get("Returned", 0)

        # 5. Format Leaderboard
        leaderboard = []
        live_brands_count = 0
        for b_name, stats in brand_stats.items():
            if stats["opens"] > 0:
                live_brands_count += 1
                cvr = round((stats["purchases"] / stats["opens"]) * 100, 1)
                status = "Healthy" if cvr >= 10 else ("Warning" if cvr >= 5 else "Critical")
                leaderboard.append({
                    "brand": b_name,
                    "opens": stats["opens"],
                    "cvr": cvr,
                    "status": status,
                    "trend": [cvr-1, cvr, cvr+0.5, cvr], # Mocked trendline for UI
                })
        leaderboard.sort(key=lambda x: x["opens"], reverse=True)

        # 6. Format Anchors
        total_refs = sum(anchor_counts.values())
        anchor_matrix = []
        if total_refs > 0:
            for b, count in sorted(anchor_counts.items(), key=lambda x: x[1], reverse=True)[:6]:
                anchor_matrix.append({
                    "brand": b, 
                    "market_share": round((count/total_refs)*100, 1), 
                    "trend": "up" # Mocked for UI
                })

        # 7. Correction Rate
        total_guesses = gap_matches + gap_corrections
        correction_rate = round((gap_corrections / total_guesses) * 100, 1) if total_guesses > 0 else 0

        # 8. Algorithm Accuracy
        total_fb = sum(global_feedback.values())
        perfect_pct = round((global_feedback.get("Perfect", 0) / total_fb) * 100, 1) if total_fb > 0 else 100.0

        # 9. Build Master Data Object
        master_data = {
            "system_health": {
                "status": "Operational",
                "live_brands": live_brands_count,
                "total_vault_opens": total_opens,
                "global_cvr": global_cvr,
                "total_rto_saved": max(0, rto_saved),
                "gmv_influenced_usd": round(gmv_total, 2)
            },
            "brand_leaderboard": leaderboard[:6], # Top 6
            "algorithm_accuracy": {
                "insight": f"Algorithm performing at {perfect_pct}% accuracy globally. Data flow stabilized.",
                "perfect": global_feedback.get("Perfect", 0),
                "too_tight": global_feedback.get("Too Tight", 0),
                "too_loose": global_feedback.get("Too Loose", 0),
                "returned": global_feedback.get("Returned", 0),
                "weekly_accuracy": [91.2, 91.8, 92.1, perfect_pct, perfect_pct, perfect_pct, perfect_pct]
            },
            "universal_anchor_matrix": anchor_matrix,
            "global_perception_gap": {
                "total_corrections": gap_corrections,
                "correction_rate_pct": correction_rate
            },
            "correction_cascade": {
                "sizes": standard_sizes,
                "anchored": [anchored_sizes[s] for s in standard_sizes],
                "corrected": [corrected_sizes[s] for s in standard_sizes]
            },
            
            # --- RETAINED MOCK DATA TO KEEP UI BEAUTIFUL ---
            "network_traffic": { "labels": ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"], "api_calls": [12,15,14,18,22,35,31] },
            "brand_health_radar": {
                "labels": ["CVR Score","Algo Accuracy","Engagement","RTO Shield","Growth","Stability"],
                "brands": [ {"name": "NETWORK AVG", "color": "#00ff88", "data": [85, 92, 88, 85, 91, 90]} ]
            },
            "hourly_activity": [ 380,240,160,120,105,160,480,1640,2980,3820,4420,4800,4300,3820,3600,3700,4800,6800,8400,7900,6600,5200,3100,1280 ],
            "api_services": [
                {"name":"Size Engine", "status":"operational", "latency":42},
                {"name":"Brand API", "status":"operational", "latency":56},
                {"name":"Analytics Pipeline", "status":"operational", "latency":32},
            ],
            "revenue_at_risk": [],
            "alert_feed": [
                {"time":"LIVE", "level":"info", "msg":f"Network synced. Processed {total_opens} sizing events."}
            ],
            "sparklines": {
                "brands": [live_brands_count]*7, "opens": [total_opens]*7, "cvr": [global_cvr]*7, 
                "rto": [rto_saved]*7, "gmv": [gmv_total]*7
            }
        }

        # 10. Load the HTML
        html_template = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>THE VAULT // GOD MODE // MASTER COMMAND CENTER</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<link href="https://fonts.googleapis.com/css2?family=Rajdhani:wght@400;500;600;700&family=JetBrains+Mono:wght@300;400;500;600;700&family=Barlow+Condensed:wght@300;400;500;600;700&family=DM+Sans:wght@300;400;500&display=swap" rel="stylesheet"/>
<style>
:root{
  --bg:       #020812;
  --bg-s:     #06101e;
  --bg-card:  #091626;
  --bg-card2: #0c1c30;
  --b:        rgba(0,184,255,0.09);
  --b2:       rgba(0,184,255,0.22);
  --b-green:  rgba(0,255,136,0.2);
  --green:    #00ff88;
  --green2:   #00cc6a;
  --cyan:     #00b8ff;
  --cyan2:    #0090cc;
  --purple:   #8b5cf6;
  --purple2:  #6d28d9;
  --amber:    #f59e0b;
  --rose:     #ff3f60;
  --t1:       #d0e4f4;
  --t2:       #3d6a8a;
  --t3:       #1a3348;
}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{
  background:var(--bg);
  color:var(--t1);
  font-family:'DM Sans',sans-serif;
  font-weight:300;
  min-height:100vh;
  overflow-x:hidden;
  cursor:default;
}
body::before{
  content:''; position:fixed;inset:0;
  background:repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0,0,0,0.18) 2px, rgba(0,0,0,0.18) 4px);
  pointer-events:none;z-index:9999;
}
body::after{
  content:''; position:fixed;inset:0;
  background-image: linear-gradient(rgba(0,184,255,0.025) 1px,transparent 1px), linear-gradient(90deg,rgba(0,184,255,0.025) 1px,transparent 1px);
  background-size:60px 60px; pointer-events:none;z-index:0;
}
.glow-blob{position:fixed;border-radius:50%;filter:blur(140px);pointer-events:none;z-index:0;}
.gb1{width:800px;height:800px;background:#001aff;opacity:.025;top:-300px;left:-200px;}
.gb2{width:600px;height:600px;background:#00ff88;opacity:.03;bottom:-200px;right:-100px;}
.gb3{width:400px;height:400px;background:#8b5cf6;opacity:.025;top:40%;left:40%;}
.z1{position:relative;z-index:1}
.font-raj{font-family:'Rajdhani',sans-serif}
.font-mono{font-family:'JetBrains Mono',monospace}
.font-cond{font-family:'Barlow Condensed',sans-serif}
.card{ background:var(--bg-card); border:1px solid var(--b); border-radius:8px; position:relative;overflow:hidden; transition:border-color .2s,box-shadow .2s; }
.card:hover{border-color:var(--b2);box-shadow:0 0 24px rgba(0,184,255,0.06)}
.card::before{ content:'';position:absolute;top:0;left:0;right:0;height:1px; background:linear-gradient(90deg,transparent,rgba(0,184,255,.25),transparent); }
.card-green{border-color:rgba(0,255,136,0.12)}
.card-green::before{background:linear-gradient(90deg,transparent,rgba(0,255,136,.3),transparent)}
.card-green:hover{border-color:rgba(0,255,136,.3);box-shadow:0 0 24px rgba(0,255,136,.05)}
.card-rose{border-color:rgba(255,63,96,0.15)}
.card-rose::before{background:linear-gradient(90deg,transparent,rgba(255,63,96,.3),transparent)}
.card-amber{border-color:rgba(245,158,11,0.15)}
.card-amber::before{background:linear-gradient(90deg,transparent,rgba(245,158,11,.3),transparent)}
.stripe::after{ content:'';position:absolute;top:0;left:0;width:2px;height:100%; }
.stripe-green::after{background:linear-gradient(180deg,var(--green),transparent)}
.stripe-cyan::after{background:linear-gradient(180deg,var(--cyan),transparent)}
.stripe-purple::after{background:linear-gradient(180deg,var(--purple),transparent)}
.stripe-amber::after{background:linear-gradient(180deg,var(--amber),transparent)}
.stripe-rose::after{background:linear-gradient(180deg,var(--rose),transparent)}
.kpi-val{ font-family:'JetBrains Mono',monospace; font-weight:700;font-size:2.4rem; line-height:1;letter-spacing:-.02em; }
.section-tag{ font-family:'Barlow Condensed',sans-serif; font-size:.62rem;font-weight:600; letter-spacing:.22em;text-transform:uppercase; color:var(--t2); }
.mono-sm{font-family:'JetBrains Mono',monospace;font-size:.72rem}
.badge{ display:inline-flex;align-items:center;gap:4px; padding:2px 8px;border-radius:4px; font-family:'JetBrains Mono',monospace; font-size:.58rem;font-weight:600;letter-spacing:.08em; }
.badge-green{background:rgba(0,255,136,.1);color:var(--green);border:1px solid rgba(0,255,136,.2)}
.badge-red{background:rgba(255,63,96,.1);color:var(--rose);border:1px solid rgba(255,63,96,.2)}
.badge-amber{background:rgba(245,158,11,.1);color:var(--amber);border:1px solid rgba(245,158,11,.2)}
.badge-blue{background:rgba(0,184,255,.1);color:var(--cyan);border:1px solid rgba(0,184,255,.2)}
.badge-purple{background:rgba(139,92,246,.1);color:var(--purple);border:1px solid rgba(139,92,246,.2)}
.badge-gray{background:rgba(61,106,138,.1);color:var(--t2);border:1px solid rgba(61,106,138,.2)}
@keyframes fadeUp{from{opacity:0;transform:translateY(16px)}to{opacity:1;transform:translateY(0)}}
@keyframes greenPulse{0%,100%{box-shadow:0 0 0 0 rgba(0,255,136,.5)}50%{box-shadow:0 0 0 6px rgba(0,255,136,0)}}
.fu{animation:fadeUp .55s ease both}
.d1{animation-delay:.05s}.d2{animation-delay:.1s}.d3{animation-delay:.15s}.d4{animation-delay:.2s}.d5{animation-delay:.25s}.d6{animation-delay:.3s}.d7{animation-delay:.35s}.d8{animation-delay:.4s}.d9{animation-delay:.45s}
.blink-dot{ width:8px;height:8px;border-radius:50%;background:var(--green); animation:greenPulse 2s infinite; }
.ticker-outer{overflow:hidden;background:rgba(0,255,136,.04);border-top:1px solid rgba(0,255,136,.08);border-bottom:1px solid rgba(0,255,136,.08)}
.ticker-inner{display:flex;white-space:nowrap;animation:ticker 32s linear infinite}
.tick-item{padding:6px 28px;font-family:'JetBrains Mono',monospace;font-size:.62rem;color:var(--green);letter-spacing:.06em}
.tick-sep{color:var(--t3);padding:0 2px}
.status-dot{width:6px;height:6px;border-radius:50%;flex-shrink:0;display:inline-block}
.alert-row{padding:8px 12px;border-radius:6px;border-left:2px solid transparent;margin-bottom:4px;transition:background .2s}
.alert-critical{border-color:var(--rose);background:rgba(255,63,96,.04)}
.alert-warning{border-color:var(--amber);background:rgba(245,158,11,.04)}
.alert-info{border-color:var(--cyan);background:rgba(0,184,255,.04)}
.lb-row{ display:grid;grid-template-columns:2rem 1fr 8rem 7rem 7rem 9rem; align-items:center;gap:12px; padding:10px 16px;border-bottom:1px solid var(--b); transition:background .15s; }
.svc-row{display:flex;align-items:center;justify-content:space-between;padding:7px 0;border-bottom:1px solid var(--b)}
.risk-track{height:3px;background:rgba(255,255,255,.06);border-radius:99px;overflow:hidden;margin-top:5px}
.risk-fill{height:100%;border-radius:99px;transition:width 1.4s cubic-bezier(.16,1,.3,1)}
.classif{ font-family:'Barlow Condensed',sans-serif;font-size:.6rem;font-weight:700; letter-spacing:.25em;text-transform:uppercase;padding:2px 8px; border:1px solid rgba(255,63,96,.4);color:rgba(255,63,96,.7);border-radius:3px; }
::-webkit-scrollbar{width:4px}
::-webkit-scrollbar-track{background:var(--bg)}
::-webkit-scrollbar-thumb{background:rgba(0,184,255,.2);border-radius:99px}
.lat-bar-track{height:4px;background:rgba(255,255,255,.06);border-radius:99px;overflow:hidden;flex:1}
.lat-bar-fill{height:100%;border-radius:99px;transition:width 1.2s ease}
.period-btn{ font-family:'JetBrains Mono',monospace;font-size:.62rem; padding:5px 12px;border-radius:4px; background:rgba(255,255,255,.04);border:1px solid var(--b); color:var(--t2);transition:all .2s;cursor:pointer; }
.period-btn.act,.period-btn:hover{background:rgba(0,184,255,.12);border-color:rgba(0,184,255,.3);color:var(--t1)}
</style>
</head>
<body>

<div class="glow-blob gb1"></div>
<div class="glow-blob gb2"></div>
<div class="glow-blob gb3"></div>

<div class="z1 max-w-screen-2xl mx-auto px-4 pb-16">

<nav class="fu flex items-center justify-between py-3 border-b" style="border-color:var(--b)">
  <div class="flex items-center gap-4">
    <div class="flex items-center gap-2">
      <div class="classif">TOP SECRET</div>
      <div class="classif" style="border-color:rgba(0,184,255,.4);color:rgba(0,184,255,.7)">INTERNAL ONLY</div>
    </div>
    <div class="w-px h-6" style="background:var(--b2)"></div>
    <div class="flex items-center gap-2.5">
      <div class="w-8 h-8 rounded-lg flex items-center justify-center font-bold text-sm" style="background:linear-gradient(135deg,var(--cyan),var(--green));color:#020812">V</div>
      <div>
        <div class="font-raj font-bold text-white tracking-wider text-base">THE VAULT</div>
        <div class="section-tag" style="color:var(--green);letter-spacing:.3em">GOD MODE ACTIVE</div>
      </div>
    </div>
  </div>

  <div class="hidden lg:flex items-center gap-5">
    <div class="flex items-center gap-2 px-3 py-1.5 rounded-md" style="background:rgba(0,255,136,.06);border:1px solid rgba(0,255,136,.15)">
      <div class="blink-dot"></div>
      <span class="mono-sm" style="color:var(--green)">LIVE STREAM ACTIVE</span>
    </div>
    <div class="flex gap-1">
      <button class="period-btn act">ALL TIME</button>
    </div>
    <div class="text-right">
      <div class="mono-sm text-white" id="clock">--:--:--</div>
      <div class="section-tag">UTC+05:30 · IST</div>
    </div>
  </div>

  <div class="flex items-center gap-2">
    <div class="text-right hidden sm:block">
      <div class="text-xs font-semibold text-white font-raj">ROOT ACCESS</div>
      <div class="section-tag">FOUNDER · CTO</div>
    </div>
    <div class="w-8 h-8 rounded-lg flex items-center justify-center text-xs font-bold font-mono" style="background:var(--bg-card2);border:1px solid var(--b);color:var(--green)">⌘</div>
  </div>
</nav>

<div class="ticker-outer fu d1">
  <div class="ticker-inner" id="ticker-el"></div>
</div>

<div class="fu d1 mt-4 flex items-center justify-between px-4 py-2 rounded-md" style="background:rgba(0,255,136,.05);border:1px solid rgba(0,255,136,.15)">
  <div class="flex items-center gap-3">
    <div class="blink-dot"></div>
    <span class="font-raj font-bold text-white tracking-wide">SYSTEM STATUS:</span>
    <span class="mono-sm" style="color:var(--green)" id="sys-status-txt">— </span>
  </div>
  <div class="flex items-center gap-6 text-xs">
    <div><span class="section-tag">UPTIME</span> <span class="font-mono font-bold text-white ml-1">99.94%</span></div>
    <div><span class="section-tag">AVG LATENCY</span> <span class="font-mono font-bold text-white ml-1">142ms</span></div>
    <div><span class="section-tag">ERR RATE</span> <span class="font-mono font-bold" style="color:var(--green)">0.00%</span></div>
  </div>
</div>

<div class="section-tag mt-6 mb-3 fu d2">▸ GLOBAL NETWORK METRICS</div>

<div class="grid grid-cols-2 lg:grid-cols-5 gap-3 fu d2">
  <div class="card card-green stripe stripe-green p-4">
    <div class="section-tag mb-3">Live Brands</div>
    <div class="kpi-val" style="color:var(--green)"><span class="cnt" data-t="__BRANDS__" data-f="int">0</span></div>
    <div class="flex items-center justify-between mt-2">
      <div class="section-tag">Active Clients</div>
      <div class="badge badge-green">ALL ONLINE</div>
    </div>
    <canvas id="sp_brands" height="32" class="mt-2"></canvas>
  </div>
  <div class="card stripe stripe-cyan p-4">
    <div class="section-tag mb-3">Total Vault Opens</div>
    <div class="kpi-val text-white"><span class="cnt" data-t="__OPENS__" data-f="comma">0</span></div>
    <div class="flex items-center justify-between mt-2">
      <div class="section-tag">Network-wide</div>
    </div>
    <canvas id="sp_opens" height="32" class="mt-2"></canvas>
  </div>
  <div class="card stripe stripe-purple p-4">
    <div class="section-tag mb-3">Global CVR</div>
    <div class="kpi-val" style="color:var(--purple)"><span class="cnt" data-t="__CVR__" data-f="float">0</span><span class="font-mono text-xl">%</span></div>
    <div class="flex items-center justify-between mt-2">
      <div class="section-tag">Opens→Purchase</div>
    </div>
    <canvas id="sp_cvr" height="32" class="mt-2"></canvas>
  </div>
  <div class="card stripe stripe-amber p-4">
    <div class="section-tag mb-3">RTOs Prevented</div>
    <div class="kpi-val" style="color:var(--amber)"><span class="cnt" data-t="__RTO__" data-f="comma">0</span></div>
    <div class="flex items-center justify-between mt-2">
      <div class="section-tag">Returns stopped</div>
    </div>
    <canvas id="sp_rto" height="32" class="mt-2"></canvas>
  </div>
  <div class="card stripe stripe-rose p-4">
    <div class="section-tag mb-3">GMV Influenced</div>
    <div class="kpi-val" style="color:var(--rose)">₹<span class="cnt" data-t="__GMV__" data-f="comma">0</span></div>
    <div class="flex items-center justify-between mt-2">
      <div class="section-tag">Vault-assisted Revenue</div>
    </div>
    <canvas id="sp_gmv" height="32" class="mt-2"></canvas>
  </div>
</div>

<div class="grid grid-cols-1 lg:grid-cols-3 gap-3 mt-4">
  <div class="card stripe stripe-cyan p-5 lg:col-span-2 fu d3">
    <div class="flex items-start justify-between mb-4">
      <div>
        <div class="section-tag mb-1">Network Intelligence · Traffic Load</div>
        <div class="font-raj font-bold text-white text-lg tracking-wide">API Call Volume</div>
      </div>
    </div>
    <canvas id="networkChart" height="130"></canvas>
  </div>
  <div class="card stripe stripe-green p-5 fu d4">
    <div class="section-tag mb-1">Infrastructure · Service Health</div>
    <div class="font-raj font-bold text-white text-lg tracking-wide mb-4">API Service Status</div>
    <div id="svc-list"></div>
    <div class="mt-4 pt-3" style="border-top:1px solid var(--b)">
      <canvas id="latencyChart" height="90"></canvas>
    </div>
  </div>
</div>

<div class="section-tag mt-5 mb-3 fu d4">▸ CLIENT COMMAND · BRAND LEADERBOARD</div>
<div class="card fu d4">
  <div class="lb-row" style="background:rgba(0,184,255,.04);border-bottom:2px solid rgba(0,184,255,.15)">
    <div class="section-tag">#</div>
    <div class="section-tag">Brand</div>
    <div class="section-tag">Vault Opens</div>
    <div class="section-tag">CVR</div>
    <div class="section-tag">Trend</div>
    <div class="section-tag">Status</div>
  </div>
  <div id="leaderboard-rows"></div>
</div>

<div class="grid grid-cols-1 lg:grid-cols-5 gap-3 mt-4">
  <div class="card p-5 lg:col-span-2 fu d5">
    <div class="section-tag mb-1">Algorithm Accuracy Engine</div>
    <div class="font-raj font-bold text-white text-lg tracking-wide mb-1">Global Fit Feedback Loop</div>
    <div class="mb-4 rounded-md p-3 flex gap-2.5" style="background:rgba(245,158,11,.06);border:1px solid rgba(245,158,11,.2)">
      <p class="font-mono text-xs leading-relaxed" style="color:var(--amber)" id="algo-insight-txt">Loading…</p>
    </div>
    <div class="relative flex items-center justify-center my-2">
      <canvas id="algoDoughnut" height="200"></canvas>
      <div class="absolute text-center pointer-events-none">
        <div class="font-mono font-bold text-2xl" style="color:var(--green)" id="algo-pct">—</div>
        <div class="section-tag">ACCURACY</div>
      </div>
    </div>
    <div id="algo-legend" class="mt-2 space-y-1.5"></div>
  </div>
  <div class="card p-5 lg:col-span-3 fu d6">
    <div class="section-tag mb-1">Universal Anchor Matrix · Industry Intelligence</div>
    <div class="font-raj font-bold text-white text-lg tracking-wide mb-1">Global Consumer Size Reference Brands</div>
    <canvas id="anchorChart" height="200"></canvas>
  </div>
</div>

<div class="section-tag mt-5 mb-3 fu d6">▸ GOD MODE · PROPRIETARY INTELLIGENCE LAYERS</div>
<div class="grid grid-cols-1 lg:grid-cols-2 gap-3">
  <div class="card card-green p-5 fu d7">
    <div class="flex items-start justify-between mb-1">
      <div>
        <div class="section-tag mb-1" style="color:var(--green)">God Mode Layer #1</div>
        <div class="font-raj font-bold text-white text-lg tracking-wide">Brand Health Radar</div>
      </div>
    </div>
    <canvas id="radarChart" height="280"></canvas>
    <div id="radar-legend" class="mt-2 flex flex-wrap gap-3"></div>
  </div>
  <div class="card p-5 fu d8" style="border-color:rgba(139,92,246,.15)">
    <div class="section-tag mb-1" style="color:var(--purple)">God Mode Layer #2</div>
    <div class="font-raj font-bold text-white text-lg tracking-wide mb-1">24-Hour Global Intent Clock</div>
    <canvas id="hourChart" height="230"></canvas>
  </div>
</div>

<div class="card mt-3 p-5 fu d8" style="border-color:rgba(0,255,136,.12)">
  <div class="flex items-start justify-between mb-5">
    <div>
      <div class="section-tag mb-1" style="color:var(--green)">God Mode Layer #3</div>
      <div class="font-raj font-bold text-white text-xl tracking-wide">The Sizing Correction Cascade</div>
    </div>
    <div class="text-right">
      <div class="section-tag mb-1">Global Correction Rate</div>
      <div class="font-mono font-bold text-3xl" style="color:var(--green)" id="corr-rate-num">—</div>
      <div class="section-tag">of all users corrected</div>
    </div>
  </div>
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
    <div><canvas id="cascadeChart" height="220"></canvas></div>
    <div>
      <div class="section-tag mb-3">PER-SIZE MISMATCH ANALYSIS</div>
      <div id="cascade-analysis" class="space-y-3"></div>
    </div>
  </div>
</div>

<div class="grid grid-cols-1 lg:grid-cols-2 gap-3 mt-3 pb-10">
  <div class="card card-rose stripe stripe-rose p-5 fu d8">
    <div class="section-tag mb-1" style="color:var(--rose)">God Mode Layer #4</div>
    <div class="font-raj font-bold text-white text-lg tracking-wide">Revenue At Risk Monitor</div>
    <div id="risk-list" class="space-y-4 text-center mt-6 text-xs" style="color:var(--t2)">Monitoring network anomalies...</div>
  </div>
  <div class="card p-5 fu d9" style="border-color:rgba(0,184,255,.12)">
    <div class="section-tag mb-1">System Console</div>
    <div class="font-raj font-bold text-white text-lg tracking-wide mb-4">Alert Feed</div>
    <div id="alert-console" class="space-y-1"></div>
  </div>
</div>

</div>

<script>
const MASTER_DATA = __INJECT_MASTER_DATA__;

Chart.defaults.color = '#3d6a8a';
Chart.defaults.borderColor = 'rgba(0,184,255,0.08)';
Chart.defaults.font.family = "'JetBrains Mono',monospace";
Chart.defaults.font.size = 10;
const D = MASTER_DATA;
const C = { green:'#00ff88', cyan:'#00b8ff', purple:'#8b5cf6', amber:'#f59e0b', rose:'#ff3f60', t2:'#3d6a8a', t3:'#1a3348' };

function animCnt(el,target,fmt='comma',dur=1300){
  if(!el)return;
  const s=performance.now();
  (function step(now){
    const p=Math.min((now-s)/dur,1);
    const v=(1-Math.pow(1-p,3))*target;
    if(fmt==='comma') el.textContent=Math.round(v).toLocaleString('en-IN');
    else if(fmt==='float') el.textContent=v.toFixed(1);
    else el.textContent=Math.round(v);
    if(p<1) requestAnimationFrame(step);
  })(s);
}

function sparkline(id,data,color){
  const ctx=document.getElementById(id);
  if(!ctx)return;
  new Chart(ctx,{
    type:'line', data:{labels:data.map((_,i)=>i),datasets:[{data,borderColor:color,borderWidth:1.5,pointRadius:0,fill:true, backgroundColor:ctx2=>{const g=ctx2.chart.ctx.createLinearGradient(0,0,0,36);g.addColorStop(0,color+'44');g.addColorStop(1,color+'00');return g},tension:0.4}]},
    options:{responsive:true,animation:{duration:1200},plugins:{legend:{display:false},tooltip:{enabled:false}},scales:{x:{display:false},y:{display:false}}}
  });
}

function initKPIs(){
  document.querySelector('[data-t="__BRANDS__"]').dataset.t = D.system_health.live_brands;
  document.querySelector('[data-t="__OPENS__"]').dataset.t = D.system_health.total_vault_opens;
  document.querySelector('[data-t="__CVR__"]').dataset.t = D.system_health.global_cvr;
  document.querySelector('[data-t="__RTO__"]').dataset.t = D.system_health.total_rto_saved;
  document.querySelector('[data-t="__GMV__"]').dataset.t = D.system_health.gmv_influenced_usd;

  document.getElementById('sys-status-txt').textContent=D.system_health.status.toUpperCase()+' — ALL SYSTEMS NOMINAL';
  document.querySelectorAll('.cnt').forEach(el=>{ animCnt(el,parseFloat(el.dataset.t),el.dataset.f); });
  sparkline('sp_brands',D.sparklines.brands,C.green);
  sparkline('sp_opens',D.sparklines.opens,C.cyan);
  sparkline('sp_cvr',D.sparklines.cvr,C.purple);
  sparkline('sp_rto',D.sparklines.rto,C.amber);
  sparkline('sp_gmv',D.sparklines.gmv,C.rose);
}

function initLeaderboard(){
  const wrap=document.getElementById('leaderboard-rows');
  const statusMap={
    Healthy:{badge:'badge-green',txt:'HEALTHY',color:C.green},
    Warning:{badge:'badge-amber',txt:'⚠ WARNING',color:C.amber},
    Critical:{badge:'badge-red',txt:'✕ CRITICAL',color:C.rose}
  };
  if(D.brand_leaderboard.length === 0) wrap.innerHTML = '<div class="text-sm p-4 text-center" style="color:var(--t2)">Awaiting data...</div>';
  D.brand_leaderboard.forEach((b,i)=>{
    const s=statusMap[b.status]||statusMap.Healthy;
    const row=document.createElement('div');
    row.className='lb-row';
    row.innerHTML=`
      <div class="font-mono text-xs" style="color:var(--t2)">${String(i+1).padStart(2,'0')}</div>
      <div><div class="font-raj font-bold text-white tracking-wide text-sm">${b.brand}</div></div>
      <div class="font-mono font-bold text-white">${b.opens.toLocaleString('en-IN')}</div>
      <div><div class="font-mono font-bold text-sm" style="color:${b.cvr>=10?C.green:b.cvr>=5?C.amber:C.rose}">${b.cvr}%</div></div>
      <div><canvas id="lbsp_${i}" height="28" style="width:100px"></canvas></div>
      <div><span class="badge ${s.badge}">${s.txt}</span></div>
    `;
    wrap.appendChild(row);
    setTimeout(()=>sparkline(`lbsp_${i}`,b.trend,b.cvr>=10?C.green:b.cvr>=5?C.amber:C.rose),100);
  });
}

function initAlgo(){
  const aa=D.algorithm_accuracy;
  document.getElementById('algo-insight-txt').textContent=aa.insight;
  const total=aa.perfect+aa.too_tight+aa.too_loose+aa.returned;
  const pct=total>0?(aa.perfect/total*100).toFixed(1):0;
  animCnt(document.getElementById('algo-pct'),parseFloat(pct),'float');
  
  const labels=['Perfect Fit','Too Tight','Too Loose','Returned'];
  const values=[aa.perfect,aa.too_tight,aa.too_loose,aa.returned];
  const colors=[C.green,C.amber,C.cyan,C.rose];
  new Chart(document.getElementById('algoDoughnut'),{
    type:'doughnut', data:{labels,datasets:[{data:values.length?values:[1],backgroundColor:colors.map(c=>c+'aa'),borderColor:colors,borderWidth:1.5,hoverOffset:8}]},
    options:{cutout:'68%',animation:{duration:1200},plugins:{legend:{display:false},tooltip:{backgroundColor:'#091626',borderColor:'rgba(0,184,255,.3)',borderWidth:1,padding:10}}}
  });

  const leg=document.getElementById('algo-legend');
  labels.forEach((lbl,i)=>{
    const v = total>0 ? ((values[i]/total)*100).toFixed(1) : 0;
    const row=document.createElement('div');
    row.className='flex items-center justify-between';
    row.innerHTML=`<div class="flex items-center gap-2"><div class="status-dot" style="background:${colors[i]}"></div><span class="mono-sm" style="color:var(--t2)">${lbl}</span></div><span class="font-mono font-bold text-xs text-white">${values[i].toLocaleString()} <span style="color:var(--t2)">(${v}%)</span></span>`;
    leg.appendChild(row);
  });
}

function initAnchor(){
  const am=D.universal_anchor_matrix;
  new Chart(document.getElementById('anchorChart'),{
    type:'bar', data:{ labels:am.map(b=>b.brand), datasets:[{ label:'Market Share %',data:am.map(b=>b.market_share), backgroundColor:['rgba(0,255,136,.3)','rgba(0,184,255,.3)','rgba(139,92,246,.3)','rgba(245,158,11,.3)','rgba(61,106,138,.3)','rgba(61,106,138,.2)'], borderColor:[C.green,C.cyan,C.purple,C.amber,C.t2,C.t2], borderWidth:1.5,borderRadius:4 }] },
    options:{ indexAxis:'y',responsive:true,animation:{duration:1400}, plugins:{legend:{display:false}}, scales:{x:{grid:{display:false},ticks:{color:C.t2}},y:{grid:{display:false},ticks:{color:C.t1}}} }
  });
}

function initCascade(){
  const cc=D.correction_cascade;
  animCnt(document.getElementById('corr-rate-num'),D.global_perception_gap.correction_rate_pct,'float');

  new Chart(document.getElementById('cascadeChart'),{
    type:'bar', data:{ labels:cc.sizes, datasets:[ {label:'Customer Anchor',data:cc.anchored,backgroundColor:'rgba(0,184,255,.3)',borderColor:C.cyan,borderWidth:1.5,borderRadius:5}, {label:'Vault Corrected',data:cc.corrected,backgroundColor:'rgba(0,255,136,.3)',borderColor:C.green,borderWidth:1.5,borderRadius:5} ] },
    options:{ responsive:true,animation:{duration:1500}, plugins:{legend:{display:false}}, scales:{x:{grid:{display:false},ticks:{color:C.t2}},y:{grid:{color:'rgba(0,184,255,.06)'},ticks:{color:C.t2}}} }
  });

  const wrap=document.getElementById('cascade-analysis');
  cc.sizes.forEach((sz,i)=>{
    const anchored=cc.anchored[i]; const corrected=cc.corrected[i];
    const delta=corrected-anchored; const direction=delta>0?'↑':delta<0?'↓':'='; const color=delta>0?C.green:delta<0?C.rose:C.t2;
    const maxVal=Math.max(1, ...[...cc.anchored,...cc.corrected]);
    const row=document.createElement('div');
    row.innerHTML=`
      <div class="flex items-center justify-between mb-1">
        <div class="flex items-center gap-2"><span class="font-mono font-bold text-sm text-white">${sz}</span><span class="font-mono text-xs" style="color:${color}">${direction}${Math.abs(delta).toLocaleString()}</span></div>
      </div>
      <div class="relative h-3 rounded-sm overflow-hidden" style="background:rgba(255,255,255,.04)">
        <div class="absolute top-0 left-0 h-full rounded-sm" style="width:${anchored/maxVal*100}%;background:rgba(0,184,255,.5);transition:width 1.4s ease 0.3s"></div>
        <div class="absolute top-0 left-0 h-1.5 mt-0.5 rounded-sm" style="width:${corrected/maxVal*100}%;background:rgba(0,255,136,.7);transition:width 1.4s ease 0.5s"></div>
      </div>
    `;
    wrap.appendChild(row);
  });
}

// UI Fillers for Mock Data
function initMockVisuals() {
  const h=D.hourly_activity; const maxHour=Math.max(...h);
  new Chart(document.getElementById('hourChart'),{ type:'bar', data:{ labels:Array.from({length:24},(_,i)=>i), datasets:[{ data:h,borderRadius:3,backgroundColor:h.map(v=>v/maxHour>.8?C.purple:C.purple+'44') }] }, options:{plugins:{legend:{display:false}},scales:{x:{display:false},y:{display:false}}} });
  
  const bhr=D.brand_health_radar;
  new Chart(document.getElementById('radarChart'),{ type:'radar', data:{ labels:bhr.labels, datasets:bhr.brands.map(b=>({ label:b.name,data:b.data,borderColor:b.color,borderWidth:2,backgroundColor:b.color+'22',pointBackgroundColor:b.color })) }, options:{plugins:{legend:{display:false}},scales:{r:{min:0,max:100,ticks:{display:false}}}} });

  const svcs=D.api_services; const svcWrap=document.getElementById('svc-list');
  svcs.forEach(s=>{
    const row=document.createElement('div'); row.className='svc-row';
    row.innerHTML=`<div class="flex items-center gap-2 flex-1"><div class="status-dot" style="background:${C.green}"></div><span class="font-mono text-xs text-white">${s.name}</span></div><div class="font-mono text-xs" style="color:var(--t2)">${s.latency}ms</div>`;
    svcWrap.appendChild(row);
  });
  
  const alerts=D.alert_feed; const alWrap=document.getElementById('alert-console');
  alerts.forEach(a=>{
    const div=document.createElement('div'); div.className=`alert-row alert-info`;
    div.innerHTML=`<div class="flex items-start gap-2"><span class="font-mono text-xs flex-shrink-0" style="color:${C.cyan}">[INFO]</span><span class="font-mono text-xs flex-1" style="color:var(--t1)">${a.msg}</span></div>`;
    alWrap.appendChild(div);
  });
}

document.addEventListener('DOMContentLoaded',()=>{
  initKPIs(); initLeaderboard(); initAlgo(); initAnchor(); initCascade(); initMockVisuals();
});
</script>
</body>
</html>
        """

        final_html = html_template.replace("__INJECT_MASTER_DATA__", json.dumps(master_data))
        return HTMLResponse(content=final_html, status_code=200)

    except Exception as e:
        return f"<h1 style='color:white; background:black; padding:20px;'>God Mode Error: {str(e)}</h1>"
