# Copyright 2026 Google LLC
# Seed script for Haven Apartment Finder Firestore collection

import logging
from google.cloud import firestore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("seed_db")

# IMPORTANT: Hardcoded Project ID string as required for Agent Platform deployment
FIRESTORE_PROJECT_ID = "qwiklabs-gcp-01-acd01b94c3ce"

SAMPLE_APARTMENTS = [
    {
        "id": "apt-101",
        "title": "The Grand View Loft",
        "neighborhood": "Downtown",
        "rent_monthly": 2800,
        "bedrooms": 2,
        "bathrooms": 1.5,
        "sqft": 950,
        "pet_friendly": True,
        "amenities": ["In-unit washer/dryer", "Rooftop deck", "Fitness center", "EV charging"],
        "available_date": "2026-10-01",
        "description": "Modern open-concept loft in the heart of downtown with stunning skyline views.",
    },
    {
        "id": "apt-102",
        "title": "Pacific Breeze Residence",
        "neighborhood": "Westside",
        "rent_monthly": 3200,
        "bedrooms": 2,
        "bathrooms": 2.0,
        "sqft": 1100,
        "pet_friendly": True,
        "amenities": ["Ocean view balcony", "Pool", "Underground parking", "Dishwasher"],
        "available_date": "2026-10-15",
        "description": "Bright and airy 2-bedroom luxury condo just blocks from the beach.",
    },
    {
        "id": "apt-103",
        "title": "Pinecrest Garden Studio",
        "neighborhood": "Northside",
        "rent_monthly": 1650,
        "bedrooms": 1,
        "bathrooms": 1.0,
        "sqft": 550,
        "pet_friendly": False,
        "amenities": ["Garden patio", "Hardwood floors", "Heat included"],
        "available_date": "2026-10-01",
        "description": "Cozy garden-level studio in a quiet residential neighborhood.",
    },
    {
        "id": "apt-104",
        "title": "Metropolitan Highrise Unit",
        "neighborhood": "Financial District",
        "rent_monthly": 4100,
        "bedrooms": 3,
        "bathrooms": 2.5,
        "sqft": 1450,
        "pet_friendly": True,
        "amenities": ["24/7 Concierge", "Private balcony", "Wine cellar", "Pet spa"],
        "available_date": "2026-11-01",
        "description": "Spacious family penthouse apartment with floor-to-ceiling panoramic windows.",
    },
    {
        "id": "apt-105",
        "title": "Oakwood Historic Flat",
        "neighborhood": "Eastside",
        "rent_monthly": 2250,
        "bedrooms": 2,
        "bathrooms": 1.0,
        "sqft": 820,
        "pet_friendly": True,
        "amenities": ["Exposed brick", "Updated kitchen", "Shared courtyard"],
        "available_date": "2026-10-01",
        "description": "Charming vintage flat with original architectural features and modern kitchen updates.",
    },
]


def seed_firestore():
    logger.info(f"Connecting to Firestore with project ID: {FIRESTORE_PROJECT_ID}")
    db = firestore.Client(project=FIRESTORE_PROJECT_ID, database="(default)")
    collection_ref = db.collection("apartments")

    for apartment in SAMPLE_APARTMENTS:
        doc_id = apartment["id"]
        doc_ref = collection_ref.document(doc_id)
        doc_ref.set(apartment)
        logger.info(f"Seeded apartment '{doc_id}': {apartment['title']}")

    logger.info("Successfully seeded all sample apartments into Firestore collection 'apartments'!")


if __name__ == "__main__":
    seed_firestore()
