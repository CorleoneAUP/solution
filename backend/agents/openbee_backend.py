"""
OPENBEE Backend — Script autonome pour Colab Terminal
Lancer avec : python openbee_backend.py
"""
import os, io, json, uuid, time, base64, tempfile, gc, threading
from typing import List
from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from groq import Groq

# ─── CONFIGURATION ────────────────────────────────────────────────────────────
PORT           = 5000
GROQ_API_KEY   = "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"
NGROK_TOKEN    = "3CoZ53dm0ivd5jQyWOBClLWABK9_288r3Le7bg8wEBQ1oErUG"
MODEL_VISION   = "meta-llama/llama-4-scout-17b-16e-instruct"
MODEL_ANALYSIS = "meta-llama/llama-4-scout-17b-16e-instruct"
MODEL_WHISPER  = "whisper-large-v3"

groq_client = Groq(api_key=GROQ_API_KEY)

# ─── FASTAPI APP ──────────────────────────────────────────────────────────────
app = FastAPI(title="OPENBEE Groq Backend")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
tasks = {}

# ─── LOGGING ─────────────────────────────────────────────────────────────────
def log(tid, msg, lvl="info"):
    if tid in tasks:
        tasks[tid]["logs"].append({"ts": time.strftime("%H:%M:%S"), "msg": msg, "level": lvl})
        print(f"[{lvl.upper()}] {msg}", flush=True)

# ─── HELPERS GROQ ─────────────────────────────────────────────────────────────
def groq_vision(image_b64: str, prompt: str, mime: str = "image/jpeg") -> str:
    resp = groq_client.chat.completions.create(
        model=MODEL_VISION,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}}
        ]}],
        max_tokens=4096
    )
    return resp.choices[0].message.content

def groq_text(system: str, prompt: str, retries: int = 3) -> str:
    for attempt in range(retries):
        try:
            resp = groq_client.chat.completions.create(
                model=MODEL_ANALYSIS,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": prompt}
                ],
                max_tokens=8192
            )
            return resp.choices[0].message.content
        except Exception as e:
            err = str(e)
            if "rate_limit" in err.lower() or "429" in err:
                wait = (attempt + 1) * 20  # 20s, 40s, 60s
                print(f"[WARN] Rate limit Groq — attente {wait}s avant retry {attempt+1}/{retries}...")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("Groq rate limit persistant après plusieurs tentatives.")

def groq_whisper(audio_bytes: bytes, filename: str) -> str:
    return groq_client.audio.transcriptions.create(
        model=MODEL_WHISPER,
        file=(filename, audio_bytes),
        response_format="text"
    )

# ─── PROCESSEURS ──────────────────────────────────────────────────────────────
def process_pdf(item, task_id):
    from pdf2image import convert_from_bytes
    log(task_id, f"📄 PDF : {item['name']}", "proc")
    pages = convert_from_bytes(item["bytes"], dpi=120)
    parts = []
    for i, page in enumerate(pages, 1):
        log(task_id, f"   → OCR page {i}/{len(pages)}", "info")
        buf = io.BytesIO(); page.save(buf, format="JPEG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        text = groq_vision(b64,
            "Extrais tout le texte de cette image avec précision. "
            "Si elle contient un diagramme, décris-le en détail.")
        parts.append(f"**Page {i}**\n{text}")
        del buf, b64, text; gc.collect()
    log(task_id, f"   ✅ PDF terminé ({len(pages)} pages)", "ok")
    return f"## 📄 {item['name']}\n\n" + "\n\n".join(parts)

def process_image(item, task_id):
    log(task_id, f"🖼️ Image : {item['name']}", "proc")
    mime = "image/png" if item["name"].lower().endswith(".png") else "image/jpeg"
    b64 = base64.b64encode(item["bytes"]).decode()
    text = groq_vision(b64, "Extrais le texte. Si c'est un diagramme, décris-le.", mime)
    log(task_id, "   ✅ Image analysée", "ok")
    return f"## 🖼️ {item['name']}\n\n{text}"

def process_text(item, task_id):
    log(task_id, f"📝 Texte : {item['name']}", "proc")
    content = item["bytes"].decode("utf-8", errors="replace")
    log(task_id, "   ✅ Texte lu directement", "ok")
    return f"## 📝 {item['name']}\n\n{content}"

def process_audio(item, task_id):
    log(task_id, f"🎙 Audio : {item['name']}", "proc")
    txt = groq_whisper(item["bytes"], item["name"])
    log(task_id, "   ✅ Transcription terminée", "ok")
    return f"## 🎙 {item['name']}\n\n{txt}"

