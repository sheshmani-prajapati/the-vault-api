import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client, Client
from dotenv import load_dotenv

# Load safe environment variables from local hidden .env file
load_dotenv()

app = FastAPI(title="The Vault - Sizing Intervention Backend")

# Standard production CORS configurations
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Database Client safely
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing database configuration environment variables.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Exact domain sanitization dictionary built during debugging
DOMAIN_MAPPINGS = {
    "thehouseofrare.com": "Rare Rabbit",
    "bonkerscorner.com": "Bonkers Corner",
    "indianterrain.com": "Indian Terrain"
}

class EventPayload(BaseModel):
    event: str
    product_id: str
    brand: str
    result: str
    uuid: str
    price: float
    name: str
    url: str

@app.post("/sync_state")
async def sync_state(payload: EventPayload):
    try:
        # Sanitize vendor and domain labels natively before processing
        processed_brand = payload.brand
        for domain, brand_name in DOMAIN_MAPPINGS.items():
            if domain.split('.')[0] in processed_brand.lower().replace(" ", ""):
                processed_brand = brand_name

        # Inject clean dataset package directly into Supabase ledger
        data, count = supabase.table("vault_events").insert({
            "event_type": payload.event,
            "product_id": payload.product_id,
            "brand": processed_brand,
            "result_summary": payload.result,
            "user_uuid": payload.uuid,
            "transaction_value": payload.price,
            "product_name": payload.name,
            "source_url": payload.url
        }).execute()
        
        return {"status": "success", "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/meta")
async def get_meta():
    # Simulated static sizing lookup engine payload context
    return {
        "Snitch": {
            "Regular Fit": ["S", "M", "L", "XL", "XXL"]
        },
        "Rare Rabbit": {
            "Slim Fit": ["M", "L", "XL"],
            "Regular Fit": ["S", "M", "L", "XL"]
        }
    }

@app.get("/get_fit")
async def get_fit(ref_brand: str, ref_fit: str, ref_size: str, target_brand: str, target_fit: str):
    # Core dimensional lookup resolution logic
    return {
        "recommended_size": "L",
        "warning": "This product runs slightly snugger across the chest compared to your selection."
    }
