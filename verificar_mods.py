#!/usr/bin/env python3
"""
Verifica a lateralidade (client/server/both) de mods Minecraft (.jar)
Prioridade: metadados internos (Fabric/Quilt/Forge) > Modrinth > CurseForge.
Requer Python 3.8+ e requests, tqdm, tomli (opcional para Python ≥ 3.11).
"""

import os
import re
import sys
import json
import zipfile
import shutil
import argparse
from typing import Optional, Dict, List, Tuple

import requests
from tqdm import tqdm

# Tenta importar tomllib (Python 3.11+), senão usa tomli
try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None
        print("Aviso: 'tomli' não instalado. A extração de metadados de mods Forge será menos precisa.",
              file=sys.stderr)

# ----------------------------------------------------------------------
# Configurações das APIs
MODRINTH_API = "https://api.modrinth.com/v2"
MODRINTH_SEARCH = f"{MODRINTH_API}/search"
CURSEFORGE_API = "https://api.curseforge.com/v1"
CURSEFORGE_GAME_ID = 432
USER_AGENT = "ModSideChecker/3.0 (contato@exemplo.com)"

CF_CAT_CLIENT = 428
CF_CAT_SERVER = 429
API_KEY_FILE = "curseforge_api_key.txt"

# ----------------------------------------------------------------------
def load_saved_api_key() -> Optional[str]:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    key_path = os.path.join(script_dir, API_KEY_FILE)
    if os.path.isfile(key_path):
        with open(key_path, 'r', encoding='utf-8') as f:
            return f.read().strip()
    return None

def save_api_key(key: str):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    key_path = os.path.join(script_dir, API_KEY_FILE)
    with open(key_path, 'w', encoding='utf-8') as f:
        f.write(key.strip())

def delete_api_key_file():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    key_path = os.path.join(script_dir, API_KEY_FILE)
    if os.path.isfile(key_path):
        os.remove(key_path)

def prompt_for_api_key() -> Optional[str]:
    saved_key = load_saved_api_key()
    if saved_key:
        tqdm.write("Chave salva do CurseForge encontrada.")
        choice = input("Digite uma nova chave, pressione Enter para usar a salva, "
                       "ou 'none' para não usar o CurseForge: ").strip()
        if choice.lower() == 'none':
            delete_api_key_file()
            tqdm.write("Chave removida. Fallback ao CurseForge desativado.")
            return None
        elif choice == '':
            tqdm.write("Usando chave salva.")
            return saved_key
        else:
            save_api_key(choice)
            tqdm.write("Nova chave salva.")
            return choice
    else:
        choice = input("Digite sua chave da API do CurseForge (ou deixe em branco para pular): ").strip()
        if choice:
            save_api_key(choice)
            tqdm.write("Chave salva para uso futuro.")
            return choice
        else:
            tqdm.write("Nenhuma chave fornecida. Fallback ao CurseForge desativado.")
            return None

# ----------------------------------------------------------------------
def slugify(text: str) -> str:
    cleaned = re.sub(r'[^\w\s-]', '', text).strip().lower()
    return re.sub(r'[\s]+', '-', cleaned)

def extract_manifest_title(jar_path: str) -> Optional[str]:
    try:
        with zipfile.ZipFile(jar_path, 'r') as zf:
            with zf.open('META-INF/MANIFEST.MF') as manifest:
                for line in manifest.read().decode('utf-8').splitlines():
                    if line.strip().lower().startswith('specification-title:'):
                        parts = line.split(':', 1)
                        if len(parts) > 1:
                            return parts[1].strip()
    except Exception:
        pass
    return None

