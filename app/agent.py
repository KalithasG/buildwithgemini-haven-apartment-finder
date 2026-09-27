# ruff: noqa
# Copyright 2026 Google LLC
# Haven Apartment Finder ADK Agent

import base64
import datetime
import json
import logging
import os
import subprocess
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

import google.auth
import google.auth.transport.requests
import requests

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from google import genai
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.code_executors import AgentEngineSandboxCodeExecutor
from google.adk.memory import VertexAiMemoryBankService
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.cloud import firestore, storage
from google.genai import types

from .a2ui_utils import a2ui_callback

logger = logging.getLogger("haven_agent")

# IMPORTANT: Hardcoded Project ID string as required for Agent Platform deployment.
# Do NOT use google.auth.default() or GOOGLE_CLOUD_PROJECT env var as those resolve to project numbers on Agent Platform.
FIRESTORE_PROJECT_ID = "qwiklabs-gcp-01-acd01b94c3ce"
GCS_BUCKET_NAME = "haven-apartments-media-qwiklabs-gcp-01-acd01b94c3ce"
MEMORY_BANK_ID = "260564464573612032"

_db_client = None


def get_firestore_client() -> firestore.Client:
    global _db_client
    if _db_client is None:
        _db_client = firestore.Client(project=FIRESTORE_PROJECT_ID, database="(default)")
    return _db_client


def get_memory_service() -> VertexAiMemoryBankService:
    """Configures the Vertex AI Memory Bank service for deployment."""
    return VertexAiMemoryBankService(
        project=FIRESTORE_PROJECT_ID,
        location="us-east1",
        agent_engine_id=MEMORY_BANK_ID,
    )


# Memory service instance for deployment
memory_service = get_memory_service()


async def generate_memories_callback(callback_context: CallbackContext):
    """WRITE: After each agent turn, extract and send durable facts, health preferences, and user allergies to Memory Bank."""
    try:
        if not getattr(callback_context, "_memory_service", None):
            setattr(callback_context, "_memory_service", memory_service)
        if getattr(callback_context, "_memory_service", None) is not None:
            await callback_context.add_session_to_memory()
    except Exception as e:
        logger.warning(f"Memory Bank add_session_to_memory skipped: {e}")
    return None


def _load_agent_engine_resource_name() -> str | None:
    possible_paths = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deployment_metadata.json"),
        os.path.join(os.getcwd(), "deployment_metadata.json"),
        "deployment_metadata.json",
    ]
    for path in possible_paths:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    remote_id = data.get("remote_agent_runtime_id")
                    if remote_id:
                        return remote_id
            except Exception as e:
                logger.warning(f"Could not read deployment_metadata.json at {path}: {e}")
    return None


code_executor = AgentEngineSandboxCodeExecutor(
    agent_engine_resource_name=_load_agent_engine_resource_name()
)


def search_apartments(
    neighborhood: str = "",
    max_rent: int = 0,
    min_bedrooms: int = 0,
    pet_friendly_only: bool = False,
) -> str:
    """Search available apartment listings in Firestore based on filters.

    Args:
        neighborhood: Optional neighborhood filter (e.g. "Downtown", "Westside", "Eastside").
        max_rent: Optional maximum monthly rent budget in USD (0 for no limit).
        min_bedrooms: Optional minimum number of bedrooms (0 for no limit).
        pet_friendly_only: If True, only return pet-friendly apartments.

    Returns:
        JSON string containing the list of matching apartment listings.
    """
    db = get_firestore_client()
    docs = db.collection("apartments").stream()

    results = []
    for doc in docs:
        data = doc.to_dict()
        data["id"] = doc.id

        if neighborhood and neighborhood.lower() not in data.get("neighborhood", "").lower():
            continue
        if max_rent > 0 and data.get("rent_monthly", 0) > max_rent:
            continue
        if min_bedrooms > 0 and data.get("bedrooms", 0) < min_bedrooms:
            continue
        if pet_friendly_only and not data.get("pet_friendly", False):
            continue

        results.append(data)

    if not results:
        return "No apartments found matching your search criteria."
    return json.dumps(results, indent=2)


