import asyncio
from browser_use import Agent, Browser, ChatBrowserUse 
from browser_use.llm import ChatGroq
# ── Workflow data ─────────────────────────────────────────────────────────────

WORKFLOW = {
    "nodes": [
        {"id": "N1",  "actor": "BUREAU DES MARCHES",                                        "shape": "Rectangle", "text": "Estimation Administratif du Projet"},
        {"id": "N2",  "actor": "BUREAU DES MARCHES",                                        "shape": "Rectangle", "text": "Lancement de la Consultation"},
        {"id": "N3",  "actor": "BUREAU DES MARCHES",                                        "shape": "Rectangle", "text": "Publication Avis de consultation sur le site du MC"},
        {"id": "N4",  "actor": "BUREAU DES MARCHES",                                        "shape": "Rectangle", "text": "Attribution provisoire"},
        {"id": "N5",  "actor": "BUREAU DES MARCHES",                                        "shape": "Rectangle", "text": "Attribution Definitive"},
        {"id": "N6",  "actor": "BUREAU DES MARCHES",                                        "shape": "Rectangle", "text": "Signature Contrat"},
        {"id": "N7",  "actor": "PRESIDENT DE LA COMMISSION INTERNE D'OUVERTURE DES PLIS",   "shape": "Rectangle", "text": "Lettre"},
        {"id": "N8",  "actor": "PRESIDENT DE LA COMMISSION INTERNE D'OUVERTURE DES PLIS",   "shape": "Rectangle", "text": "Depot des offres"},
        {"id": "N9",  "actor": "PRESIDENT DE LA COMMISSION INTERNE D'OUVERTURE DES PLIS",   "shape": "Diamond",   "text": "ETUDE"},
        {"id": "N10", "actor": "COMMISSION OUVERTURE DES PLIS",                             "shape": "Rectangle", "text": "PV D'OUVERTURE DES PLIS"},
        {"id": "N11", "actor": "COMMISSION EVALUATION DES OFFRES",                          "shape": "Rectangle", "text": "PV EVALUATION DES OFFRES"},
        {"id": "N12", "actor": "COMMISSION OUVERTURE ET EVALUATION DES OFFRES",             "shape": "Rectangle", "text": "PV OUVERTURE ET EVALUATION DES OFFRES"},
        {"id": "N13", "actor": "DAM",                                                       "shape": "Rectangle", "text": "Distribution"},
        {"id": "N14", "actor": "FIN",                                                       "shape": "Oval",      "text": "Classement"},
    ],
    "edges": [
        {"from": "N1",  "to": "N2",  "label": None},
        {"from": "N2",  "to": "N7",  "label": None},
        {"from": "N2",  "to": "N3",  "label": None},
        {"from": "N7",  "to": "N8",  "label": None},
        {"from": "N8",  "to": "N9",  "label": None},
        {"from": "N9",  "to": "N12", "label": "NON"},
        {"from": "N9",  "to": "N10", "label": "OUI"},
        {"from": "N10", "to": "N11", "label": None},
        {"from": "N11", "to": "N13", "label": None},
        {"from": "N12", "to": "N13", "label": None},
        {"from": "N13", "to": "N4",  "label": None},
        {"from": "N4",  "to": "N5",  "label": None},
        {"from": "N5",  "to": "N6",  "label": None},
    ]
}

# ── Configuration ─────────────────────────────────────────────────────────────

OPENBEE_URL   = "https://myetch.openbeedemo.com"   
USERNAME      = "Corleone"                             
PASSWORD      = "Openbee1234!"                     
WORKFLOW_NAME = "Generated Workflow"

LLM = llm = ChatGroq(
	model='meta-llama/llama-4-scout-17b-16e-instruct',
	# temperature=0.1,
)

# ── Build task prompt ─────────────────────────────────────────────────────────

def build_task(workflow: dict) -> str:
    nodes = workflow["nodes"]
    edges = workflow["edges"]
    actors = list(dict.fromkeys(n["actor"] for n in nodes))

    node_lines = "\n".join(
        f"  - ID={n['id']}  shape={n['shape']}  actor=\"{n['actor']}\"  label=\"{n['text']}\""
        for n in nodes
    )
    edge_lines = "\n".join(
        f"  - {e['from']} → {e['to']}" + (f"  [label: {e['label']}]" if e["label"] else "")
        for e in edges
    )
    actor_lines = "\n".join(f"  - {a}" for a in actors)

    return f"""
You are automating the creation of a workflow inside the OpenBee Portal application.

## Step 1 — Log in
Go to: {OPENBEE_URL}
Username: {USERNAME}
Password: {PASSWORD}

Wait for the application to fully load (the loading spinner disappears and the main navigation is visible).

## Step 2 — Navigate to the workflow designer
Find the "Workflow" or "Workflows" section in the administration menu and open it.
Then create a new workflow named: "{WORKFLOW_NAME}"

## Step 3 — Create swimlanes / actor lanes
The workflow has {len(actors)} actors. Create one swimlane (also called "lane", "pool", or "actor") for each:
{actor_lines}

## Step 4 — Add nodes
Add each node below to the canvas. Place each node in the swimlane that matches its actor.
Use the correct shape for each node:
  - Rectangle = task or activity
  - Diamond   = decision or gateway (typically a diamond/rhombus shape)
  - Oval      = start or end event (typically a circle or rounded shape)

{node_lines}

## Step 5 — Draw connections
Connect the nodes with arrows exactly as listed. Where a label is shown (OUI / NON),
set that label on the arrow after drawing it.

{edge_lines}

## Step 6 — Save
Save or publish the workflow. Confirm it is saved successfully.

## Important rules
- Wait for the UI to respond after each action before moving on.
- Handle any dialog or popup that appears before continuing.
- Do NOT skip any node or connection.
- If placing nodes requires drag-and-drop from a shape palette, drag the correct shape onto the canvas.
- After placing each node, set its label and assign it to the correct actor lane.
"""

# ── Run ───────────────────────────────────────────────────────────────────────

async def main(workflow=WORKFLOW ,):
    task = build_task(workflow)

    browser = Browser()

    agent = Agent(
        task=task,
        llm=LLM,
        browser=browser,
        max_actions_per_step=5,
        max_failures=3,
    )

    print("Starting browser-use agent with Gemini...")
    print(f"Target: {OPENBEE_URL}")
    print(f"Nodes: {len(WORKFLOW['nodes'])}  |  Edges: {len(WORKFLOW['edges'])}\n")

    result = await agent.run(max_steps=120)

    print("\n── Agent result ──────────────────────────────")
    print(result)


if __name__ == "__main__":
    asyncio.run(main())