def extract_metadata(jar_path: str) -> Dict[str, Optional[str]]:
    """
    Retorna dicionário com:
        'name'   : nome legível (fabric/quilt/forge) ou None
        'title'  : Specification-Title do MANIFEST.MF ou None
        'slug'   : identificador slug (ex: modid)
        'env'    : 'client', 'server', 'both' ou None se não declarado
                   ( '*' é tratado como 'both' )
    """
    result = {'name': None, 'title': None, 'slug': None, 'env': None}
    try:
        with zipfile.ZipFile(jar_path, 'r') as zf:
            # 1. Fabric / Quilt
            for meta_file in ('fabric.mod.json', 'quilt.mod.json'):
                try:
                    data = json.loads(zf.read(meta_file))
                    name = data.get('name')
                    if name:
                        result['name'] = name
                    modid = data.get('id')
                    if modid:
                        result['slug'] = modid
                    env = data.get('environment', '*')
                    if env == '*':
                        result['env'] = 'both'
                    elif env in ('client', 'server'):
                        result['env'] = env
                    # Se houver 'environment' mas for outro valor, ignoramos (None)
                    return result
                except (KeyError, json.JSONDecodeError):
                    continue

            # 2. Forge (mods.toml)
            if tomllib:
                try:
                    toml_data = tomllib.loads(zf.read('META-INF/mods.toml').decode('utf-8'))
                    mods = toml_data.get('mods', [])
                    if mods:
                        first = mods[0]
                        modid = first.get('modId')
                        display = first.get('displayName')
                        if modid:
                            result['slug'] = modid
                            if display:
                                result['name'] = display
                            side = first.get('side', 'BOTH').upper()
                            if side == 'CLIENT':
                                result['env'] = 'client'
                            elif side == 'SERVER':
                                result['env'] = 'server'
                            else:
                                result['env'] = 'both'
                            return result
                except Exception:
                    pass

            # 3. mcmod.info (legado) – não tem lateralidade confiável
            try:
                data = json.loads(zf.read('mcmod.info'))
                if isinstance(data, list) and data:
                    modid = data[0].get('modid')
                    name = data[0].get('name')
                    if modid:
                        result['slug'] = modid
                    if name:
                        result['name'] = name
                    return result
            except Exception:
                pass

    except (zipfile.BadZipFile, IOError):
        pass

    # Fallback: título do MANIFEST.MF
    title = extract_manifest_title(jar_path)
    result['title'] = title

    # Se não temos slug, gera a partir do nome do arquivo
    if not result['slug']:
        result['slug'] = slugify(os.path.splitext(os.path.basename(jar_path))[0])
    return result