def get_apartment_details(apartment_id: str) -> str:
    """Fetch complete details for a specific apartment listing by ID.

    Args:
        apartment_id: The ID of the apartment (e.g. "apt-101", "apt-102").

    Returns:
        JSON string containing the apartment details.
    """
    db = get_firestore_client()
    doc_ref = db.collection("apartments").document(apartment_id)
    doc = doc_ref.get()

    if not doc.exists:
        return f"Apartment with ID '{apartment_id}' not found."

    data = doc.to_dict()
    data["id"] = doc.id
    return json.dumps(data, indent=2)


def add_apartment_listing(
    title: str,
    neighborhood: str,
    rent_monthly: int,
    bedrooms: int,
    bathrooms: float,
    sqft: int,
    pet_friendly: bool,
    amenities: list[str],
    description: str,
) -> str:
    """Add a new apartment listing to the Firestore database.

    Args:
        title: Title/name of the property (e.g. "Sunset Villa Flat").
        neighborhood: Neighborhood name (e.g. "Downtown", "Eastside").
        rent_monthly: Monthly rent in USD (e.g. 2500).
        bedrooms: Number of bedrooms (e.g. 2).
        bathrooms: Number of bathrooms (e.g. 1.5).
        sqft: Square footage (e.g. 850).
        pet_friendly: Whether pets are allowed (True or False).
        amenities: List of amenities (e.g. ["Balcony", "Parking"]).
        description: Detailed description of the apartment.

    Returns:
        Confirmation message with the generated listing ID.
    """
    db = get_firestore_client()

    docs = list(db.collection("apartments").stream())
    next_num = len(docs) + 101
    apartment_id = f"apt-{next_num}"

    listing_data = {
        "id": apartment_id,
        "title": title,
        "neighborhood": neighborhood,
        "rent_monthly": rent_monthly,
        "bedrooms": bedrooms,
        "bathrooms": bathrooms,
        "sqft": sqft,
        "pet_friendly": pet_friendly,
        "amenities": amenities,
        "available_date": datetime.date.today().isoformat(),
        "description": description,
    }

    db.collection("apartments").document(apartment_id).set(listing_data)
    return f"Successfully created new apartment listing '{title}' with ID: {apartment_id}"


def bookmark_apartment(user_id: str, apartment_id: str, notes: str = "") -> str:
    """Bookmark an apartment listing for a user in Firestore.

    Args:
        user_id: User identifier or username.
        apartment_id: ID of the apartment to bookmark (e.g. "apt-101").
        notes: Optional personal notes about why the apartment was saved.

    Returns:
        Confirmation message for the bookmarked property.
    """
    db = get_firestore_client()

    doc = db.collection("apartments").document(apartment_id).get()
    if not doc.exists:
        return f"Cannot bookmark: Apartment '{apartment_id}' does not exist."

    bookmark_id = f"{user_id}_{apartment_id}"
    bookmark_data = {
        "user_id": user_id,
        "apartment_id": apartment_id,
        "apartment_title": doc.to_dict().get("title", ""),
        "notes": notes,
        "saved_at": datetime.datetime.now().isoformat(),
    }

    db.collection("bookmarks").document(bookmark_id).set(bookmark_data)
    return f"Successfully bookmarked apartment '{doc.to_dict().get('title')}' ({apartment_id}) for user '{user_id}'."