# ─── PIPELINE SÉQUENTIELLE ────────────────────────────────────────────────────
def run_pipeline(task_id, queue):
    tasks[task_id]["status"] = "processing"
    sections = []
    try:
        for item in queue:
            t = item["type"]
            if   t == "pdf":   sections.append(process_pdf(item, task_id))
            elif t == "audio": sections.append(process_audio(item, task_id))
            elif t == "image": sections.append(process_image(item, task_id))
            elif t == "text":  sections.append(process_text(item, task_id))
            else: log(task_id, f"⚠️ Type ignoré : {t}", "warn")

        context = "\n\n---\n\n".join(sections)
        log(task_id, f"🔄 Analyse métier ({MODEL_ANALYSIS})...", "proc")

        SYSTEM = (
            'Tu es un expert BPMN (Business Process Model and Notation) spécialisé dans la modélisation de processus administratifs.\n'
            'Ta seule mission est d\'analyser un texte brut et d\'en extraire un JSON de workflow BPMN.\n'
            'Tu ne rédiges JAMAIS de texte libre. Tu réponds UNIQUEMENT avec un objet JSON valide, sans balises markdown.\n\n'
            '═══ RÈGLES DE DÉTECTION ═══\n\n'
            'ACTEURS (= colonnes swimlane) :\n'
            '→ Tout département, service, rôle ou personne qui EFFECTUE au moins une action dans le texte.\n'
            '→ Noms en MAJUSCULES, courts et explicites (ex: "BUREAU DES MARCHES", "COMMISSION INTERNE").\n'
            '→ Toujours inclure "FIN" comme dernière colonne : elle reçoit tous les nœuds terminaux.\n\n'
            'NŒUDS (nodes) :\n'
            '→ shape "Oval" — Événement de DÉBUT ou de FIN :\n'
            '   - Début : premier déclencheur du processus.\n'
            '   - Fin : état terminal (classement, archivage, notification finale).\n'
            '   - Les Oval de FIN sont TOUJOURS dans l\'acteur "FIN".\n\n'
            '→ shape "Rectangle" — Tâche ou activité concrète :\n'
            '   - Détection : verbe d\'action + sujet identifiable (qui fait quoi).\n'
            '   - Texte court et actionnable (verbe à l\'infinitif si possible).\n'
            '   - Ex: "le bureau prépare l\'estimation" → Rectangle, actor="BUREAU DES MARCHES", text="Préparer l\'estimation"\n\n'
            '→ shape "Diamond" — Décision / Passerelle XOR :\n'
            '   - Détection : "si", "selon", "en cas de", résultat binaire, question conditionnelle.\n'
            '   - Une Diamond a TOUJOURS exactement 2 sorties (edges) avec labels OUI/NON.\n'
            '   - Texte = question courte : "Visa accordé ?", "Recours fondé ?", "Offre conforme ?"\n\n'
            'EDGES (= flèches) :\n'
            '→ label null : flux normal séquentiel.\n'
            '→ label "OUI" / "NON" : sorties obligatoires d\'une Diamond.\n'
            '→ label texte libre : sortie nommée si le domaine l\'exige.\n'
            '→ IDs des nœuds séquentiels : "N1", "N2", "N3"...\n\n'
            '═══ FORMAT DE SORTIE OBLIGATOIRE ═══\n'
            'JSON valide uniquement, sans balises markdown, sans commentaire, sans texte avant ou après.\n'
            '{"actors": [...], "nodes": [{"id":"N1","actor":"...","shape":"Oval|Rectangle|Diamond","text":"..."},...], '
            '"edges": [{"from":"N1","to":"N2","label":null},...] }'
        )

        USER = (
            '═══ EXEMPLE (few-shot) ═══\n'
            'Texte source :\n'
            '"Après attribution définitive, le service contractant prépare le projet de contrat en 4 exemplaires.\n'
            'Le dossier complet est transmis à la commission sectorielle pour vérification.\n'
            'Si le dossier est conforme, la commission délivre un visa et on passe à la signature du contrat.\n'
            'Si non conforme, la procédure est déclarée infructueuse et le soumissionnaire peut exercer un recours.\n'
            'La commission sectorielle étudie le recours : si fondé, l\'attribution est annulée ; sinon, attribution définitive.\n'
            'Après signature, un ODS est émis, puis le dossier est classé."\n\n'
            'JSON attendu :\n'
            '{\n'
            '  "actors": ["SERVICE CONTRACTANT", "COMMISSION SECTORIELLE", "FIN"],\n'
            '  "nodes": [\n'
            '    {"id":"N1",  "actor":"SERVICE CONTRACTANT",    "shape":"Oval",      "text":"Attribution définitive"},\n'
            '    {"id":"N2",  "actor":"SERVICE CONTRACTANT",    "shape":"Rectangle", "text":"Préparer projet de contrat (4 ex.)"},\n'
            '    {"id":"N3",  "actor":"COMMISSION SECTORIELLE", "shape":"Rectangle", "text":"Vérifier dossier complet"},\n'
            '    {"id":"N4",  "actor":"COMMISSION SECTORIELLE", "shape":"Diamond",   "text":"Dossier conforme ?"},\n'
            '    {"id":"N5",  "actor":"SERVICE CONTRACTANT",    "shape":"Rectangle", "text":"Procédure infructueuse"},\n'
            '    {"id":"N6",  "actor":"COMMISSION SECTORIELLE", "shape":"Diamond",   "text":"Recours fondé ?"},\n'
            '    {"id":"N7",  "actor":"COMMISSION SECTORIELLE", "shape":"Rectangle", "text":"Annuler attribution provisoire"},\n'
            '    {"id":"N8",  "actor":"COMMISSION SECTORIELLE", "shape":"Rectangle", "text":"Délivrer visa — Attribution définitive"},\n'
            '    {"id":"N9",  "actor":"SERVICE CONTRACTANT",    "shape":"Rectangle", "text":"Signer le contrat"},\n'
            '    {"id":"N10", "actor":"SERVICE CONTRACTANT",    "shape":"Rectangle", "text":"Émettre ODS"},\n'
            '    {"id":"N11", "actor":"FIN",                   "shape":"Oval",      "text":"Classement dossier"}\n'
            '  ],\n'
            '  "edges": [\n'
            '    {"from":"N1",  "to":"N2",  "label":null},\n'
            '    {"from":"N2",  "to":"N3",  "label":null},\n'
            '    {"from":"N3",  "to":"N4",  "label":null},\n'
            '    {"from":"N4",  "to":"N5",  "label":"NON"},\n'
            '    {"from":"N4",  "to":"N8",  "label":"OUI"},\n'
            '    {"from":"N5",  "to":"N6",  "label":null},\n'
            '    {"from":"N6",  "to":"N7",  "label":"OUI"},\n'
            '    {"from":"N6",  "to":"N8",  "label":"NON"},\n'
            '    {"from":"N7",  "to":"N11", "label":null},\n'
            '    {"from":"N8",  "to":"N9",  "label":null},\n'
            '    {"from":"N9",  "to":"N10", "label":null},\n'
            '    {"from":"N10", "to":"N11", "label":null}\n'
            '  ]\n'
            '}\n\n'
            '═══ TEXTE À ANALYSER ═══\n'
            f'{context[:14000]}\n\n'
            'Génère maintenant le JSON BPMN complet pour ce texte. Réponds uniquement avec le JSON, sans aucun texte autour.'
        )

        analysis = groq_text(SYSTEM, USER)
        log(task_id, "   ✅ JSON BPMN généré", "ok")

        # Nettoyage et parsing du JSON retourné
        import re
        json_clean = re.sub(r'^```(?:json)?\s*|\s*```$', '', analysis.strip(), flags=re.MULTILINE)
        try:
            workflow = json.loads(json_clean)
        except Exception:
            workflow = {"raw": json_clean, "parse_error": True}

        tasks[task_id]["result"] = {
            "context": context,    # Texte brut extrait (OCR / transcriptions)
            "workflow": workflow   # JSON BPMN structuré
        }
        tasks[task_id]["status"] = "completed"
        log(task_id, "🏁 Pipeline terminée !", "ok")

    except Exception as e:
        import traceback
        tasks[task_id]["status"] = "failed"
        log(task_id, f"❌ Erreur : {e}", "warn")
        traceback.print_exc()

