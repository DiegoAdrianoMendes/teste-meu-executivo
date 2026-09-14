"""
Script: export_openapi.py
Exporta o schema OpenAPI do FastAPI para um arquivo JSON.
Usado pela GitHub Action do backend para publicar o contrato.

Uso:
    python3 scripts/export_openapi.py [output_path]
    python3 scripts/export_openapi.py openapi.json
"""
import json
import sys
from pathlib import Path

# Adiciona o diretório raiz ao path para importar o app
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.main import app

output_path = sys.argv[1] if len(sys.argv) > 1 else "openapi.json"

schema = app.openapi()
with open(output_path, "w", encoding="utf-8") as f:
    json.dump(schema, f, indent=2, ensure_ascii=False)

print(f"OpenAPI schema exportado para: {output_path}")
print(f"  Versão: {schema.get('info', {}).get('version', 'N/A')}")
print(f"  Endpoints: {len(schema.get('paths', {}))}")
print(f"  Schemas: {len(schema.get('components', {}).get('schemas', {}))}")