def schedule_tour(
    apartment_id: str,
    user_name: str,
    user_email: str,
    preferred_date: str,
    preferred_time_slot: str = "Morning",
    notes: str = "",
) -> str:
    """Schedule an in-person or virtual tour request for a specific apartment listing.

    Args:
        apartment_id: ID of the apartment to tour (e.g. "apt-101").
        user_name: Full name of the user requesting the tour.
        user_email: Email address of the user for confirmation.
        preferred_date: Desired date for the tour (e.g. "2026-10-15").
        preferred_time_slot: Time of day preference (e.g. "Morning", "Afternoon", "Evening").
        notes: Any specific questions or requests for the property manager.

    Returns:
        Confirmation message containing the tour request ID and details.
    """
    db = get_firestore_client()

    doc = db.collection("apartments").document(apartment_id).get()
    if not doc.exists:
        return f"Cannot schedule tour: Apartment '{apartment_id}' does not exist."

    apartment_title = doc.to_dict().get("title", apartment_id)

    existing_requests = list(db.collection("tour_requests").stream())
    request_id = f"tour-{len(existing_requests) + 1001}"

    tour_data = {
        "request_id": request_id,
        "apartment_id": apartment_id,
        "apartment_title": apartment_title,
        "user_name": user_name,
        "user_email": user_email,
        "preferred_date": preferred_date,
        "preferred_time_slot": preferred_time_slot,
        "notes": notes,
        "status": "CONFIRMED",
        "created_at": datetime.datetime.now().isoformat(),
    }

    db.collection("tour_requests").document(request_id).set(tour_data)
    return (
        f"Tour request confirmed! Request ID: {request_id}. "
        f"Scheduled tour for '{apartment_title}' on {preferred_date} ({preferred_time_slot}) for {user_name} ({user_email})."
    )


def get_zip_code_info(zip_code: str) -> str:
    """Fetch city, state, latitude, and longitude for a US 5-digit postal zip code using public Zippopotam.us API.

    Args:
        zip_code: 5-digit US postal zip code (e.g. "90210", "10001", "94102").

    Returns:
        JSON string containing city, state, latitude, and longitude.
    """
    clean_zip = str(zip_code).strip()
    url = f"https://api.zippopotam.us/us/{clean_zip}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "HavenApartmentFinder/1.0"})
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status != 200:
                return f"Unable to fetch location details for zip code '{clean_zip}'."
            data = json.loads(response.read().decode("utf-8"))
            places = data.get("places", [])
            if not places:
                return f"No location information found for zip code '{clean_zip}'."
            place = places[0]
            info = {
                "zip_code": clean_zip,
                "city": place.get("place name"),
                "state": place.get("state"),
                "state_abbreviation": place.get("state abbreviation"),
                "latitude": place.get("latitude"),
                "longitude": place.get("longitude"),
            }
            return json.dumps(info, indent=2)
    except Exception as e:
        return f"Error fetching zip code info: {str(e)}"