# ----------------------------------------------------------------------
def modrinth_search_slug(slug: str) -> Optional[str]:
    url = f"{MODRINTH_API}/project/{slug}"
    headers = {'User-Agent': USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        client = data.get('client_side', 'unknown')
        server = data.get('server_side', 'unknown')
        client_ok = client in ('required', 'optional')
        server_ok = server in ('required', 'optional')
        client_no = client == 'unsupported'
        server_no = server == 'unsupported'
        if client_ok and server_ok:
            return 'both'
        if client_ok and server_no:
            return 'client'
        if client_no and server_ok:
            return 'server'
        return None
    except requests.RequestException:
        return None

def modrinth_search_text(query: str) -> Optional[str]:
    params = {'query': query, 'limit': 1}
    headers = {'User-Agent': USER_AGENT}
    try:
        resp = requests.get(MODRINTH_SEARCH, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        hits = resp.json().get('hits', [])
        if not hits:
            return None
        slug = hits[0].get('slug')
        if slug:
            return modrinth_search_slug(slug)
    except requests.RequestException:
        pass
    return None

def check_modrinth(terms: List[str]) -> Optional[str]:
    for term in terms:
        side = modrinth_search_slug(term)
        if side:
            return side
        side = modrinth_search_text(term)
        if side:
            return side
    return None

def check_curseforge(terms: List[str], api_key: str) -> Optional[str]:
    if not api_key:
        return None
    headers = {
        'x-api-key': api_key,
        'Accept': 'application/json',
    }
    for term in terms:
        search_url = f"{CURSEFORGE_API}/mods/search"
        params = {
            'gameId': CURSEFORGE_GAME_ID,
            'searchFilter': term,
            'pageSize': 1,
        }
        try:
            resp = requests.get(search_url, headers=headers, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json().get('data', [])
            if data:
                mod_id = data[0]['id']
                details_url = f"{CURSEFORGE_API}/mods/{mod_id}"
                resp = requests.get(details_url, headers=headers, timeout=10)
                resp.raise_for_status()
                mod = resp.json().get('data', {})
                categories = mod.get('categories', [])
                cat_ids = {c['id'] for c in categories}
                is_client = CF_CAT_CLIENT in cat_ids
                is_server = CF_CAT_SERVER in cat_ids
                if is_client and is_server:
                    return 'both'
                if is_client:
                    return 'client'
                if is_server:
                    return 'server'
        except requests.RequestException:
            continue
    return None

def verify_mod(jar_path: str, cf_api_key: Optional[str]) -> Tuple[str, str]:
    filename = os.path.basename(jar_path)
    meta = extract_metadata(jar_path)

    # 1. Verificar se os próprios metadados já definem a lateralidade
    env = meta.get('env')
    if env is not None and env in ('client', 'server', 'both'):
        tqdm.write(f"  {filename}: lateralidade definida pelo mod: {env}")
        return filename, env

    # Se chegou aqui, precisa consultar online
    # Preparar lista de termos de busca (já usada nas funções de fallback)
    search_terms = []
    if meta.get('name'):
        search_terms.append(meta['name'])
    if meta.get('title'):
        search_terms.append(meta['title'])
    slug = meta.get('slug')
    if slug:
        if slug not in search_terms:
            search_terms.append(slug)
    file_term = os.path.splitext(filename)[0]
    file_term_clean = re.sub(r'[-_](v?\d+[\.\d]*).*', '', file_term)
    if file_term_clean and file_term_clean not in search_terms:
        search_terms.append(file_term_clean)

    tqdm.write(f"  {filename}: termos={search_terms}")

    # 2. Modrinth
    side = check_modrinth(search_terms)
    if side:
        tqdm.write(f"  Modrinth: {side}")
        return filename, side

    # 3. CurseForge
    if cf_api_key:
        tqdm.write(f"  Consultando CurseForge...")
        side = check_curseforge(search_terms, cf_api_key)
        if side:
            tqdm.write(f"  CurseForge: {side}")
            return filename, side
        else:
            tqdm.write("  CurseForge: não encontrado.")
    else:
        tqdm.write("  CurseForge: desativado.")

    return filename, 'unknown'

# ----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Verifica a lateralidade de mods Minecraft (.jar).")
    parser.add_argument('--dir', default='.', help="Diretório onde os .jar estão (padrão: diretório atual)")
    args = parser.parse_args()
    target_dir = os.path.abspath(args.dir)

    cf_api_key = prompt_for_api_key()

    jar_files = sorted([f for f in os.listdir(target_dir) if f.endswith('.jar')])
    if not jar_files:
        print("Nenhum arquivo .jar encontrado.")
        return

    total = len(jar_files)
    tqdm.write(f"\nEncontrados {total} mods para verificar.\n")

    results = []
    progress = tqdm(jar_files, desc="Verificando mods", unit="mod", dynamic_ncols=True)

    try:
        for fname in progress:
            full_path = os.path.join(target_dir, fname)
            progress.set_postfix_str(fname)
            name, side = verify_mod(full_path, cf_api_key)
            results.append((name, side))
            progress.update(1)
    except KeyboardInterrupt:
        tqdm.write("\nInterrompido pelo usuário. Gerando resultados parciais...")
    finally:
        progress.close()

    categories = {'client': [], 'server': [], 'both': [], 'unknown': []}
    for name, side in results:
        categories.setdefault(side, []).append(name)

    output_file = os.path.join(target_dir, 'mods_sides.txt')
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("Verificação de lateralidade dos mods\n")
        f.write("=" * 50 + "\n\n")
        for section, label in [('client', 'Client-side'),
                               ('server', 'Server-side'),
                               ('both',   'Both (client e server)'),
                               ('unknown','Desconhecido')]:
            f.write(f"[{label}]\n")
            if categories[section]:
                for mod in sorted(categories[section]):
                    f.write(f"  - {mod}\n")
            else:
                f.write("  (nenhum)\n")
            f.write("\n")

    tqdm.write(f"\nResultado salvo em {output_file}")
    stats = {k: len(v) for k, v in categories.items()}
    tqdm.write(f"Resumo: client={stats['client']}, server={stats['server']}, both={stats['both']}, unknown={stats['unknown']}")

    organize = input("\nDeseja organizar os mods em pastas (client, server, both)? (s/N): ").strip().lower()
    if organize == 's':
        base = target_dir
        client_dir = os.path.join(base, 'client')
        server_dir = os.path.join(base, 'server')
        both_dir   = os.path.join(base, 'both')
        os.makedirs(client_dir, exist_ok=True)
        os.makedirs(server_dir, exist_ok=True)
        os.makedirs(both_dir, exist_ok=True)

        copied = 0
        for name, side in results:
            src = os.path.join(base, name)
            if side == 'client':
                shutil.copy2(src, os.path.join(client_dir, name))
                copied += 1
            elif side == 'server':
                shutil.copy2(src, os.path.join(server_dir, name))
                copied += 1
            elif side == 'both':
                shutil.copy2(src, os.path.join(both_dir, name))
                copied += 1

        tqdm.write(f"{copied} mod(s) copiados para as pastas client/, server/ e both/.")
    else:
        tqdm.write("Organização em pastas ignorada.")

if __name__ == '__main__':
    main()
