import os
from pathlib import Path
from typing import List, Dict, Tuple
from mistralai import Mistral
from dotenv import load_dotenv
import torch
import torch.nn.functional as F
from torch import Tensor
from transformers import AutoTokenizer, AutoModel
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
import json
import pickle
from datetime import datetime

# Chargement des variables d'environnement
load_dotenv()

def average_pool(last_hidden_states: Tensor, attention_mask: Tensor) -> Tensor:
    last_hidden = last_hidden_states.masked_fill(~attention_mask[..., None].bool(), 0.0)
    return last_hidden.sum(dim=1) / attention_mask.sum(dim=1)[..., None]

class TranscriptChunk:
    def __init__(self, content: str, is_image_description: bool, source_file: str, chunk_index: int):
        self.content = content
        self.is_image_description = is_image_description
        self.source_file = source_file
        self.chunk_index = chunk_index
        self.embedding = None
    
    def to_dict(self) -> dict:
        return {
            'content': self.content,
            'is_image_description': self.is_image_description,
            'source_file': self.source_file,
            'chunk_index': self.chunk_index
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'TranscriptChunk':
        return cls(
            content=data['content'],
            is_image_description=data['is_image_description'],
            source_file=data['source_file'],
            chunk_index=data['chunk_index']
        )

class TranscriptRAG:
    def __init__(self, transcripts_dir: str, chunk_size: int = 512):
        self.transcripts_dir = Path(transcripts_dir)
        self.chunk_size = chunk_size
        self.cache_dir = self.transcripts_dir / '.cache'
        self.cache_dir.mkdir(exist_ok=True)
        
        self.chunks_cache_file = self.cache_dir / 'chunks.json'
        self.embeddings_cache_file = self.cache_dir / 'embeddings.pt'
        self.metadata_cache_file = self.cache_dir / 'metadata.json'
        
        # Initialisation du modèle E5
        self.tokenizer = AutoTokenizer.from_pretrained('intfloat/e5-large-v2')
        self.model = AutoModel.from_pretrained('intfloat/e5-large-v2')
        self.model.eval()  # Mettre en mode évaluation
        
        self.chunks: List[TranscriptChunk] = []
        self.mistral_client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])
        
    def _get_files_metadata(self) -> dict:
        """Obtient les métadonnées des fichiers de transcription."""
        metadata = {}
        for file_path in self.transcripts_dir.glob("*.txt"):
            metadata[str(file_path)] = {
                'mtime': os.path.getmtime(file_path),
                'size': os.path.getsize(file_path)
            }
        return metadata
    
    def _should_update_cache(self) -> bool:
        """Vérifie si le cache doit être mis à jour."""
        if not all(f.exists() for f in [self.chunks_cache_file, self.embeddings_cache_file, self.metadata_cache_file]):
            return True
            
        try:
            with open(self.metadata_cache_file, 'r') as f:
                cached_metadata = json.load(f)
            
            current_metadata = self._get_files_metadata()
            
            # Vérifier si les fichiers ont changé
            return cached_metadata != current_metadata
        except:
            return True
    
    def get_embedding(self, text: str) -> torch.Tensor:
        # Préfixer le texte avec "passage: " pour E5
        text = f"passage: {text}"
        
        # Tokenization
        inputs = self.tokenizer(text, max_length=512, padding=True, truncation=True, return_tensors='pt')
        
        # Obtenir l'embedding
        with torch.no_grad():
            outputs = self.model(**inputs)
            embedding = average_pool(outputs.last_hidden_state, inputs['attention_mask'])
            # Normaliser l'embedding
            embedding = F.normalize(embedding, p=2, dim=1)
            
        return embedding.squeeze()
    
    def process_transcript_file(self, file_path: Path) -> List[TranscriptChunk]:
        chunks = []
        current_text = ""
        is_in_image_section = False
        chunk_index = 0
        
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        for line in lines:
            line = line.strip()
            
            if line.startswith("===================="):
                # Si on a du texte accumulé, on le traite
                if current_text:
                    chunks.extend(self._create_chunks(current_text, is_in_image_section, str(file_path), chunk_index))
                    chunk_index += 1
                    current_text = ""
                
                is_in_image_section = "IMAGE" in line
                continue
                
            if line and not line.startswith("="):
                current_text += line + "\n"
                
        # Traiter le dernier chunk
        if current_text:
            chunks.extend(self._create_chunks(current_text, is_in_image_section, str(file_path), chunk_index))
            
        return chunks
    
    def _create_chunks(self, text: str, is_image_description: bool, source_file: str, chunk_index: int) -> List[TranscriptChunk]:
        # Pour les descriptions d'images, on garde tout en un seul chunk
        if is_image_description:
            return [TranscriptChunk(text, True, source_file, chunk_index)]
        
        # Pour le texte normal, on découpe en chunks de taille fixe
        chunks = []
        words = text.split()
        current_chunk = []
        current_length = 0
        
        for word in words:
            current_length += len(word) + 1
            current_chunk.append(word)
            
            if current_length >= self.chunk_size:
                chunk_text = " ".join(current_chunk)
                chunks.append(TranscriptChunk(chunk_text, False, source_file, chunk_index))
                current_chunk = []
                current_length = 0
                chunk_index += 1
        
        if current_chunk:
            chunk_text = " ".join(current_chunk)
            chunks.append(TranscriptChunk(chunk_text, False, source_file, chunk_index))
            
        return chunks
    
    def load_all_transcripts(self):
        """Charge et traite tous les fichiers de transcription."""
        if not self._should_update_cache():
            print("Chargement depuis le cache...")
            # Charger depuis le cache
            with open(self.chunks_cache_file, 'r') as f:
                chunks_data = json.load(f)
                self.chunks = [TranscriptChunk.from_dict(chunk_data) for chunk_data in chunks_data]
            
            # Charger les embeddings
            embeddings = torch.load(self.embeddings_cache_file)
            for chunk, embedding in zip(self.chunks, embeddings):
                chunk.embedding = embedding
            
            return
            
        print("Génération du cache...")
        # Traiter les fichiers et générer les embeddings
        for file_path in self.transcripts_dir.glob("*.txt"):
            new_chunks = self.process_transcript_file(file_path)
            self.chunks.extend(new_chunks)
        
        # Calculer les embeddings pour tous les chunks
        embeddings = []
        for chunk in self.chunks:
            embedding = self.get_embedding(chunk.content)
            chunk.embedding = embedding
            embeddings.append(embedding)
        
        # Sauvegarder dans le cache
        with open(self.chunks_cache_file, 'w') as f:
            json.dump([chunk.to_dict() for chunk in self.chunks], f)
        
        torch.save(embeddings, self.embeddings_cache_file)
        
        # Sauvegarder les métadonnées
        with open(self.metadata_cache_file, 'w') as f:
            json.dump(self._get_files_metadata(), f)
    
    def search(self, query: str, top_k: int = 3) -> List[Tuple[TranscriptChunk, float]]:
        """Recherche les chunks les plus pertinents pour une requête."""
        # Préfixer la requête avec "query: " pour E5
        query = f"query: {query}"
        query_embedding = self.get_embedding(query)
        
        # Calculer les similarités
        similarities = []
        for chunk in self.chunks:
            # Calculer la similarité cosinus
            sim = torch.dot(query_embedding, chunk.embedding).item()
            # Appliquer un facteur de confiance réduit pour les descriptions d'images
            if chunk.is_image_description:
                sim *= 0.7  # Réduire la confiance de 30% pour les descriptions d'images
            similarities.append((chunk, sim))
        
        # Trier par similarité
        similarities.sort(key=lambda x: x[1], reverse=True)
        return similarities[:top_k]
    
    def query(self, user_query: str) -> str:
        """Effectue une requête complète avec recherche et génération de réponse."""
        relevant_chunks = self.search(user_query)
        
        # Construire le contexte pour Mistral
        chunks_text = []
        for chunk, _ in relevant_chunks:
            prefix = "[Description d'image] " if chunk.is_image_description else ""
            chunks_text.append(f"{prefix}{chunk.content}")
        context = "\n\n".join(chunks_text)
        
        # Construire le prompt
        messages = [
            {
                "role": "system",
                "content": "Tu es un assistant qui répond aux questions en te basant sur les transcriptions fournies. "
                          "Certaines informations proviennent de descriptions d'images (marquées comme telles) et sont donc moins fiables "
                          "que le texte pur. Prends cela en compte dans tes réponses. "
                          "Si un message d'accueil ou de réassurance a déjà été donné par l'assistant, ne le répète pas dans ta réponse."
            },
            {
                "role": "user",
                "content": f"Contexte:\n{context}\n\nQuestion: {user_query}"
            }
        ]
        
        # Obtenir la réponse de Mistral
        response = self.mistral_client.chat.complete(
            model="mistral-small-latest",
            messages=messages
        )
        
        return response.choices[0].message.content

# Exemple d'utilisation
if __name__ == "__main__":
    rag = TranscriptRAG("/Users/rekta/projet/backend/transcripts")
    rag.load_all_transcripts()
    
    # Exemple de requête
    question = "Qui est le plus beau?"
    reponse = rag.query(question)
    print(f"Question: {question}")
    print('--------------------------------')
    print(f"Réponse: {reponse}") 