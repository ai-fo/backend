"""Configuration pour le système RAG."""
from pathlib import Path

# Chemins des dossiers
BASE_DIR = Path(__file__).parent
PDF_FOLDER = BASE_DIR / "pdfs"  # Dossier par défaut pour les PDFs
PDF_DIR = "pdfs"  # Dossier contenant les PDFs
PDF_PATTERN = "*.pdf"  # Pattern pour trouver les PDFs
CACHE_DIR = BASE_DIR / "cache"  # Dossier pour stocker les caches
DOC_CACHE_FILE = CACHE_DIR / "doc_cache.json"  # Cache des documents prétraités
IMAGE_CACHE_FILE = CACHE_DIR / "image_cache.json"  # Cache des analyses Pixtral
TEMP_DIR = BASE_DIR / "temp_images"
TRANSCRIPTS_DIR = BASE_DIR / "transcripts"  # Nouveau dossier pour les transcriptions
PROMPTS_DIR = BASE_DIR / "prompts"  # Dossier pour stocker les prompts

# Créer les dossiers s'ils n'existent pas
PDF_FOLDER.mkdir(exist_ok=True)
CACHE_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)
TRANSCRIPTS_DIR.mkdir(exist_ok=True)  # Création du dossier des transcriptions
PROMPTS_DIR.mkdir(exist_ok=True)  # Création du dossier des prompts