# ─── ENDPOINTS ────────────────────────────────────────────────────────────────
@app.post("/ingest")
async def ingest(bg: BackgroundTasks, files: List[UploadFile] = File(...), metadata: str = Form(...)):
    tid = str(uuid.uuid4()); meta = json.loads(metadata); queue = []
    for f, m in zip(files, meta):
        queue.append({"id": m["id"], "name": f.filename, "type": m["type"], "bytes": await f.read()})
    tasks[tid] = {"status": "queued", "logs": [], "result": None}
    bg.add_task(run_pipeline, tid, queue)
    return {"task_id": tid}

@app.get("/status/{task_id}")
async def get_status(task_id: str):
    return tasks.get(task_id, {"status": "not_found"})

@app.get("/health")
def health():
    return {"status": "ok", "vision": MODEL_VISION, "analysis": MODEL_ANALYSIS}

# ─── LANCEMENT ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    from pyngrok import ngrok
    import socket

    # Vérifier que le port est libre
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(('localhost', PORT)) == 0:
            print(f"⚠️ Port {PORT} déjà occupé. Ferme l'autre processus et réessaie.")
            exit(1)

    # Nettoyage Ngrok (tue tous les tunnels existants)
    print("🧹 Nettoyage des tunnels Ngrok...")
    ngrok.set_auth_token(NGROK_TOKEN)
    try:
        for t in ngrok.get_tunnels():
            print(f"  → Fermeture tunnel : {t.public_url}")
            ngrok.disconnect(t.public_url)
    except Exception:
        pass
    ngrok.kill()
    time.sleep(2)

    # Connexion Ngrok
    ngrok.set_auth_token(NGROK_TOKEN)
    public_url = ngrok.connect(PORT).public_url
    print(f"\n🚀 BACKEND PUBLIC URL : {public_url}")
    print("   👆 Copie cette URL dans ton interface Streamlit\n", flush=True)

    # uvicorn.run() bloquant — parfait pour un script terminal
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