def geocode_address(address: str) -> str:
    """Geocode an address string into latitude, longitude, and formatted address using Google Geocoding API.

    Args:
        address: Full or partial street address or location name (e.g. "1600 Amphitheatre Pkwy, Mountain View, CA").

    Returns:
        JSON string with formatted_address, latitude, and longitude.
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    headers = {"Content-Type": "application/json"}
    encoded_addr = urllib.parse.quote(address.strip())

    if api_key:
        headers["X-Goog-Api-Key"] = api_key
    else:
        try:
            token = subprocess.check_output(["gcloud", "auth", "print-access-token"]).decode().strip()
            headers["Authorization"] = f"Bearer {token}"
        except Exception:
            pass

    url = f"https://geocode.googleapis.com/v4/geocode/address/{encoded_addr}"

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = data.get("results", [])
            if not results:
                return f"No geocoding results found for address '{address}'."
            top = results[0]
            loc = top.get("location", {})
            info = {
                "address_query": address,
                "formatted_address": top.get("formattedAddress"),
                "latitude": loc.get("latitude"),
                "longitude": loc.get("longitude"),
            }
            return json.dumps(info, indent=2)
    except Exception as e:
        return f"Geocoding error: {str(e)}"


def find_nearby_places(
    latitude: float,
    longitude: float,
    place_type: str = "restaurant",
    radius_meters: float = 1000.0,
) -> str:
    """Find nearby places of a specific type around coordinates using Google Places API (New).

    Args:
        latitude: Latitude coordinate of the central location (e.g. 37.422).
        longitude: Longitude coordinate of the central location (e.g. -122.084).
        place_type: Type of place to search for (e.g. "restaurant", "supermarket", "transit_station", "park", "coffee_shop").
        radius_meters: Radius in meters around the central point (default 1000 meters).

    Returns:
        JSON string listing nearby places with name, address, location, and types.
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    headers = {
        "Content-Type": "application/json",
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location,places.types",
    }

    if api_key:
        headers["X-Goog-Api-Key"] = api_key
    else:
        try:
            token = subprocess.check_output(["gcloud", "auth", "print-access-token"]).decode().strip()
            headers["Authorization"] = f"Bearer {token}"
        except Exception:
            pass

    places_url = "https://places.googleapis.com/v1/places:searchNearby"
    body = json.dumps({
        "includedTypes": [place_type],
        "maxResultCount": 5,
        "locationRestriction": {
            "circle": {
                "center": {"latitude": latitude, "longitude": longitude},
                "radius": float(radius_meters),
            }
        },
    }).encode("utf-8")

    try:
        req = urllib.request.Request(places_url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            raw_places = data.get("places", [])
            if not raw_places:
                return f"No nearby '{place_type}' places found within {radius_meters}m."

            clean_places = []
            for item in raw_places:
                disp_name = item.get("displayName", {}).get("text") if isinstance(item.get("displayName"), dict) else item.get("displayName")
                clean_places.append({
                    "name": disp_name,
                    "address": item.get("formattedAddress"),
                    "location": item.get("location"),
                    "types": item.get("types", []),
                })
            return json.dumps(clean_places, indent=2)
    except Exception as e:
        return f"Places API error: {str(e)}"


async def generate_interior_mockup(
    prompt: str,
    tool_context: ToolContext = None,
) -> str:
    """Generate an apartment interior design mockup image using gemini-3.1-flash-lite-image model in global region.

    Saves the generated image as an artifact in Playground's Artifacts panel and uploads it to public Cloud Storage.

    Args:
        prompt: Description of the interior design mockup to generate (e.g. "Modern Scandinavian living room with floor to ceiling windows").

    Returns:
        The public Cloud Storage HTTPS URL of the generated image.
    """
    client = genai.Client(vertexai=True, project=FIRESTORE_PROJECT_ID, location="global")

    response = client.models.generate_content(
        model="gemini-3.1-flash-lite-image",
        contents=f"High quality interior design photography of: {prompt}",
        config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
    )

    image_bytes = None
    for part in response.candidates[0].content.parts:
        if part.inline_data and part.inline_data.data:
            image_bytes = part.inline_data.data
            break

    if not image_bytes:
        return "Failed to generate image bytes from model response."

    filename = f"interior_mockup_{int(datetime.datetime.now().timestamp())}.jpg"

    if tool_context is not None:
        try:
            artifact_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
            await tool_context.save_artifact(filename, artifact_part)
        except Exception as e:
            logger.warning(f"Could not save artifact: {e}")

    storage_client = storage.Client(project=FIRESTORE_PROJECT_ID)
    bucket = storage_client.bucket(GCS_BUCKET_NAME)
    blob = bucket.blob(filename)
    blob.upload_from_string(image_bytes, content_type="image/jpeg")

    public_url = f"https://storage.googleapis.com/{GCS_BUCKET_NAME}/{filename}"
    return public_url


async def generate_property_video(
    prompt: str, tool_context: ToolContext = None
) -> str:
    """Generate a short video tour for an apartment or property listing using Google's Omni model (gemini-omni-flash-preview) in the global region.

    Saves the generated video as an artifact in Playground's Artifacts panel and uploads it to public Cloud Storage.

    Args:
        prompt: Description of the property or apartment video tour to generate (e.g. "A 5-second video tour of a luxury downtown penthouse balcony").

    Returns:
        The public Cloud Storage HTTPS URL of the generated video.
    """
    credentials, _ = google.auth.default()
    auth_req = google.auth.transport.requests.Request()
    credentials.refresh(auth_req)
    access_token = credentials.token

    url = f"https://aiplatform.googleapis.com/v1beta1/projects/{FIRESTORE_PROJECT_ID}/locations/global/interactions"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": "gemini-omni-flash-preview",
        "input": [
            {
                "type": "text",
                "text": f"High quality 5-second property video tour of: {prompt}",
            }
        ],
        "response_format": [
            {"type": "video", "duration": "5s"}
        ],
        "generation_config": {
            "video_config": {"task": "text_to_video"}
        },
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=180)
        if resp.status_code != 200:
            logger.error(f"Video generation API error: {resp.status_code} {resp.text}")
            return f"Failed to generate video tour: HTTP {resp.status_code}"

        data = resp.json()
        video_bytes = None

        if "steps" in data:
            for step in data["steps"]:
                if step.get("type") == "model_output":
                    for content in step.get("content", []):
                        if content.get("type") == "video":
                            if "data" in content:
                                video_bytes = base64.b64decode(content["data"])
                            elif "uri" in content:
                                gcs_uri = content["uri"]
                                if gcs_uri.startswith("gs://"):
                                    parts = gcs_uri[5:].split("/", 1)
                                    b_name, b_path = parts[0], parts[1]
                                    storage_client = storage.Client(project=FIRESTORE_PROJECT_ID)
                                    blob = storage_client.bucket(b_name).blob(b_path)
                                    video_bytes = blob.download_as_bytes()

        if not video_bytes:
            return "Failed to extract video bytes from model response."

        filename = f"property_tour_{int(datetime.datetime.now().timestamp())}.mp4"

        if tool_context is not None:
            try:
                artifact_part = types.Part.from_bytes(data=video_bytes, mime_type="video/mp4")
                await tool_context.save_artifact(filename, artifact_part)
            except Exception as e:
                logger.warning(f"Could not save video artifact: {e}")

        storage_client = storage.Client(project=FIRESTORE_PROJECT_ID)
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(video_bytes, content_type="video/mp4")

        public_url = f"https://storage.googleapis.com/{GCS_BUCKET_NAME}/{filename}"
        return public_url
    except Exception as e:
        logger.error(f"Error during video tour generation: {e}")
        return f"Error generating video tour: {e}"


schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

instruction = schema_manager.generate_system_prompt(
    role_description="Haven, a smart property and apartment finder assistant. Help users search, filter, view details, bookmark, add listings, schedule tours, look up zip codes, geocode addresses to coordinates, search nearby places, generate interior design mockup images, and generate short property tour videos.",
    workflow_description="Analyze the request, execute tools as needed, and return structured UI when appropriate.",
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        "{\"Image\": {\"url\": {\"literalString\": \"https://...\"}}}. Never point an "
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects. "
        "IMPORTANT HEALTH & ALLERGY MEMORY RULE: Always record, remember, and respect any user allergies mentioned (e.g. pet dander/fur allergies, mold, dust, pollen, chemical sensitivities, or food allergies). "
        "Use recalled user facts from Memory Bank across sessions to personalize recommendations, filter out non-compliant properties (e.g. avoid pet-friendly or high-pollen/mold buildings for allergic users), and highlight allergy-safe nearby amenities."
    ),
    include_schema=True,
    include_examples=True,
)


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-flash-latest",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=instruction,
    code_executor=code_executor,
    tools=[
        PreloadMemoryTool(),
        search_apartments,
        get_apartment_details,
        add_apartment_listing,
        bookmark_apartment,
        schedule_tour,
        get_zip_code_info,
        geocode_address,
        find_nearby_places,
        generate_interior_mockup,
        generate_property_video,
    ],
    after_model_callback=a2ui_callback,
    after_agent_callback=generate_memories_callback,
)

def memory_bank_service_builder():
    """Configures the Vertex AI Memory Bank service for deployment."""
    return VertexAiMemoryBankService(
        project=FIRESTORE_PROJECT_ID,
        location="us-east1",
        agent_engine_id=MEMORY_BANK_ID,
    )


app = App(
    root_agent=root_agent,
    name="app",
    memory_service_builder=memory_bank_service_builder,
)
